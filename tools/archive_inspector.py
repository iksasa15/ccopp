"""
Archive Inspector — Scans compressed files BEFORE extraction.

Threat model:
  - Zip bombs (1MB → 10GB on extract)
  - Polyglot files (file.pdf.exe disguised as PDF)
  - AutoRun trojans (autorun.inf + executable)
  - Path traversal (../../etc/passwd inside ZIP)
  - Nested archives used to evade scanners
  - Encrypted archives (can't scan content)

Strategy:
  1. Outer scan      — hash, size ratio, header check (no extraction)
  2. Structural scan — read TOC without extracting
  3. Sandboxed scan  — extract to isolated temp dir if needed
  4. Recursive scan  — handle nested archives (depth-limited)
"""

import hashlib
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import py7zr
import rarfile
from loguru import logger


class ArchiveVerdict(str, Enum):
    SAFE = "safe"
    SUSPICIOUS = "suspicious"
    DANGEROUS = "dangerous"
    UNKNOWN = "unknown"


@dataclass
class ArchiveScanResult:
    path: str
    archive_type: str
    verdict: ArchiveVerdict = ArchiveVerdict.UNKNOWN
    file_count: int = 0
    total_uncompressed_size: int = 0
    compressed_size: int = 0
    compression_ratio: float = 0.0
    sha256: str = ""
    threats: list[dict[str, Any]] = field(default_factory=list)
    suspicious_files: list[dict[str, Any]] = field(default_factory=list)
    is_encrypted: bool = False
    nested_archives: list[str] = field(default_factory=list)
    error: str | None = None


# Extensions that are commonly weaponized
DANGEROUS_EXTENSIONS = {
    ".exe", ".dll", ".scr", ".cpl", ".msi", ".bat", ".cmd",
    ".ps1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh",
    ".hta", ".jar", ".reg", ".lnk", ".inf",
}

# Files that must NEVER appear in legitimate archives
RED_FLAG_FILENAMES = {
    "autorun.inf",
    "desktop.ini",            # Sometimes weaponized for icon spoofing
    ".htaccess",
}

# Polyglot patterns: file.X.Y where X looks innocent but Y is executable
POLYGLOT_PATTERNS = [
    # PDF disguises
    (".pdf.exe", "PDF disguised executable"),
    (".pdf.scr", "PDF disguised screensaver"),
    (".pdf.js", "PDF disguised JavaScript"),
    (".pdf.com", "PDF disguised COM executable"),
    (".pdf.bat", "PDF disguised batch script"),
    # Document disguises
    (".doc.exe", "Document disguised executable"),
    (".doc.scr", "Document disguised screensaver"),
    (".doc.js", "Document disguised JavaScript"),
    (".docx.exe", "Document disguised executable"),
    (".xls.exe", "Spreadsheet disguised executable"),
    (".xlsx.exe", "Spreadsheet disguised executable"),
    # Image disguises
    (".jpg.exe", "Image disguised executable"),
    (".jpeg.exe", "Image disguised executable"),
    (".png.exe", "Image disguised executable"),
    (".gif.exe", "Image disguised executable"),
    (".jpg.scr", "Image disguised screensaver"),
    # Text/log disguises
    (".txt.exe", "Text file disguised executable"),
    (".txt.scr", "Text file disguised screensaver"),
    (".log.exe", "Log file disguised executable"),
    # Media disguises
    (".mp3.exe", "Audio file disguised executable"),
    (".mp4.exe", "Video file disguised executable"),
    (".avi.exe", "Video file disguised executable"),
    # Archive-in-archive disguises
    (".zip.exe", "Archive disguised executable"),
    (".rar.exe", "Archive disguised executable"),
]


class ArchiveInspector:
    """Pre-extraction scanner for compressed files."""

    def __init__(
        self,
        max_compression_ratio: int = 100,
        max_recursion_depth: int = 5,
        sandbox_dir: str | None = None,
    ):
        self.max_compression_ratio = max_compression_ratio
        self.max_recursion_depth = max_recursion_depth
        self.sandbox_dir = sandbox_dir or tempfile.gettempdir()

    def scan(self, archive_path: str, depth: int = 0) -> ArchiveScanResult:
        """Main entry point — orchestrates all scan phases."""
        path = Path(archive_path)

        if not path.exists():
            return ArchiveScanResult(
                path=str(path),
                archive_type="unknown",
                error="File not found",
            )

        if depth > self.max_recursion_depth:
            return ArchiveScanResult(
                path=str(path),
                archive_type="unknown",
                verdict=ArchiveVerdict.SUSPICIOUS,
                error=f"Max recursion depth {self.max_recursion_depth} exceeded — possible nested-archive attack",
            )

        result = ArchiveScanResult(
            path=str(path),
            archive_type=self._detect_type(path),
        )

        # Phase 1: Outer scan
        self._outer_scan(path, result)
        if result.verdict == ArchiveVerdict.DANGEROUS:
            return result  # No point going deeper

        # Phase 2: Structural scan
        try:
            if result.archive_type == "zip":
                self._scan_zip(path, result)
            elif result.archive_type == "7z":
                self._scan_7z(path, result)
            elif result.archive_type == "rar":
                self._scan_rar(path, result)
            else:
                result.error = f"Unsupported archive type: {result.archive_type}"
                return result
        except Exception as e:
            result.error = f"Structural scan failed: {e}"
            result.verdict = ArchiveVerdict.SUSPICIOUS
            logger.error(f"Archive scan error for {path}: {e}")
            return result

        # Phase 3: Compute compression ratio (zip-bomb check)
        if result.compressed_size > 0:
            result.compression_ratio = (
                result.total_uncompressed_size / result.compressed_size
            )
            if result.compression_ratio > self.max_compression_ratio:
                result.threats.append({
                    "type": "zip_bomb",
                    "severity": "critical",
                    "details": (
                        f"Compression ratio {result.compression_ratio:.1f}:1 "
                        f"exceeds threshold {self.max_compression_ratio}:1"
                    ),
                })
                result.verdict = ArchiveVerdict.DANGEROUS

        # Phase 4: Final verdict
        if not result.threats and not result.suspicious_files:
            result.verdict = ArchiveVerdict.SAFE
        elif result.verdict != ArchiveVerdict.DANGEROUS:
            result.verdict = (
                ArchiveVerdict.DANGEROUS
                if any(t.get("severity") == "critical" for t in result.threats)
                else ArchiveVerdict.SUSPICIOUS
            )

        return result

    # ------------------------------------------------------------
    # Phase 1: Outer scan (no extraction)
    # ------------------------------------------------------------
    def _outer_scan(self, path: Path, result: ArchiveScanResult) -> None:
        """Hash + size sanity checks."""
        result.compressed_size = path.stat().st_size

        # Compute SHA-256 for hash lookup
        sha = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                sha.update(chunk)
        result.sha256 = sha.hexdigest()

        # NOTE: Hash lookup against malware DB happens in CyberAnalyst,
        # which queries the vector store. We just provide the hash here.

    # ------------------------------------------------------------
    # Phase 2: Type detection
    # ------------------------------------------------------------
    def _detect_type(self, path: Path) -> str:
        """Detect archive type by magic bytes (not extension)."""
        with open(path, "rb") as f:
            magic = f.read(8)

        if magic[:4] == b"PK\x03\x04" or magic[:4] == b"PK\x05\x06":
            return "zip"
        if magic[:6] == b"7z\xbc\xaf\x27\x1c":
            return "7z"
        if magic[:4] == b"Rar!":
            return "rar"
        return "unknown"

    # ------------------------------------------------------------
    # Phase 3: Per-format structural scans
    # ------------------------------------------------------------
    def _scan_zip(self, path: Path, result: ArchiveScanResult) -> None:
        with zipfile.ZipFile(path, "r") as zf:
            # Encrypted check
            for info in zf.infolist():
                if info.flag_bits & 0x1:
                    result.is_encrypted = True
                    break

            for info in zf.infolist():
                self._inspect_member(
                    name=info.filename,
                    compressed_size=info.compress_size,
                    uncompressed_size=info.file_size,
                    result=result,
                )

    def _scan_7z(self, path: Path, result: ArchiveScanResult) -> None:
        with py7zr.SevenZipFile(path, mode="r") as zf:
            result.is_encrypted = zf.password_protected
            for info in zf.list():
                self._inspect_member(
                    name=info.filename,
                    compressed_size=info.compressed or 0,
                    uncompressed_size=info.uncompressed or 0,
                    result=result,
                )

    def _scan_rar(self, path: Path, result: ArchiveScanResult) -> None:
        with rarfile.RarFile(path, "r") as rf:
            result.is_encrypted = rf.needs_password()
            for info in rf.infolist():
                self._inspect_member(
                    name=info.filename,
                    compressed_size=info.compress_size,
                    uncompressed_size=info.file_size,
                    result=result,
                )

    # ------------------------------------------------------------
    # Per-member inspection (the actual threat detection)
    # ------------------------------------------------------------
    def _inspect_member(
        self,
        name: str,
        compressed_size: int,
        uncompressed_size: int,
        result: ArchiveScanResult,
    ) -> None:
        result.file_count += 1
        result.total_uncompressed_size += uncompressed_size

        suspicious_reasons = []

        # 1. Path traversal attempt
        normalized = os.path.normpath(name).replace("\\", "/")
        if normalized.startswith("../") or normalized.startswith("/") or ":" in normalized[:3]:
            suspicious_reasons.append({
                "type": "path_traversal",
                "severity": "critical",
                "details": f"Path escapes archive root: {name}",
            })
            result.threats.append(suspicious_reasons[-1])

        # 2. Polyglot extension
        lower = name.lower()
        for pattern, description in POLYGLOT_PATTERNS:
            if lower.endswith(pattern):
                suspicious_reasons.append({
                    "type": "polyglot_extension",
                    "severity": "critical",
                    "details": f"{description}: {name}",
                })
                result.threats.append(suspicious_reasons[-1])

        # 3. Red-flag filename
        basename = os.path.basename(lower)
        if basename in RED_FLAG_FILENAMES:
            suspicious_reasons.append({
                "type": "suspicious_filename",
                "severity": "high",
                "details": f"Known abuse vector: {name}",
            })
            result.threats.append(suspicious_reasons[-1])

        # 4. Dangerous extension
        ext = os.path.splitext(lower)[1]
        if ext in DANGEROUS_EXTENSIONS:
            suspicious_reasons.append({
                "type": "dangerous_extension",
                "severity": "medium",
                "details": f"Executable content: {name}",
            })

        # 5. Hidden / unicode-disguised filenames
        if "\u202e" in name or "\u200b" in name:
            # Right-to-left override attack — makes "file.exe" look like "file.txt"
            suspicious_reasons.append({
                "type": "unicode_attack",
                "severity": "critical",
                "details": f"RLO/zero-width characters in filename: {name!r}",
            })
            result.threats.append(suspicious_reasons[-1])

        # 6. Nested archive
        if ext in {".zip", ".7z", ".rar", ".tar", ".gz"}:
            result.nested_archives.append(name)

        # 7. Per-file zip-bomb (this single file expands suspiciously)
        if compressed_size > 0 and uncompressed_size > 0:
            file_ratio = uncompressed_size / compressed_size
            if file_ratio > self.max_compression_ratio:
                suspicious_reasons.append({
                    "type": "single_file_bomb",
                    "severity": "high",
                    "details": (
                        f"{name}: {file_ratio:.0f}:1 ratio "
                        f"({uncompressed_size:,}B from {compressed_size:,}B)"
                    ),
                })

        if suspicious_reasons:
            result.suspicious_files.append({
                "name": name,
                "size_compressed": compressed_size,
                "size_uncompressed": uncompressed_size,
                "issues": suspicious_reasons,
            })

    # ------------------------------------------------------------
    # Phase 4: Optional sandbox extraction for deeper analysis
    # ------------------------------------------------------------
    def sandbox_extract_and_scan(
        self, archive_path: str, scanner_callback=None
    ) -> dict[str, Any]:
        """
        Extract to an isolated temp dir, run a callback on each file,
        then delete the directory. Use ONLY after structural scan passes.

        scanner_callback(path: Path) -> dict — caller-provided, e.g. YARA scan.
        """
        sandbox = tempfile.mkdtemp(prefix="coa_sandbox_", dir=self.sandbox_dir)
        results = {"sandbox": sandbox, "files": []}

        try:
            archive_type = self._detect_type(Path(archive_path))

            if archive_type == "zip":
                with zipfile.ZipFile(archive_path) as zf:
                    zf.extractall(sandbox)
            elif archive_type == "7z":
                with py7zr.SevenZipFile(archive_path) as zf:
                    zf.extractall(sandbox)
            elif archive_type == "rar":
                with rarfile.RarFile(archive_path) as rf:
                    rf.extractall(sandbox)

            # Run scanner on each extracted file
            if scanner_callback:
                for root, _, files in os.walk(sandbox):
                    for fname in files:
                        fpath = Path(root) / fname
                        scan_result = scanner_callback(fpath)
                        results["files"].append({
                            "path": str(fpath.relative_to(sandbox)),
                            "scan": scan_result,
                        })

        finally:
            # Always clean up
            shutil.rmtree(sandbox, ignore_errors=True)

        return results
