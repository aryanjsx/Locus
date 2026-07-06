"""Tests for :mod:`aura.core.event_bus`."""
from __future__ import annotations

import threading

import pytest

from aura.core.event_bus import EventBus, EventType


def test_multiple_subscribers_fire_in_registration_order() -> None:
    bus = EventBus()
    seen: list[str] = []
    bus.subscribe(EventType.MODE_CHANGED, lambda e: seen.append("a"))
    bus.subscribe(EventType.MODE_CHANGED, lambda e: seen.append("b"))
    bus.subscribe(EventType.MODE_CHANGED, lambda e: seen.append("c"))
    bus.emit(EventType.MODE_CHANGED, {"n": 1})
    assert seen == ["a", "b", "c"]


def test_wildcard_receives_every_event() -> None:
    bus = EventBus()
    received: list[str] = []
    bus.subscribe(EventBus.WILDCARD, lambda e: received.append(e["event"]))
    bus.emit(EventType.WAKE_WORD_DETECTED, {})
    bus.emit(EventType.MODE_CHANGED, {"k": "v"})
    bus.emit(EventType.SYSTEM_ERROR, None)
    assert received == ["WAKE_WORD_DETECTED", "MODE_CHANGED", "SYSTEM_ERROR"]


def test_emit_with_zero_subscribers_does_not_raise() -> None:
    bus = EventBus()
    bus.emit(EventType.PIPELINE_STATE_CHANGED, {"x": 1})


def test_subscriber_exception_does_not_block_others() -> None:
    bus = EventBus()
    good: list[dict] = []

    def boom(_e: dict) -> None:
        raise RuntimeError("boom")

    bus.subscribe(EventType.COMMAND_RECEIVED, boom)
    bus.subscribe(EventType.COMMAND_RECEIVED, lambda e: good.append(e))

    bus.emit(EventType.COMMAND_RECEIVED, {"k": "v"})

    assert good, "good subscriber must still fire after bad one raises"


def test_nested_emit_does_not_deadlock() -> None:
    bus = EventBus()
    depth = {"current": 0, "max": 0}

    def handler(payload: dict) -> None:
        depth["current"] += 1
        depth["max"] = max(depth["max"], depth["current"])
        if depth["current"] < 4:
            bus.emit(EventType.SYSTEM_ERROR, {"depth": depth["current"]})
        depth["current"] -= 1

    bus.subscribe(EventType.SYSTEM_ERROR, handler)
    bus.emit(EventType.SYSTEM_ERROR, {"depth": 0})
    assert depth["max"] == 4


def test_recursive_subscriber_error_does_not_infinite_loop() -> None:
    bus = EventBus()

    def always_raises(_e: dict) -> None:
        raise ValueError("self-recursive error")

    bus.subscribe(EventType.COMMAND_ERROR, always_raises)
    bus.emit(EventType.COMMAND_ERROR, {})


def test_thread_safety_under_concurrent_emit() -> None:
    bus = EventBus()
    counter = {"n": 0}
    lock = threading.Lock()

    def sub(_e: dict) -> None:
        with lock:
            counter["n"] += 1

    bus.subscribe(EventType.COMMAND_COMPLETED, sub)

    def emitter() -> None:
        for _ in range(500):
            bus.emit(EventType.COMMAND_COMPLETED, {"x": 1})

    threads = [threading.Thread(target=emitter) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert counter["n"] == 8 * 500


def test_unsubscribe_removes_only_that_handler() -> None:
    bus = EventBus()
    a: list[int] = []
    b: list[int] = []
    handler_a = lambda _: a.append(1)
    handler_b = lambda _: b.append(1)
    bus.subscribe(EventType.EXECUTION_COMPLETE, handler_a)
    bus.subscribe(EventType.EXECUTION_COMPLETE, handler_b)

    bus.unsubscribe(EventType.EXECUTION_COMPLETE, handler_a)
    bus.emit(EventType.EXECUTION_COMPLETE, {})
    assert a == []
    assert b == [1]


def test_invalid_event_type_string_rejected() -> None:
    bus = EventBus()
    with pytest.raises(TypeError, match="raw string"):
        bus.subscribe("some.string", lambda _: None)
    with pytest.raises(TypeError, match="raw string"):
        bus.emit("some.string", {})
