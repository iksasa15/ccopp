"""
Unit tests for the validation layer.

Run with: pytest tests/unit/test_validation.py -v
"""

import json
import pytest

from validation.schemas import (
    ActionType, AgentName, Evidence, Finding, LLMVerdict,
    Location, ThreatLevel, safe_parse_json
)


# ============================================================
# safe_parse_json — robust JSON extraction
# ============================================================

class TestSafeParseJson:
    def test_clean_json(self):
        result = safe_parse_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_markdown_fenced(self):
        text = '```json\n{"key": "value"}\n```'
        result = safe_parse_json(text)
        assert result == {"key": "value"}

    def test_with_prose_around(self):
        text = 'Here is the JSON: {"key": "value"} hope this helps'
        result = safe_parse_json(text)
        assert result == {"key": "value"}

    def test_trailing_comma(self):
        text = '{"key": "value",}'
        result = safe_parse_json(text)
        assert result == {"key": "value"}

    def test_single_quotes(self):
        # Embedded in prose so the standalone parse fails first
        text = "garbage prefix {'key': 'value'} suffix"
        result = safe_parse_json(text)
        assert result == {"key": "value"}

    def test_empty_string(self):
        assert safe_parse_json("") is None

    def test_no_json(self):
        assert safe_parse_json("just plain text") is None

    def test_nested_objects(self):
        text = '{"outer": {"inner": [1, 2, 3]}}'
        result = safe_parse_json(text)
        assert result["outer"]["inner"] == [1, 2, 3]


# ============================================================
# Pydantic schemas — strict validation
# ============================================================

class TestLocation:
    def test_valid_location_with_pid(self):
        loc = Location(pid=1234, process_name="test.exe")
        assert loc.pid == 1234

    def test_empty_location_rejected(self):
        with pytest.raises(ValueError, match="at least one identifier"):
            Location()

    def test_valid_sha256(self):
        valid_hash = "a" * 64
        loc = Location(file_path="/test", file_hash_sha256=valid_hash)
        assert loc.file_hash_sha256 == valid_hash

    def test_invalid_sha256(self):
        with pytest.raises(ValueError):
            Location(file_path="/test", file_hash_sha256="not-a-hash")

    def test_invalid_port(self):
        with pytest.raises(ValueError):
            Location(remote_ip="1.2.3.4", remote_port=99999)


class TestFinding:
    def _make_valid_finding(self, **overrides) -> Finding:
        defaults = {
            "agent_name": AgentName.RESOURCE_WARDEN,
            "threat_level": ThreatLevel.MEDIUM,
            "title": "Test finding",
            "description": "A test description.",
            "location": Location(pid=1234),
            "confidence": 0.7,
            "recommended_action": ActionType.WATCH,
        }
        defaults.update(overrides)
        return Finding(**defaults)

    def test_valid_finding(self):
        f = self._make_valid_finding()
        assert f.threat_level == ThreatLevel.MEDIUM

    def test_critical_cannot_ignore(self):
        with pytest.raises(ValueError, match="Critical threats cannot recommend IGNORE"):
            self._make_valid_finding(
                threat_level=ThreatLevel.CRITICAL,
                recommended_action=ActionType.IGNORE,
            )

    def test_clean_low_confidence_rejected(self):
        with pytest.raises(ValueError, match="CLEAN verdicts require confidence"):
            self._make_valid_finding(
                threat_level=ThreatLevel.CLEAN,
                confidence=0.3,
            )

    def test_mitre_format_validation(self):
        with pytest.raises(ValueError, match="Invalid MITRE technique"):
            self._make_valid_finding(mitre_techniques=["INVALID"])

    def test_valid_mitre_formats(self):
        f = self._make_valid_finding(mitre_techniques=["T1059", "T1059.001"])
        assert "T1059" in f.mitre_techniques

    def test_title_too_short(self):
        with pytest.raises(ValueError):
            self._make_valid_finding(title="ab")

    def test_confidence_bounds(self):
        with pytest.raises(ValueError):
            self._make_valid_finding(confidence=1.5)
        with pytest.raises(ValueError):
            self._make_valid_finding(confidence=-0.1)


class TestThreatLevel:
    def test_numeric_ordering(self):
        assert ThreatLevel.CRITICAL.numeric > ThreatLevel.HIGH.numeric
        assert ThreatLevel.HIGH.numeric > ThreatLevel.MEDIUM.numeric
        assert ThreatLevel.CLEAN.numeric == 0

    def test_can_compare_levels(self):
        levels = [ThreatLevel.LOW, ThreatLevel.CRITICAL, ThreatLevel.MEDIUM]
        max_level = max(levels, key=lambda t: t.numeric)
        assert max_level == ThreatLevel.CRITICAL


class TestLLMVerdict:
    def test_valid_verdict(self):
        v = LLMVerdict(
            is_malicious=True,
            confidence=0.85,
            threat_level="high",
            title="Suspicious PowerShell",
            description="Encoded PowerShell command detected.",
            recommended_action="quarantine",
            reasoning="The command line contains -enc flag indicating obfuscation.",
        )
        assert v.threat_level == "high"

    def test_invalid_threat_level(self):
        with pytest.raises(ValueError):
            LLMVerdict(
                is_malicious=True,
                confidence=0.85,
                threat_level="HIGH",  # wrong case
                title="Test",
                description="Test description.",
                recommended_action="quarantine",
                reasoning="Test reasoning here.",
            )
