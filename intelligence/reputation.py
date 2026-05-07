"""
Reputation Engine — Tracks history of files and processes on the device.

Concept: A file's reputation depends on:
  - Age (first seen): old files are usually trustworthy, brand new files are suspicious
  - Prevalence (how often executed): rarely-run files = suspicious
  - Origin (where did it come from): downloaded vs OS-installed
  - User feedback: did the user mark it safe/unsafe before?

Reputation scores feed into the council's confidence calculation.
"""

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass
class ReputationRecord:
    sha256: str
    first_seen: datetime
    last_seen: datetime
    execution_count: int
    user_verdict: str | None  # "safe" | "malicious" | None
    origin: str | None  # "system" | "download" | "removable_media" | "network_share" | "unknown"
    age_days: int
    
    @property
    def reputation_score(self) -> float:
        """
        Score from 0.0 (suspicious) to 1.0 (trusted).
        
        Factors:
          - Age: 30+ days = +0.3
          - Prevalence: 10+ executions = +0.2
          - User verdict: explicit safe = +0.4
          - Origin: system = +0.3, download = -0.1
        """
        score = 0.5  # neutral start

        # Age boost
        if self.age_days >= 90:
            score += 0.3
        elif self.age_days >= 30:
            score += 0.2
        elif self.age_days <= 1:
            score -= 0.2  # brand new = suspicious

        # Prevalence boost
        if self.execution_count >= 50:
            score += 0.2
        elif self.execution_count >= 10:
            score += 0.1

        # User verdict (strongest signal)
        if self.user_verdict == "safe":
            score += 0.4
        elif self.user_verdict == "malicious":
            score = 0.0  # immediate red flag

        # Origin
        if self.origin == "system":
            score += 0.3
        elif self.origin == "download":
            score -= 0.1
        elif self.origin == "removable_media":
            score -= 0.2

        return max(0.0, min(1.0, score))


class ReputationEngine:
    """SQLite-backed reputation tracking."""

    def __init__(self, db_path: str = "./data/reputation.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS reputation (
                    sha256 TEXT PRIMARY KEY,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    execution_count INTEGER DEFAULT 0,
                    user_verdict TEXT,
                    origin TEXT,
                    file_path TEXT,
                    file_size INTEGER,
                    notes TEXT
                );
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_last_seen ON reputation(last_seen);
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_user_verdict ON reputation(user_verdict);
            """)

    def record_observation(
        self,
        sha256: str,
        file_path: str | None = None,
        file_size: int | None = None,
        origin: str | None = None,
    ) -> None:
        """Record that a file was seen/executed."""
        if not sha256 or len(sha256) != 64:
            return

        now = datetime.utcnow().isoformat()
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT first_seen, execution_count FROM reputation WHERE sha256 = ?",
                (sha256.lower(),),
            ).fetchone()

            if existing:
                conn.execute(
                    """UPDATE reputation
                       SET last_seen = ?,
                           execution_count = execution_count + 1,
                           file_path = COALESCE(?, file_path)
                       WHERE sha256 = ?""",
                    (now, file_path, sha256.lower()),
                )
            else:
                conn.execute(
                    """INSERT INTO reputation
                       (sha256, first_seen, last_seen, execution_count,
                        origin, file_path, file_size)
                       VALUES (?, ?, ?, 1, ?, ?, ?)""",
                    (sha256.lower(), now, now, origin, file_path, file_size),
                )

    def get(self, sha256: str) -> ReputationRecord | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM reputation WHERE sha256 = ?", (sha256.lower(),)
            ).fetchone()

        if not row:
            return None

        first = datetime.fromisoformat(row["first_seen"])
        last = datetime.fromisoformat(row["last_seen"])
        age = max(0, (datetime.utcnow() - first).days)

        return ReputationRecord(
            sha256=row["sha256"],
            first_seen=first,
            last_seen=last,
            execution_count=row["execution_count"],
            user_verdict=row["user_verdict"],
            origin=row["origin"],
            age_days=age,
        )

    def set_user_verdict(self, sha256: str, verdict: str) -> None:
        """User explicitly marks a file safe or malicious."""
        if verdict not in ("safe", "malicious", None):
            raise ValueError(f"Invalid verdict: {verdict}")
        
        with self._conn() as conn:
            conn.execute(
                "UPDATE reputation SET user_verdict = ? WHERE sha256 = ?",
                (verdict, sha256.lower()),
            )
        logger.info(f"User verdict for {sha256[:16]}...: {verdict}")

    def get_score(self, sha256: str) -> float:
        """Quick lookup: just the reputation score."""
        record = self.get(sha256)
        if record is None:
            return 0.5  # neutral for unseen files
        return record.reputation_score

    def stats(self) -> dict[str, Any]:
        with self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) as c FROM reputation").fetchone()["c"]
            marked_safe = conn.execute(
                "SELECT COUNT(*) as c FROM reputation WHERE user_verdict = 'safe'"
            ).fetchone()["c"]
            marked_bad = conn.execute(
                "SELECT COUNT(*) as c FROM reputation WHERE user_verdict = 'malicious'"
            ).fetchone()["c"]
        return {
            "total_files_tracked": total,
            "user_marked_safe": marked_safe,
            "user_marked_malicious": marked_bad,
        }
