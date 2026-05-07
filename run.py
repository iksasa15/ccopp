"""
Council of Agents — v0.2 Entry Point.

Wires together all subsystems:
  - LLM client + Validator
  - Resilience primitives
  - Agents (Resource Warden, Arbitrator)
  - LangGraph orchestration
  - Database + Audit Log
  - Trust Manager + Baseline + Reputation
  - Notifications
  - Plugin Registry

Commands:
    python run.py scan-system          # Run full council scan
    python run.py scan-archive <path>  # Scan an archive
    python run.py verify-audit         # Verify audit log integrity
    python run.py baseline-stats       # Show baseline learning stats
    python run.py list-quarantine      # List quarantined files

Web UI (React): from repo root run — uvicorn api.app:app --host 127.0.0.1 --port 8765
Then: cd web && npm run dev  → http://localhost:5173
"""

import asyncio
import contextlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml
from loguru import logger
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from validation.schemas import AgentName, CouncilDecision, ScanContext, ThreatLevel

console = Console()


# ============================================================
# Configuration loading
# ============================================================

def load_config(config_path: str = "./config/settings.yaml") -> dict[str, Any]:
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def setup_logging(config: dict[str, Any]) -> None:
    log_cfg = config.get("logging", {})
    log_dir = Path(config["app"]["log_dir"])
    log_dir.mkdir(parents=True, exist_ok=True)

    logger.remove()
    logger.add(
        sys.stderr,
        level=log_cfg.get("level", "INFO"),
        format=log_cfg.get("format", "{time} | {level} | {message}"),
    )
    logger.add(
        log_dir / "council_{time}.log",
        rotation=log_cfg.get("rotation", "10 MB"),
        retention=log_cfg.get("retention", "30 days"),
        level="DEBUG",
    )


# ============================================================
# System initialization
# ============================================================

class CouncilSystem:
    """Top-level coordinator. Wires all components."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.db = None
        self.audit = None
        self.baseline = None
        self.reputation = None
        self.trust = None
        self.notifications = None
        self.registry = None
        self.graph = None

    async def initialize(self) -> None:
        """Bring all subsystems online."""
        # Ensure data directories exist
        Path(self.config["app"]["data_dir"]).mkdir(parents=True, exist_ok=True)

        # 1. Database + Audit Log
        from persistence.models import Database
        from security.audit_log import AuditLogger, AuditEvent

        self.db = Database(self.config["database"]["url"])
        await self.db.init_schema()
        self.audit = AuditLogger(self.db)
        await self.audit.log(AuditEvent.SYSTEM_STARTED, "system", {"version": self.config["app"]["version"]})

        # Verify audit integrity on startup
        if self.config.get("audit", {}).get("verify_on_startup", True):
            integrity = await self.audit.verify_integrity()
            if not integrity["is_valid"]:
                logger.error(f"AUDIT LOG TAMPERING DETECTED at entry #{integrity['broken_at']}")
                console.print(Panel(
                    f"[bold red]⚠️  Audit log integrity check FAILED[/bold red]\n"
                    f"Tampering detected at entry #{integrity['broken_at']}",
                    title="Security Alert",
                ))

        # 2. Intelligence layer
        from intelligence.behavioral_baseline import BehavioralBaseline
        from intelligence.reputation import ReputationEngine
        from intelligence.trust_manager import TrustManager

        self.baseline = BehavioralBaseline(self.config["baseline"]["storage_path"])
        self.reputation = ReputationEngine(self.config["reputation"]["db_path"])
        self.trust = TrustManager()

        # 3. Notifications
        from notifications.manager import (
            NotificationManager, NotificationConfig, NotificationChannel,
            windows_toast_handler, in_app_handler_factory
        )

        notif_cfg = self.config.get("notifications", {})
        if notif_cfg.get("enabled", True):
            self.notifications = NotificationManager()
            self._websocket_clients = set()
            self.notifications.register_handler(
                NotificationChannel.TOAST, windows_toast_handler
            )
            self.notifications.register_handler(
                NotificationChannel.IN_APP,
                in_app_handler_factory(self._websocket_clients),
            )

        # 4. Plugin Registry
        from plugins.registry import get_registry
        self.registry = get_registry()
        if self.config.get("plugins", {}).get("auto_discover_entry_points", True):
            self.registry.discover_entry_points()

        # 5. LangGraph
        from core.council_graph import build_council_graph
        self.graph = build_council_graph()

        logger.info("Council system initialized")

    async def shutdown(self) -> None:
        """Clean shutdown."""
        from security.audit_log import AuditEvent
        if self.baseline:
            self.baseline.save()
        if self.audit:
            await self.audit.log(AuditEvent.SYSTEM_STOPPED, "system", {})
        if self.db:
            await self.db.close()


# ============================================================
# Commands — shared core for CLI + HTTP API
# ============================================================

@dataclass
class ScanSystemOutcome:
    ok: bool
    scan_id: str
    is_admin: bool
    decision: CouncilDecision | None = None
    error: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "scan_id": self.scan_id,
            "is_admin": self.is_admin,
            "error": self.error,
            "decision": self.decision.model_dump(mode="json") if self.decision else None,
        }


async def execute_scan_system(
    system: CouncilSystem,
    *,
    is_admin: bool | None = None,
    show_progress: bool = True,
) -> ScanSystemOutcome:
    """Run a full council scan; safe to call from FastAPI (set show_progress=False)."""
    from agents.arbitrator import ArbitratorAgent
    from agents.resource_warden import ResourceWarden
    from core.agent_registry import get_agent_registry
    from security.audit_log import AuditEvent
    from tools.system_probe import warn_if_not_admin

    if is_admin is None:
        is_admin = warn_if_not_admin()

    warden = ResourceWarden(
        ollama_url=system.config["llm"]["ollama_url"],
        model_name=system.config["llm"]["primary_model"],
        cpu_threshold=system.config["agents"]["resource_warden"]["cpu_threshold_percent"],
        mem_threshold=system.config["agents"]["resource_warden"]["memory_threshold_percent"],
        pre_filter_top_n=system.config["agents"]["resource_warden"]["pre_filter_top_n"],
        trust_manager=system.trust,
        baseline=system.baseline,
    )
    arbitrator = ArbitratorAgent(
        ollama_url=system.config["llm"]["ollama_url"],
        model_name=system.config["llm"]["primary_model"],
    )

    registry = get_agent_registry()
    scan_id = str(uuid4())
    registry.register_all(
        scan_id,
        {"_warden_instance": warden, "_arbitrator_instance": arbitrator},
    )

    state = {
        "scan_id": scan_id,
        "scan_type": "system",
        "target_path": None,
        "iteration": 0,
        "max_iterations": system.config["agents"]["arbitrator"]["max_iterations"],
        "reports": {},
        "shared_evidence": {},
    }

    await system.audit.log(
        AuditEvent.SCAN_STARTED,
        "user",
        {"scan_id": scan_id, "type": "system", "is_admin": is_admin},
    )

    progress_ctx = (
        console.status("[cyan]Council deliberating...[/cyan]")
        if show_progress
        else contextlib.nullcontext()
    )

    try:
        try:
            with progress_ctx:
                result = await system.graph.ainvoke(
                    state,
                    config={"configurable": {"thread_id": scan_id}},
                )

            decision = result.get("final_decision")

            if system.notifications and decision:
                await system.notifications.notify_decision(decision)

            await system.audit.log(
                AuditEvent.SCAN_COMPLETED,
                "system",
                {
                    "scan_id": scan_id,
                    "threat_level": decision.overall_threat_level.value if decision else "unknown",
                    "findings_count": len(decision.primary_findings) if decision else 0,
                },
            )

            system.baseline.save()
            return ScanSystemOutcome(
                ok=True, scan_id=scan_id, is_admin=is_admin, decision=decision
            )
        except Exception as e:
            logger.exception(f"Scan failed: {e}")
            await system.audit.log(
                AuditEvent.SCAN_FAILED,
                "system",
                {"scan_id": scan_id, "error": str(e)},
            )
            return ScanSystemOutcome(
                ok=False, scan_id=scan_id, is_admin=is_admin, error=str(e)
            )
    finally:
        registry.unregister(scan_id)


async def cmd_scan_system(system: CouncilSystem) -> None:
    """Run a full council scan of the system."""
    from tools.system_probe import warn_if_not_admin

    console.print(Panel("[bold cyan]🛡️  Council of Agents — System Scan[/bold cyan]"))

    is_admin = warn_if_not_admin()
    if not is_admin:
        console.print(
            "[yellow]⚠️  Running without Administrator privileges. "
            "Some system processes will be invisible. "
            "Consider re-running as Administrator.[/yellow]\n"
        )

    outcome = await execute_scan_system(system, is_admin=is_admin, show_progress=True)
    if outcome.ok:
        _display_decision(outcome.decision)
    elif outcome.error:
        console.print(f"[red]Scan failed: {outcome.error}[/red]")


async def execute_scan_archive(
    system: CouncilSystem, archive_path: str
) -> dict[str, Any]:
    """Scan an archive; returns JSON-serializable dict."""
    from security.audit_log import AuditEvent
    from tools.archive_inspector import ArchiveInspector

    path = Path(archive_path)
    if not path.exists():
        return {"ok": False, "error": f"File not found: {archive_path}"}

    inspector = ArchiveInspector()
    result = inspector.scan(archive_path)

    await system.audit.log(
        AuditEvent.SCAN_COMPLETED,
        "user",
        {
            "type": "archive",
            "path": archive_path,
            "verdict": result.verdict.value,
            "threats_count": len(result.threats),
        },
    )

    return {
        "ok": True,
        "path": archive_path,
        "verdict": result.verdict.value,
        "archive_type": result.archive_type,
        "file_count": result.file_count,
        "compression_ratio": result.compression_ratio,
        "sha256": result.sha256,
        "threats": result.threats,
        "suspicious_files": result.suspicious_files[:50],
        "nested_archives": result.nested_archives[:50],
        "is_encrypted": result.is_encrypted,
        "inspector_error": result.error,
    }


async def cmd_scan_archive(system: CouncilSystem, archive_path: str) -> None:
    """Scan an archive for threats before extraction."""
    from tools.archive_inspector import ArchiveVerdict

    if not Path(archive_path).exists():
        console.print(f"[red]File not found: {archive_path}[/red]")
        return

    console.print(Panel(f"[bold cyan]📦 Scanning archive: {archive_path}[/bold cyan]"))

    payload = await execute_scan_archive(system, archive_path)
    if not payload.get("ok"):
        console.print(f"[red]{payload.get('error', 'Unknown error')}[/red]")
        return

    verdict_colors = {
        ArchiveVerdict.SAFE: "green",
        ArchiveVerdict.SUSPICIOUS: "yellow",
        ArchiveVerdict.DANGEROUS: "red",
        ArchiveVerdict.UNKNOWN: "white",
    }
    v = ArchiveVerdict(payload["verdict"])
    color = verdict_colors[v]

    console.print(f"[bold {color}]Verdict: {v.value.upper()}[/bold {color}]")
    console.print(
        f"Files: {payload['file_count']} | "
        f"Compression ratio: {payload['compression_ratio']:.1f}:1"
    )

    threats = payload.get("threats") or []
    if threats:
        table = Table(title="Threats Detected", show_lines=True)
        table.add_column("Type", style="red")
        table.add_column("Severity", style="yellow")
        table.add_column("Details")
        for t in threats:
            table.add_row(
                t.get("type", "?"),
                t.get("severity", "?"),
                (t.get("details") or "")[:80],
            )
        console.print(table)

    suspicious = payload.get("suspicious_files") or []
    if suspicious:
        console.print(
            f"\n[yellow]⚠️  {len(suspicious)} suspicious file(s) inside[/yellow]"
        )
        for sf in suspicious[:10]:
            console.print(f"  • {sf['name']}: {[i['type'] for i in sf['issues']]}")

    nested = payload.get("nested_archives") or []
    if nested:
        console.print(f"\n[cyan]📦 {len(nested)} nested archive(s):[/cyan]")
        for na in nested[:5]:
            console.print(f"  • {na}")


async def execute_verify_audit(system: CouncilSystem) -> dict[str, Any]:
    integrity = await system.audit.verify_integrity()
    return dict(integrity)


async def cmd_verify_audit(system: CouncilSystem) -> None:
    """Verify audit log integrity."""
    console.print(Panel("[bold cyan]🔐 Verifying audit log integrity...[/bold cyan]"))

    integrity = await execute_verify_audit(system)

    if integrity["is_valid"]:
        console.print(
            f"[green]✓ Audit log is valid. {integrity['total_entries']} entries verified.[/green]"
        )
    else:
        console.print(
            f"[red]✗ TAMPERING DETECTED at entry #{integrity['broken_at']}[/red]"
        )


async def execute_baseline_stats(system: CouncilSystem) -> dict[str, Any]:
    stats = system.baseline.stats()
    rep_stats = system.reputation.stats()
    return {"baseline": stats, "reputation": rep_stats}


async def cmd_baseline_stats(system: CouncilSystem) -> None:
    """Show behavioral baseline statistics."""
    payload = await execute_baseline_stats(system)
    stats = payload["baseline"]
    rep_stats = payload["reputation"]

    table = Table(title="System Knowledge")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Process baselines", str(stats["total_processes"]))
    table.add_row("Mature baselines (5+ days, 50+ obs)", str(stats["mature_baselines"]))
    table.add_row("Total observations", str(stats["total_observations"]))
    table.add_row("Files in reputation DB", str(rep_stats["total_files_tracked"]))
    table.add_row("User-marked safe", str(rep_stats["user_marked_safe"]))
    table.add_row("User-marked malicious", str(rep_stats["user_marked_malicious"]))

    console.print(table)


async def execute_list_quarantine(system: CouncilSystem) -> dict[str, Any]:
    from security.quarantine import QuarantineManager

    qm = QuarantineManager(system.config["quarantine"]["directory"])
    items = qm.list_quarantined()
    return {"count": len(items), "items": items[:100]}


async def cmd_list_quarantine(system: CouncilSystem) -> None:
    """List quarantined files."""
    payload = await execute_list_quarantine(system)
    items = payload["items"]

    if not items:
        console.print("[green]No quarantined files.[/green]")
        return

    table = Table(title=f"Quarantined Files ({payload['count']})")
    table.add_column("SHA-256 (truncated)", style="cyan")
    table.add_column("Quarantined At")
    table.add_column("Size")

    for item in items[:20]:
        table.add_row(
            item["sha256"][:16] + "...",
            item["quarantined_at"],
            f"{item['size_bytes']:,} bytes",
        )

    console.print(table)


def _display_decision(decision) -> None:
    """Pretty-print a CouncilDecision."""
    if decision is None:
        console.print("[yellow]No decision produced.[/yellow]")
        return

    level_colors = {
        ThreatLevel.CLEAN: "green",
        ThreatLevel.INFO: "blue",
        ThreatLevel.LOW: "yellow",
        ThreatLevel.MEDIUM: "yellow",
        ThreatLevel.HIGH: "red",
        ThreatLevel.CRITICAL: "bold red",
    }
    color = level_colors.get(decision.overall_threat_level, "white")
    
    console.print(Panel(
        f"[{color}]Threat Level: {decision.overall_threat_level.value.upper()}[/{color}]\n"
        f"Confidence: {decision.confidence:.0%}\n"
        f"Consensus: {'✓' if decision.consensus_reached else '✗'}\n\n"
        f"[bold]Arabic:[/bold] {decision.user_summary_ar}\n"
        f"[bold]English:[/bold] {decision.user_summary_en}",
        title="Council Decision",
    ))

    if decision.primary_findings:
        table = Table(title=f"Endorsed Findings ({len(decision.primary_findings)})")
        table.add_column("Agent", style="cyan")
        table.add_column("Level", style="yellow")
        table.add_column("Title")
        table.add_column("Confidence")
        for f in decision.primary_findings[:10]:
            level_color = level_colors.get(f.threat_level, "white")
            table.add_row(
                f.agent_name.value,
                f"[{level_color}]{f.threat_level.value}[/{level_color}]",
                f.title[:60],
                f"{f.confidence:.0%}",
            )
        console.print(table)


# ============================================================
# Main entry point
# ============================================================

async def main():
    if len(sys.argv) < 2:
        console.print("[red]Usage: python run.py <command> [args][/red]")
        console.print("Commands:")
        console.print("  scan-system            — Run a full system scan")
        console.print("  scan-archive <path>    — Scan an archive file")
        console.print("  verify-audit           — Verify audit log integrity")
        console.print("  baseline-stats         — Show learning statistics")
        console.print("  list-quarantine        — List quarantined files")
        sys.exit(1)

    command = sys.argv[1]
    config = load_config()
    setup_logging(config)

    system = CouncilSystem(config)
    await system.initialize()

    try:
        if command == "scan-system":
            await cmd_scan_system(system)
        elif command == "scan-archive":
            if len(sys.argv) < 3:
                console.print("[red]Usage: scan-archive <path>[/red]")
                return
            await cmd_scan_archive(system, sys.argv[2])
        elif command == "verify-audit":
            await cmd_verify_audit(system)
        elif command == "baseline-stats":
            await cmd_baseline_stats(system)
        elif command == "list-quarantine":
            await cmd_list_quarantine(system)
        else:
            console.print(f"[red]Unknown command: {command}[/red]")
    finally:
        await system.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
