"""
AURA — Task Planner.

Multi-step execution with pre-flight validation and rollback on failure.

- :class:`TaskStep` — a single ``(action, params, rollback_action,
  rollback_params)`` tuple.
- :class:`TaskPlan` — an ordered list of :class:`TaskStep` with a human
  description.
- :class:`TaskExecutor` — validates every step against the registry
  *before* executing any, runs steps sequentially, and on failure walks
  back through already-completed steps running their rollback actions
  (if any).

All plan steps execute through the provided ``Router`` with ``source="planner"``
so they are subject to permission, safety, and rate-limit checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Sequence

from aura.core.errors import PlanError
from aura.core.event_bus import EventBus, EventType
from aura.core.logger import get_logger
from aura.core.result import CommandResult
from aura.core.tracing import TraceScope, current_trace_id

_logger = get_logger("aura.planner")


class _NullScope:
    """Drop-in no-op ``with`` context used when a parent TraceScope exists."""
    def __enter__(self):  # noqa: D401
        return self
    def __exit__(self, exc_type, exc, tb):
        return False


@dataclass(frozen=True, slots=True)
class TaskStep:
    action: str
    params: dict[str, Any] = field(default_factory=dict)
    rollback_action: str | None = None
    rollback_params: dict[str, Any] = field(default_factory=dict)
    description: str = ""


@dataclass(slots=True)
class TaskPlan:
    description: str
    steps: list[TaskStep] = field(default_factory=list)


@dataclass(slots=True)
class TaskReport:
    success: bool
    description: str
    completed: list[str] = field(default_factory=list)
    failed_at: str | None = None
    error: str | None = None
    rollbacks: list[str] = field(default_factory=list)
    results: list[CommandResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "description": self.description,
            "completed": self.completed,
            "failed_at": self.failed_at,
            "error": self.error,
            "rollbacks": self.rollbacks,
            "result_count": len(self.results),
        }


# Hard upper bound so a malformed plan cannot stall the system.
MAX_PLAN_STEPS: int = 32


class TaskExecutor:
    """Executes a :class:`TaskPlan` through the Router with rollback."""

    def __init__(self, bus: EventBus, router: Any) -> None:
        self._bus = bus
        self._router = router

    def _has_action(self, action: str) -> bool:
        registry = getattr(self._router, "_registry", None)
        if registry is None:
            return False
        return bool(registry.has(action))

    def validate(self, plan: TaskPlan) -> None:
        if not isinstance(plan, TaskPlan):
            raise PlanError("plan must be a TaskPlan instance")
        if not plan.steps:
            raise PlanError("plan has no steps")
        if len(plan.steps) > MAX_PLAN_STEPS:
            raise PlanError(
                f"plan has {len(plan.steps)} steps; cap is {MAX_PLAN_STEPS}"
            )
        for idx, step in enumerate(plan.steps):
            if not isinstance(step, TaskStep):
                raise PlanError(f"step {idx} is not a TaskStep")
            if not self._has_action(step.action):
                raise PlanError(
                    f"step {idx}: unknown action {step.action!r}"
                )
            if step.rollback_action and not self._has_action(step.rollback_action):
                raise PlanError(
                    f"step {idx}: unknown rollback action "
                    f"{step.rollback_action!r}"
                )

    def execute(self, plan: TaskPlan) -> TaskReport:
        """Run *plan*; on any step failure, rollback and return a failed report.

        A single :class:`TraceScope` is held for the lifetime of the plan
        so every step (and every rollback) shares ONE ``trace_id`` — the
        plan's own ID.  Correlating all plan activity in logs / audit is
        therefore trivial.
        """
        # One trace for the whole plan — inherit if already inside a scope.
        if current_trace_id() is None:
            scope_ctx = TraceScope()
            self._owned_scope = True
        else:
            scope_ctx = _NullScope()
            self._owned_scope = False

        with scope_ctx:
            return self._execute_inner(plan)

    def _execute_inner(self, plan: TaskPlan) -> TaskReport:
        self.validate(plan)
        report = TaskReport(success=True, description=plan.description)
        completed_steps: List[TaskStep] = []
        plan_trace = current_trace_id()

        self._bus.emit(
            EventType.PLAN_STARTED,
            {
                "description": plan.description,
                "steps": [s.action for s in plan.steps],
                "step_detail": [
                    {"action": s.action, "params": dict(s.params),
                     "rollback_action": s.rollback_action,
                     "rollback_params": dict(s.rollback_params)}
                    for s in plan.steps
                ],
                "trace_id": plan_trace,
            },
        )

        for step in plan.steps:
            self._bus.emit(
                EventType.PLAN_STEP_STARTED,
                {"action": step.action, "trace_id": current_trace_id()},
            )
            try:
                result = self._router.execute_action(
                    step.action, step.params, source="planner"
                )
            except Exception as exc:  # noqa: BLE001 — boundary must not leak
                report.success = False
                report.failed_at = step.action
                report.error = f"{type(exc).__name__}: {exc}"
                self._rollback(completed_steps, report)
                self._bus.emit(
                    EventType.PLAN_FAILED,
                    {
                        "description": plan.description,
                        "failed_at": step.action,
                        "error": report.error,
                        "trace_id": current_trace_id(),
                    },
                )
                return report

            report.results.append(result)
            if not result.success:
                report.success = False
                report.failed_at = step.action
                report.error = result.message
                self._rollback(completed_steps, report)
                self._bus.emit(
                    EventType.PLAN_FAILED,
                    {
                        "description": plan.description,
                        "failed_at": step.action,
                        "error": result.message,
                        "trace_id": current_trace_id(),
                    },
                )
                return report

            completed_steps.append(step)
            report.completed.append(step.action)
            self._bus.emit(
                EventType.PLAN_STEP_COMPLETED,
                {"action": step.action, "trace_id": current_trace_id()},
            )

        self._bus.emit(
            EventType.PLAN_COMPLETED,
            {
                "description": plan.description,
                "completed": report.completed,
                "trace_id": current_trace_id(),
            },
        )
        return report

    def _rollback(
        self, completed_steps: Sequence[TaskStep], report: TaskReport
    ) -> None:
        for step in reversed(completed_steps):
            if not step.rollback_action:
                continue
            self._bus.emit(
                EventType.PLAN_ROLLBACK,
                {
                    "action": step.rollback_action,
                    "of": step.action,
                    "trace_id": current_trace_id(),
                },
            )
            try:
                self._router.execute_action(
                    step.rollback_action,
                    step.rollback_params,
                    source="planner",
                )
                report.rollbacks.append(step.rollback_action)
            except Exception as exc:  # noqa: BLE001
                _logger.error(
                    "plan.rollback.failed",
                    extra={
                        "event": "plan.rollback.failed",
                        "action": step.rollback_action,
                        "data": {
                            "error": str(exc),
                            "error_type": type(exc).__name__,
                        },
                    },
                )
