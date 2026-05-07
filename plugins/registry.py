"""
Plugin Registry — Dynamic agent discovery and loading.

Allows adding new agents without modifying core code.
Two registration methods:
  1. Python entry_points (proper plugins, installed via pip)
  2. YAML-defined agents (config-only, no code)
"""

import importlib
import inspect
from pathlib import Path
from typing import Any, Callable

import yaml
from loguru import logger

from validation.schemas import AgentName


class PluginError(Exception):
    """Raised when plugin loading fails."""


class AgentRegistry:
    """Central registry of available agents."""

    def __init__(self):
        self._agents: dict[str, type] = {}
        self._instances: dict[str, Any] = {}
        self._yaml_agents: dict[str, dict] = {}

    # ============================================================
    # Class-based registration (decorator pattern)
    # ============================================================

    def register(self, name: str) -> Callable:
        """Decorator to register an agent class.
        
        Usage:
            @registry.register("custom_agent")
            class MyAgent(BaseAgent): ...
        """
        def decorator(cls: type) -> type:
            if name in self._agents:
                raise PluginError(f"Agent '{name}' already registered")
            
            # Validate it's an agent
            if not hasattr(cls, "analyze"):
                raise PluginError(f"Agent class {cls.__name__} missing analyze() method")
            
            self._agents[name] = cls
            logger.info(f"Registered agent: {name} ({cls.__module__}.{cls.__name__})")
            return cls
        return decorator

    # ============================================================
    # Entry-points discovery (for installed packages)
    # ============================================================

    def discover_entry_points(self, group: str = "council_of_agents.agents") -> int:
        """
        Find agents registered via setup.py entry_points.
        
        Example setup.py:
            entry_points={
                "council_of_agents.agents": [
                    "memory_inspector = mypackage.agents:MemoryInspector",
                ],
            }
        """
        count = 0
        try:
            from importlib.metadata import entry_points
            eps = entry_points(group=group)
            
            for ep in eps:
                try:
                    cls = ep.load()
                    self._agents[ep.name] = cls
                    logger.info(f"Loaded entry-point agent: {ep.name}")
                    count += 1
                except Exception as e:
                    logger.error(f"Failed to load entry-point '{ep.name}': {e}")
        except Exception as e:
            logger.warning(f"Entry-point discovery failed: {e}")
        
        return count

    # ============================================================
    # Filesystem discovery (auto-load from agents/ directory)
    # ============================================================

    def discover_filesystem(self, agents_dir: str = "./agents") -> int:
        """
        Auto-discover agents in a directory.
        Looks for .py files containing classes with @register decorator.
        """
        count = 0
        agents_path = Path(agents_dir)
        if not agents_path.exists():
            return 0

        for py_file in agents_path.glob("*.py"):
            if py_file.name.startswith("_"):
                continue
            try:
                module_name = f"agents.{py_file.stem}"
                spec = importlib.util.spec_from_file_location(module_name, py_file)
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    count += 1
            except Exception as e:
                logger.warning(f"Could not auto-load {py_file}: {e}")
        
        return count

    # ============================================================
    # YAML-defined agents (config-only)
    # ============================================================

    def load_yaml_agents(self, yaml_path: str) -> int:
        """
        Load agents defined in YAML — no code required.
        
        YAML schema:
            agents:
              - name: registry_watcher
                description: Monitors registry changes
                tools: [registry_probe]
                system_prompt: |
                  You watch the Windows registry for...
                rules:
                  - if: "registry_path startswith 'HKEY_LOCAL_MACHINE\\\\Software\\\\Microsoft\\\\Run'"
                    then: alert_high
        """
        path = Path(yaml_path)
        if not path.exists():
            return 0

        with open(path, encoding="utf-8") as f:
            config = yaml.safe_load(f)

        count = 0
        for agent_def in config.get("agents", []):
            name = agent_def.get("name")
            if not name:
                continue
            self._yaml_agents[name] = agent_def
            count += 1
            logger.info(f"Registered YAML agent: {name}")

        return count

    # ============================================================
    # Instantiation
    # ============================================================

    def create(self, name: str, *args, **kwargs) -> Any:
        """Instantiate an agent by name."""
        if name in self._instances:
            return self._instances[name]

        if name in self._agents:
            instance = self._agents[name](*args, **kwargs)
        elif name in self._yaml_agents:
            from plugins.yaml_agent import YAMLAgent
            instance = YAMLAgent(definition=self._yaml_agents[name], *args, **kwargs)
        else:
            raise PluginError(f"No agent registered with name '{name}'")

        self._instances[name] = instance
        return instance

    def list_agents(self) -> dict[str, str]:
        """Return all registered agents and their types."""
        result = {}
        for name, cls in self._agents.items():
            result[name] = f"class:{cls.__module__}.{cls.__name__}"
        for name in self._yaml_agents:
            result[name] = "yaml"
        return result

    def get_metadata(self, name: str) -> dict[str, Any]:
        if name in self._agents:
            cls = self._agents[name]
            return {
                "name": name,
                "type": "class",
                "module": cls.__module__,
                "class": cls.__name__,
                "docstring": inspect.getdoc(cls) or "",
            }
        if name in self._yaml_agents:
            return {
                "name": name,
                "type": "yaml",
                "definition": self._yaml_agents[name],
            }
        raise PluginError(f"Unknown agent: {name}")


# ============================================================
# Singleton registry
# ============================================================

_global_registry: AgentRegistry | None = None


def get_registry() -> AgentRegistry:
    global _global_registry
    if _global_registry is None:
        _global_registry = AgentRegistry()
    return _global_registry
