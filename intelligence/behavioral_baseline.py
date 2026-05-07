"""
Behavioral Baseline — Per-device learning of "normal" activity.

The single most effective false-positive reducer. After 7 days of observation,
the system knows YOUR specific patterns:
  - Which processes you run regularly
  - Their typical CPU/memory ranges
  - Which networks they talk to
  - When they normally run (work hours, idle hours)

A process running outside its baseline = anomaly worth investigating.
A process matching its baseline = ignore even if it has "scary" attributes.
"""

import json
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass
class ProcessBaseline:
    """Learned profile of one process."""
    name: str
    exe_paths: set[str] = field(default_factory=set)
    typical_parents: set[str] = field(default_factory=set)
    typical_command_args: list[str] = field(default_factory=list)
    
    # Numeric ranges (mean, stdev)
    cpu_observations: list[float] = field(default_factory=list)
    memory_observations: list[float] = field(default_factory=list)
    thread_count_observations: list[int] = field(default_factory=list)
    
    # Network behavior
    typical_remote_ips: set[str] = field(default_factory=set)
    typical_remote_ports: set[int] = field(default_factory=set)
    
    # Temporal patterns
    first_seen: datetime = field(default_factory=datetime.utcnow)
    last_seen: datetime = field(default_factory=datetime.utcnow)
    observation_count: int = 0
    active_hours: set[int] = field(default_factory=set)  # hours of day (0-23)
    
    @property
    def is_mature(self) -> bool:
        """A baseline is mature after 50+ observations spanning 5+ days."""
        days_observed = (datetime.utcnow() - self.first_seen).days
        return self.observation_count >= 50 and days_observed >= 5
    
    @property
    def cpu_stats(self) -> tuple[float, float]:
        """Mean, stdev of CPU usage."""
        if len(self.cpu_observations) < 2:
            return (0.0, 0.0)
        return (statistics.mean(self.cpu_observations), statistics.stdev(self.cpu_observations))
    
    @property
    def memory_stats(self) -> tuple[float, float]:
        if len(self.memory_observations) < 2:
            return (0.0, 0.0)
        return (statistics.mean(self.memory_observations), statistics.stdev(self.memory_observations))


@dataclass
class AnomalyScore:
    is_anomaly: bool
    score: float  # 0.0 = normal, 1.0 = highly anomalous
    deviations: list[str]
    matched_baseline: bool


class BehavioralBaseline:
    """
    Builds and queries per-process baselines for THIS device.
    
    Persistent — learns continuously and survives restarts.
    """

    def __init__(self, storage_path: str = "./data/baseline.json"):
        self.storage_path = Path(storage_path)
        self.baselines: dict[str, ProcessBaseline] = {}
        self._load()

    def observe(self, process_info: dict[str, Any]) -> None:
        """Record a single process observation. Call on every scan."""
        name = (process_info.get("name") or "").lower()
        if not name:
            return

        baseline = self.baselines.get(name)
        if baseline is None:
            baseline = ProcessBaseline(name=name)
            self.baselines[name] = baseline

        # Update baseline
        if exe := process_info.get("exe"):
            baseline.exe_paths.add(exe.lower())
        if parent := process_info.get("parent_name"):
            baseline.typical_parents.add(parent.lower())

        if cpu := process_info.get("cpu_percent"):
            baseline.cpu_observations.append(float(cpu))
            if len(baseline.cpu_observations) > 1000:
                baseline.cpu_observations = baseline.cpu_observations[-1000:]

        if mem := process_info.get("memory_percent"):
            baseline.memory_observations.append(float(mem))
            if len(baseline.memory_observations) > 1000:
                baseline.memory_observations = baseline.memory_observations[-1000:]

        if threads := process_info.get("num_threads"):
            baseline.thread_count_observations.append(int(threads))
            if len(baseline.thread_count_observations) > 500:
                baseline.thread_count_observations = baseline.thread_count_observations[-500:]

        baseline.last_seen = datetime.utcnow()
        baseline.observation_count += 1
        baseline.active_hours.add(datetime.utcnow().hour)

    def evaluate(self, process_info: dict[str, Any]) -> AnomalyScore:
        """Compare a process against its baseline. Returns anomaly score."""
        name = (process_info.get("name") or "").lower()
        baseline = self.baselines.get(name)

        if baseline is None or not baseline.is_mature:
            # Unknown or immature baseline — neutral verdict
            return AnomalyScore(
                is_anomaly=False, score=0.0, deviations=[], matched_baseline=False
            )

        deviations = []
        score = 0.0

        # 1. Path mismatch (very strong signal)
        exe = (process_info.get("exe") or "").lower()
        if exe and baseline.exe_paths and exe not in baseline.exe_paths:
            deviations.append(
                f"Path mismatch: '{exe}' never seen before. "
                f"Known: {list(baseline.exe_paths)[:3]}"
            )
            score += 0.6

        # 2. Parent mismatch
        parent = (process_info.get("parent_name") or "").lower()
        if parent and baseline.typical_parents and parent not in baseline.typical_parents:
            deviations.append(
                f"Unusual parent: '{parent}'. Typical: {list(baseline.typical_parents)[:3]}"
            )
            score += 0.4

        # 3. CPU outlier (3+ stdev from mean)
        cpu = process_info.get("cpu_percent")
        if cpu is not None and len(baseline.cpu_observations) >= 30:
            mean, stdev = baseline.cpu_stats
            if stdev > 0:
                z = abs(cpu - mean) / stdev
                if z > 3.0:
                    deviations.append(
                        f"CPU outlier: {cpu:.1f}% vs typical {mean:.1f}±{stdev:.1f}% (z={z:.1f})"
                    )
                    score += min(0.3, z * 0.05)

        # 4. Memory outlier
        mem = process_info.get("memory_percent")
        if mem is not None and len(baseline.memory_observations) >= 30:
            mean, stdev = baseline.memory_stats
            if stdev > 0:
                z = abs(mem - mean) / stdev
                if z > 3.0:
                    deviations.append(
                        f"Memory outlier: {mem:.1f}% vs typical {mean:.1f}±{stdev:.1f}%"
                    )
                    score += min(0.2, z * 0.04)

        # 5. Activity at unusual hour
        current_hour = datetime.utcnow().hour
        if baseline.active_hours and current_hour not in baseline.active_hours:
            # Only flag if baseline has been narrow (e.g. only daytime usage)
            if len(baseline.active_hours) <= 12:
                deviations.append(
                    f"Active at unusual hour {current_hour}:00 "
                    f"(typical hours: {sorted(baseline.active_hours)})"
                )
                score += 0.15

        score = min(1.0, score)
        return AnomalyScore(
            is_anomaly=(score >= 0.5),
            score=score,
            deviations=deviations,
            matched_baseline=True,
        )

    # ============================================================
    # Persistence
    # ============================================================

    def save(self) -> None:
        """Persist baselines to disk."""
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        for name, b in self.baselines.items():
            data[name] = {
                "name": b.name,
                "exe_paths": list(b.exe_paths),
                "typical_parents": list(b.typical_parents),
                "typical_command_args": b.typical_command_args[-50:],
                "cpu_observations": b.cpu_observations[-200:],
                "memory_observations": b.memory_observations[-200:],
                "thread_count_observations": b.thread_count_observations[-100:],
                "typical_remote_ips": list(b.typical_remote_ips)[:100],
                "typical_remote_ports": list(b.typical_remote_ports),
                "first_seen": b.first_seen.isoformat(),
                "last_seen": b.last_seen.isoformat(),
                "observation_count": b.observation_count,
                "active_hours": list(b.active_hours),
            }

        with open(self.storage_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        logger.info(f"Saved {len(self.baselines)} baselines to {self.storage_path}")

    def _load(self) -> None:
        """Load baselines from disk."""
        if not self.storage_path.exists():
            logger.info("No existing baseline. Starting fresh.")
            return

        try:
            with open(self.storage_path, encoding="utf-8") as f:
                data = json.load(f)

            for name, d in data.items():
                self.baselines[name] = ProcessBaseline(
                    name=d["name"],
                    exe_paths=set(d.get("exe_paths", [])),
                    typical_parents=set(d.get("typical_parents", [])),
                    typical_command_args=d.get("typical_command_args", []),
                    cpu_observations=d.get("cpu_observations", []),
                    memory_observations=d.get("memory_observations", []),
                    thread_count_observations=d.get("thread_count_observations", []),
                    typical_remote_ips=set(d.get("typical_remote_ips", [])),
                    typical_remote_ports=set(d.get("typical_remote_ports", [])),
                    first_seen=datetime.fromisoformat(d["first_seen"]),
                    last_seen=datetime.fromisoformat(d["last_seen"]),
                    observation_count=d.get("observation_count", 0),
                    active_hours=set(d.get("active_hours", [])),
                )
            logger.info(f"Loaded {len(self.baselines)} baselines from {self.storage_path}")
        except Exception as e:
            logger.error(f"Failed to load baseline: {e}")

    # ============================================================
    # Maintenance
    # ============================================================

    def prune_stale(self, max_age_days: int = 90) -> int:
        """Remove baselines for processes not seen in N days."""
        cutoff = datetime.utcnow() - timedelta(days=max_age_days)
        stale = [name for name, b in self.baselines.items() if b.last_seen < cutoff]
        for name in stale:
            del self.baselines[name]
        if stale:
            logger.info(f"Pruned {len(stale)} stale baselines.")
        return len(stale)

    def stats(self) -> dict[str, Any]:
        return {
            "total_processes": len(self.baselines),
            "mature_baselines": sum(1 for b in self.baselines.values() if b.is_mature),
            "total_observations": sum(b.observation_count for b in self.baselines.values()),
        }
