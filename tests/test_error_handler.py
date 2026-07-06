"""Tests for :mod:`aura.core.error_handler`."""
from __future__ import annotations

from aura.core.error_handler import _classify, handle_error, install_default_subscribers
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


def test_classification_table_is_exhaustive() -> None:
    cases: dict[type[Exception], str] = {
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
    for cls, code in cases.items():
        assert _classify(cls("boom")) == code, cls


def test_unknown_exception_falls_back_to_internal_error() -> None:
    assert _classify(ValueError("?")) == "INTERNAL_ERROR"
    assert _classify(KeyError("?")) == "INTERNAL_ERROR"


def test_handle_error_returns_failure_result_with_error_code() -> None:
    result = handle_error(PolicyError("no"))
    assert not result.success
    assert result.error_code == "POLICY_BLOCKED"
    assert result.message.startswith("[POLICY_BLOCKED]")


def test_handle_error_preserves_chained_cause_classification() -> None:
    try:
        try:
            raise ValueError("inner")
        except ValueError as inner:
            raise SandboxError("outer") from inner
    except SandboxError as exc:
        result = handle_error(exc)
    assert result.error_code == "SANDBOX_ERROR"
    assert "outer" in result.message


def test_handle_error_emits_bus_event() -> None:
    bus = EventBus()
    received: list[dict] = []
    bus.subscribe(EventType.COMMAND_ERROR, lambda e: received.append(e))
    handle_error(RegistryError("oops"), bus=bus, context={"action": "x"})
    assert received
    assert received[0]["error_code"] == "UNKNOWN_COMMAND"
    assert received[0]["action"] == "x"


def test_handle_error_injects_active_trace_id() -> None:
    from aura.core.tracing import TraceScope

    with TraceScope() as sc:
        result = handle_error(PolicyError("x"))
    assert result.data["trace_id"] == sc.trace_id


def test_handle_error_never_raises_on_recursive_bus_failure() -> None:
    """A throwing subscriber on COMMAND_ERROR must not propagate."""
    bus = EventBus()
    bus.subscribe(EventType.COMMAND_ERROR, lambda _: (_ for _ in ()).throw(RuntimeError("x")))
    handle_error(PolicyError("contained"), bus=bus)


def test_install_default_subscribers_logs_errors(caplog) -> None:
    import logging

    bus = EventBus()
    logger = logging.getLogger("aura.error-handler-test")
    logger.setLevel(logging.ERROR)
    install_default_subscribers(bus, logger)
    caplog.set_level(logging.ERROR, logger="aura.error-handler-test")
    bus.emit(EventType.COMMAND_ERROR, {"error_code": "X", "action": "a"})
    assert any("X" in r.getMessage() for r in caplog.records)
