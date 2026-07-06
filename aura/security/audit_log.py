"""
AURA — Audit Log (tamper-evident, rotating).

A *separate* JSON-line log dedicated to security-relevant events:

- confirmation lifecycle (requested / accepted / denied / timed out)
- permission denials
- rate-limit rejections
- policy violations (shell denylist, sandbox escape, …)
- every destructive command execution and its outcome
- full command lifecycle (executing / completed)
- plan lifecycle (started / completed / rollback / failed) including
  the full structure of the plan at ``plan.started``
- worker lifecycle (ready / crashed / shutdown)

Tamper evidence
---------------
Every record includes two fields:

* ``prev_hash`` — SHA-256 of the previous record's ``hash`` field.
* ``hash``     — SHA-256 of ``prev_hash`` concatenated with the canonical
  JSON of the record body (all fields except ``hash`` itself).

On startup we load the last record's ``hash`` so the chain continues
across process restarts.  ANY modification of ANY line invalidates every
subsequent ``hash`` — detectable by a single forward pass.

Rotation
--------
Uses :class:`logging.handlers.RotatingFileHandler` so long-running
systems never grow the file without bound.  The hash chain is kept in
memory so it survives rotation transparently.

Verification
------------
See :func:`verify_chain` — given a path, returns ``(ok, first_bad_line)``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import logging.handlers
import threading
import time
from pathlib import Path
from typing import Any

from aura.security.audit_events import AuditEventRegistry, get_audit_event_registry
from aura.core.config_loader import get as get_config
from aura.core.event_bus import EventBus

_GENESIS_HASH = "0" * 64

# Sidecar filename containing the last hash of any rotation segment
# that was evicted (purged past ``backupCount``).  The chain verifier
# uses this hash as the starting ``prev_hash`` for the oldest surviving
# segment; without it, a legitimate rotation that drops the genesis
# segment would be indistinguishable from an attacker truncating the
# log.
_HASH_SIDECAR_SUFFIX = ".chain"


def _sidecar_for(base_path: Path) -> Path:
    """Return the sidecar path for a given live audit log path."""
    return base_path.with_name(base_path.name + _HASH_SIDECAR_SUFFIX)


def _read_last_hash_of_file(path: Path) -> str | None:
    """Return the ``hash`` field of the final JSON record in *path*,
    or ``None`` if the file is missing / empty / unparseable."""
    if not path.exists():
        return None
    try:
        last: str | None = None
        with path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except Exception:
                    continue
                h = record.get("hash")
                if isinstance(h, str) and len(h) == 64:
                    last = h
        return last
    except Exception:
        return None


def _read_sidecar(path: Path) -> str | None:
    """Return the purged-segment hash stored in the sidecar, or
    ``None`` if the sidecar is absent / malformed."""
    sidecar = _sidecar_for(path)
    if not sidecar.exists():
        return None
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except Exception:
        return None
    h = data.get("purged_last_hash") if isinstance(data, dict) else None
    if isinstance(h, str) and len(h) == 64:
        return h
    return None


def _write_sidecar(path: Path, purged_hash: str) -> None:
    """Persist the last hash of a segment about to be evicted."""
    sidecar = _sidecar_for(path)
    try:
        sidecar.write_text(
            json.dumps(
                {
                    "purged_last_hash": purged_hash,
                    "updated_at": time.strftime(
                        "%Y-%m-%dT%H:%M:%S", time.localtime()
                    ),
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    except Exception:
        # Sidecar write must never break audit logging itself.
        pass


class _AuraRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """``RotatingFileHandler`` that refuses to rotate an empty file.

    Back-ports the Python 3.12 behaviour of `gh-116263`_ to earlier
    interpreters (the CI matrix still includes 3.11).  Without this
    guard, the *very first* audit record — which carries a 64-char
    ``prev_hash`` of all zeros plus a 64-char ``hash`` — can exceed the
    configured ``maxBytes`` all on its own.  3.11's stock handler would
    then rotate the still-empty ``audit.log`` to ``audit.log.1`` before
    writing, propagating empty segments up the rotation stack on every
    subsequent emit and polluting the tamper-evident chain with blank
    files that break cross-segment verification.

    On rollover, the handler also persists the last-hash of any
    *evicted* segment (``audit.log.<backupCount>``) into the sidecar
    described above, so the tamper-evident chain survives truncation
    without losing the ability to distinguish legit rotation from
    tampering.

    .. _gh-116263: https://github.com/python/cpython/pull/116263
    """

    def shouldRollover(self, record: logging.LogRecord) -> int:  # noqa: N802
        if self.stream is None:
            self.stream = self._open()
        if self.maxBytes > 0:
            self.stream.seek(0, 2)
            pos = self.stream.tell()
            if not pos:
                return 0
            msg = "%s\n" % self.format(record)
            if pos + len(msg) >= self.maxBytes:
                return 1
        return 0

    def doRollover(self) -> None:  # noqa: N802
        """Overrides parent rollover to snapshot the evicted segment's
        last hash into the sidecar BEFORE the rename chain discards it.

        The stdlib behaviour: ``audit.log.<backupCount>`` (if present)
        is removed during the rename shuffle.  We read that file's
        last JSON record, extract its ``hash``, and persist it to the
        sidecar so :func:`verify_chain_dir` can pick up where the
        truncation starts.
        """
        try:
            base = Path(self.baseFilename)
            evicted = base.with_name(
                f"{base.name}.{self.backupCount}"
            )
            if self.backupCount > 0 and evicted.exists():
                purged = _read_last_hash_of_file(evicted)
                if purged:
                    _write_sidecar(base, purged)
        except Exception:
            # Never let sidecar bookkeeping abort rotation.
            pass
        super().doRollover()


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _last_hash_in(path: Path) -> str:
    """Scan *path* backwards to find the most recent record's ``hash``.
    Returns the genesis hash if the file is missing / empty / unreadable."""
    if not path.exists():
        return _GENESIS_HASH
    try:
        with path.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            chunk = 4096
            buffer = b""
            while size > 0:
                read_size = min(chunk, size)
                size -= read_size
                fh.seek(size)
                buffer = fh.read(read_size) + buffer
                if b"\n" in buffer.strip(b"\n"):
                    break
            for line in reversed(buffer.splitlines()):
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except Exception:
                    continue
                h = data.get("hash")
                if isinstance(h, str) and len(h) == 64:
                    return h
    except Exception:
        pass
    return _GENESIS_HASH


class _ChainedAuditFormatter(logging.Formatter):
    """Formatter that emits JSON lines with a SHA-256 hash chain."""

    def __init__(self, initial_hash: str) -> None:
        super().__init__()
        self._prev_hash = initial_hash or _GENESIS_HASH
        self._lock = threading.Lock()

    @property
    def current_hash(self) -> str:
        with self._lock:
            return self._prev_hash

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        # ``RotatingFileHandler.shouldRollover`` calls ``format()`` once
        # just to measure size, then ``emit()`` calls it again to write.
        # We must NOT advance the hash chain twice per record — so we
        # memoise the output on the record itself.
        cached = getattr(record, "_aura_audit_formatted", None)
        if cached is not None:
            return cached

        body: dict[str, Any] = {
            "timestamp": time.strftime(
                "%Y-%m-%dT%H:%M:%S", time.localtime(record.created)
            ) + f".{int(record.msecs):03d}",
            "event": record.__dict__.get("event", record.getMessage()),
        }
        payload = record.__dict__.get("data")
        if isinstance(payload, dict):
            body["payload"] = payload
        else:
            body["payload"] = {}

        with self._lock:
            body["prev_hash"] = self._prev_hash
            canonical = _canonical(body)
            new_hash = _sha256(self._prev_hash + canonical)
            body["hash"] = new_hash
            self._prev_hash = new_hash

        try:
            output = json.dumps(body, ensure_ascii=False, default=str)
        except Exception:
            output = json.dumps(
                {"event": body.get("event"), "error": "serialize_failed",
                 "prev_hash": body.get("prev_hash"), "hash": body.get("hash")}
            )
        record._aura_audit_formatted = output  # type: ignore[attr-defined]
        return output


class AuditLogger:
    """Subscribes to security events on the bus and writes them to disk."""

    def __init__(
        self,
        bus: EventBus,
        path: str | Path | None = None,
        *,
        max_bytes: int | None = None,
        backup_count: int | None = None,
        event_registry: AuditEventRegistry | None = None,
    ) -> None:
        self._bus = bus
        self._event_registry = event_registry or get_audit_event_registry()
        target = Path(path) if path else Path(
            get_config("audit.file", "logs/audit.log")
        ).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)

        self._max_bytes = int(
            max_bytes if max_bytes is not None
            else get_config("audit.max_bytes", 5 * 1024 * 1024)
        )
        self._backup_count = int(
            backup_count if backup_count is not None
            else get_config("audit.backup_count", 5)
        )

        self._logger = logging.getLogger("aura.audit")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False

        # Remove any stale audit handler from a prior instance (tests,
        # re-bootstraps, etc.) so we always write to *this* path and
        # continue *this* chain.
        for old in list(self._logger.handlers):
            if getattr(old, "_aura_audit", False):
                try:
                    old.close()
                except Exception:
                    pass
                self._logger.removeHandler(old)

        initial_hash = _last_hash_in(target)
        handler = _AuraRotatingFileHandler(
            target,
            maxBytes=self._max_bytes,
            backupCount=self._backup_count,
            encoding="utf-8",
        )
        self._formatter = _ChainedAuditFormatter(initial_hash)
        handler.setFormatter(self._formatter)
        handler._aura_audit = True  # type: ignore[attr-defined]
        self._logger.addHandler(handler)

        self._subscribed = False
        self._path = target

    @property
    def path(self) -> Path:
        return self._path

    def current_hash(self) -> str | None:
        fm = self._formatter
        return fm.current_hash if isinstance(fm, _ChainedAuditFormatter) else None

    def subscribe(self) -> None:
        """Subscribe to the bus WILDCARD to log all events.

        Safe to call multiple times — idempotent.
        """
        if getattr(self, "_subscribed", False):
            return
        self._bus.subscribe(EventBus.WILDCARD, self._handle)
        self._subscribed = True

    def unsubscribe(self) -> None:
        if getattr(self, "_subscribed", False):
            self._bus.unsubscribe(EventBus.WILDCARD, self._handle)
            self._subscribed = False

    def _handle(self, envelope: dict[str, Any]) -> None:
        event_name = envelope.get("event") or "audit"
        self._logger.info(
            event_name,
            extra={"event": event_name, "data": envelope.get("payload", {})},
        )


# ---------------------------------------------------------------------------
# Stand-alone chain verifier
# ---------------------------------------------------------------------------
def _verify_stream(
    lines: Any, starting_prev: str
) -> tuple[bool, int | None, str]:
    """Walk *lines* (iterable of raw lines), return ``(ok, bad_idx, last_hash)``."""
    prev = starting_prev
    for idx, raw in enumerate(lines, start=1):
        line = raw.rstrip("\n")
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            return False, idx, prev
        stored_hash = record.get("hash")
        prev_hash = record.get("prev_hash")
        if stored_hash is None or prev_hash is None:
            return False, idx, prev
        if prev_hash != prev:
            return False, idx, prev
        body = {k: v for k, v in record.items() if k != "hash"}
        canonical = _canonical(body)
        expected = _sha256(prev + canonical)
        if expected != stored_hash:
            return False, idx, prev
        prev = stored_hash
    return True, None, prev


def verify_chain(path: str | Path) -> tuple[bool, int | None]:
    """Verify the hash chain of a single audit-log file.

    Returns
    -------
    (ok, first_bad_line) :
        * ``(True, None)``  — every record verifies.
        * ``(False, N)``    — line *N* (1-indexed) failed verification.
    """
    p = Path(path)
    if not p.exists():
        return True, None  # empty chain trivially consistent

    with p.open("r", encoding="utf-8") as fh:
        ok, bad, _ = _verify_stream(fh, _GENESIS_HASH)
    return ok, bad


def verify_chain_dir(
    base_path: str | Path,
) -> tuple[bool, str | None, int | None]:
    """Verify the full hash chain **across** rotated log files.

    Simple boolean-oriented wrapper around
    :func:`verify_chain_dir_detailed`.  See that function for status
    semantics.  Legitimate rotation (``TRUNCATED`` without tampering)
    is reported as ``True``; any hash mismatch or schema corruption is
    reported as ``False`` with the offending filename and 1-indexed
    line number.
    """
    status, filename, bad_line = verify_chain_dir_detailed(base_path)
    if status == "TAMPERED":
        return False, filename, bad_line
    # "OK" and "TRUNCATED" are both *valid* chain states.
    return True, None, None


def verify_chain_dir_detailed(
    base_path: str | Path,
) -> tuple[str, str | None, int | None]:
    """Verify the full hash chain across rotated segments, with
    three-state status output.

    ``RotatingFileHandler`` names rotated segments ``base.N`` where
    ``N`` increases with *age*.  The live file is ``base``; the chain
    therefore runs oldest→newest:

        base.N, base.N-1, …, base.2, base.1, base

    Returns
    -------
    (status, filename, bad_line) :
        * ``("OK", None, None)``
            every segment verifies, and the oldest segment starts
            from the genesis hash (or from the sidecar-recorded
            purged hash, if the live log has been rotated past
            ``backupCount`` times).
        * ``("TRUNCATED", name, 1)``
            the sidecar is missing / stale and the oldest surviving
            segment's first record ``prev_hash`` is non-genesis - the
            prefix is gone but the surviving chain is internally
            consistent.  This is *not* tampering.
        * ``("TAMPERED", name, line)``
            a record's ``hash`` / ``prev_hash`` failed verification.
            This IS tampering.
    """
    base = Path(base_path)
    parent = base.parent

    segments: list[tuple[int, Path]] = []
    prefix = base.name + "."
    if parent.exists():
        for entry in parent.iterdir():
            if not entry.is_file():
                continue
            if entry.name == base.name:
                continue
            if entry.name.endswith(_HASH_SIDECAR_SUFFIX):
                # Ignore our own sidecar during segment enumeration.
                continue
            if not entry.name.startswith(prefix):
                continue
            suffix = entry.name[len(prefix):]
            if not suffix.isdigit():
                continue
            segments.append((int(suffix), entry))

    segments.sort(key=lambda pair: pair[0], reverse=True)
    ordered_paths: list[Path] = [p for _, p in segments]
    if base.exists():
        ordered_paths.append(base)

    # Pick the starting prev_hash: sidecar first, genesis otherwise.
    sidecar_prev = _read_sidecar(base)
    prev = sidecar_prev or _GENESIS_HASH
    starting_from_sidecar = sidecar_prev is not None

    # Sub-case: no segments at all and no live file.
    if not ordered_paths:
        return "OK", None, None

    # Walk each segment.  The FIRST segment is special: if its first
    # record's prev_hash doesn't match our starting prev, we must
    # distinguish "truncated history" (valid chain, missing prefix)
    # from "tampered" (body doesn't match stored hash).
    for idx, path in enumerate(ordered_paths):
        first_segment = (idx == 0)
        with path.open("r", encoding="utf-8") as fh:
            raw_lines = [line.rstrip("\n") for line in fh]
        non_empty = [(i + 1, ln) for i, ln in enumerate(raw_lines) if ln.strip()]

        if first_segment and non_empty and not starting_from_sidecar:
            # Peek the first record: if its prev_hash is a valid
            # 64-hex string but != genesis, we are looking at a
            # truncated head.  Check the intra-record hash is still
            # valid before labelling it TRUNCATED vs TAMPERED.
            line_no, first_line = non_empty[0]
            try:
                first_rec = json.loads(first_line)
            except json.JSONDecodeError:
                return "TAMPERED", path.name, line_no
            ph = first_rec.get("prev_hash")
            stored = first_rec.get("hash")
            if (
                isinstance(ph, str) and len(ph) == 64
                and ph != _GENESIS_HASH
                and isinstance(stored, str) and len(stored) == 64
            ):
                body = {k: v for k, v in first_rec.items() if k != "hash"}
                canonical = _canonical(body)
                expected = _sha256(ph + canonical)
                if expected == stored:
                    # Chain is self-consistent from this record onward
                    # even though the prefix is missing.  Seed `prev`
                    # with ph and continue verifying; a later tamper
                    # still FAILS normally.
                    prev = ph
                    # Fall through into the normal _verify_stream for
                    # the full file, seeded with this prev.  The
                    # prev==ph seeding above means the first record
                    # will re-verify and match.
                    # Mark this status so later segments that match
                    # cleanly preserve TRUNCATED as the final state.
                    ok, bad, prev = _verify_stream(iter(raw_lines), prev)
                    if not ok:
                        return "TAMPERED", path.name, bad
                    # Remaining segments must chain normally.
                    for tail in ordered_paths[idx + 1:]:
                        with tail.open("r", encoding="utf-8") as tfh:
                            ok, bad, prev = _verify_stream(tfh, prev)
                        if not ok:
                            return "TAMPERED", tail.name, bad
                    return "TRUNCATED", path.name, line_no

        # Normal verification path.
        ok, bad, prev = _verify_stream(iter(raw_lines), prev)
        if not ok:
            return "TAMPERED", path.name, bad

    return "OK", None, None
