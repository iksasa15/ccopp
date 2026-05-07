"""
Pydantic Schemas — Strict validation for all LLM outputs and inter-agent messages.

Why this matters:
  - 7B local models often return malformed JSON
  - Without validation, garbage findings poison the council's decision
  - Pydantic gives us automatic coercion, error messages, and self-documentation
"""

from datetime import datetime
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ============================================================
# Enums — single source of truth across the system
# ============================================================

class ThreatLevel(str, Enum):
    CLEAN = "clean"
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def numeric(self) -> int:
        """Allows comparison: CRITICAL > HIGH > MEDIUM ..."""
        order = {
            "clean": 0, "info": 1, "low": 2,
            "medium": 3, "high": 4, "critical": 5
        }
        return order[self.value]


class ActionType(str, Enum):
    IGNORE = "ignore"
    WATCH = "watch"
    ALERT = "alert"
    QUARANTINE = "quarantine"
    TERMINATE = "terminate"
    BLOCK_NETWORK = "block_network"


class AgentName(str, Enum):
    ARBITRATOR = "arbitrator"
    RESOURCE_WARDEN = "resource_warden"
    CYBER_ANALYST = "cyber_analyst"
    TRAFFIC_OBSERVER = "traffic_observer"


# ============================================================
# Core Finding Schema — what every agent MUST return
# ============================================================

class Location(BaseModel):
    """Where the threat lives."""
    pid: int | None = None
    process_name: str | None = None
    file_path: str | None = None
    file_hash_sha256: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$|^$")
    remote_ip: str | None = None
    remote_port: int | None = Field(default=None, ge=0, le=65535)
    registry_key: str | None = None

    @model_validator(mode="after")
    def at_least_one_location(self) -> "Location":
        if not any([
            self.pid, self.file_path, self.remote_ip,
            self.registry_key, self.process_name
        ]):
            raise ValueError("Location must specify at least one identifier")
        return self


class Evidence(BaseModel):
    """Raw data supporting the finding — for transparency."""
    raw_data: dict[str, Any] = Field(default_factory=dict)
    red_flags: list[str] = Field(default_factory=list)
    reasoning: str = Field(default="", max_length=2000)
    references: list[str] = Field(default_factory=list)  # CVE, MITRE links


class Finding(BaseModel):
    """A single piece of evidence from an agent. Strict schema."""
    model_config = ConfigDict(use_enum_values=False)

    finding_id: str = Field(default_factory=lambda: str(uuid4()))
    agent_name: AgentName
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    threat_level: ThreatLevel
    title: str = Field(min_length=3, max_length=120)
    description: str = Field(min_length=10, max_length=2000)
    location: Location
    evidence: Evidence = Field(default_factory=Evidence)
    confidence: float = Field(ge=0.0, le=1.0)
    recommended_action: ActionType
    mitre_techniques: list[str] = Field(default_factory=list)

    @field_validator("mitre_techniques")
    @classmethod
    def validate_mitre_format(cls, v: list[str]) -> list[str]:
        """MITRE techniques follow T#### or T####.### pattern."""
        import re
        pattern = re.compile(r"^T\d{4}(\.\d{3})?$")
        for technique in v:
            if not pattern.match(technique):
                raise ValueError(
                    f"Invalid MITRE technique format: {technique} "
                    "(expected T#### or T####.###)"
                )
        return v

    @model_validator(mode="after")
    def consistency_check(self) -> "Finding":
        """A CRITICAL finding shouldn't recommend IGNORE."""
        if self.threat_level == ThreatLevel.CRITICAL and self.recommended_action == ActionType.IGNORE:
            raise ValueError("Critical threats cannot recommend IGNORE action")
        if self.threat_level == ThreatLevel.CLEAN and self.confidence < 0.5:
            raise ValueError("CLEAN verdicts require confidence >= 0.5")
        return self


# ============================================================
# LLM Output Schema — what we expect FROM the model
# ============================================================

class LLMVerdict(BaseModel):
    """
    Strict schema for LLM JSON output. 
    Used with `instructor` library to force compliant generation.
    """
    is_malicious: bool
    confidence: float = Field(ge=0.0, le=1.0)
    threat_level: Literal["clean", "info", "low", "medium", "high", "critical"]
    title: str = Field(min_length=3, max_length=120)
    description: str = Field(min_length=10, max_length=1500)
    mitre_techniques: list[str] = Field(default_factory=list, max_length=10)
    recommended_action: Literal["ignore", "watch", "alert", "quarantine", "terminate", "block_network"]
    reasoning: str = Field(min_length=10, max_length=2000)
    confidence_factors: list[str] = Field(
        default_factory=list,
        description="Why the confidence score is what it is"
    )


# ============================================================
# Council Decision Schema — the Arbitrator's final verdict
# ============================================================

class AgentReport(BaseModel):
    """One agent's contribution to the council."""
    agent: AgentName
    findings: list[Finding]
    scan_duration_ms: int = Field(ge=0)
    errors_encountered: list[str] = Field(default_factory=list)


class CouncilDecision(BaseModel):
    """Final verdict from the Arbitrator after deliberation."""
    decision_id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    overall_threat_level: ThreatLevel
    confidence: float = Field(ge=0.0, le=1.0)
    consensus_reached: bool
    voting_summary: dict[str, int] = Field(default_factory=dict)
    primary_findings: list[Finding] = Field(
        description="Findings the Arbitrator endorses"
    )
    suppressed_findings: list[Finding] = Field(
        default_factory=list,
        description="Findings rejected as false positives"
    )
    recommended_actions: list[ActionType]
    user_summary_ar: str = Field(description="Arabic summary for the user")
    user_summary_en: str = Field(description="English summary for the user")
    technical_report: str = Field(description="Detailed technical explanation")


# ============================================================
# System State Schema — shared between agents (LangGraph)
# ============================================================

class ScanContext(BaseModel):
    """Mutable state passed between agents via LangGraph."""
    scan_id: str = Field(default_factory=lambda: str(uuid4()))
    started_at: datetime = Field(default_factory=datetime.utcnow)
    target_path: str | None = None  # for file/archive scans
    scan_type: Literal["system", "file", "archive", "scheduled"] = "system"

    # Agent reports accumulate here
    reports: dict[AgentName, AgentReport] = Field(default_factory=dict)

    # Cross-agent shared knowledge
    shared_evidence: dict[str, Any] = Field(default_factory=dict)
    iteration: int = Field(default=0, ge=0)
    max_iterations: int = Field(default=5, ge=1, le=10)

    # Final decision (filled by Arbitrator)
    final_decision: CouncilDecision | None = None


# ============================================================
# Validation utilities — safe parsing helpers
# ============================================================

def safe_parse_json(text: str) -> dict[str, Any] | None:
    """
    Robust JSON extraction from LLM output. Handles:
    - Markdown code fences (```json ... ```)
    - Leading/trailing prose
    - Trailing commas (common LLM mistake)
    - Single quotes instead of double
    """
    import json
    import re

    if not text or not text.strip():
        return None

    text = text.strip()

    # Strip markdown fences
    if "```" in text:
        match = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.DOTALL)
        if match:
            text = match.group(1)

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Find the first { ... } that balances correctly
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                candidate = text[start:i + 1]
                # Try parsing the extracted block
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    # Try fixing common issues
                    fixed = candidate.replace("'", '"')
                    fixed = re.sub(r",\s*([}\]])", r"\1", fixed)  # trailing commas
                    try:
                        return json.loads(fixed)
                    except json.JSONDecodeError:
                        continue

    return None
