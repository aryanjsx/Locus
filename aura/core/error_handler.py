"""
AURA — Centralised Error Handler.

Every failure path funnels through :func:`handle_error`, which emits a
structured ``command.error`` event on the bus and returns a uniform
:class:`~aura.core.result.CommandResult` whose ``error_code`` column
identifies the category.  The router uses this instead of scattered
``try/except`` blocks; the bus logger writes a JSON record for every
event emitted.
"""

from __future__ import annotations

from typing import Any

from aura.core.errors import (
    AuraError,
    ConfigError,
    ConfirmationDenied,
    ConfirmationTimeout,
    EngineError,
    ExecutionError,
    PermissionDenied,
    PlanError,
    PluginError,
    PolicyError,
    RateLimitError,
    RegistryError,
    SandboxError,
    SchemaError,
)
from aura.core.event_bus import EventBus, EventType
from aura.core.result import CommandResult
from aura.core.tracing import current_trace_id

_ERROR_CODES: dict[type, str] = {
    ConfigError: "CONFIG_ERROR",
    SchemaError: "SCHEMA_ERROR",
    SandboxError: "SANDBOX_ERROR",
    PluginError: "PLUGIN_ERROR",
    PolicyError: "POLICY_BLOCKED",
    PermissionDenied: "PERMISSION_DENIED",
    ConfirmationDenied: "CONFIRMATION_DENIED",
    ConfirmationTimeout: "CONFIRMATION_TIMEOUT",
    RateLimitError: "RATE_LIMIT_BLOCKED",
    PlanError: "PLAN_ERROR",
    EngineError: "ENGINE_ERROR",
    RegistryError: "UNKNOWN_COMMAND",
    ExecutionError: "EXECUTION_ERROR",
    AuraError: "AURA_ERROR",
}


def _classify(exc: BaseException) -> str:
    for cls, code in _ERROR_CODES.items():
        if isinstance(exc, cls):
            return code
    return "INTERNAL_ERROR"


def handle_error(
    exc: BaseException,
    *,
    bus: EventBus | None = None,
    context: dict[str, Any] | None = None,
) -> CommandResult:
    """Translate *exc* into a standardised :class:`CommandResult`.

    Emits ``command.error`` on *bus* (if provided) with the classified
    error code and originating context so that the logger subscription
    captures every failure uniformly.
    """
    code = _classify(exc)
    context = dict(context or {})
    if "trace_id" not in context:
        tid = current_trace_id()
        if tid is not None:
            context["trace_id"] = tid

    payload = {
        "error_code": code,
        "error_type": type(exc).__name__,
        "error": str(exc),
        **context,
    }

    if bus is not None:
        bus.emit(EventType.COMMAND_ERROR, payload)

    return CommandResult(
        success=False,
        message=f"[{code}] {exc}",
        data=payload,
        command_type=context.get("action", ""),
        error_code=code,
    )


def install_default_subscribers(bus: EventBus, logger: Any) -> None:
    """Subscribe the logger to error and subscriber-error channels."""
    def _on_error(payload: dict[str, Any]) -> None:
        logger.error(
            payload.get("error_code", "INTERNAL_ERROR"),
            extra={
                "event": "COMMAND_ERROR",
                "action": payload.get("action"),
                "data": payload,
            },
        )

    bus.subscribe(EventType.COMMAND_ERROR, _on_error)
    bus.subscribe(EventType.SYSTEM_ERROR, _on_error)
