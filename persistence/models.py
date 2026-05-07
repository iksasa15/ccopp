"""
Database Models — SQLAlchemy ORM definitions.

Tables:
  - scan_history: every scan ever performed
  - findings: persistent record of findings (queryable)
  - user_feedback: user verdicts for active learning
  - council_decisions: full decision records
  - quarantine_log: every quarantine action
  - audit_log: tamper-evident audit trail (append-only)
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON, Boolean, Column, DateTime, Float, ForeignKey, Index,
    Integer, String, Text, create_engine
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class ScanHistory(Base):
    __tablename__ = "scan_history"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    scan_type: Mapped[str] = mapped_column(String(32), nullable=False)  # system|file|archive|scheduled
    target_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    overall_threat_level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    iterations: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    findings: Mapped[list["FindingRecord"]] = relationship(
        back_populates="scan", cascade="all, delete-orphan"
    )
    decision: Mapped["DecisionRecord"] = relationship(
        back_populates="scan", uselist=False, cascade="all, delete-orphan"
    )


class FindingRecord(Base):
    __tablename__ = "findings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scan_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scan_history.id"), index=True
    )
    agent_name: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    threat_level: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    recommended_action: Mapped[str] = mapped_column(String(32), nullable=False)

    # Location fields (denormalized for fast querying)
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    process_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    file_hash_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    remote_ip: Mapped[str | None] = mapped_column(String(45), nullable=True, index=True)
    remote_port: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Full evidence as JSON
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    mitre_techniques: Mapped[list[str]] = mapped_column(JSON, default=list)

    # Whether this finding was endorsed by the Arbitrator
    is_endorsed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    scan: Mapped["ScanHistory"] = relationship(back_populates="findings")
    feedback: Mapped[list["UserFeedback"]] = relationship(back_populates="finding")


class DecisionRecord(Base):
    __tablename__ = "decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scan_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scan_history.id"), unique=True
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    overall_threat_level: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    consensus_reached: Mapped[bool] = mapped_column(Boolean, default=False)
    voting_summary: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    recommended_actions: Mapped[list[str]] = mapped_column(JSON, default=list)
    user_summary_ar: Mapped[str] = mapped_column(Text, nullable=False)
    user_summary_en: Mapped[str] = mapped_column(Text, nullable=False)
    technical_report: Mapped[str] = mapped_column(Text, nullable=False)

    scan: Mapped["ScanHistory"] = relationship(back_populates="decision")


class UserFeedback(Base):
    __tablename__ = "user_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    finding_id: Mapped[str] = mapped_column(String(36), ForeignKey("findings.id"))
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    verdict: Mapped[str] = mapped_column(String(32), nullable=False)
    # verdict: "true_positive" | "false_positive" | "true_negative" | "uncertain"
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    finding: Mapped["FindingRecord"] = relationship(back_populates="feedback")


class QuarantineRecord(Base):
    __tablename__ = "quarantine"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    quarantined_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    original_path: Mapped[str] = mapped_column(Text, nullable=False)
    quarantine_path: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    encryption_key_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    triggered_by_finding: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("findings.id"), nullable=True
    )
    restored_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AuditLog(Base):
    """
    Append-only audit trail. Tamper-evident via hash chaining.
    Each row's hash includes the previous row's hash (blockchain-like).
    """
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)  # "user" | "system" | "agent:name"
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    
    previous_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    row_hash: Mapped[str] = mapped_column(String(64), nullable=False)


# ============================================================
# Indexes for common queries
# ============================================================

Index("idx_findings_threat_endorsed", FindingRecord.threat_level, FindingRecord.is_endorsed)
Index("idx_scan_started_type", ScanHistory.started_at, ScanHistory.scan_type)


# ============================================================
# Session factory
# ============================================================

class Database:
    """Async database manager."""

    def __init__(self, db_url: str = "sqlite+aiosqlite:///./data/council.db"):
        self.db_url = db_url
        self.engine = create_async_engine(db_url, echo=False, future=True)
        self.SessionLocal = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def init_schema(self) -> None:
        """Create tables if they don't exist."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        await self.engine.dispose()

    def session(self) -> AsyncSession:
        return self.SessionLocal()


# ============================================================
# Synchronous version for migrations / scripts
# ============================================================

def get_sync_engine(db_url: str = "sqlite:///./data/council.db"):
    return create_engine(db_url, echo=False)
