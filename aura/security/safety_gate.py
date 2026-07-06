"""
AURA — Safety Gate.

Two confirmation surfaces share the same acceptance rules:

* :meth:`SafetyGate.request` — CLI / registry path (text stdin or ``input_fn``).
* :meth:`SafetyGate.check` — voice pipeline path (TTS prompt + STT listen).

Accepted tokens (case-insensitive): ``yes``, ``confirm``, ``do it``, ``proceed``.
Denial, silence, or timeout cancels the action.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from aura.core.config_loader import get as get_config
from aura.core.errors import ConfirmationDenied, ConfirmationTimeout
from aura.core.event_bus import EventBus, EventType
from aura.core.tracing import current_trace_id

logger = logging.getLogger("aura.safety_gate")

_POLL_INTERVAL = 0.05  # seconds — Windows kbhit loop resolution

# Destructive-action-specific TTS prompts (voice ``check`` path)
_CONFIRMATION_PROMPTS: dict[str, str] = {
    "delete_file":       "I'm about to delete {filename}. Say yes to confirm.",
    "rmdir":             "This will permanently delete the folder {name} and all contents. Confirm?",
    "delete_folder":     "This will permanently delete the folder {name} and all contents. Confirm?",
    "git_push":          "Pushing commits to {branch} on {remote}. Confirm?",
    "git_reset_hard":    "Hard reset will discard all uncommitted changes. This cannot be undone. Confirm?",
    "git_branch_delete": "Deleting branch {name}. Confirm?",
    "git_force_push":    "Force push to {branch} will overwrite remote history. Are you absolutely sure?",
    "docker_remove":     "Removing container {name} and its data. Confirm?",
    "docker_prune":      "System prune removes all stopped containers and unused images. Confirm?",
    "kill_process":      "Killing process {name}. Confirm?",
}

ACCEPTED_RESPONSES: frozenset[str] = frozenset({"yes", "confirm", "do it", "proceed"})


def _read_line_non_blocking(prompt: str, timeout: float) -> str | None:
    """Write *prompt* and read one line from stdin, returning ``None`` on timeout."""
    sys.stdout.write(prompt)
    try:
        sys.stdout.flush()
    except Exception:
        pass

    if sys.platform == "win32":
        return _win_read_line(timeout)
    return _posix_read_line(timeout)


def _posix_read_line(timeout: float) -> str | None:
    import select

    try:
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
    except (OSError, ValueError):
        return None
    if not ready:
        return None
    try:
        return sys.stdin.readline().rstrip("\n")
    except Exception:
        return None


def _win_read_line(timeout: float) -> str | None:
    try:
        import msvcrt  # type: ignore[import-not-found]
    except ImportError:
        return None

    deadline = time.monotonic() + timeout
    buf: list[str] = []
    while time.monotonic() < deadline:
        if msvcrt.kbhit():
            ch = msvcrt.getwche()
            if ch in ("\r", "\n"):
                try:
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                except Exception:
                    pass
                return "".join(buf)
            if ch == "\x08":
                if buf:
                    buf.pop()
                continue
            if ch == "\x03":
                raise KeyboardInterrupt
            buf.append(ch)
        else:
            time.sleep(_POLL_INTERVAL)
    return None


class SafetyGate:
    """Interactive / voice confirmation with audit logging."""

    TIMEOUT_SECONDS: float = 8.0

    def __init__(
        self,
        bus: EventBus | Any,
        *,
        input_fn: Callable[[str], str] | None = None,
        output_fn: Callable[[str], None] | None = None,
        tts_engine: Any = None,
        stt_engine: Any = None,
        config: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> None:
        self._bus = bus
        self._input_fn = input_fn
        self._output_fn = output_fn or print
        self._tts = tts_engine
        self._stt = stt_engine

        safety_cfg = (config or {}).get("safety", {})
        configured_timeout = timeout
        if configured_timeout is None:
            configured_timeout = safety_cfg.get(
                "confirmation_timeout",
                safety_cfg.get("confirm_timeout"),
            )
        if configured_timeout is None:
            configured_timeout = float(
                get_config("safety.confirmation_timeout", self.TIMEOUT_SECONDS)
            )
        self._timeout = max(1.0, float(configured_timeout))

        self._audit_log_path = safety_cfg.get("audit_log", "logs/safety_audit.log")
        self._ensure_audit_dir()

    def _ensure_audit_dir(self) -> None:
        try:
            Path(self._audit_log_path).parent.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            logger.warning("Could not create audit log directory: %s", exc)

    def request(
        self,
        *,
        action: str,
        params: dict,
        source: str,
        permission: str,
        trace_id: str | None = None,
    ) -> None:
        """Block until the user confirms or the gate cancels (CLI / registry path)."""
        trace_id = trace_id or current_trace_id()
        self._bus.emit(
            EventType.SAFETY_CONFIRMATION_REQ,
            {
                "action": action,
                "source": source,
                "permission": permission,
                "trace_id": trace_id,
                "timeout_seconds": self._timeout,
            },
        )

        prompt = (
            f"\n[AURA-SAFETY] '{action}' ({permission}) — "
            f"respond 'yes' / 'confirm' / 'proceed' within {self._timeout:.0f}s "
            f"to execute.  Anything else cancels.\n> "
        )

        raw = self._read_line(prompt)

        if raw is None:
            self._bus.emit(
                EventType.SAFETY_DENIED,
                {"action": action, "source": source, "trace_id": trace_id, "reason": "timeout"},
            )
            raise ConfirmationTimeout(
                f"No confirmation for '{action}' within {self._timeout:.0f}s — cancelled."
            )

        response = raw.strip().lower()
        if response in ACCEPTED_RESPONSES:
            self._bus.emit(
                EventType.SAFETY_CONFIRMED,
                {
                    "action": action,
                    "source": source,
                    "trace_id": trace_id,
                    "response": response,
                },
            )
            return

        self._bus.emit(
            EventType.SAFETY_DENIED,
            {
                "action": action,
                "source": source,
                "trace_id": trace_id,
                "response": response,
            },
        )
        raise ConfirmationDenied(
            f"Confirmation refused for '{action}' (received {response!r})."
        )

    def check(self, command_plan: Any) -> bool:
        """Voice-based confirmation for the command engine pipeline."""
        from aura.core.event_bus import EventType

        action = command_plan.action
        params = command_plan.params
        executor = command_plan.executor

        prompt_template = _CONFIRMATION_PROMPTS.get(action)
        if prompt_template:
            try:
                prompt_text = prompt_template.format(**params)
            except KeyError:
                prompt_text = prompt_template.format_map(
                    {
                        k: params.get(k, "unknown")
                        for k in ("filename", "name", "branch", "remote", "N")
                    }
                )
        else:
            prompt_text = (
                f"I'm about to execute {action}. This action is destructive. "
                "Say yes to confirm."
            )

        self._bus.emit(EventType.SAFETY_CONFIRMATION_REQ, {
            "action": action,
            "executor": executor,
            "timeout_seconds": self._timeout,
        })

        if self._tts:
            self._tts.speak(prompt_text, priority=True)
            self._tts.wait_until_idle(timeout=30)
        else:
            self._output_fn(f"[SAFETY] {prompt_text}")

        response_text = ""
        if self._stt:
            try:
                result = self._stt.listen_and_transcribe()
                response_text = result.text.strip().lower() if result.text else ""
            except Exception as exc:
                logger.error("STT failed during safety check: %s", exc)
                response_text = ""
        elif self._input_fn is not None:
            raw = self._read_line(f"[SAFETY] {prompt_text}\n> ")
            response_text = raw.strip().lower() if raw else ""
        else:
            try:
                sys.stdout.write(f"[SAFETY] {prompt_text}\n> ")
                sys.stdout.flush()
                response_text = input().strip().lower()
            except Exception:
                response_text = ""

        confirmed = response_text in ACCEPTED_RESPONSES
        reason = "user_confirmed" if confirmed else f"denied (response: {response_text!r})"
        if not response_text:
            reason = "timeout"

        self._audit_log(
            "CONFIRMED" if confirmed else "CANCELLED",
            executor=executor,
            action=action,
            params=params,
            reason=reason,
        )

        if confirmed:
            self._bus.emit(EventType.SAFETY_CONFIRMED, {
                "action": action,
                "executor": executor,
            })
        else:
            self._bus.emit(EventType.SAFETY_DENIED, {
                "action": action,
                "executor": executor,
                "reason": reason,
            })

        return confirmed

    def _read_line(self, prompt: str) -> str | None:
        if self._input_fn is None:
            return _read_line_non_blocking(prompt, self._timeout)

        holder: list[str | None] = [None]
        done = threading.Event()

        def worker() -> None:
            try:
                holder[0] = self._input_fn(prompt)  # type: ignore[misc]
            except Exception:
                holder[0] = None
            finally:
                done.set()

        t = threading.Thread(target=worker, daemon=True, name="aura-safety-fallback")
        t.start()
        done.wait(timeout=self._timeout)
        if not done.is_set():
            return None
        return holder[0]

    def _audit_log(
        self,
        decision: str,
        *,
        executor: str,
        action: str,
        params: dict[str, Any],
        reason: str = "",
    ) -> None:
        timestamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

        if decision == "CONFIRMED":
            line = (
                f"{timestamp} | CONFIRMED | executor={executor} action={action} "
                f"params={params}\n"
            )
        else:
            line = (
                f"{timestamp} | CANCELLED | executor={executor} action={action} "
                f"reason={reason}\n"
            )

        try:
            with open(self._audit_log_path, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception as exc:
            logger.error("Audit log write failed: %s", exc)

        logger.info("SafetyGate %s: %s %s", decision, action, reason)


class AutoConfirmGate(SafetyGate):
    """Silent gate that approves every request (tests / ``--yes`` only)."""

    def request(self, **kwargs: Any) -> None:  # type: ignore[override]
        return None

    def check(self, command_plan: Any) -> bool:
        self._audit_log(
            "CONFIRMED",
            executor=getattr(command_plan, "executor", "unknown"),
            action=getattr(command_plan, "action", "unknown"),
            params=getattr(command_plan, "params", {}),
            reason="auto_confirm",
        )
        return True
