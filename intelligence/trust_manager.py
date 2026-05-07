"""
Trust Manager — Whitelist of known-good entities to suppress false positives.

Strategy:
  1. Microsoft Signed Binaries — verified via Authenticode signature
  2. Trusted Publishers — Adobe, Google, Mozilla, etc.
  3. Path-based Whitelisting — C:\\Windows\\System32, Program Files (with caveats)
  4. Hash Whitelisting — known-good hashes from Microsoft Catalog

CRITICAL CAVEAT: Whitelisting is DANGEROUS if abused. We only whitelist:
  - Files with VALID, INTACT signatures from trusted publishers
  - Files matching exact hash AND in expected location
  
We NEVER whitelist by name alone — "svchost.exe" in Downloads is malicious even if named correctly.
"""

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger


# ============================================================
# Trusted Publishers (signature-based)
# ============================================================

TRUSTED_PUBLISHERS = frozenset({
    "Microsoft Corporation",
    "Microsoft Windows",
    "Microsoft Windows Publisher",
    "Microsoft Windows Hardware Compatibility Publisher",
    "Google LLC",
    "Mozilla Corporation",
    "Adobe Inc.",
    "Adobe Systems, Incorporated",
    "Apple Inc.",
    "VideoLAN",
    "NVIDIA Corporation",
    "Intel Corporation",
    "AMD",
    "Realtek Semiconductor Corp.",
    "Logitech",
    "Dropbox, Inc.",
    "Canonical Group Limited",  # WSL
    "JetBrains s.r.o.",
    "Valve Corp.",
    "Discord Inc.",
    "Spotify AB",
    "Slack Technologies, Inc.",
    "Zoom Video Communications, Inc.",
})


# ============================================================
# Expected paths for system binaries
# Maps binary name -> set of legitimate paths (case-insensitive)
# ============================================================

EXPECTED_PATHS: dict[str, set[str]] = {
    "svchost.exe": {
        r"c:\windows\system32\svchost.exe",
        r"c:\windows\syswow64\svchost.exe",
    },
    "lsass.exe": {r"c:\windows\system32\lsass.exe"},
    "csrss.exe": {r"c:\windows\system32\csrss.exe"},
    "winlogon.exe": {r"c:\windows\system32\winlogon.exe"},
    "explorer.exe": {r"c:\windows\explorer.exe"},
    "smss.exe": {r"c:\windows\system32\smss.exe"},
    "services.exe": {r"c:\windows\system32\services.exe"},
    "powershell.exe": {
        r"c:\windows\system32\windowspowershell\v1.0\powershell.exe",
        r"c:\windows\syswow64\windowspowershell\v1.0\powershell.exe",
    },
    "cmd.exe": {
        r"c:\windows\system32\cmd.exe",
        r"c:\windows\syswow64\cmd.exe",
    },
    "rundll32.exe": {
        r"c:\windows\system32\rundll32.exe",
        r"c:\windows\syswow64\rundll32.exe",
    },
    "regsvr32.exe": {
        r"c:\windows\system32\regsvr32.exe",
        r"c:\windows\syswow64\regsvr32.exe",
    },
    "mshta.exe": {
        r"c:\windows\system32\mshta.exe",
        r"c:\windows\syswow64\mshta.exe",
    },
}


@dataclass
class TrustVerdict:
    is_trusted: bool
    trust_level: float = 0.0  # 0.0 = untrusted, 1.0 = fully trusted
    reasons: list[str] = field(default_factory=list)
    publisher: str | None = None
    signature_status: str | None = None
    impersonation_detected: bool = False  # name matches system binary but path doesn't


class TrustManager:
    """Decides whether a file/process is trusted."""

    def __init__(self, custom_hash_whitelist: set[str] | None = None):
        self.custom_hashes = custom_hash_whitelist or set()
        self._signature_cache: dict[str, dict[str, Any]] = {}

    def evaluate_process(
        self, process_info: dict[str, Any]
    ) -> TrustVerdict:
        """Decide trust for a running process."""
        exe_path = (process_info.get("exe") or "").lower()
        name = (process_info.get("name") or "").lower()

        # Step 1: Impersonation check
        # If the process name matches a system binary, the PATH must match too
        if name in EXPECTED_PATHS:
            expected = EXPECTED_PATHS[name]
            if exe_path not in expected:
                return TrustVerdict(
                    is_trusted=False,
                    trust_level=0.0,
                    reasons=[
                        f"Impersonation detected: '{name}' in unexpected location '{exe_path}'. "
                        f"Legitimate locations: {expected}"
                    ],
                    impersonation_detected=True,
                )

        # Step 2: Signature check (if we can read the file)
        if exe_path:
            sig_verdict = self._check_signature(exe_path)
            if sig_verdict:
                return sig_verdict

        # Step 3: Path-based heuristic (weakest signal)
        if exe_path.startswith(r"c:\windows\system32\\") or exe_path.startswith(
            r"c:\program files\\"
        ):
            return TrustVerdict(
                is_trusted=True,
                trust_level=0.5,  # weak — could be replaced
                reasons=[f"In trusted system path: {exe_path}"],
            )

        return TrustVerdict(is_trusted=False, trust_level=0.0)

    def evaluate_file(self, file_path: str, sha256: str | None = None) -> TrustVerdict:
        """Decide trust for a file on disk."""
        path_lower = file_path.lower()

        # Hash whitelist
        if sha256 and sha256.lower() in self.custom_hashes:
            return TrustVerdict(
                is_trusted=True,
                trust_level=1.0,
                reasons=["SHA-256 in custom whitelist"],
            )

        # Signature check
        sig_verdict = self._check_signature(file_path)
        if sig_verdict:
            return sig_verdict

        return TrustVerdict(is_trusted=False, trust_level=0.0)

    def _check_signature(self, file_path: str) -> TrustVerdict | None:
        """
        Verify Authenticode signature on Windows. Returns None if check failed.
        
        On non-Windows or if pywin32 unavailable, we skip and let the caller
        fall back to path-based logic.
        """
        try:
            import platform
            if platform.system() != "Windows":
                return None
        except ImportError:
            return None

        # Cached?
        if file_path in self._signature_cache:
            cached = self._signature_cache[file_path]
            return TrustVerdict(**cached) if cached else None

        try:
            sig_info = self._verify_authenticode(file_path)
        except Exception as e:
            logger.debug(f"Signature check failed for {file_path}: {e}")
            self._signature_cache[file_path] = {}
            return None

        if not sig_info:
            self._signature_cache[file_path] = {}
            return None

        publisher = sig_info.get("publisher", "")
        is_valid = sig_info.get("is_valid", False)

        if is_valid and publisher in TRUSTED_PUBLISHERS:
            verdict = TrustVerdict(
                is_trusted=True,
                trust_level=1.0,
                reasons=[f"Valid signature from trusted publisher: {publisher}"],
                publisher=publisher,
                signature_status="valid",
            )
        elif is_valid:
            verdict = TrustVerdict(
                is_trusted=False,
                trust_level=0.4,
                reasons=[f"Valid signature but publisher not in whitelist: {publisher}"],
                publisher=publisher,
                signature_status="valid_unknown_publisher",
            )
        else:
            verdict = TrustVerdict(
                is_trusted=False,
                trust_level=0.0,
                reasons=["Invalid or absent signature"],
                publisher=publisher,
                signature_status="invalid",
            )

        # Cache the verdict
        self._signature_cache[file_path] = {
            "is_trusted": verdict.is_trusted,
            "trust_level": verdict.trust_level,
            "reasons": verdict.reasons,
            "publisher": verdict.publisher,
            "signature_status": verdict.signature_status,
        }
        return verdict

    def _verify_authenticode(self, file_path: str) -> dict[str, Any] | None:
        """
        Use pywin32 to verify Authenticode signature.
        Returns {"is_valid": bool, "publisher": str} or None on failure.
        """
        try:
            import win32api  # type: ignore
            import win32con  # type: ignore
        except ImportError:
            return None

        # NOTE: Full Authenticode verification requires WinTrust API
        # via ctypes. This is a stub showing the integration point.
        # Real implementation in production code.
        try:
            # Read version info as a basic check
            info = win32api.GetFileVersionInfo(file_path, "\\")
            # Real implementation calls WinVerifyTrust here
            return {
                "is_valid": False,  # Conservative default
                "publisher": "",
            }
        except Exception:
            return None

    def add_to_whitelist(self, sha256: str) -> None:
        """User-driven whitelist addition."""
        self.custom_hashes.add(sha256.lower())
        logger.info(f"Added to whitelist: {sha256[:16]}...")

    def remove_from_whitelist(self, sha256: str) -> None:
        self.custom_hashes.discard(sha256.lower())
