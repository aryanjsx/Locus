"""End-to-end router tests covering the full safety pipeline."""
from __future__ import annotations


from aura.runtime.command_registry import CommandRegistry
from aura.core.event_bus import EventBus, EventType
from aura.runtime.execution_engine import ExecutionEngine
from aura.security.permissions import PermissionLevel, PermissionValidator
from aura.security.plugin_manifest import PluginManifest
from aura.security.rate_limiter import RateLimiter
from aura.core.result import CommandResult
from aura.runtime.router import Router
from aura.security.safety_gate import AutoConfirmGate
from tests._inprocess_port import InProcessWorkerPort


def _build(auto_confirm=True, rate_limiter=None, permission_validator=None,
           safety_gate=None):
    bus = EventBus()
    engine = ExecutionEngine(bus)

    class _Owner:
        pass
    owner = _Owner()

    def _cpu() -> CommandResult:
        return CommandResult(success=True, message="25%", data={"pct": 25})

    def _delete(path: str) -> CommandResult:
        return CommandResult(success=True, message=f"deleted {path}",
                             data={"path": path})

    engine.register("system.cpu", _cpu, plugin_instance=owner)
    engine.register("file.delete", _delete, plugin_instance=owner)

    # Security policy is fixed at construction after the lockdown.
    registry = CommandRegistry(
        bus, InProcessWorkerPort(engine),
        manifest=PluginManifest.permissive(),
        auto_confirm=auto_confirm,
        rate_limiter=rate_limiter or RateLimiter(
            max_per_minute=1000, repeat_threshold=1000,
        ),
        permission_validator=permission_validator or PermissionValidator(),
        safety_gate=safety_gate or AutoConfirmGate(bus),
    )
    registry.register_metadata(
        "system.cpu", plugin="t", permission_level=PermissionLevel.LOW,
    )
    registry.register_metadata(
        "file.delete", plugin="t",
        permission_level=PermissionLevel.HIGH, destructive=True,
    )

    def parse(text: str):
        from aura.core.intent import Intent
        if text.lower() == "cpu":
            return Intent(action="system.cpu", args={})
        if text.lower().startswith("delete "):
            return Intent(
                action="file.delete",
                args={"path": text.split(" ", 1)[1]},
            )
        return None

    router = Router(
        bus, registry, intent_parsers=[parse],
        safety_gate=safety_gate or AutoConfirmGate(bus),
        permission_validator=permission_validator or PermissionValidator(),
        rate_limiter=rate_limiter or RateLimiter(
            max_per_minute=1000, repeat_threshold=1000,
        ),
        auto_confirm=auto_confirm,
    )
    return router, bus


def test_happy_path_cpu():
    router, _ = _build()
    result = router.route("cpu", source="cli")
    assert result.success is True


def test_unknown_command_surfaces_error_code():
    router, _ = _build()
    result = router.route("fly to mars", source="cli")
    assert result.success is False
    assert result.error_code == "UNKNOWN_COMMAND"


def test_llm_cannot_run_high_permission():
    router, _ = _build()
    result = router.route("delete target.txt", source="llm")
    assert result.success is False
    assert result.error_code == "PERMISSION_DENIED"


def test_rate_limit_surfaces_error_code():
    rl = RateLimiter(max_per_minute=1, repeat_threshold=1000)
    router, _ = _build(rate_limiter=rl)
    router.route("cpu", source="cli")
    result = router.route("cpu", source="cli")
    assert result.success is False
    assert result.error_code == "RATE_LIMIT_BLOCKED"


def test_trace_id_present_in_events():
    router, bus = _build()
    trace_ids: list[str] = []
    bus.subscribe(EventType.COMMAND_COMPLETED,
                  lambda payload: trace_ids.append(payload.get("trace_id")))
    router.route("cpu", source="cli")
    assert trace_ids
    assert all(isinstance(t, str) and len(t) == 12 for t in trace_ids)
