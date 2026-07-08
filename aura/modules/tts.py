"""
AURA — Text-to-Speech Engine (Phase 2).

Automatically selects Piper TTS (offline) or Edge TTS (online) based on
ModeMonitor. All calls are non-blocking — audio is queued and played
sequentially. Concurrent speak() calls never overlap.

interrupt() stops active playback AND drains the queue.
Subscribes to RECORDING_STARTED to auto-mute when user speaks.
"""

from __future__ import annotations

import logging
import queue
import tempfile
import threading
import time
from pathlib import Path

from aura.core.event_bus import EventType, bus
from aura.utils.mode_monitor import mode_monitor

logger = logging.getLogger("aura.tts")

# Per-item synthesis/playback ceiling — prevents worker thread blocking forever.
_SYNTHESIS_TIMEOUT_SEC = 90.0
_PLAYBACK_TIMEOUT_SEC = 60.0


class TTSEngine:
    """Voice output with automatic engine switching and priority queue."""

    def __init__(self, config: dict) -> None:
        tts_cfg = config.get("tts", {})
        self._offline_engine: str = tts_cfg.get("offline_engine", tts_cfg.get("engine", "piper"))
        self._online_engine: str = tts_cfg.get("online_engine", "edge-tts")
        self._piper_voice: str = tts_cfg.get("piper_voice", tts_cfg.get("voice", "en_US-lessac-medium"))
        self._edge_voice: str = tts_cfg.get("edge_voice", "en-US-GuyNeural")
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._worker_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._speaking = threading.Event()
        self._interrupted = False
        self._current_mode: str = "OFFLINE"
        self._pyttsx3_engine = None

        # Subscribe to mode changes
        bus.subscribe(EventType.MODE_CHANGED, self._on_mode_changed)

        # Auto-interrupt when recording starts (user is speaking)
        bus.subscribe(EventType.RECORDING_STARTED, lambda _: self.interrupt())

    def _on_mode_changed(self, payload) -> None:
        """React to connectivity changes."""
        data = payload.data if hasattr(payload, "data") else payload
        self._current_mode = data.get("mode", "OFFLINE") if isinstance(data, dict) else "OFFLINE"

    def start(self) -> None:
        """Start the internal audio worker thread."""
        if self._worker_thread is not None and self._worker_thread.is_alive():
            return

        self._stop_event.clear()
        self._current_mode = mode_monitor.current_mode
        self._worker_thread = threading.Thread(
            target=self._worker, name="TTSWorker", daemon=True
        )
        self._worker_thread.start()
        logger.info("TTS engine started (mode: %s)", self._current_mode)

    def speak(self, text: str, priority: bool = False) -> None:
        """Queue text for speaking. Non-blocking.

        If priority=True, clears the queue before adding.
        """
        if text is None or not isinstance(text, str) or not text.strip():
            return

        bus.emit(EventType.TTS_SPEAK_REQUEST, {"text_preview": text[:50]})

        if priority:
            self._clear_queue()

        self._queue.put(text)

    def interrupt(self) -> None:
        """Stop current audio and clear the queue."""
        # 1. Clear the queue first
        self._clear_queue()

        # 2. Stop active sounddevice playback (Piper/Edge path)
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass

        # 3. Stop pyttsx3 if it is running
        try:
            if self._pyttsx3_engine:
                self._pyttsx3_engine.stop()
        except Exception:
            pass

        # 4. Signal the worker to skip current item
        self._interrupted = True

    def wait_until_idle(self, timeout: float = 120.0) -> bool:
        """Block until the speech queue is drained and playback has finished.

        Returns True if idle before timeout, False if timed out while the worker
        was still synthesizing/playing (_speaking set) or the queue was non-empty.
        """
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            if self._queue.empty() and not self._speaking.is_set():
                return True
            time.sleep(0.05)
        logger.warning(
            "TTS wait_until_idle timed out after %.0fs (queue_empty=%s, speaking=%s)",
            timeout,
            self._queue.empty(),
            self._speaking.is_set(),
        )
        return False

    def _clear_queue(self) -> None:
        """Drain all items from the queue."""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    def _worker(self) -> None:
        """Daemon thread that processes the speech queue."""
        while not self._stop_event.is_set():
            try:
                text = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if text is None:
                break

            # Reset interrupted flag at the start of each item
            self._interrupted = False

            self._speaking.set()
            bus.emit(EventType.TTS_SPEAKING_STARTED, {"text_preview": text[:50]})
            start = time.perf_counter()

            try:
                success = self._synthesize_and_play(text)
            finally:
                self._speaking.clear()

            duration_ms = int((time.perf_counter() - start) * 1000)
            bus.emit(EventType.TTS_SPEAKING_FINISHED, {"duration_ms": duration_ms})

            if not success:
                logger.warning("TTS playback failed for: %s", text[:50])

    def _synthesize_and_play(self, text: str) -> bool:
        """Synthesize and play using the best available engine."""
        if self._interrupted:
            return False

        current_mode = mode_monitor.current_mode

        if current_mode == "ONLINE":
            try:
                if self._try_edge_tts(text):
                    return True
            except Exception as exc:
                logger.info("Edge TTS exception: %s", exc)

        if self._interrupted:
            return False

        if self._offline_engine == "piper":
            try:
                if self._try_piper(text):
                    return True
            except Exception as exc:
                logger.info("Piper exception: %s — falling through to pyttsx3", exc)

        if self._interrupted:
            return False

        try:
            return self._try_pyttsx3(text)
        except Exception as exc:
            logger.error("All TTS engines failed: %s", exc)
            return False

    def _try_edge_tts(self, text: str) -> bool:
        """Synthesize with edge-tts and play the resulting audio."""
        if self._interrupted:
            return False
        tmp_path: str | None = None
        try:
            import asyncio
            import edge_tts

            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                tmp_path = tmp.name

            async def _synthesize():
                communicate = edge_tts.Communicate(text, self._edge_voice)
                await asyncio.wait_for(communicate.save(tmp_path), timeout=45.0)

            synth_start = time.perf_counter()
            try:
                asyncio.run(_synthesize())
            except TimeoutError:
                partial_bytes = 0
                if tmp_path and Path(tmp_path).exists():
                    partial_bytes = Path(tmp_path).stat().st_size
                logger.warning(
                    "edge-tts synthesis timed out after 45s "
                    "(text_len=%s, partial_mp3_bytes=%s, synth_elapsed_sec=%.1f) "
                    "— playback never started",
                    len(text),
                    partial_bytes,
                    time.perf_counter() - synth_start,
                )
                return False

            if self._interrupted:
                return False

            mp3_bytes = Path(tmp_path).stat().st_size if Path(tmp_path).exists() else 0
            self._play_file(tmp_path, source="edge-tts", synth_elapsed_sec=time.perf_counter() - synth_start, file_bytes=mp3_bytes)
            return True
        except Exception as exc:
            logger.debug("Edge TTS error: %s", exc)
            return False
        finally:
            if tmp_path:
                Path(tmp_path).unlink(missing_ok=True)

    def _try_piper(self, text: str) -> bool:
        """Synthesize with Piper TTS and play the resulting audio."""
        if self._interrupted:
            return False
        tmp_path: str | None = None
        try:
            import subprocess

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp_path = tmp.name

            proc = subprocess.run(
                ["piper", "-m", self._piper_voice, "-f", tmp_path],
                input=text.encode("utf-8"),
                capture_output=True,
                timeout=30,
            )
            if proc.returncode != 0:
                stderr = proc.stderr.decode("utf-8", errors="replace").strip()
                logger.info(
                    "Piper failed (rc=%s, voice=%s): %s",
                    proc.returncode,
                    self._piper_voice,
                    stderr[:400] or "(no stderr)",
                )
                return False
            if self._interrupted:
                return False
            wav_bytes = Path(tmp_path).stat().st_size if Path(tmp_path).exists() else 0
            self._play_file(tmp_path, source="piper", file_bytes=wav_bytes)
            return True
        except Exception as exc:
            logger.debug("Piper TTS error: %s", exc)
            return False
        finally:
            if tmp_path:
                Path(tmp_path).unlink(missing_ok=True)

    def _try_pyttsx3(self, text: str) -> bool:
        """Final fallback — always available via pyttsx3."""
        if self._interrupted:
            return False
        try:
            import pyttsx3

            engine = pyttsx3.init()
            self._pyttsx3_engine = engine
            engine.say(text)
            engine.runAndWait()
            engine.stop()
            self._pyttsx3_engine = None
            return True
        except Exception as exc:
            logger.error("pyttsx3 fallback failed: %s", exc)
            self._pyttsx3_engine = None
            return False

    def _sounddevice_playback_diag(
        self,
        expected_sec: float,
        elapsed_sec: float,
        *,
        source: str = "",
        file_bytes: int = 0,
        synth_elapsed_sec: float | None = None,
    ) -> str:
        """Collect whatever playback state sounddevice exposes at timeout."""
        import sounddevice as sd

        parts = [
            f"source={source or 'unknown'}",
            f"expected_audio_sec={expected_sec:.2f}",
            f"elapsed_since_play_start_sec={elapsed_sec:.2f}",
        ]
        if file_bytes:
            parts.append(f"file_bytes={file_bytes}")
        if synth_elapsed_sec is not None:
            parts.append(f"synth_elapsed_sec={synth_elapsed_sec:.2f}")
        try:
            st = sd.get_stream()
            if st is not None:
                pos = float(st.time)
                parts.append(f"stream_time_sec={pos:.2f}")
                parts.append(f"stream_active={st.active}")
                parts.append(f"stream_stopped={st.stopped}")
                if expected_sec > 0:
                    pct = min(100.0, 100.0 * pos / expected_sec)
                    parts.append(f"estimated_pct_played={pct:.1f}")
                    if pct >= 95.0:
                        parts.append("inference=likely_full_playback_stall_on_completion")
                    elif pct <= 5.0:
                        parts.append("inference=likely_no_audible_output")
                    else:
                        parts.append("inference=likely_partial_playback")
        except Exception as exc:
            parts.append(f"stream_query_error={exc!s}")
        try:
            status = sd.get_status()
            if status:
                parts.append(f"sd_status={status}")
        except Exception:
            pass
        return ", ".join(parts)

    def _play_file(
        self,
        filepath: str,
        *,
        source: str = "",
        file_bytes: int = 0,
        synth_elapsed_sec: float | None = None,
    ) -> None:
        """Play an audio file using sounddevice + soundfile, or pygame."""
        if self._interrupted:
            return
        try:
            import sounddevice as sd
            import soundfile as sf

            data, samplerate = sf.read(filepath)
            if not file_bytes:
                file_bytes = Path(filepath).stat().st_size if Path(filepath).exists() else 0
            frames = len(data)
            expected_sec = frames / float(samplerate) if samplerate else 0.0
            play_started = time.perf_counter()
            done = threading.Event()

            def _play_blocking() -> None:
                try:
                    sd.play(data, samplerate)
                    sd.wait()
                finally:
                    done.set()

            player = threading.Thread(target=_play_blocking, name="TTS-sd-play", daemon=True)
            player.start()
            if not done.wait(timeout=_PLAYBACK_TIMEOUT_SEC):
                diag = self._sounddevice_playback_diag(
                    expected_sec,
                    time.perf_counter() - play_started,
                    source=source,
                    file_bytes=file_bytes,
                    synth_elapsed_sec=synth_elapsed_sec,
                )
                logger.warning(
                    "sounddevice playback timed out after %.0fs — stopping stream (%s)",
                    _PLAYBACK_TIMEOUT_SEC,
                    diag,
                )
                sd.stop()
            return
        except Exception:
            pass

        if self._interrupted:
            return

        try:
            import pygame.mixer

            pygame.mixer.init()
            pygame.mixer.music.load(filepath)
            pygame.mixer.music.play()
            play_started = time.perf_counter()
            deadline = time.perf_counter() + _PLAYBACK_TIMEOUT_SEC
            while pygame.mixer.music.get_busy():
                if self._interrupted:
                    pygame.mixer.music.stop()
                    break
                if time.perf_counter() >= deadline:
                    elapsed = time.perf_counter() - play_started
                    logger.warning(
                        "pygame playback timed out after %.0fs — stopping "
                        "(source=%s, file_bytes=%s, elapsed_sec=%.2f, get_busy=True)",
                        _PLAYBACK_TIMEOUT_SEC,
                        source or "unknown",
                        file_bytes,
                        elapsed,
                    )
                    pygame.mixer.music.stop()
                    break
                time.sleep(0.1)
            pygame.mixer.quit()
        except Exception as exc:
            logger.debug("Audio playback failed: %s", exc)
