"""
AURA — Plugin Loader.

Scans a plugins directory for packages that expose a ``plugin.py``
module with a ``Plugin`` class.  For each one, it:

1. imports the module
2. instantiates the ``Plugin`` class
3. asks it for a ``{action: entry}`` dict via ``register_commands()``
4. hands each bound-method handler to the :class:`ExecutionEngine`
5. registers the matching metadata (permission level, destructive, …)
   with the :class:`CommandRegistry`
6. collects intent parsers for the router

The loader is the ONLY place where a plugin instance is held.  Callable
references to executors live only in the engine, so external code has
no supported path to invoke them directly.
"""

from __future__ import annotations

import importlib
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from aura.security.audit_events import (
    AuditEventRegistry,
    get_audit_event_registry,
)
from aura.core.errors import PluginError
from aura.core.event_bus import EventBus, EventType
from aura.runtime.execution_engine import ExecutionEngine
from aura.security.permissions import PermissionLevel
from aura.core.plugin_base import IntentParser, Plugin
from aura.security.plugin_manifest import (
    PluginManifest,
    PluginManifestError,
    default_manifest_path,
)

_SKIP_PREFIXES: tuple[str, ...] = ("_", ".")
_SKIP_NAMES: frozenset[str] = frozenset({"__pycache__"})


@dataclass
class LoadedPlugin:
    name: str
    actions: list[str] = field(default_factory=list)
    intents: list[IntentParser] = field(default_factory=list)


class PluginLoader:
    def __init__(
        self,
        bus: EventBus,
        registry: Any,  # duck-typed metadata sink (register_metadata / list)
        engine: ExecutionEngine,
        *,
        package_prefix: str = "plugins",
        manifest: PluginManifest | None = None,
        manifest_path: Path | None = None,
        audit_events: AuditEventRegistry | None = None,
    ) -> None:
        self._bus = bus
        self._registry = registry
        self._engine = engine
        self._package_prefix = package_prefix
        self.__instances: dict[str, Plugin] = {}
        self._loaded: dict[str, LoadedPlugin] = {}
        self._lock = threading.RLock()
        if manifest is None:
            manifest = PluginManifest.load(
                manifest_path or default_manifest_path()
            )
        self._manifest = manifest
        self._audit_events = audit_events or get_audit_event_registry()

    def discover(self, plugins_dir: Path) -> list[Path]:
        if not plugins_dir.is_dir():
            raise PluginError(f"Plugins directory not found: {plugins_dir}")
        candidates: list[Path] = []
        for child in sorted(plugins_dir.iterdir()):
            if not child.is_dir():
                continue
            if child.name in _SKIP_NAMES:
                continue
            if any(child.name.startswith(p) for p in _SKIP_PREFIXES):
                continue
            if not (child / "plugin.py").exists():
                continue
            candidates.append(child)
        return candidates

    def load_all(self, plugins_dir: Path) -> list[LoadedPlugin]:
        loaded: list[LoadedPlugin] = []
        for plugin_dir in self.discover(plugins_dir):
            loaded.append(self._load_one(plugin_dir))
        return loaded

    def _load_one(self, plugin_dir: Path) -> LoadedPlugin:
        plugin_name = plugin_dir.name
        module_name = f"{self._package_prefix}.{plugin_name}.plugin"

        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            raise PluginError(
                f"Failed to import {module_name}: {exc}"
            ) from exc

        plugin_cls = getattr(module, "Plugin", None)
        if plugin_cls is None or not isinstance(plugin_cls, type):
            raise PluginError(
                f"{module_name} does not expose a 'Plugin' class"
            )
        if not issubclass(plugin_cls, Plugin):
            raise PluginError(
                f"{module_name}.Plugin must subclass aura.core.plugin_base.Plugin"
            )

        try:
            instance = plugin_cls(bus=self._bus)
        except Exception as exc:
            raise PluginError(
                f"Plugin {plugin_name} failed to initialise: {exc}"
            ) from exc

        commands = self._safe_call(instance.register_commands, plugin_name)
        if not isinstance(commands, dict):
            raise PluginError(
                f"{plugin_name}.register_commands() must return dict, "
                f"got {type(commands).__name__}"
            )

        actions: list[str] = []
        for action, entry in commands.items():
            handler, description, destructive, level = self._normalise_entry(
                plugin_name, action, entry
            )
            # Authoritative check against the safety manifest — the
            # plugin's self-declared flags MUST match exactly.
            try:
                manifest_entry = self._manifest.check(
                    plugin=plugin_name,
                    action=action,
                    permission_level=level,
                    destructive=destructive,
                )
            except PluginManifestError as exc:
                raise PluginError(str(exc)) from exc

            # Register audit-event coverage so the global registry
            # knows this action is covered by the declared events.
            self._audit_events.register_action_coverage(
                action, manifest_entry.audit_events
            )
            if manifest_entry.destructive:
                # Belt-and-braces: manifest.load() already enforces this,
                # but a future defensive refactor should not lose the rule.
                from aura.security.audit_events import AuditCoverageError
                try:
                    self._audit_events.require_coverage(action)
                except AuditCoverageError as exc:
                    raise PluginError(str(exc)) from exc

            self._engine.register(action, handler, plugin_instance=instance)
            self._registry.register_metadata(
                action,
                plugin=plugin_name,
                description=description,
                destructive=destructive,
                permission_level=level,
            )
            actions.append(action)

        intents = self._safe_call(instance.register_intents, plugin_name) or []
        if not isinstance(intents, list):
            raise PluginError(
                f"{plugin_name}.register_intents() must return list, "
                f"got {type(intents).__name__}"
            )
        for parser in intents:
            if not callable(parser):
                raise PluginError(
                    f"{plugin_name}.register_intents() returned non-callable"
                )

        loaded = LoadedPlugin(
            name=plugin_name,
            actions=actions,
            intents=list(intents),
        )
        with self._lock:
            self.__instances[plugin_name] = instance
            self._loaded[plugin_name] = loaded

        self._bus.emit(
            EventType.PLUGIN_LOADED,
            {
                "name": plugin_name,
                "actions": actions,
                "intent_parsers": len(intents),
            },
        )
        return loaded

    @staticmethod
    def _normalise_entry(
        plugin: str,
        action: str,
        entry: Any,
    ) -> tuple[Callable[..., Any], str, bool, PermissionLevel]:
        if callable(entry):
            return entry, "", False, PermissionLevel.MEDIUM
        if isinstance(entry, dict):
            handler = entry.get("handler")
            if not callable(handler):
                raise PluginError(
                    f"{plugin}.{action}: 'handler' must be callable"
                )
            level_raw = entry.get("permission_level", PermissionLevel.MEDIUM)
            try:
                level = PermissionLevel.parse(level_raw)
            except ValueError as exc:
                raise PluginError(
                    f"{plugin}.{action}: invalid permission_level "
                    f"{level_raw!r} ({exc})"
                ) from exc
            return (
                handler,
                str(entry.get("description", "") or ""),
                bool(entry.get("destructive", False)),
                level,
            )
        raise PluginError(
            f"{plugin}.{action}: registration must be callable or dict, "
            f"got {type(entry).__name__}"
        )

    @staticmethod
    def _safe_call(fn: Callable[[], Any], plugin: str) -> Any:
        try:
            return fn()
        except Exception as exc:
            raise PluginError(
                f"{plugin}.{fn.__name__}() raised {type(exc).__name__}: {exc}"
            ) from exc

    def loaded(self) -> list[LoadedPlugin]:
        with self._lock:
            return list(self._loaded.values())

    def intent_parsers(self) -> list[IntentParser]:
        with self._lock:
            parsers: list[IntentParser] = []
            for lp in self._loaded.values():
                parsers.extend(lp.intents)
            return parsers
