"""TaskExecutor must give every step in a plan the SAME trace_id."""
from __future__ import annotations

from aura.runtime.command_registry import CommandRegistry
from aura.core.event_bus import EventBus, EventType
from aura.runtime.execution_engine import ExecutionEngine
from aura.security.permissions import PermissionLevel
from aura.runtime.planner import TaskExecutor, TaskPlan, TaskStep
from aura.security.plugin_manifest import PluginManifest
from aura.security.rate_limiter import RateLimiter
from aura.core.result import CommandResult
from aura.runtime.router import Router
from aura.security.safety_gate import AutoConfirmGate
from tests._inprocess_port import InProcessWorkerPort


def _build_router():
    bus = EventBus()
    engine = ExecutionEngine(bus)
    registry = CommandRegistry(
        bus, InProcessWorkerPort(engine), manifest=PluginManifest.permissive()
    )

    class _Plugin:
        pass

    inst = _Plugin()
    engine.register("step.a", lambda: CommandResult(True, "A"), plugin_instance=inst)
    engine.register("step.b", lambda: CommandResult(True, "B"), plugin_instance=inst)
    registry.register_metadata(
        "step.a", plugin="t", permission_level=PermissionLevel.LOW
    )
    registry.register_metadata(
        "step.b", plugin="t", permission_level=PermissionLevel.LOW
    )
    router = Router(
        bus, registry, [],
        safety_gate=AutoConfirmGate(bus),
        rate_limiter=RateLimiter(
            max_per_minute=1000, repeat_threshold=1000, source_overrides={}
        ),
    )
    return bus, router


def test_plan_steps_share_single_trace_id() -> None:
    bus, router = _build_router()
    executor = TaskExecutor(bus, router)

    plan_trace_ids: list[str] = []
    step_trace_ids: list[str] = []

    def on_plan_started(payload: dict) -> None:
        tid = payload.get("trace_id")
        if tid:
            plan_trace_ids.append(tid)

    def on_step(payload: dict) -> None:
        tid = payload.get("trace_id")
        if tid:
            step_trace_ids.append(tid)

    bus.subscribe(EventType.PLAN_STARTED, on_plan_started)
    bus.subscribe(EventType.PLAN_STEP_STARTED, on_step)
    bus.subscribe(EventType.PLAN_STEP_COMPLETED, on_step)

    plan = TaskPlan(
        description="two-step demo",
        steps=[TaskStep(action="step.a"), TaskStep(action="step.b")],
    )
    report = executor.execute(plan)
    assert report.success

    assert len(plan_trace_ids) == 1
    single = plan_trace_ids[0]
    assert step_trace_ids, "expected step events"
    assert all(t == single for t in step_trace_ids), (
        f"trace_ids drift within plan: {step_trace_ids!r}"
    )
