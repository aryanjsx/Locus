# aura/core/session_controller.py
#
# AURA Session Controller
# ────────────────────────────────────────────────────────────────────
# Manages the ACTIVE state: the window between a wake word and going
# back to sleep. Coordinates three things:
#   1. Continuous listening loop  (re-arms STT after each command)
#   2. Inactivity timer           (10 minutes → go to sleep)
#   3. WakeWordListener gating    (paused during session, resumed after)
#
# Threading model:
#   - All event handlers run in the EventBus caller's thread
#   - The inactivity timer fires on a daemon thread (threading.Timer)
#   - wake.resume() is scheduled on a timer AFTER TTS finishes speaking
#     the sleep announcement — so AURA doesn't hear its own voice
# ────────────────────────────────────────────────────────────────────

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any, TYPE_CHECKING

from aura.core.event_bus import bus, EventType

if TYPE_CHECKING:
    from aura.modules.wake_word import WakeWordListener

logger = logging.getLogger("aura.session_controller")

_WAKE_REARM_DELAY_SECONDS: float = 3.0
_ECHO_PREVENTION_SECONDS: float = 0.5


class SessionController:
    """
    Owns the ACTIVE state of the AURA pipeline.

    Lifecycle
    ---------
    idle  --[WAKE_WORD_DETECTED]--> active
    active --[inactivity / manual]--> idle

    While active, the substate cycle is:
    LISTENING -> (pipeline runs) -> SPEAKING -> LISTENING -> ...

    The SessionController drives this cycle by:
    - Emitting LISTEN_NOW after each TTS_SPEAKING_FINISHED
    - Emitting LISTEN_NOW after silence-only recording cycles
    - Cancelling and restarting the inactivity timer on each transcription
    """

    def __init__(self, config: dict[str, Any], wake_listener: "WakeWordListener") -> None:
        self._config = config
        self._wake = wake_listener
        self._active = False
        self._pipeline_busy = False

        timeout_minutes: float = float(
            config.get("session", {}).get("inactivity_timeout_minutes", 10)
        )
        self._timeout_seconds: float = timeout_minutes * 60.0
        self._inactivity_timer: threading.Timer | None = None

        bus.subscribe(EventType.WAKE_WORD_DETECTED, self._on_wake_word)
        bus.subscribe(EventType.TRANSCRIPTION_COMPLETE, self._on_transcription_complete)
        bus.subscribe(EventType.RECORDING_STOPPED, self._on_recording_stopped)
        bus.subscribe(EventType.TTS_SPEAKING_FINISHED, self._on_speaking_finished)
        bus.subscribe(EventType.SESSION_ENDED, self._on_session_ended)

        logger.info(
            f"SessionController ready. "
            f"Inactivity timeout: {timeout_minutes} minute(s)."
        )

    # ──────────────────────────────────────────────────────────────────
    # Event handlers
    # ──────────────────────────────────────────────────────────────────

    def _on_wake_word(self, payload: dict) -> None:
        """
        'Hey Kommy' was detected.
        Start a session if one is not already running.
        """
        if self._active:
            logger.debug("Wake word fired during active session — ignored.")
            return

        logger.info("Wake word detected — starting session.")
        self._active = True
        self._pipeline_busy = False

        self._wake.pause()

        bus.emit(EventType.SESSION_STARTED, {
            "timestamp": datetime.now().isoformat(),
        })
        bus.emit(EventType.TTS_SPEAK_REQUEST, {
            "text": "I'm listening.",
            "priority": "high",
        })

        self._reset_inactivity_timer()

    def _on_transcription_complete(self, payload: dict) -> None:
        """
        STT finished a recording cycle.
        Reset inactivity timer if actual speech was captured.
        """
        if not self._active:
            return

        text: str = payload.get("text", "").strip()
        if not text:
            return

        logger.debug(f"Transcription received — resetting inactivity timer. text='{text[:60]}'")
        self._reset_inactivity_timer()
        self._pipeline_busy = True

    def _on_recording_stopped(self, payload: dict) -> None:
        """
        STTEngine finished a recording cycle (possibly with silence only).
        If the pipeline is NOT busy (silence cycle), immediately re-arm listening.
        """
        if not self._active:
            return
        if self._pipeline_busy:
            return

        logger.debug("Silent recording cycle — re-arming listener.")
        threading.Timer(
            0.1,
            lambda: bus.emit(EventType.LISTEN_NOW, {"source": "silence_loop"}),
        ).start()

    def _on_speaking_finished(self, payload: dict) -> None:
        """
        TTS finished speaking a response.
        Re-arm the STTEngine for the next command.

        Also handles the post-session-end case:
        if the session has already ended, re-arm WakeWordListener instead.
        """
        if not self._active:
            logger.info("TTS finished post-session announcement — re-arming WakeWordListener.")
            threading.Timer(_WAKE_REARM_DELAY_SECONDS, self._wake.resume).start()
            return

        self._pipeline_busy = False
        logger.debug("TTS finished — re-arming STT for next command.")
        threading.Timer(
            _ECHO_PREVENTION_SECONDS,
            lambda: bus.emit(EventType.LISTEN_NOW, {"source": "session_loop"}),
        ).start()

    def _on_session_ended(self, payload: dict) -> None:
        """
        Session ended — either manually or via timeout.
        Clean up state. WakeWordListener re-arm is handled by _on_speaking_finished
        AFTER the goodbye TTS finishes.
        """
        if not self._active:
            return

        reason: str = payload.get("reason", "unknown")
        logger.info(f"Session ending. Reason: {reason}")

        self._active = False
        self._pipeline_busy = False
        self._cancel_inactivity_timer()

    # ──────────────────────────────────────────────────────────────────
    # Inactivity timer
    # ──────────────────────────────────────────────────────────────────

    def _reset_inactivity_timer(self) -> None:
        """Cancel the running timer (if any) and start a fresh one."""
        self._cancel_inactivity_timer()
        self._inactivity_timer = threading.Timer(
            self._timeout_seconds,
            self._on_inactivity_timeout,
        )
        self._inactivity_timer.daemon = True
        self._inactivity_timer.start()
        logger.debug(
            f"Inactivity timer reset — expires in {self._timeout_seconds:.0f}s."
        )

    def _cancel_inactivity_timer(self) -> None:
        if self._inactivity_timer is not None:
            self._inactivity_timer.cancel()
            self._inactivity_timer = None

    def _on_inactivity_timeout(self) -> None:
        """
        Timer expired with no transcription.
        End the session and announce it.
        This fires on a daemon thread — emit events to stay thread-safe.
        """
        if not self._active:
            return

        logger.info(
            f"Inactivity timeout ({self._timeout_seconds:.0f}s) — ending session."
        )
        self._active = False
        self._pipeline_busy = False
        self._inactivity_timer = None

        bus.emit(EventType.INACTIVITY_TIMEOUT, {})
        bus.emit(EventType.SESSION_ENDED, {"reason": "inactivity_timeout"})

        bus.emit(EventType.TTS_SPEAK_REQUEST, {
            "text": "Going to sleep. Say Hey Kommy when you need me.",
            "priority": "high",
        })

    # ──────────────────────────────────────────────────────────────────
    # Public read-only state
    # ──────────────────────────────────────────────────────────────────

    @property
    def is_active(self) -> bool:
        """True if a session is currently open."""
        return self._active
