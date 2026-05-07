"""
Agent Instance Registry — non-serializable storage for agent objects.

Why this exists:
  LangGraph serializes the state with msgpack for checkpointing. Agent objects
  (with LLM clients, baselines, etc.) cannot be serialized. So we keep them
  here, indexed by scan_id, and the graph nodes look them up.

Lifecycle:
  - Before graph.ainvoke(): register agents under the scan_id
  - During execution: nodes lookup via get(scan_id, key)
  - After: unregister (cleanup)
"""

import threading
from typing import Any


class AgentInstanceRegistry:
    """Thread-safe registry mapping scan_id -> agent instances dict."""

    def __init__(self):
        self._instances: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def register(self, scan_id: str, key: str, instance: Any) -> None:
        """Register an agent instance under a scan_id."""
        with self._lock:
            if scan_id not in self._instances:
                self._instances[scan_id] = {}
            self._instances[scan_id][key] = instance

    def register_all(self, scan_id: str, instances: dict[str, Any]) -> None:
        """Register multiple instances at once."""
        with self._lock:
            self._instances.setdefault(scan_id, {}).update(instances)

    def get(self, scan_id: str, key: str) -> Any:
        """Retrieve an instance. Returns None if not found."""
        with self._lock:
            return self._instances.get(scan_id, {}).get(key)

    def unregister(self, scan_id: str) -> None:
        """Remove all instances for a scan."""
        with self._lock:
            self._instances.pop(scan_id, None)


# Singleton
_registry: AgentInstanceRegistry | None = None


def get_agent_registry() -> AgentInstanceRegistry:
    global _registry
    if _registry is None:
        _registry = AgentInstanceRegistry()
    return _registry
