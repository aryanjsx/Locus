"""
Phase-3 hardening: ``verify_chain_dir`` distinguishes LEGIT rotation
(segment purged past ``backupCount``) from actual tampering.

Design
------
* On rollover, the handler records the last hash of the file about to
  be evicted into a sidecar (``audit.log.chain``).
* On verification, the oldest surviving segment's first record must
  chain from that sidecar hash.  If the sidecar is absent AND the
  first record's prev_hash is a non-genesis 64-hex that verifies
  internally, the verifier returns ``TRUNCATED`` (treated as valid by
  the boolean wrapper).
* ANY hash mismatch / corruption remains ``TAMPERED``.

The old tests still live in ``test_audit_chain_rotation.py`` (no
sidecar path because ``backup_count`` is generous there - no eviction
occurs); this file covers the eviction / truncation / sidecar
scenarios explicitly.
"""
from __future__ import annotations

import json
from pathlib import Path


from aura.core.event_bus import EventBus, EventType
from aura.security.audit_log import (
    AuditLogger,
    _read_sidecar,
    verify_chain,
    verify_chain_dir,
    verify_chain_dir_detailed,
)


def _write_many(bus: EventBus, count: int) -> None:
    for i in range(count):
        bus.emit(EventType.COMMAND_EXECUTING, {"action": "probe", "i": i})


def _rotated_files(tmp_path: Path) -> list[Path]:
    return sorted(
        (p for p in tmp_path.iterdir() if p.name.startswith("audit.log.")
         and p.suffix != ".chain"
         and p.name != "audit.log"),
        key=lambda p: int(p.name.rsplit(".", 1)[1]),
        reverse=True,
    )


# ---------------------------------------------------------------------
# Sidecar is written when rotation evicts the oldest segment.
# ---------------------------------------------------------------------
def test_sidecar_is_written_on_eviction(tmp_path: Path):
    bus = EventBus()
    log_path = tmp_path / "audit.log"
    # Tiny max_bytes + tiny backup_count forces eviction FAST.
    audit = AuditLogger(
        bus, path=log_path, max_bytes=256, backup_count=2,
    )
    audit.subscribe()

    # Generate enough rotations to force at least one eviction
    # (the stdlib handler keeps <= backup_count backups).
    _write_many(bus, 80)

    sidecar = log_path.with_name(log_path.name + ".chain")
    assert sidecar.exists(), "Sidecar must be written once rotation evicts a segment"
    blob = json.loads(sidecar.read_text(encoding="utf-8"))
    assert isinstance(blob, dict)
    assert isinstance(blob.get("purged_last_hash"), str)
    assert len(blob["purged_last_hash"]) == 64


# ---------------------------------------------------------------------
# With sidecar present, verification reports OK even after eviction.
# ---------------------------------------------------------------------
def test_rotation_with_eviction_verifies_ok_via_sidecar(tmp_path: Path):
    bus = EventBus()
    log_path = tmp_path / "audit.log"
    audit = AuditLogger(
        bus, path=log_path, max_bytes=256, backup_count=2,
    )
    audit.subscribe()
    _write_many(bus, 80)

    assert _read_sidecar(log_path) is not None, "precondition: sidecar present"

    status, fname, bad = verify_chain_dir_detailed(log_path)
    assert status == "OK", (status, fname, bad)
    ok, fname, bad = verify_chain_dir(log_path)
    assert ok, (fname, bad)


# ---------------------------------------------------------------------
# When the sidecar is deleted (e.g. box rebuilt), verifier falls back
# to the TRUNCATED status: valid chain, missing prefix.  The boolean
# wrapper treats that as valid (no tampering).
# ---------------------------------------------------------------------
def test_missing_sidecar_yields_truncated_not_tampered(tmp_path: Path):
    bus = EventBus()
    log_path = tmp_path / "audit.log"
    audit = AuditLogger(
        bus, path=log_path, max_bytes=256, backup_count=2,
    )
    audit.subscribe()
    _write_many(bus, 80)

    sidecar = log_path.with_name(log_path.name + ".chain")
    assert sidecar.exists()
    sidecar.unlink()

    status, fname, bad = verify_chain_dir_detailed(log_path)
    assert status == "TRUNCATED", (status, fname, bad)
    assert fname is not None  # points at the oldest surviving segment

    # Boolean wrapper must NOT flag legit truncation as tampering.
    ok, fname2, bad2 = verify_chain_dir(log_path)
    assert ok, (fname2, bad2)


# ---------------------------------------------------------------------
# Tampering is still detected after eviction.
# ---------------------------------------------------------------------
def test_tampering_after_eviction_is_detected(tmp_path: Path):
    bus = EventBus()
    log_path = tmp_path / "audit.log"
    audit = AuditLogger(
        bus, path=log_path, max_bytes=256, backup_count=2,
    )
    audit.subscribe()
    _write_many(bus, 80)

    # Tamper a rotated segment (oldest surviving).
    rotated = _rotated_files(tmp_path)
    assert rotated
    victim = rotated[0]
    lines = victim.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["payload"]["action"] = "HACKED"
    lines[0] = json.dumps(first, ensure_ascii=False)
    victim.write_text("\n".join(lines) + "\n", encoding="utf-8")

    status, fname, bad = verify_chain_dir_detailed(log_path)
    assert status == "TAMPERED", (status, fname, bad)
    assert fname == victim.name
    assert bad == 1

    ok, fname2, bad2 = verify_chain_dir(log_path)
    assert not ok
    assert fname2 == victim.name


# ---------------------------------------------------------------------
# If the sidecar hash itself is stale (doesn't match the oldest
# surviving segment's first record prev_hash), the verifier MUST flag
# it as TAMPERED, not silently accept the bogus seed.  A stale sidecar
# is basically an attacker-controlled claim about the purged chain.
# ---------------------------------------------------------------------
def test_sidecar_mismatch_is_detected_as_tampered(tmp_path: Path):
    bus = EventBus()
    log_path = tmp_path / "audit.log"
    audit = AuditLogger(
        bus, path=log_path, max_bytes=256, backup_count=2,
    )
    audit.subscribe()
    _write_many(bus, 80)

    sidecar = log_path.with_name(log_path.name + ".chain")
    # Corrupt the sidecar with a plausible-looking but wrong hash.
    sidecar.write_text(
        json.dumps({"purged_last_hash": "a" * 64, "updated_at": "x"}),
        encoding="utf-8",
    )

    status, fname, bad = verify_chain_dir_detailed(log_path)
    assert status == "TAMPERED", (status, fname, bad)

    ok, _, _ = verify_chain_dir(log_path)
    assert not ok


# ---------------------------------------------------------------------
# Sidecar is never parsed AS a rotated segment (it has a different
# suffix pattern and must not DoS the walker).
# ---------------------------------------------------------------------
def test_sidecar_is_not_walked_as_a_segment(tmp_path: Path):
    bus = EventBus()
    log_path = tmp_path / "audit.log"
    audit = AuditLogger(
        bus, path=log_path, max_bytes=10 * 1024 * 1024,
    )
    audit.subscribe()
    _write_many(bus, 3)

    sidecar = log_path.with_name(log_path.name + ".chain")
    # Sidecar contains non-numeric suffix => walker MUST NOT parse it
    # as a rotated segment (otherwise it'd fail on JSON decode).
    sidecar.write_text("not json at all", encoding="utf-8")

    # The walker must not crash on the malformed sidecar; it should
    # simply fail to consult it and fall back to genesis.  With no
    # rotations the live log chain starts at genesis, so the chain
    # still verifies OK.
    ok, fname, bad = verify_chain_dir(log_path)
    assert ok, (fname, bad)


# ---------------------------------------------------------------------
# No-rotation path still works (regression guard).
# ---------------------------------------------------------------------
def test_no_rotation_genesis_path(tmp_path: Path):
    bus = EventBus()
    log_path = tmp_path / "audit.log"
    audit = AuditLogger(
        bus, path=log_path, max_bytes=10 * 1024 * 1024,
    )
    audit.subscribe()
    _write_many(bus, 5)
    assert verify_chain(log_path) == (True, None)
    ok, _, _ = verify_chain_dir(log_path)
    assert ok

    status, _, _ = verify_chain_dir_detailed(log_path)
    assert status == "OK"
