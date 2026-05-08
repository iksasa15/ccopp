"""
System Probe — Deterministic Windows telemetry gathering.

Critical fixes in v0.2:
  - gather() is the new API (replacing full_snapshot())
  - cpu_percent() is properly warmed up before reading
  - WMI client is lazily initialized and cached
  - Windows admin detection
  - Graceful handling of access-denied processes (System processes need admin)
  - Per-process error isolation
"""

import ctypes
import os
import platform
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil
from loguru import logger


def is_admin() -> bool:
    """
    Check if the current process has Administrator privileges.
    
    On Windows: True only if running elevated.
    On Linux: True only if running as root.
    """
    try:
        if os.name == "nt":
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        else:
            return os.geteuid() == 0
    except Exception:
        return False


def warn_if_not_admin() -> bool:
    """Print a warning if not running with elevated privileges. Returns is_admin status."""
    admin = is_admin()
    if not admin:
        if os.name == "nt":
            logger.warning(
                "⚠️  Not running as Administrator. The Council will see only YOUR "
                "processes. System processes (svchost, lsass, services) will be "
                "INVISIBLE. To detect malware running as SYSTEM, restart the app "
                "as Administrator (right-click → 'Run as administrator')."
            )
        else:
            logger.warning(
                "Not running as root. Limited process visibility on Linux."
            )
    else:
        logger.info("✓ Running with elevated privileges. Full system visibility enabled.")
    return admin


class SystemProbe:
    """Gathers raw system state for the Resource Warden."""

    # Process attributes we want to gather
    PROCESS_ATTRIBUTES = [
        "pid", "ppid", "name", "exe", "cmdline", "username",
        "create_time", "memory_percent", "num_threads", "status",
    ]

    def __init__(self, warm_cpu: bool = True):
        self.is_windows = platform.system() == "Windows"
        self.is_admin = is_admin()
        self._wmi_client = None  # lazy init
        self._cpu_warmed = False

        if warm_cpu:
            # First call to cpu_percent() always returns 0.0 — warm it up
            psutil.cpu_percent(interval=None)
            for proc in psutil.process_iter():
                try:
                    proc.cpu_percent(interval=None)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            self._cpu_warmed = True
            time.sleep(0.5)  # let CPU readings settle

    @property
    def wmi_client(self):
        """Lazy WMI client — only initialize when actually needed."""
        if self._wmi_client is None and self.is_windows:
            try:
                import wmi
                self._wmi_client = wmi.WMI()
                logger.debug("WMI client initialized")
            except ImportError:
                logger.warning("wmi module not available — limited Windows probing")
                self._wmi_client = False  # sentinel for "tried and failed"
            except Exception as e:
                logger.error(f"WMI initialization failed: {e}")
                self._wmi_client = False
        return self._wmi_client if self._wmi_client else None

    # ============================================================
    # Main API
    # ============================================================

    def gather(self) -> dict[str, Any]:
        """
        Take a complete snapshot of system state.
        This is the v0.2 API. Equivalent to full_snapshot() in v0.1.
        """
        return self.full_snapshot()

    def full_snapshot(self) -> dict[str, Any]:
        """Take a complete snapshot."""
        return {
            "timestamp": datetime.utcnow().isoformat(),
            "is_admin": self.is_admin,
            "system": self._system_info(),
            "resources": self._resource_summary(),
            "processes": self._all_processes(),
            "boot_time": datetime.fromtimestamp(psutil.boot_time()).isoformat(),
        }

    # ============================================================
    # System info
    # ============================================================

    def _system_info(self) -> dict[str, Any]:
        return {
            "platform": platform.platform(),
            "hostname": platform.node(),
            "cpu_count": psutil.cpu_count(logical=True),
            "physical_cores": psutil.cpu_count(logical=False),
            "total_memory_gb": round(psutil.virtual_memory().total / (1024**3), 2),
        }

    def _resource_summary(self) -> dict[str, Any]:
        vm = psutil.virtual_memory()
        # Quick interval=0.1 for current snapshot. cpu was warmed in __init__
        return {
            "cpu_percent": psutil.cpu_percent(interval=0.1),
            "memory_percent": vm.percent,
            "memory_available_gb": round(vm.available / (1024**3), 2),
            "disk_usage": self._safe_disk_usage(),
        }

    def _safe_disk_usage(self) -> list[dict[str, Any]]:
        usages = []
        for p in psutil.disk_partitions(all=False):
            if not p.fstype:
                continue  # Skip CD-ROMs / empty drives
            try:
                u = psutil.disk_usage(p.mountpoint)
                usages.append({
                    "mountpoint": p.mountpoint,
                    "fstype": p.fstype,
                    "percent": u.percent,
                    "free_gb": round(u.free / (1024**3), 2),
                })
            except (PermissionError, OSError):
                continue  # e.g. encrypted/locked drives
        return usages

    # ============================================================
    # Process enumeration — the heart of the Resource Warden
    # ============================================================

    def _all_processes(self) -> list[dict[str, Any]]:
        """
        Enumerate processes with rich metadata.
        Each process is wrapped in its own try/except so one failure
        doesn't break the whole scan.
        """
        results = []
        access_denied_count = 0

        for proc in psutil.process_iter(self.PROCESS_ATTRIBUTES):
            try:
                info = self._extract_process_info(proc)
                if info:
                    results.append(info)
            except psutil.AccessDenied:
                access_denied_count += 1
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
            except Exception as e:
                logger.debug(f"Process iteration error: {type(e).__name__}: {e}")
                continue

        if access_denied_count > 0 and not self.is_admin:
            logger.warning(
                f"Access denied for {access_denied_count} process(es). "
                f"Run as Administrator for full visibility."
            )

        return results

    def _extract_process_info(self, proc: psutil.Process) -> dict[str, Any] | None:
        """Pull all relevant fields from a single Process object."""
        try:
            with proc.oneshot():
                info = proc.info.copy() if hasattr(proc, "info") else {}

                # Get cpu_percent SEPARATELY (not in oneshot — needs interval=0.0
                # since we warmed it up)
                try:
                    info["cpu_percent"] = proc.cpu_percent(interval=None)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    info["cpu_percent"] = 0.0

                # Format cmdline
                cmdline = info.get("cmdline")
                info["cmdline"] = " ".join(cmdline) if cmdline else ""

                # Format create_time
                ct = info.get("create_time")
                info["create_time"] = (
                    datetime.fromtimestamp(ct).isoformat() if ct else None
                )

                # Parent process name
                try:
                    parent = proc.parent()
                    info["parent_name"] = parent.name() if parent else None
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    info["parent_name"] = None

                # Connection count (full details handled by Traffic Observer)
                try:
                    conns = proc.net_connections(kind="inet")
                    info["connection_count"] = len(conns)
                except (psutil.AccessDenied, psutil.NoSuchProcess):
                    info["connection_count"] = None
                except Exception:
                    # Newer psutil may have removed this method or restrict it
                    info["connection_count"] = None

                return info

        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            return None

    # ============================================================
    # Optional WMI features (slow — call sparingly)
    # ============================================================

    def autoruns_via_wmi(self) -> list[dict[str, Any]]:
        """
        List startup entries (registry Run keys, scheduled tasks).
        Slow: call once per scan, not per process.
        """
        client = self.wmi_client
        if not client:
            return []

        autoruns = []
        try:
            for entry in client.Win32_StartupCommand():
                autoruns.append({
                    "name": getattr(entry, "Name", None),
                    "command": getattr(entry, "Command", None),
                    "location": getattr(entry, "Location", None),
                    "user": getattr(entry, "User", None),
                })
        except Exception as e:
            logger.error(f"WMI autoruns query failed: {e}")

        return autoruns

    def services(self) -> list[dict[str, Any]]:
        """List Windows services. Slow."""
        client = self.wmi_client
        if not client:
            return []

        services = []
        try:
            for svc in client.Win32_Service():
                services.append({
                    "name": getattr(svc, "Name", None),
                    "display_name": getattr(svc, "DisplayName", None),
                    "state": getattr(svc, "State", None),
                    "start_mode": getattr(svc, "StartMode", None),
                    "path": getattr(svc, "PathName", None),
                    "account": getattr(svc, "StartName", None),
                })
        except Exception as e:
            logger.error(f"WMI services query failed: {e}")

        return services

    def network_connections(self) -> list[dict[str, Any]]:
        """All TCP/UDP connections — system-wide."""
        connections = []
        try:
            for conn in psutil.net_connections(kind="inet"):
                connections.append({
                    "fd": conn.fd,
                    "family": conn.family.name if hasattr(conn.family, "name") else str(conn.family),
                    "type": conn.type.name if hasattr(conn.type, "name") else str(conn.type),
                    "local_ip": conn.laddr.ip if conn.laddr else None,
                    "local_port": conn.laddr.port if conn.laddr else None,
                    "remote_ip": conn.raddr.ip if conn.raddr else None,
                    "remote_port": conn.raddr.port if conn.raddr else None,
                    "status": conn.status,
                    "pid": conn.pid,
                })
        except (psutil.AccessDenied, PermissionError):
            if not self.is_admin:
                logger.warning(
                    "net_connections requires Administrator on Windows. "
                    "Network analysis will be limited."
                )
        return connections

    # ============================================================
    # Quick filesystem scan (used by scan-system API)
    # ============================================================

    def scan_filesystem_quick(
        self,
        *,
        max_files: int = 8000,
        max_findings: int = 60,
    ) -> dict[str, Any]:
        """
        Fast best-effort file scan across high-risk locations.
        This is intentionally bounded to avoid long API latency.
        """
        roots = self._default_file_scan_roots()
        suspicious_ext = {
            ".exe", ".dll", ".scr", ".msi", ".bat", ".cmd", ".ps1", ".vbs",
            ".js", ".jar", ".hta", ".sh", ".app", ".dylib",
        }

        findings: list[dict[str, Any]] = []
        scanned_files = 0
        skipped_roots: list[str] = []

        for root in roots:
            p = Path(root).expanduser()
            if not p.exists():
                skipped_roots.append(str(p))
                continue

            try:
                for dirpath, _, filenames in os.walk(p):
                    for name in filenames:
                        scanned_files += 1
                        if scanned_files > max_files:
                            break

                        full = Path(dirpath) / name
                        ext = full.suffix.lower()
                        if ext not in suspicious_ext:
                            continue

                        lowered = name.lower()
                        signals: list[str] = []

                        # Example: invoice.pdf.exe
                        if any(
                            lowered.endswith(f"{mid}{ext}")
                            for mid in (".pdf", ".doc", ".docx", ".txt", ".jpg", ".png")
                        ):
                            signals.append("double_extension_disguise")

                        if "temp" in str(full).lower() or "download" in str(full).lower():
                            signals.append("exec_in_temp_or_downloads")

                        if ext in {".ps1", ".vbs", ".js", ".hta"}:
                            signals.append("script_dropper_extension")

                        if not signals:
                            continue

                        findings.append(
                            {
                                "path": str(full),
                                "extension": ext,
                                "signals": signals,
                                "recommended_action": "review_file",
                            }
                        )
                        if len(findings) >= max_findings:
                            break
                    if scanned_files > max_files or len(findings) >= max_findings:
                        break
            except (PermissionError, OSError):
                skipped_roots.append(str(p))
                continue

            if scanned_files > max_files or len(findings) >= max_findings:
                break

        return {
            "enabled": True,
            "roots": roots,
            "scanned_files": scanned_files,
            "max_files": max_files,
            "findings_count": len(findings),
            "findings": findings,
            "skipped_roots": skipped_roots,
        }

    def _default_file_scan_roots(self) -> list[str]:
        home = Path.home()
        if os.name == "nt":
            return [
                os.environ.get("TEMP", r"C:\Windows\Temp"),
                os.environ.get("APPDATA", str(home / "AppData" / "Roaming")),
                str(home / "Downloads"),
                r"C:\Users\Public",
            ]
        return [
            "/tmp",
            "/var/tmp",
            str(home / "Downloads"),
            str(home / "Library" / "LaunchAgents"),
        ]
