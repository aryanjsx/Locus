"""SafetyGate tests — acceptance tokens, rejection, and timeout."""
from __future__ import annotations

import time

import pytest

from aura.core.errors import ConfirmationDenied, ConfirmationTimeout
from aura.core.event_bus import EventBus, EventType
from aura.security.safety_gate import SafetyGate


def _gate(responses: list[str], timeout: float = 1.0) -> tuple[SafetyGate, EventBus]:
    bus = EventBus()
    idx = {"i": 0}
    # The gate enforces a 1-second minimum timeout; use a reader sleep that
    # exceeds whatever effective timeout is applied.
    reader_sleep = max(timeout, 1.0) * 4

    def fake_input(_prompt: str) -> str:
        i = idx["i"]
        idx["i"] += 1
        if i >= len(responses):
            time.sleep(reader_sleep)
            return ""
        return responses[i]

    return SafetyGate(
        bus,
        input_fn=fake_input,
        output_fn=lambda _m: None,
        timeout=timeout,
    ), bus


@pytest.mark.parametrize("token", ["yes", "Yes", "YES", "confirm", "CONFIRM", "proceed", "Proceed "])
def test_accepted_tokens_allow_execution(token: str):
    gate, _ = _gate([token])
    gate.request(action="test", params={}, source="cli", permission="HIGH")


@pytest.mark.parametrize("token", ["no", "n", "nope", "ok", ""])
def test_rejected_tokens_raise_confirmation_denied(token: str):
    gate, _ = _gate([token])
    with pytest.raises(ConfirmationDenied):
        gate.request(action="test", params={}, source="cli", permission="HIGH")


def test_timeout_cancels_execution():
    gate, _ = _gate([], timeout=1.0)
    t0 = time.monotonic()
    with pytest.raises(ConfirmationTimeout):
        gate.request(action="test", params={}, source="cli", permission="HIGH")
    elapsed = time.monotonic() - t0
    assert 0.9 <= elapsed < 3.0


def test_events_emitted_in_bus():
    events: list[str] = []
    gate, bus = _gate(["yes"])
    bus.subscribe(EventBus.WILDCARD, lambda env: events.append(env["event"]))
    gate.request(action="dangerous", params={}, source="cli", permission="HIGH")
    assert "SAFETY_CONFIRMATION_REQ" in events
    assert "SAFETY_CONFIRMED" in events
