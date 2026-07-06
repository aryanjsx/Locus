# aura/core/event_bus.py
# AURA Central Event Bus — all cross-module communication happens here.
# Modules NEVER import each other directly — they talk through this bus.

from __future__ import annotations
import logging
from collections import defaultdict
from enum import Enum, auto
from typing import Any, Callable

logger = logging.getLogger("aura.event_bus")


class EventType(Enum):
    # Pipeline lifecycle
    WAKE_WORD_DETECTED      = auto()
    WAKE_WORD_ERROR         = auto()
    RECORDING_STARTED       = auto()
    RECORDING_STOPPED       = auto()
    TRANSCRIPTION_COMPLETE  = auto()
    INTENT_CLASSIFIED       = auto()
    LLM_REQUEST_SENT        = auto()
    LLM_RESPONSE_RECEIVED   = auto()
    COMMAND_PLAN_READY      = auto()
    EXECUTION_STARTED       = auto()
    EXECUTION_COMPLETE      = auto()

    # Safety gate
    SAFETY_CONFIRMATION_REQ = auto()
    SAFETY_CONFIRMED        = auto()
    SAFETY_DENIED           = auto()

    # TTS
    TTS_SPEAK_REQUEST       = auto()
    TTS_SPEAKING_STARTED    = auto()
    TTS_SPEAKING_FINISHED   = auto()

    # Session management
    SESSION_STARTED         = auto()
    SESSION_ENDED           = auto()
    INACTIVITY_TIMEOUT      = auto()
    LISTEN_NOW              = auto()

    # Command lifecycle
    COMMAND_RECEIVED        = auto()
    COMMAND_EXECUTING       = auto()
    COMMAND_COMPLETED       = auto()
    COMMAND_ERROR           = auto()
    COMMAND_DESTRUCTIVE     = auto()
    COMMAND_AUTO_CONFIRMED  = auto()

    # Intent parsing (router layer)
    INTENT_PARSED           = auto()

    # Validation / security gate
    PERMISSION_DENIED       = auto()
    RATE_LIMIT_BLOCKED      = auto()
    POLICY_BLOCKED          = auto()
    SANDBOX_BLOCKED         = auto()
    SCHEMA_REJECTED         = auto()

    # Plan lifecycle
    PLAN_STARTED            = auto()
    PLAN_COMPLETED          = auto()
    PLAN_FAILED             = auto()
    PLAN_ROLLBACK           = auto()
    PLAN_STEP_STARTED       = auto()
    PLAN_STEP_COMPLETED     = auto()

    # Registry / worker lifecycle
    REGISTRY_REGISTERED     = auto()
    REGISTRY_UNREGISTERED   = auto()
    WORKER_READY            = auto()
    WORKER_CRASHED          = auto()
    WORKER_SHUTDOWN         = auto()

    # Plugin loading
    PLUGIN_LOADED           = auto()

    # Audit
    AUDIT_CHAIN_BREAK       = auto()

    # System state
    MODE_CHANGED            = auto()
    SYSTEM_ERROR            = auto()
    PIPELINE_STATE_CHANGED  = auto()


# Type alias — all payloads are plain dicts
EventPayload = dict[str, Any]


def _event_name(event_type: EventType) -> str:
    """Extract the .name from an EventType, or raise TypeError for raw strings."""
    if isinstance(event_type, str):
        raise TypeError(
            f"EventBus received a raw string {event_type!r} where an "
            f"EventType enum member was expected. Fix the caller to "
            f"pass EventType.<MEMBER> instead of a string literal."
        )
    return event_type.name


class EventBus:
    """
    Synchronous central event bus.
    Handlers execute in the emitter's thread — keep them fast.
    """

    WILDCARD = "__ALL__"

    def __init__(self) -> None:
        self._handlers: dict[EventType, list[Callable]] = defaultdict(list)
        self._wildcard_handlers: list[Callable] = []

    def subscribe(self, event_type: EventType | str, handler: Callable) -> None:
        """Register a handler for an event type, or WILDCARD for all events."""
        if event_type == self.WILDCARD:
            if handler not in self._wildcard_handlers:
                self._wildcard_handlers.append(handler)
                logger.debug(f"Subscribed {handler.__qualname__} to WILDCARD")
        else:
            name = _event_name(event_type)
            if handler not in self._handlers[event_type]:
                self._handlers[event_type].append(handler)
                logger.debug(f"Subscribed {handler.__qualname__} to {name}")

    def unsubscribe(self, event_type: EventType | str, handler: Callable) -> None:
        """Remove a handler from an event type."""
        if event_type == self.WILDCARD:
            try:
                self._wildcard_handlers.remove(handler)
            except ValueError:
                pass
        else:
            try:
                self._handlers[event_type].remove(handler)
            except ValueError:
                pass

    def emit(self, event_type: EventType, payload: EventPayload | None = None) -> None:
        """
        Emit an event. Calls all registered handlers synchronously.
        Exceptions in handlers are caught and logged — they never crash the emitter.
        """
        name = _event_name(event_type)
        payload = payload or {}
        for handler in list(self._handlers[event_type]):
            try:
                handler(payload)
            except Exception as exc:
                logger.error(
                    f"Handler {handler.__qualname__} raised on {name}: {exc}",
                    exc_info=True,
                )
        envelope = {"event": name, "payload": payload}
        for handler in list(self._wildcard_handlers):
            try:
                handler(envelope)
            except Exception as exc:
                logger.error(
                    f"Wildcard handler {handler.__qualname__} raised on {name}: {exc}",
                    exc_info=True,
                )


# Global singleton — import this everywhere instead of instantiating locally
_bus_instance: EventBus | None = None


def get_event_bus() -> EventBus:
    """Return the process-wide EventBus singleton."""
    global _bus_instance
    if _bus_instance is None:
        _bus_instance = EventBus()
    return _bus_instance


def reset_event_bus() -> None:
    """Discard the singleton (tests only)."""
    global _bus_instance
    _bus_instance = None


bus = get_event_bus()
