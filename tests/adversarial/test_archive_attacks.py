"""
Adversarial Tests — Simulated attacks to verify the system catches them.

These tests build malicious archives in-memory and feed them to the scanner.
If any test FAILS to flag a known attack pattern, that's a regression.
"""

import io
import os
import struct
import tempfile
import zipfile
from pathlib import Path

import pytest

from tools.archive_inspector import ArchiveInspector, ArchiveVerdict


@pytest.fixture
def inspector():
    return ArchiveInspector(max_compression_ratio=100)


@pytest.fixture
def tmpdir():
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


# ============================================================
# Zip Bomb Detection
# ============================================================

class TestZipBomb:
    def test_high_compression_ratio_caught(self, inspector, tmpdir):
        """Create a zip with high compression — should flag as zip bomb."""
        bomb_path = tmpdir / "bomb.zip"
        
        # 10MB of zeros compresses to ~10KB → ratio 1000:1
        with zipfile.ZipFile(bomb_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            zf.writestr("payload.txt", b"\x00" * (10 * 1024 * 1024))
        
        result = inspector.scan(str(bomb_path))
        
        assert result.verdict == ArchiveVerdict.DANGEROUS
        assert any(t["type"] == "zip_bomb" or t["type"] == "single_file_bomb" 
                  for t in result.threats + 
                  [issue for sf in result.suspicious_files for issue in sf["issues"]])

    def test_normal_archive_not_flagged(self, inspector, tmpdir):
        """A normal archive should pass."""
        normal = tmpdir / "normal.zip"
        with zipfile.ZipFile(normal, "w") as zf:
            zf.writestr("readme.txt", b"Some normal content here." * 100)
        
        result = inspector.scan(str(normal))
        assert result.verdict == ArchiveVerdict.SAFE


# ============================================================
# Polyglot File Detection
# ============================================================

class TestPolyglotDetection:
    @pytest.mark.parametrize("filename,description", [
        ("invoice.pdf.exe", "PDF disguised executable"),
        ("photo.jpg.exe", "Image disguised executable"),
        ("document.doc.scr", "Doc disguised screensaver"),
        ("report.pdf.js", "PDF disguised JavaScript"),
    ])
    def test_polyglot_filename_caught(self, inspector, tmpdir, filename, description):
        archive = tmpdir / "evil.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr(filename, b"fake content")
        
        result = inspector.scan(str(archive))
        
        # Polyglots must at minimum be flagged as suspicious (not safe)
        assert result.verdict in {ArchiveVerdict.DANGEROUS, ArchiveVerdict.SUSPICIOUS}, \
            f"Polyglot {filename} not flagged: verdict={result.verdict}"
        polyglot_threats = [
            t for t in result.threats if t["type"] == "polyglot_extension"
        ]
        assert len(polyglot_threats) > 0, \
            f"No polyglot threat raised for {filename}"


# ============================================================
# Path Traversal Detection
# ============================================================

class TestPathTraversal:
    def test_parent_directory_escape(self, inspector, tmpdir):
        """A file with ../ should be flagged."""
        archive = tmpdir / "traversal.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            # Manually craft entry with traversal path
            info = zipfile.ZipInfo(filename="../../etc/passwd")
            zf.writestr(info, b"root:x:0:0::/root:/bin/bash")
        
        result = inspector.scan(str(archive))
        
        assert result.verdict == ArchiveVerdict.DANGEROUS
        traversal_threats = [
            t for t in result.threats if t["type"] == "path_traversal"
        ]
        assert len(traversal_threats) > 0

    def test_absolute_path_caught(self, inspector, tmpdir):
        archive = tmpdir / "abs.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            info = zipfile.ZipInfo(filename="/etc/shadow")
            zf.writestr(info, b"data")
        
        result = inspector.scan(str(archive))
        traversal_threats = [
            t for t in result.threats if t["type"] == "path_traversal"
        ]
        assert len(traversal_threats) > 0


# ============================================================
# Unicode RLO Attack Detection
# ============================================================

class TestUnicodeAttacks:
    def test_rlo_character_caught(self, inspector, tmpdir):
        """U+202E reverses display: 'photo\\u202egpj.exe' looks like 'photoexe.jpg'."""
        archive = tmpdir / "rlo.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            tricky = "photo\u202egpj.exe"
            zf.writestr(tricky, b"trojan content")
        
        result = inspector.scan(str(archive))
        
        unicode_threats = [
            t for t in result.threats if t["type"] == "unicode_attack"
        ]
        assert len(unicode_threats) > 0


# ============================================================
# Suspicious Filenames
# ============================================================

class TestSuspiciousFilenames:
    def test_autorun_inf_caught(self, inspector, tmpdir):
        archive = tmpdir / "autorun.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("autorun.inf", b"[autorun]\nopen=evil.exe")
            zf.writestr("evil.exe", b"MZ\x90\x00")  # PE header
        
        result = inspector.scan(str(archive))
        
        suspicious = [
            sf for sf in result.suspicious_files
            if any(i["type"] == "suspicious_filename" for i in sf["issues"])
        ]
        assert len(suspicious) > 0


# ============================================================
# Nested Archive Detection
# ============================================================

class TestNestedArchives:
    def test_nested_zips_detected(self, inspector, tmpdir):
        """An archive containing other archives should be flagged for inspection."""
        inner = tmpdir / "inner.zip"
        with zipfile.ZipFile(inner, "w") as zf:
            zf.writestr("payload.txt", b"data")
        
        outer = tmpdir / "outer.zip"
        with zipfile.ZipFile(outer, "w") as zf:
            zf.write(inner, "inner.zip")
        
        result = inspector.scan(str(outer))
        
        assert "inner.zip" in result.nested_archives


# ============================================================
# Combined Attack Scenarios
# ============================================================

class TestCombinedAttacks:
    def test_multi_vector_archive(self, inspector, tmpdir):
        """An archive with multiple attack vectors — should be DANGEROUS."""
        archive = tmpdir / "combined.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("invoice.pdf.exe", b"MZ")          # polyglot
            zf.writestr("autorun.inf", b"[autorun]")        # suspicious filename
            zf.writestr("../../escape.txt", b"data")        # traversal
        
        result = inspector.scan(str(archive))
        
        assert result.verdict == ArchiveVerdict.DANGEROUS
        # Should have multiple distinct threat types
        threat_types = {t["type"] for t in result.threats}
        assert len(threat_types) >= 2


# ============================================================
# Negative tests — legitimate archives MUST NOT be flagged
# ============================================================

class TestLegitimateArchives:
    def test_normal_documents_safe(self, inspector, tmpdir):
        archive = tmpdir / "docs.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("report.docx", b"PK\x03\x04 fake docx content")
            zf.writestr("data.xlsx", b"PK\x03\x04 fake xlsx content")
            zf.writestr("readme.txt", b"This is the readme")
        
        result = inspector.scan(str(archive))
        assert result.verdict == ArchiveVerdict.SAFE
        assert len(result.threats) == 0

    def test_source_code_archive_safe(self, inspector, tmpdir):
        """A typical source code archive (with .py, .js files)."""
        archive = tmpdir / "source.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("main.py", b"print('hello')")
            zf.writestr("script.js", b"console.log('hi')")  # JS gets warned but not dangerous
            zf.writestr("README.md", b"# Project")
        
        result = inspector.scan(str(archive))
        # JS triggers "dangerous_extension" but at MEDIUM severity
        # so verdict should be SUSPICIOUS, not DANGEROUS
        assert result.verdict in {ArchiveVerdict.SAFE, ArchiveVerdict.SUSPICIOUS}
