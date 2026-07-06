"""
Phase-2 hardening: :func:`verify_chain_dir` walks the full tamper-
evident hash chain across ``audit.log.N → ... → audit.log``.

If any single record — in any rotated segment — is modified, the
verifier MUST report the exact file and line that breaks the chain.
"""
from __future__ import annotations

import json
from pathlib import Path

from aura.security.audit_log import AuditLogger, verify_chain, verify_chain_dir
from aura.core.event_bus import EventBus, EventType


def _write_many(bus: EventBus, count: int) -> None:
    for i in range(count):
        bus.emit(EventType.COMMAND_EXECUTING, {"action": "probe", "i": i})


def test_verify_chain_dir_handles_no_rotation(tmp_path: Path):
    bus = EventBus()
    log_path = tmp_path / "audit.log"
    audit = AuditLogger(
        bus, path=log_path, max_bytes=10 * 1024 * 1024, backup_count=3
    )
    audit.subscribe()
    _write_many(bus, 5)

    ok, bad_file, bad_line = verify_chain_dir(log_path)
    assert ok, (bad_file, bad_line)
    assert bad_file is None
    assert bad_line is None
    # Single-file helper also agrees.
    assert verify_chain(log_path) == (True, None)


def test_verify_chain_dir_walks_rotated_files(tmp_path: Path):
    bus = EventBus()
    log_path = tmp_path / "audit.log"
    # Tiny max_bytes forces rotation; backup_count is generous enough
    # that no segment is evicted (eviction is normal operation but
    # breaks strict-from-genesis verification by design).
    audit = AuditLogger(bus, path=log_path, max_bytes=256, backup_count=100)
    audit.subscribe()

    _write_many(bus, 40)

    # We should have rotated segments alongside the live file.
    rotated = sorted(
        p.name for p in tmp_path.iterdir() if p.name.startswith("audit.log.")
    )
    assert rotated, "expected at least one rotated segment"

    ok, bad_file, bad_line = verify_chain_dir(log_path)
    assert ok, (bad_file, bad_line)


def test_verify_chain_dir_detects_tamper_in_rotated_segment(tmp_path: Path):
    bus = EventBus()
    log_path = tmp_path / "audit.log"
    audit = AuditLogger(bus, path=log_path, max_bytes=256, backup_count=100)
    audit.subscribe()
    _write_many(bus, 40)

    # Pick the *oldest* rotated file (highest suffix number) and
    # tamper with it — this is the first file the verifier visits, so
    # the break must be reported against it specifically.
    rotated = sorted(
        (p for p in tmp_path.iterdir() if p.name.startswith("audit.log.")),
        key=lambda p: int(p.name.rsplit(".", 1)[1]),
        reverse=True,
    )
    victim = rotated[0]
    lines = victim.read_text(encoding="utf-8").splitlines()
    lines = [ln for ln in lines if ln.strip()]
    assert lines, f"rotated file {victim.name} is empty — rotation guard failed"
    # Tamper line 1's payload — hash will no longer match.
    first = json.loads(lines[0])
    first["payload"]["action"] = "HACKED"
    # Keep the original hash untouched so the mismatch is detected.
    lines[0] = json.dumps(first, ensure_ascii=False)
    victim.write_text("\n".join(lines) + "\n", encoding="utf-8")

    ok, bad_file, bad_line = verify_chain_dir(log_path)
    assert not ok
    assert bad_file == victim.name
    assert bad_line == 1


def test_verify_chain_dir_detects_gap_between_segments(tmp_path: Path):
    bus = EventBus()
    log_path = tmp_path / "audit.log"
    audit = AuditLogger(bus, path=log_path, max_bytes=256, backup_count=100)
    audit.subscribe()
    _write_many(bus, 30)

    # Remove the last record of the oldest rotated file (largest N) so
    # the first record of the next-older segment no longer links to a
    # valid prev_hash.
    rotated = sorted(
        (p for p in tmp_path.iterdir() if p.name.startswith("audit.log.")),
        key=lambda p: int(p.name.rsplit(".", 1)[1]),
        reverse=True,
    )
    oldest = rotated[0]
    lines = oldest.read_text(encoding="utf-8").splitlines()
    if len(lines) >= 2:
        oldest.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
        ok, bad_file, _ = verify_chain_dir(log_path)
        assert not ok
        # The break is detected in a *later* segment or the live log —
        # whichever first sees prev_hash != expected.
        assert bad_file is not None


def test_verify_chain_dir_ignores_foreign_files(tmp_path: Path):
    """An attacker dropping extra files next to the log must not DoS verify."""
    bus = EventBus()
    log_path = tmp_path / "audit.log"
    audit = AuditLogger(bus, path=log_path, max_bytes=10 * 1024 * 1024)
    audit.subscribe()
    _write_many(bus, 3)

    (tmp_path / "audit.log.backup").write_text("garbage", encoding="utf-8")
    (tmp_path / "unrelated.txt").write_text("noise", encoding="utf-8")

    ok, bad_file, _ = verify_chain_dir(log_path)
    assert ok, bad_file
