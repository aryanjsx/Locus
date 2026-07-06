"""Unit tests for the tamper-evident audit log (hash chain + rotation)."""
from __future__ import annotations

import json
from pathlib import Path

from aura.security.audit_log import AuditLogger, verify_chain
from aura.core.event_bus import EventBus, EventType


def _bus() -> EventBus:
    return EventBus()


def test_chain_verifies_for_unmodified_log(tmp_path: Path) -> None:
    bus = _bus()
    audit = AuditLogger(bus, path=tmp_path / "audit.log")
    audit.subscribe()

    for i in range(5):
        bus.emit(EventType.COMMAND_DESTRUCTIVE,
                 {"action": f"file.delete.{i}", "trace_id": f"t{i}"})

    ok, bad = verify_chain(audit.path)
    assert ok is True
    assert bad is None


def test_chain_breaks_on_tampering(tmp_path: Path) -> None:
    bus = _bus()
    audit = AuditLogger(bus, path=tmp_path / "audit.log")
    audit.subscribe()

    for i in range(3):
        bus.emit(EventType.POLICY_BLOCKED,
                 {"action": "shell", "reason": f"reason-{i}", "trace_id": f"t{i}"})

    path = audit.path
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 3

    middle = json.loads(lines[1])
    middle["payload"]["reason"] = "ALTERED-BY-ATTACKER"
    lines[1] = json.dumps(middle, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    ok, bad = verify_chain(path)
    assert ok is False
    assert bad == 2


def test_chain_continues_across_logger_restarts(tmp_path: Path) -> None:
    log = tmp_path / "audit.log"

    bus1 = _bus()
    a1 = AuditLogger(bus1, path=log)
    a1.subscribe()
    bus1.emit(EventType.COMMAND_DESTRUCTIVE, {"action": "run1", "trace_id": "x"})

    bus2 = _bus()
    a2 = AuditLogger(bus2, path=log)
    a2.subscribe()
    bus2.emit(EventType.COMMAND_DESTRUCTIVE, {"action": "run2", "trace_id": "y"})

    ok, bad = verify_chain(log)
    assert ok is True, f"chain broke at line {bad}"
