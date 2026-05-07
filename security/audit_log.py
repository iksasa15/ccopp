"""
Audit Log — Tamper-evident logging.

Every security-relevant event is recorded with a hash that includes the previous
row's hash. To tamper with row N, an attacker would need to recompute every
subsequent row. This makes silent log modification detectable.

This is NOT a blockchain — it's a hash chain (Merkle-style). Simpler and faster.
"""

import hashlib
import json
from datetime import datetime
from typing import Any

from loguru import logger
from sqlalchemy import select

from persistence.models import AuditLog, Database


class AuditLogger:
    """Append-only logger with cryptographic chaining."""

    def __init__(self, db: Database):
        self.db = db

    async def log(
        self,
        event_type: str,
        actor: str,
        details: dict[str, Any],
    ) -> None:
        """Append a new event to the audit log."""
        async with self.db.session() as session:
            # Get the previous hash
            prev = await session.scalar(
                select(AuditLog).order_by(AuditLog.id.desc()).limit(1)
            )
            previous_hash = prev.row_hash if prev else None

            timestamp = datetime.utcnow()
            row_hash = self._compute_hash(
                timestamp=timestamp,
                event_type=event_type,
                actor=actor,
                details=details,
                previous_hash=previous_hash,
            )

            entry = AuditLog(
                timestamp=timestamp,
                event_type=event_type,
                actor=actor,
                details=details,
                previous_hash=previous_hash,
                row_hash=row_hash,
            )
            session.add(entry)
            await session.commit()

    @staticmethod
    def _compute_hash(
        timestamp: datetime,
        event_type: str,
        actor: str,
        details: dict[str, Any],
        previous_hash: str | None,
    ) -> str:
        """Deterministic SHA-256 of the row's content + previous hash."""
        canonical = json.dumps(
            {
                "timestamp": timestamp.isoformat(),
                "event_type": event_type,
                "actor": actor,
                "details": details,
                "previous_hash": previous_hash or "GENESIS",
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    async def verify_integrity(self) -> dict[str, Any]:
        """
        Walk the chain from start to end. Detect tampering.
        Returns {"is_valid": bool, "broken_at": int | None, "total_entries": int}.
        """
        async with self.db.session() as session:
            entries = (await session.scalars(
                select(AuditLog).order_by(AuditLog.id.asc())
            )).all()

        if not entries:
            return {"is_valid": True, "broken_at": None, "total_entries": 0}

        previous_hash = None
        for entry in entries:
            expected = self._compute_hash(
                timestamp=entry.timestamp,
                event_type=entry.event_type,
                actor=entry.actor,
                details=entry.details,
                previous_hash=previous_hash,
            )

            if expected != entry.row_hash:
                logger.error(
                    f"Audit log tampering detected at entry #{entry.id}. "
                    f"Expected hash {expected[:16]}..., got {entry.row_hash[:16]}..."
                )
                return {
                    "is_valid": False,
                    "broken_at": entry.id,
                    "total_entries": len(entries),
                }

            if entry.previous_hash != previous_hash:
                logger.error(
                    f"Chain break at entry #{entry.id}. "
                    f"Expected previous_hash {previous_hash}, got {entry.previous_hash}"
                )
                return {
                    "is_valid": False,
                    "broken_at": entry.id,
                    "total_entries": len(entries),
                }

            previous_hash = entry.row_hash

        return {"is_valid": True, "broken_at": None, "total_entries": len(entries)}


# ============================================================
# Convenience event types
# ============================================================

class AuditEvent:
    """Standardized event types for consistent logging."""

    SCAN_STARTED = "scan.started"
    SCAN_COMPLETED = "scan.completed"
    SCAN_FAILED = "scan.failed"

    THREAT_DETECTED = "threat.detected"
    THREAT_QUARANTINED = "threat.quarantined"
    THREAT_DISMISSED = "threat.dismissed"

    USER_FEEDBACK = "feedback.submitted"
    USER_OVERRIDE = "user.override"

    CONFIG_CHANGED = "config.changed"
    WHITELIST_MODIFIED = "whitelist.modified"

    AGENT_FAILURE = "agent.failure"
    LLM_UNAVAILABLE = "llm.unavailable"
    CIRCUIT_OPENED = "circuit.opened"

    BASELINE_UPDATED = "baseline.updated"
    DATABASE_BACKUP = "db.backup"

    SYSTEM_STARTED = "system.started"
    SYSTEM_STOPPED = "system.stopped"
    INTEGRITY_CHECK = "integrity.check"
