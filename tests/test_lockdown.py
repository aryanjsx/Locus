"""
Phase-3 LOCKDOWN tests (closure-walk-safe capability).

This suite pins the new, stronger invariants that replaced the old
"capability token + handed-out dispatch closure" design:

* There is **no** ``_acquire_capability`` method on any component.
* There is **no** ``_engine_dispatch`` / ``_worker_dispatch`` function
  anywhere.
* Walking the registry's proxy-and-pipeline closures never yields any
  callable other than the registry's own safe pipeline.
* The registry remains immutable after construction.
* ``register_metadata`` still mutates a dict (allowed) but every other
  attribute mutation is rejected.
* Intent / Router contracts around explicit ``source`` remain intact.
"""
from __future__ import annotations

import pytest

from aura.runtime.command_registry import (
    CommandRegistry,
    assert_safe_closures,
)
from aura.core.errors import RegistryError, SchemaError
from aura.core.event_bus import EventBus, EventType
from aura.runtime.execution_engine import ExecutionEngine
from aura.core.intent import Intent
from aura.security.permissions import PermissionLevel
from aura.security.plugin_manifest import (
    ManifestEntry,
    PluginManifest,
)
from aura.core.result import CommandResult
from aura.runtime.router import Router
from aura.core.schema import CommandSpec
from aura.runtime.worker_client import WorkerClient
from tests._inprocess_port import InProcessWorkerPort


# ---------------------------------------------------------------------
# Capability-token APIs have been REMOVED.  Anyone still importing them
# must update — we assert their absence as part of the lockdown.
# ---------------------------------------------------------------------
def test_engine_has_no_capability_surface():
    engine = ExecutionEngine(EventBus())
    forbidden = [
        "_acquire_capability", "_seal",
        "_ExecutionEngine__dispatch",
        "_capability_consumed",
    ]
    for name in forbidden:
        assert not hasattr(engine, name), (
            f"ExecutionEngine must not expose {name!r} after Phase-3 "
            "lockdown (capability-token model removed)."
        )


def test_worker_client_has_no_capability_surface():
    client = WorkerClient(EventBus())
    forbidden = [
        "_acquire_capability", "_seal",
        "_WorkerClient__dispatch",
    ]
    for name in forbidden:
        assert not hasattr(client, name), (
            f"WorkerClient must not expose {name!r} after Phase-3 lockdown."
        )


def test_capability_tokens_no_longer_importable():
    """The old token symbols are gone; importing them must fail."""
    with pytest.raises(ImportError):
        from aura.runtime.execution_engine import _engine_capability_token  # noqa: F401
    with pytest.raises(ImportError):
        from aura.runtime.worker_client import _worker_capability_token  # noqa: F401


# ---------------------------------------------------------------------
# Registry exposes only the documented API surface.
# ---------------------------------------------------------------------
def test_registry_does_not_expose_bypass_surface():
    bus = EventBus()
    engine = ExecutionEngine(bus)
    registry = CommandRegistry(
        bus, InProcessWorkerPort(engine),
        manifest=PluginManifest.permissive(),
    )

    forbidden = [
        "_engine", "_worker", "_worker_port", "_dispatcher_source",
        "_dispatch", "__dispatch", "_seal", "_acquire_capability",
        "_CommandRegistry__dispatch",
        "_CommandRegistry__has",
        "_CommandRegistry__engine",
        "_CommandRegistry__worker",
        "attach_security", "attach_manifest",
        "engine", "worker", "dispatch",
    ]
    for name in forbidden:
        assert not hasattr(registry, name), (
            f"CommandRegistry must not expose {name!r} after lockdown"
        )


def test_registry_dir_only_shows_public_api():
    bus = EventBus()
    engine = ExecutionEngine(bus)
    registry = CommandRegistry(
        bus, InProcessWorkerPort(engine),
        manifest=PluginManifest.permissive(),
    )
    public = set(dir(registry))
    assert public == {
        "execute", "register_metadata", "unregister",
        "has", "get", "list",
    }


def test_registry_executor_proxy_only_exposes_execute():
    bus = EventBus()
    engine = ExecutionEngine(bus)
    registry = CommandRegistry(
        bus, InProcessWorkerPort(engine),
        manifest=PluginManifest.permissive(),
    )
    proxy = registry._executor
    assert dir(proxy) == ["execute"]
    for name in ("_call", "_fn", "_dispatch", "__dict__", "__slots__"):
        with pytest.raises(AttributeError):
            getattr(proxy, name)


# ---------------------------------------------------------------------
# Registry construction invariants.
# ---------------------------------------------------------------------
def test_registry_construction_requires_manifest():
    bus = EventBus()
    engine = ExecutionEngine(bus)
    with pytest.raises(RegistryError) as excinfo:
        CommandRegistry(
            bus, InProcessWorkerPort(engine), manifest=None  # type: ignore[arg-type]
        )
    assert "manifest" in str(excinfo.value).lower()


def test_registry_rejects_callable_worker_port():
    """A callable port would show up in the closure walk as an unsafe
    callable - so the constructor refuses it up-front."""
    bus = EventBus()

    def not_a_port(request):  # pragma: no cover - rejected at init
        return {}

    with pytest.raises(RegistryError):
        CommandRegistry(
            bus, not_a_port,  # type: ignore[arg-type]
            manifest=PluginManifest.permissive(),
        )


def test_registry_requires_send_method():
    bus = EventBus()

    class _NoSend:
        pass

    with pytest.raises(RegistryError):
        CommandRegistry(
            bus, _NoSend(),  # type: ignore[arg-type]
            manifest=PluginManifest.permissive(),
        )


# ---------------------------------------------------------------------
# Manifest enforcement on register_metadata.
# ---------------------------------------------------------------------
def _with_manifest(manifest: PluginManifest) -> CommandRegistry:
    bus = EventBus()
    engine = ExecutionEngine(bus)
    return CommandRegistry(
        bus, InProcessWorkerPort(engine), manifest=manifest
    )


def test_registry_rejects_unknown_action_against_manifest():
    manifest = PluginManifest(
        {
            "known.action": ManifestEntry(
                plugin="x",
                action="known.action",
                permission_level=PermissionLevel.LOW,
                destructive=False,
                audit_events=("command.executing",),
            ),
        }
    )
    registry = _with_manifest(manifest)
    with pytest.raises(RegistryError) as excinfo:
        registry.register_metadata(
            "unknown.action",
            plugin="x",
            permission_level=PermissionLevel.LOW,
        )
    assert "unknown" in str(excinfo.value).lower()


def test_registry_rejects_destructive_flag_lie():
    manifest = PluginManifest(
        {
            "x.rm": ManifestEntry(
                plugin="x",
                action="x.rm",
                permission_level=PermissionLevel.HIGH,
                destructive=True,
                audit_events=("command.destructive",),
            ),
        }
    )
    registry = _with_manifest(manifest)
    with pytest.raises(RegistryError) as excinfo:
        registry.register_metadata(
            "x.rm",
            plugin="x",
            permission_level=PermissionLevel.HIGH,
            destructive=False,
        )
    assert "destructive" in str(excinfo.value).lower()


def test_registry_rejects_permission_lie():
    manifest = PluginManifest(
        {
            "x.a": ManifestEntry(
                plugin="x",
                action="x.a",
                permission_level=PermissionLevel.HIGH,
                destructive=False,
                audit_events=("command.executing",),
            ),
        }
    )
    registry = _with_manifest(manifest)
    with pytest.raises(RegistryError) as excinfo:
        registry.register_metadata(
            "x.a",
            plugin="x",
            permission_level=PermissionLevel.LOW,
            destructive=False,
        )
    assert "permission_level" in str(excinfo.value)


# ---------------------------------------------------------------------
# Schema limits.
# ---------------------------------------------------------------------
def test_validate_command_rejects_oversized_action_name():
    from aura.core.schema import MAX_ACTION_NAME_LEN, validate_command

    oversized = "x" * (MAX_ACTION_NAME_LEN + 1)
    with pytest.raises(SchemaError) as excinfo:
        validate_command({"action": oversized, "params": {}})
    assert str(MAX_ACTION_NAME_LEN) in str(excinfo.value)


def test_validate_command_accepts_name_exactly_at_limit():
    from aura.core.schema import MAX_ACTION_NAME_LEN, validate_command

    at_limit = "x" * MAX_ACTION_NAME_LEN
    spec = validate_command({"action": at_limit, "params": {}})
    assert spec.action == at_limit


# ---------------------------------------------------------------------
# Intent / Router source contract.
# ---------------------------------------------------------------------
def test_intent_construction_rejects_source_kwarg():
    with pytest.raises(TypeError):
        Intent(action="file.delete", source="cli")  # type: ignore[call-arg]


def test_intent_has_no_source_attribute():
    intent = Intent(action="system.cpu")
    assert not hasattr(intent, "source")


# ---------------------------------------------------------------------
# Audit events + destructive event emission.
# ---------------------------------------------------------------------
def test_registry_emits_audit_events_on_every_execution():
    bus = EventBus()
    engine = ExecutionEngine(bus)

    class _Owner:
        pass

    engine.register(
        "audit.probe", lambda: CommandResult(True, "ok"),
        plugin_instance=_Owner(),
    )
    registry = CommandRegistry(
        bus, InProcessWorkerPort(engine),
        manifest=PluginManifest.permissive(), auto_confirm=True,
    )
    registry.register_metadata(
        "audit.probe", plugin="t", permission_level=PermissionLevel.LOW,
    )

    events: list[str] = []
    bus.subscribe(EventType.COMMAND_EXECUTING, lambda payload: events.append("COMMAND_EXECUTING"))
    bus.subscribe(EventType.COMMAND_COMPLETED, lambda payload: events.append("COMMAND_COMPLETED"))

    registry.execute(
        CommandSpec(action="audit.probe", params={}, requires_confirm=False),
        source="cli",
    )
    assert "COMMAND_EXECUTING" in events
    assert "COMMAND_COMPLETED" in events


def test_destructive_execution_emits_destructive_event():
    bus = EventBus()
    engine = ExecutionEngine(bus)

    class _Owner:
        pass

    engine.register(
        "audit.destructive", lambda: CommandResult(True, "wiped"),
        plugin_instance=_Owner(),
    )

    manifest = PluginManifest(
        {
            "audit.destructive": ManifestEntry(
                plugin="t",
                action="audit.destructive",
                permission_level=PermissionLevel.HIGH,
                destructive=True,
                audit_events=("command.destructive",),
            ),
        }
    )
    registry = CommandRegistry(
        bus, InProcessWorkerPort(engine),
        manifest=manifest, auto_confirm=True,
    )
    registry.register_metadata(
        "audit.destructive",
        plugin="t",
        permission_level=PermissionLevel.HIGH,
        destructive=True,
    )

    destructive: list[str] = []
    bus.subscribe(
        EventType.COMMAND_DESTRUCTIVE,
        lambda payload: destructive.append(payload.get("action")),
    )
    registry.execute(
        CommandSpec(action="audit.destructive", params={},
                    requires_confirm=False),
        source="cli",
    )
    assert destructive == ["audit.destructive"]


# ---------------------------------------------------------------------
# Router.execute_intent requires explicit source.
# ---------------------------------------------------------------------
def test_router_execute_intent_requires_source():
    bus = EventBus()
    engine = ExecutionEngine(bus)

    class _Owner:
        pass

    engine.register(
        "r.probe", lambda: CommandResult(True, "ok"),
        plugin_instance=_Owner(),
    )
    registry = CommandRegistry(
        bus, InProcessWorkerPort(engine),
        manifest=PluginManifest.permissive(),
        auto_confirm=True,
    )
    registry.register_metadata(
        "r.probe", plugin="t", permission_level=PermissionLevel.LOW,
    )
    router = Router(bus, registry, intent_parsers=[])

    intent = Intent(action="r.probe", args={})
    with pytest.raises(TypeError):
        router.execute_intent(intent)  # type: ignore[call-arg]

    with pytest.raises(SchemaError):
        router.execute_intent(intent, source="   ")

    result = router.execute_intent(intent, source="cli")
    assert result.success is True


# ---------------------------------------------------------------------
# Registry is immutable after construction.
# ---------------------------------------------------------------------
def test_registry_is_immutable_after_construction():
    bus = EventBus()
    engine = ExecutionEngine(bus)
    registry = CommandRegistry(
        bus, InProcessWorkerPort(engine),
        manifest=PluginManifest.permissive(),
    )

    with pytest.raises(AttributeError):
        registry._safety_gate = None  # type: ignore[misc]
    with pytest.raises(AttributeError):
        registry._rate_limiter = None  # type: ignore[misc]
    with pytest.raises(AttributeError):
        registry._executor = lambda *a, **k: None  # type: ignore[misc]
    with pytest.raises(AttributeError):
        del registry._executor  # type: ignore[misc]


# ---------------------------------------------------------------------
# Closure-walk self-audit: only the safe pipeline is a reachable callable.
# ---------------------------------------------------------------------
def test_closure_walk_self_audit_passes_on_freshly_built_registry():
    bus = EventBus()
    engine = ExecutionEngine(bus)
    registry = CommandRegistry(
        bus, InProcessWorkerPort(engine),
        manifest=PluginManifest.permissive(),
    )
    # Must not raise: every closure cell is non-callable or the safe pipeline.
    assert_safe_closures(registry)


# ---------------------------------------------------------------------
# bootstrap() return signature (worker is NOT exposed).
# ---------------------------------------------------------------------
def test_bootstrap_signature_does_not_return_worker():
    import inspect

    from aura.cli import bootstrap

    sig = inspect.signature(bootstrap)
    anno = str(sig.return_annotation)
    assert "WorkerClient" not in anno, (
        f"bootstrap() must not return the WorkerClient; got {anno!r}"
    )
    assert "Router" in anno
    assert "CommandRegistry" in anno
