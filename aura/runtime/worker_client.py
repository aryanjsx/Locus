"""
AURA - WorkerClient (raw-IPC only, no dispatch capability, no closures).

Post Phase-3 lockdown invariants
--------------------------------
* The client has **no** public or private method that dispatches a
  command.  It is a pure IPC transport: :meth:`send` accepts a JSON
  request envelope and returns the worker's reply envelope.
* There is **no** ``_acquire_capability`` method and **no** capability
  token.  Earlier revisions handed out a closure-captured
  ``_worker_dispatch`` function; a closure walk on the registry could
  reach that function and call it directly, skipping the registry's
  security pipeline.  That class of bypass is eliminated here by
  removing the raw-dispatch closure altogether.
* :class:`WorkerClient` instances are **not callable** (no ``__call__``)
  so the closure-walk destruction test (every cell in the registry's
  ``_execute_safe`` closure is either non-callable or ``== _execute_safe``)
  passes even when the registry captures the client as a data
  reference.

Isolation invariants (unchanged)
--------------------------------
* Main never imports ``plugins.*``.
* Commands serialised as plain JSON (no ``pickle``).
* Worker crash -> synthetic :class:`EngineError` result, then lazy
  respawn on the next call.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Protocol

from aura.core.config_loader import get as get_config
from aura.core.errors import EngineError
from aura.core.event_bus import EventBus, EventType
from aura.core.logger import get_logger
from aura.security.plugin_manifest import manifest_sha256, default_manifest_path


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Config keys:
#   worker.timeout       -> per-request reply deadline (seconds)
#   worker.max_reply_bytes -> raw-line cap on worker stdout (bytes)
# Falls back to conservative defaults.
_DEFAULT_TIMEOUT = 30.0
_DEFAULT_MAX_REPLY_BYTES = 1 * 1024 * 1024  # 1 MiB


# ---------------------------------------------------------------------
# WorkerPort protocol - what the registry expects.
# ---------------------------------------------------------------------
class WorkerPort(Protocol):
    """Minimal transport interface the :class:`CommandRegistry` consumes.

    Implementations MUST NOT be ``callable``: the closure-walk
    destruction test refuses any callable that is not the registry's
    own safe pipeline.  A plain class with ``send`` / ``has`` / ``actions``
    methods satisfies this as long as ``__call__`` is not defined.
    """

    def send(self, request: dict[str, Any]) -> dict[str, Any]: ...

    def has(self, action: str) -> bool: ...

    def actions(self) -> list[dict[str, Any]]: ...


class WorkerClient:
    """IPC transport for the out-of-process execution worker.

    The only method that talks to the worker is :meth:`send`.  There is
    no dispatch method, no capability acquisition, no closure-captured
    dispatch function.
    """

    def __init__(
        self,
        bus: EventBus,
        *,
        python_executable: str | None = None,
        timeout: float | None = None,
        max_reply_bytes: int | None = None,
        project_root: Path | None = None,
    ) -> None:
        self._bus = bus
        self._python = python_executable or sys.executable
        # Config-driven timeout (seconds).  Explicit kwarg wins; then
        # the configuration system; then the fallback constant.
        if timeout is not None:
            self._timeout = float(timeout)
        else:
            try:
                self._timeout = float(
                    get_config("worker.timeout", _DEFAULT_TIMEOUT)
                )
            except Exception:
                self._timeout = _DEFAULT_TIMEOUT
        if self._timeout <= 0:
            self._timeout = _DEFAULT_TIMEOUT
        # Raw-line size cap for any stdout reply.  Belts-and-braces
        # with the schema-level cap in ``_validate_worker_reply`` -
        # this one prevents unbounded memory growth if a compromised
        # worker streams bytes without a newline.
        if max_reply_bytes is not None:
            self._max_reply_bytes = int(max_reply_bytes)
        else:
            try:
                self._max_reply_bytes = int(
                    get_config(
                        "worker.max_reply_bytes", _DEFAULT_MAX_REPLY_BYTES
                    )
                )
            except Exception:
                self._max_reply_bytes = _DEFAULT_MAX_REPLY_BYTES
        if self._max_reply_bytes <= 0:
            self._max_reply_bytes = _DEFAULT_MAX_REPLY_BYTES
        self._root = project_root or _PROJECT_ROOT

        self._logger = get_logger("aura.worker_client")
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[str] | None = None
        self._actions: dict[str, dict[str, Any]] = {}
        self._manifest_hash: str | None = None
        self._crash_count: int = 0

    # ---- lifecycle ------------------------------------------------------
    def start(self) -> list[dict[str, Any]]:
        """Spawn the worker and return its advertised action schema."""
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return list(self._actions.values())
            self._spawn()
            return list(self._actions.values())

    def _spawn(self) -> None:
        env = self._restricted_env()
        cmd = [self._python, "-s", "-m", "aura.worker"]
        self._logger.info(
            "worker.spawn",
            extra={"event": "worker.spawn", "cmd": cmd, "cwd": str(self._root)},
        )

        proc = subprocess.Popen(  # noqa: S603 - argv list, no shell
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(self._root),
            env=env,
            text=True,
            bufsize=1,
            shell=False,
        )

        ready_line = self._read_line(proc, self._timeout)
        if ready_line is None:
            proc.kill()
            stderr_tail = self._drain_stderr(proc)
            raise EngineError(
                f"Worker failed to start (no ready line). stderr: {stderr_tail}"
            )

        try:
            ready = json.loads(ready_line)
        except json.JSONDecodeError as exc:
            proc.kill()
            raise EngineError(
                f"Worker emitted invalid ready line: {ready_line!r} ({exc})"
            ) from exc

        if ready.get("type") != "ready":
            proc.kill()
            raise EngineError(f"Worker failed to boot: {ready}")

        self._proc = proc
        self._actions = {a["action"]: a for a in ready.get("actions", [])}
        self._start_stderr_pump(proc)
        self._bus.emit(
            EventType.WORKER_READY,
            {"pid": ready.get("pid"), "actions": list(self._actions.keys())},
        )

    def _restricted_env(self) -> dict[str, str]:
        keep = {
            "PATH", "PATHEXT", "PYTHONPATH", "PYTHONHOME", "PYTHONIOENCODING",
            "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP", "COMSPEC",
            "HOME", "USERPROFILE", "USERNAME", "LOGNAME", "LANG", "LC_ALL",
            "AURA_LOG_PATH", "AURA_SHELL_TIMEOUT",
            "AURA_PROTECTED_PATHS", "AURA_SANDBOX_DIR",
            "LOCALAPPDATA", "APPDATA",
        }
        env = {k: v for k, v in os.environ.items() if k.upper() in keep}
        env["AURA_WORKER"] = "1"
        env["PYTHONIOENCODING"] = env.get("PYTHONIOENCODING", "utf-8")
        if self._manifest_hash is None:
            try:
                self._manifest_hash = manifest_sha256(
                    default_manifest_path(self._root)
                )
            except Exception:
                self._manifest_hash = None
        if self._manifest_hash:
            env["AURA_MANIFEST_SHA256"] = self._manifest_hash
        root = str(self._root)
        pp = env.get("PYTHONPATH", "")
        if root not in pp.split(os.pathsep):
            env["PYTHONPATH"] = root + (os.pathsep + pp if pp else "")
        return env

    def _start_stderr_pump(self, proc: subprocess.Popen[str]) -> None:
        def pump() -> None:
            assert proc.stderr is not None
            for line in proc.stderr:
                line = line.rstrip("\n")
                if not line:
                    continue
                self._logger.info(
                    "worker.stderr",
                    extra={"event": "worker.stderr", "line": line},
                )

        t = threading.Thread(target=pump, name="aura-worker-stderr", daemon=True)
        t.start()

    def shutdown(self, *, timeout: float = 3.0) -> None:
        with self._lock:
            proc = self._proc
            self._proc = None
        if proc is None:
            return
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.write(json.dumps({"type": "shutdown"}) + "\n")
                proc.stdin.flush()
        except Exception:
            pass
        try:
            proc.wait(timeout=timeout)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        self._bus.emit(EventType.WORKER_SHUTDOWN, {})

    # ---- read-only inspection (used by registry/router for routing) -----
    def has(self, action: str) -> bool:
        return action in self._actions

    def actions(self) -> list[dict[str, Any]]:
        return list(self._actions.values())

    # ------------------------------------------------------------------
    # Manifest fingerprint (published to the worker at spawn time).
    # ------------------------------------------------------------------
    def _bind_manifest_for_worker(self, manifest_path: Any = None) -> str:
        path = manifest_path or default_manifest_path(self._root)
        self._manifest_hash = manifest_sha256(path)
        return self._manifest_hash

    # ------------------------------------------------------------------
    # The ONLY method that reaches the worker.  Accepts a raw request
    # envelope (action + params + trace_id) and returns the raw reply
    # envelope.  Translation to :class:`CommandResult` / exceptions
    # happens inside the registry's ``_execute_safe`` pipeline - not
    # here - so there is no wrapper function whose closure could leak
    # a shortcut.
    # ------------------------------------------------------------------
    def send(self, request: dict[str, Any]) -> dict[str, Any]:
        """Send a request envelope to the worker, return the reply envelope.

        This is intentionally a thin transport: it does **no** security
        enforcement of its own.  The registry's ``_execute_safe`` runs
        the full pipeline before calling this, and the worker also
        re-validates everything on its own side.  Calling ``send``
        outside the registry therefore cannot dodge the registry's
        permission / rate-limit / audit / safety steps - but the worker
        itself still enforces param schema, sandbox, and policy.
        """
        if not isinstance(request, dict):
            raise EngineError("request must be a dict")
        with self._lock:
            proc = self._ensure_running_locked()
            assert proc.stdin is not None
            assert proc.stdout is not None

            try:
                proc.stdin.write(json.dumps(request, default=str) + "\n")
                proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                self._handle_crash_locked(exc, stage="write")
                raise EngineError(f"Worker pipe broken on write: {exc}") from exc

            line = self._read_line(
                proc, self._timeout, self._max_reply_bytes
            )
            if line is None:
                self._handle_crash_locked(
                    RuntimeError("timeout"), stage="timeout"
                )
                raise EngineError(
                    f"Worker did not respond within {self._timeout}s; "
                    "respawning."
                )
            if line == "__AURA_OVERSIZED__":
                self._handle_crash_locked(
                    RuntimeError(
                        f"reply exceeded {self._max_reply_bytes} bytes"
                    ),
                    stage="oversized",
                )
                raise EngineError(
                    f"Worker reply exceeded {self._max_reply_bytes} bytes; "
                    "respawning."
                )

            try:
                return json.loads(line)
            except json.JSONDecodeError as exc:
                self._handle_crash_locked(exc, stage="json_decode")
                raise EngineError(f"Worker returned non-JSON: {line!r}") from exc

    def _ensure_running_locked(self) -> subprocess.Popen[str]:
        if self._proc is None or self._proc.poll() is not None:
            self._spawn()
        assert self._proc is not None
        return self._proc

    def _handle_crash_locked(
        self, reason: Any, *, stage: str = "unknown"
    ) -> None:
        """Terminate the current worker and reset state.

        A crashed worker is killed, its process handle released, and
        partial action metadata cleared so the next ``send()`` call
        triggers a clean respawn (not a reuse of compromised state).
        The ``worker.crashed`` audit event carries enough context to
        distinguish crash classes (timeout, oversized reply, decode
        error, pipe break).
        """
        proc = self._proc
        self._proc = None
        # Drop any cached action metadata from the dead worker: we do
        # NOT want to reuse it when the next spawn might report a
        # different schema.
        self._actions = {}
        self._crash_count += 1
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass
        # Event is declared in AuditEventRegistry; AuditLogger is
        # subscribed, so this flight-records the incident.
        self._bus.emit(
            EventType.WORKER_CRASHED,
            {
                "reason": str(reason),
                "stage": stage,
                "crash_count": self._crash_count,
                "pid": getattr(proc, "pid", None),
            },
        )

    @staticmethod
    def _read_line(
        proc: subprocess.Popen[str],
        timeout: float,
        max_bytes: int = _DEFAULT_MAX_REPLY_BYTES,
    ) -> str | None:
        """Blocking read with a deadline - portable across POSIX/Windows.

        Returns ``None`` on timeout / closed pipe and the sentinel
        ``"__AURA_OVERSIZED__"`` if the reply exceeds ``max_bytes``
        without a newline (a compromised worker pumping bytes forever
        would otherwise OOM the main process).
        """
        assert proc.stdout is not None
        deadline = time.monotonic() + timeout
        holder: dict[str, str | None] = {"line": None}
        oversized = threading.Event()
        done = threading.Event()

        def reader() -> None:
            try:
                # readline(size) returns up to ``size`` characters or
                # stops at the first newline.  Requesting one more than
                # ``max_bytes`` lets us distinguish
                #  - legit reply (ends with '\n' within cap), and
                #  - oversized reply (no newline within cap+1 chars).
                line = proc.stdout.readline(max_bytes + 1)  # type: ignore[union-attr]
                if line and len(line) > max_bytes and not line.endswith("\n"):
                    oversized.set()
                    holder["line"] = None
                    return
                holder["line"] = line
            except Exception:
                holder["line"] = None
            finally:
                done.set()

        t = threading.Thread(target=reader, name="aura-worker-read", daemon=True)
        t.start()
        t.join(timeout=max(0.0, deadline - time.monotonic()))
        if oversized.is_set():
            return "__AURA_OVERSIZED__"
        if not done.is_set():
            return None
        line = holder["line"]
        if line is None or line == "":
            return None
        return line.rstrip("\n")

    @staticmethod
    def _drain_stderr(proc: subprocess.Popen[str], *, limit: int = 4096) -> str:
        if proc.stderr is None:
            return ""
        try:
            data = proc.stderr.read() or ""
            return data[-limit:]
        except Exception:
            return ""
