"""
aura/modules/wake_word.py
─────────────────────────────────────────────────────────────────────────────
Wake Word Listener  —  Three-tier fallback architecture

  Tier 1 → Whisper-based  (uses the already-loaded tiny Whisper model)
  Tier 2 → openwakeword   (offline ONNX; may underperform on some platforms)
  Tier 3 → CTRL+SPACE     (keyboard hotkey, always works)

Events emitted on EventBus:
  WAKE_WORD_DETECTED  — { "timestamp": datetime, "source": tier name }
  WAKE_WORD_ERROR     — { "error": str, "module": "WakeWordListener" }
  SYSTEM_ERROR        — { "error": str, "module": "WakeWordListener",
                          "severity": "warning" }
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from datetime import datetime
from typing import Optional

import numpy as np
import sounddevice as sd
from dotenv import load_dotenv

from aura.core.event_bus import EventType, bus
from aura.utils.mic_lock import mic_lock


def _resolve_input_device(config):
    """Resolve the microphone device index (inlined from audio_input)."""
    ww = config.get("wake_word", {}) if isinstance(config, dict) else {}
    explicit = ww.get("input_device") if isinstance(ww, dict) else None
    if explicit is not None:
        return int(explicit)
    default_in = sd.default.device[0]
    if default_in is None or default_in < 0:
        return 0
    return int(default_in)

load_dotenv()
logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000


class WakeWordListener:
    """
    Continuously monitors microphone audio in a daemon thread.
    Emits WAKE_WORD_DETECTED on the EventBus when the wake word is spoken.
    """

    def __init__(self, config) -> None:
        self._config = config
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._started = False
        self._pause_event = threading.Event()
        self._paused: bool = False

        ww = config.get("wake_word", {}) if isinstance(config, dict) else {}
        _get = lambda key, default: ww.get(key, default) if isinstance(ww, dict) else getattr(ww, key, default)

        self._engine = _get("engine", "whisper")
        self._phrases: list[str] = _get("phrases", ["hey kommy", "kommy"])
        self._listen_duration: float = float(_get("listen_duration", 2.0))
        self._vad_threshold: float = float(_get("vad_threshold", 0.008))
        self._vad_pre_frames: int = int(_get("vad_pre_frames", 3))
        self._input_device = _get("input_device", None)
        if self._input_device is not None:
            self._input_device = int(self._input_device)
        self._debug = bool(_get("debug_scores", False))
        self._no_speech_threshold: float = float(_get("no_speech_threshold", 0.3))

        # openwakeword settings (Tier 2)
        self._oww_model = _get("oww_model", "hey_jarvis")
        self._oww_threshold = float(_get("oww_threshold", 0.05))
        self._oww_patience = int(_get("oww_patience", 1))
        self._chunk_ms = int(_get("oww_chunk_ms", 80))
        self._chunk_samples = int(SAMPLE_RATE * self._chunk_ms / 1000)

        # Precompile phrase patterns + common Whisper mishearings
        self._phrase_patterns = [re.compile(re.escape(p), re.IGNORECASE) for p in self._phrases]
        self._fuzzy_alts = [
            "hey kommy", "kommy", "hey commie", "heykami",
            "hey comi", "hey commy", "a kommy", "hey komi",
            "yeah kommy", "he kommy", "hay kommy",
            "hey comic", "hey tommy", "hey connie",
        ]

        # Whisper model reference (set by main.py before start)
        self._whisper_model = None

    def set_whisper_model(self, model) -> None:
        """Share the already-loaded Whisper model from STTEngine."""
        self._whisper_model = model

    # ── Public interface ──────────────────────────────────────────────────────

    def start(self) -> None:
        if self._started:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="aura-wakeword", daemon=True,
        )
        self._thread.start()
        self._started = True
        logger.info("[WakeWord] Listener started (engine: %s)", self._engine)

    def stop(self) -> None:
        self._stop_event.set()
        self._pause_event.clear()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self._started = False
        logger.info("[WakeWord] Listener stopped")

    def pause(self) -> None:
        """
        Pause wake word detection during an active session.
        The detection thread keeps running — it just suppresses events.
        This avoids the latency of stopping and restarting the engine.
        """
        self._paused = True
        self._pause_event.set()
        logger.info("WakeWordListener paused — session active.")

    def resume(self) -> None:
        """
        Resume detection after a session ends.
        Safe to call from any thread.
        """
        self._paused = False
        self._pause_event.clear()
        logger.info("WakeWordListener resumed — waiting for wake word.")

    # ── Engine dispatcher ─────────────────────────────────────────────────────

    def _run(self) -> None:
        self._register_keyboard_hotkey()

        if self._engine == "whisper" and self._whisper_model is not None:
            try:
                logger.info("[WakeWord] Using Whisper-based wake word detection")
                self._listen_whisper()
                return
            except Exception as e:
                logger.warning("[WakeWord] Whisper wake failed: %s", e)
                self._emit_error(f"Whisper wake failed: {e}")

        if self._engine == "oww" or self._engine == "whisper":
            try:
                logger.info("[WakeWord] Using openwakeword detection")
                self._listen_openwakeword()
                return
            except Exception as e:
                logger.warning("[WakeWord] openwakeword failed: %s", e)
                self._emit_error(f"openwakeword failed: {e}")

        logger.info("[WakeWord] Falling back to CTRL+SPACE only")
        self._keyboard_only_loop()

    # ── Tier 1: Whisper-based VAD + keyword spotting ──────────────────────────

    def _listen_whisper(self) -> None:
        """
        Lightweight voice-activity detection → short Whisper transcription →
        keyword match.  Uses the Whisper model already loaded for STT, so
        there is zero additional model loading cost.

        Flow:
          1. Read 100ms audio chunks, compute RMS
          2. When RMS exceeds vad_threshold for vad_pre_frames consecutive
             frames, start recording
          3. Record for listen_duration seconds (captures the wake phrase)
          4. Run Whisper transcription on the buffer
          5. If any configured phrase appears in the text → emit wake event
          6. Cooldown 1.5s, then resume monitoring
        """
        chunk_samples = int(SAMPLE_RATE * 0.1)  # 100ms
        record_chunks = int(self._listen_duration / 0.1)
        inp = _resolve_input_device(self._config) if self._input_device is None else self._input_device
        try:
            dev_name = sd.query_devices(inp)["name"]
        except Exception:
            dev_name = str(inp)

        phrases_str = " / ".join(f'"{p}"' for p in self._phrases)
        print(
            f'[WakeWord] Listening on mic [{inp}] — say {phrases_str}. '
            f'Ctrl+Space always works.'
        )
        logger.info(
            "[WakeWord] Whisper wake ready — phrases: %s, "
            "listen: %.1fs, vad: %.4f, mic: [%s] %s",
            self._phrases, self._listen_duration, self._vad_threshold, inp, dev_name,
        )

        stream_kwargs = dict(
            samplerate=SAMPLE_RATE, channels=1, dtype="float32",
            blocksize=chunk_samples, device=inp,
        )

        while not self._stop_event.is_set():
            if self._pause_event.is_set():
                time.sleep(0.05)
                continue

            try:
                with mic_lock:
                    with sd.InputStream(**stream_kwargs) as stream:
                        speech_count = 0
                        while (
                            not self._stop_event.is_set()
                            and not self._pause_event.is_set()
                        ):
                            chunk, _ = stream.read(chunk_samples)
                            rms = float(np.sqrt(np.mean(chunk ** 2)))

                            if rms >= self._vad_threshold:
                                speech_count += 1
                            else:
                                speech_count = 0

                            if speech_count < self._vad_pre_frames:
                                continue

                            # Speech detected — record for listen_duration
                            if self._debug:
                                print(f"[WakeWord] Voice activity (rms={rms:.4f}), recording {self._listen_duration}s...")

                            audio_buf = [chunk.flatten()]
                            for _ in range(record_chunks - 1):
                                if self._stop_event.is_set() or self._pause_event.is_set():
                                    break
                                c, _ = stream.read(chunk_samples)
                                audio_buf.append(c.flatten())

                            audio = np.concatenate(audio_buf)
                            text = self._whisper_transcribe(audio)

                            if self._debug:
                                print(f'[WakeWord] Heard: "{text}"')

                            if self._matches_wake_phrase(text):
                                command = self._strip_wake_phrase(text)
                                logger.info('[WakeWord] DETECTED via Whisper: "%s" (cmd: "%s")', text, command)
                                print(f'[WakeWord] Wake phrase detected: "{text}"')
                                self._emit_detected("whisper", transcript=text, command=command)
                                self._cooldown(1.5)
                                speech_count = 0
                            else:
                                speech_count = 0

            except Exception as exc:
                if self._pause_event.is_set() or self._stop_event.is_set():
                    continue
                bus.emit(EventType.WAKE_WORD_ERROR, {
                    "error": str(exc),
                    "module": "WakeWordListener",
                    "timestamp": datetime.now(),
                })
                logger.warning("[WakeWord] Mic/stream error: %s — retrying in 2s", exc)
                time.sleep(2)
                continue

    def _whisper_transcribe(self, audio: np.ndarray) -> str:
        """Run Whisper tiny on a short audio buffer. Returns lowercase text."""
        try:
            if audio.dtype != np.float32:
                audio = audio.astype(np.float32)
            result = self._whisper_model.transcribe(
                audio, fp16=False, language="en",
                no_speech_threshold=self._no_speech_threshold,
                initial_prompt="Hey Kommy.",
            )
            return result.get("text", "").strip().lower()
        except Exception as exc:
            logger.debug("[WakeWord] Whisper transcribe error: %s", exc)
            return ""

    def _matches_wake_phrase(self, text: str) -> bool:
        """Check if text matches a wake phrase, with fuzzy tolerance for
        common Whisper mishearings of 'Hey Kommy'."""
        if not text:
            return False
        if any(p.search(text) for p in self._phrase_patterns):
            return True
        for alt in self._fuzzy_alts:
            if alt in text:
                return True
        return False

    # ── Tier 2: openwakeword (kept as fallback) ───────────────────────────────

    def _listen_openwakeword(self) -> None:
        from openwakeword.model import Model

        inference_framework = "onnx"
        try:
            import tflite_runtime  # noqa: F401
            inference_framework = "tflite"
        except ImportError:
            pass

        oww_model = Model(
            wakeword_models=[self._oww_model],
            inference_framework=inference_framework,
        )

        inp = _resolve_input_device(self._config) if self._input_device is None else self._input_device
        print(
            f"[WakeWord] openwakeword on mic [{inp}] — "
            f"model: {self._oww_model}, threshold: {self._oww_threshold}"
        )

        stream_kwargs = dict(
            samplerate=SAMPLE_RATE, channels=1, dtype="float32",
            blocksize=self._chunk_samples, device=inp,
        )
        patience = {self._oww_model: self._oww_patience}
        threshold = {self._oww_model: self._oww_threshold}

        while not self._stop_event.is_set():
            if self._pause_event.is_set():
                time.sleep(0.05)
                continue
            try:
                with mic_lock:
                    with sd.InputStream(**stream_kwargs) as stream:
                        while not self._stop_event.is_set() and not self._pause_event.is_set():
                            audio_chunk, _ = stream.read(self._chunk_samples)
                            pcm = (np.clip(audio_chunk.flatten(), -1, 1) * 32767).astype(np.int16)
                            prediction = oww_model.predict(pcm, patience=patience, threshold=threshold)
                            if prediction.get(self._oww_model, 0) > 0:
                                logger.info("[WakeWord] DETECTED via openwakeword")
                                self._emit_detected("openwakeword")
                                self._cooldown(1.5)
                                oww_model.reset()
            except Exception as exc:
                if self._pause_event.is_set() or self._stop_event.is_set():
                    continue
                bus.emit(EventType.WAKE_WORD_ERROR, {
                    "error": str(exc),
                    "module": "WakeWordListener",
                    "timestamp": datetime.now(),
                })
                logger.warning("[WakeWord] OWW error: %s — retrying in 2s", exc)
                time.sleep(2)
                continue

    # ── Tier 3: CTRL+SPACE ────────────────────────────────────────────────────

    def _register_keyboard_hotkey(self) -> None:
        try:
            import keyboard
            keyboard.add_hotkey("ctrl+space", lambda: (
                logger.info("[WakeWord] DETECTED via CTRL+SPACE"),
                self._emit_detected("keyboard"),
            ))
            logger.info("[WakeWord] CTRL+SPACE hotkey registered")
        except Exception as e:
            logger.warning("[WakeWord] Could not register CTRL+SPACE: %s", e)

    def _keyboard_only_loop(self) -> None:
        logger.info("[WakeWord] CTRL+SPACE only mode")
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=0.2)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _strip_wake_phrase(self, text: str) -> str:
        """Remove the wake phrase from the transcript to extract the command.

        'hey kommy what is python' → 'what is python'
        'kommy open chrome'        → 'open chrome'
        'hey kommy'                → ''
        """
        t = text.lower().strip()
        for phrase in sorted(self._phrases, key=len, reverse=True):
            idx = t.find(phrase)
            if idx >= 0:
                t = t[idx + len(phrase):]
                break
        for alt in sorted(self._fuzzy_alts, key=len, reverse=True):
            idx = t.find(alt)
            if idx >= 0:
                t = t[idx + len(alt):]
                break
        return t.strip(" ,.:!?")

    def _emit_detected(self, source: str, transcript: str = "", command: str = "") -> None:
        if self._paused:
            return
        bus.emit(EventType.WAKE_WORD_DETECTED, {
            "timestamp": datetime.now().isoformat(),
            "source": source,
            "transcript": transcript,
            "command": command,
        })

    def _emit_error(self, message: str) -> None:
        bus.emit(EventType.SYSTEM_ERROR, {
            "error": message,
            "module": "WakeWordListener",
            "severity": "warning",
        })

    def _cooldown(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            if self._stop_event.is_set() or self._pause_event.is_set():
                break
            time.sleep(0.05)
