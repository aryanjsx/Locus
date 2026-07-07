"""
AURA — Speech-to-Text Engine (Phase 2).

Offline STT using OpenAI Whisper running locally. Captures audio from
the microphone, detects silence to end recording, and returns a
transcription result.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

import numpy as np

from aura.utils.audio_input import resolve_input_device
from aura.utils.mic_lock import mic_lock
from aura.core.event_bus import EventType, bus

logger = logging.getLogger("aura.stt")

_DEFAULT_RMS_SILENCE_THRESHOLD = 0.003
_CHUNK_DURATION_MS = 100  # 100ms chunks for silence detection


@dataclass
class TranscriptionResult:
    """Result of a speech-to-text operation."""

    text: str
    confidence: float  # 0.0–1.0
    duration_ms: int
    is_empty: bool


class STTEngine:
    """Whisper-based speech-to-text with silence-detection recording."""

    def __init__(self, config: dict) -> None:
        stt_cfg = config.get("stt", {})
        self._model_name: str = stt_cfg.get("model", "base")
        self._silence_timeout: float = stt_cfg.get("silence_timeout", 2.0)
        self._max_recording: int = stt_cfg.get("max_recording", 30)
        self._rms_silence_threshold: float = stt_cfg.get(
            "rms_silence_threshold", _DEFAULT_RMS_SILENCE_THRESHOLD
        )
        self._model = None
        self._sample_rate = 16000
        self._config = config
        self._input_device: int | None = None

        bus.subscribe(EventType.LISTEN_NOW, self._on_listen_now)

    def preload(self) -> None:
        """Load the Whisper model into RAM. Must be called at startup."""
        import whisper

        start = time.perf_counter()
        logger.info("Loading Whisper model '%s'...", self._model_name)
        self._model = whisper.load_model(self._model_name)
        elapsed = int((time.perf_counter() - start) * 1000)
        logger.info("Whisper model '%s' loaded in %dms", self._model_name, elapsed)

    def _on_listen_now(self, payload: dict) -> None:
        """
        Called by SessionController when AURA should start listening
        for the next command in an active session.

        Runs in the event bus caller's thread — immediately hands off to a
        worker thread so we never block the bus.
        """
        threading.Thread(
            target=self.listen_and_transcribe,
            daemon=True,
            name="stt-session-listen",
        ).start()

    def transcribe(
        self, audio_data: np.ndarray, sample_rate: int = 16000
    ) -> TranscriptionResult:
        """Run Whisper inference on an audio buffer.

        Never raises to the caller — returns an empty result on error.
        """
        if self._model is None:
            logger.error("Whisper model not loaded — call preload() first")
            return TranscriptionResult(
                text="", confidence=0.0, duration_ms=0, is_empty=True
            )

        try:
            if audio_data is None or (hasattr(audio_data, "__len__") and len(audio_data) == 0):
                return TranscriptionResult(
                    text="", confidence=0.0, duration_ms=0, is_empty=True
                )

            start = time.perf_counter()

            # Whisper expects float32 audio normalized to [-1, 1]
            if audio_data.dtype != np.float32:
                audio_data = audio_data.astype(np.float32)

            # Resample if needed
            if sample_rate != 16000:
                ratio = 16000 / sample_rate
                new_length = int(len(audio_data) * ratio)
                indices = np.linspace(0, len(audio_data) - 1, new_length).astype(int)
                audio_data = audio_data[indices]

            result = self._model.transcribe(audio_data, fp16=False)
            text = result.get("text", "").strip()
            duration_ms = int((time.perf_counter() - start) * 1000)

            return TranscriptionResult(
                text=text,
                confidence=1.0 if text else 0.0,
                duration_ms=duration_ms,
                is_empty=not bool(text),
            )
        except Exception as exc:
            logger.exception("Transcription failed: %s", exc)
            return TranscriptionResult(
                text="", confidence=0.0, duration_ms=0, is_empty=True
            )

    def listen_and_transcribe(
        self, max_duration: float | None = None
    ) -> TranscriptionResult:
        """Full pipeline: record audio → detect silence → transcribe.

        Parameters
        ----------
        max_duration : float | None
            Maximum recording time in seconds. Overrides the configured
            ``stt.max_recording`` value when provided.  Used by SafetyGate
            to enforce the shorter confirmation timeout window.

        Emits RECORDING_STARTED, RECORDING_STOPPED, TRANSCRIPTION_COMPLETE.
        """
        import sounddevice as sd

        bus.emit(EventType.RECORDING_STARTED, {})

        effective_max = max_duration if max_duration is not None else self._max_recording
        chunk_samples = int(self._sample_rate * _CHUNK_DURATION_MS / 1000)
        max_chunks = int(effective_max * 1000 / _CHUNK_DURATION_MS)
        silence_chunks_needed = int(self._silence_timeout * 1000 / _CHUNK_DURATION_MS)

        audio_chunks: list[np.ndarray] = []
        silence_count = 0
        recording_start = time.perf_counter()

        try:
            if self._input_device is None:
                self._input_device = resolve_input_device(self._config)

            with mic_lock:
                with sd.InputStream(
                    samplerate=self._sample_rate,
                    channels=1,
                    dtype="float32",
                    blocksize=chunk_samples,
                    device=self._input_device,
                ) as stream:
                    for _ in range(max_chunks):
                        chunk, _ = stream.read(chunk_samples)
                        audio_chunks.append(chunk.flatten())

                        rms = float(np.sqrt(np.mean(chunk**2)))
                        if rms < self._rms_silence_threshold:
                            silence_count += 1
                        else:
                            silence_count = 0

                        if silence_count >= silence_chunks_needed and len(audio_chunks) > silence_chunks_needed:
                            break

        except Exception as exc:
            logger.error("Recording failed: %s", exc)
            bus.emit(EventType.RECORDING_STOPPED, {"duration_ms": 0})
            return TranscriptionResult(
                text="", confidence=0.0, duration_ms=0, is_empty=True
            )

        recording_duration_ms = int((time.perf_counter() - recording_start) * 1000)
        bus.emit(EventType.RECORDING_STOPPED, {"duration_ms": recording_duration_ms})

        if not audio_chunks:
            return TranscriptionResult(
                text="", confidence=0.0, duration_ms=0, is_empty=True
            )

        audio_data = np.concatenate(audio_chunks)
        result = self.transcribe(audio_data, self._sample_rate)

        # Include duration_ms in the payload (recording + transcription time)
        total_duration_ms = int((time.perf_counter() - recording_start) * 1000)
        result = TranscriptionResult(
            text=result.text,
            confidence=result.confidence,
            duration_ms=total_duration_ms,
            is_empty=result.is_empty,
        )

        bus.emit(
            EventType.TRANSCRIPTION_COMPLETE,
            {"text": result.text, "confidence": result.confidence, "duration_ms": result.duration_ms},
        )
        return result
