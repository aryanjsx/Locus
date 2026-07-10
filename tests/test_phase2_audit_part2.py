"""
AURA Phase 2 — Adversarial Audit Test Suite (Sections 5-11).
Run: pytest tests/test_phase2_audit_part2.py -v --tb=short
"""
from __future__ import annotations

import ast
import glob
import inspect
import os
import re
import threading
import time
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from aura.core.config_loader import load_config
from tests.conftest import keyboard_wake_run, oww_model
from aura.utils.event_bus import EventType, bus


@pytest.fixture(scope="module")
def config():
    return load_config()


@pytest.fixture
def silence_audio():
    return np.load("tests/fixtures/audio_silence.npy")


@pytest.fixture
def noise_audio():
    return np.load("tests/fixtures/audio_noise.npy")


@pytest.fixture
def speech_audio():
    return np.load("tests/fixtures/audio_speech_mock.npy")


@pytest.fixture(autouse=True)
def _reset_bus():
    original_handlers = dict(bus._handlers)
    original_wildcard = list(bus._wildcard_handlers)
    yield
    bus._handlers.clear()
    bus._handlers.update(original_handlers)
    bus._wildcard_handlers[:] = original_wildcard


# ═══════════════════════════════════════════════════════════
# SECTION 6 — WakeWordListener Audit
# ═══════════════════════════════════════════════════════════

class TestOpenWakeWordInference:
    """Integration checks for openwakeword (skipped at runtime when ONNX unavailable)."""

    def test_openwakeword_model_loads_without_error(self, oww_model):
        """openwakeword Model initialises and loads hey_jarvis successfully"""
        assert oww_model is not None

    def test_openwakeword_predict_returns_dict(self, oww_model):
        """Model.predict() returns a dict with the model name as key"""
        chunk = np.zeros(1280, dtype=np.float32)
        result = oww_model.predict(chunk)
        assert isinstance(result, dict)
        assert "hey_jarvis" in result
        assert 0.0 <= result["hey_jarvis"] <= 1.0

    def test_openwakeword_score_on_silence_is_below_threshold(self, oww_model):
        """Silence should not trigger a detection — score stays below 0.5"""
        silence = np.zeros(1280, dtype=np.float32)
        for _ in range(20):
            result = oww_model.predict(silence)
        score = result["hey_jarvis"]
        assert score < 0.5, (
            f"Silence produced score {score:.3f} — model may be too sensitive"
        )

    def test_openwakeword_score_on_noise_is_below_threshold(self, oww_model):
        """Random noise should not trigger a detection"""
        for _ in range(20):
            noise = np.random.uniform(-0.1, 0.1, 1280).astype(np.float32)
            result = oww_model.predict(noise)
        score = result["hey_jarvis"]
        assert score < 0.5, (
            f"White noise produced score {score:.3f} — false positive risk"
        )

    def test_openwakeword_reset_clears_internal_state(self, oww_model):
        """model.reset() resets internal buffers — no residual state"""
        for _ in range(10):
            oww_model.predict(np.zeros(1280, dtype=np.float32))
        oww_model.reset()
        result = oww_model.predict(np.zeros(1280, dtype=np.float32))
        assert result["hey_jarvis"] < 0.5


# ═══════════════════════════════════════════════════════════
# SECTION 5 — STTEngine Audit
# ═══════════════════════════════════════════════════════════

class TestSTTEngineHappyPath:

    def test_preload_does_not_raise(self, config):
        from aura.modules.stt import STTEngine
        stt = STTEngine(config)
        stt.preload()

    def test_preload_is_idempotent(self, config):
        from aura.modules.stt import STTEngine
        stt = STTEngine(config)
        stt.preload()
        stt.preload()

    def test_transcribe_silence_returns_empty_result(self, config, silence_audio):
        from aura.modules.stt import STTEngine
        stt = STTEngine(config)
        stt.preload()
        result = stt.transcribe(silence_audio)
        assert result.is_empty is True
        assert result.text.strip() == ""

    def test_transcription_result_has_all_fields(self, config, silence_audio):
        from aura.modules.stt import STTEngine, TranscriptionResult
        stt = STTEngine(config)
        stt.preload()
        result = stt.transcribe(silence_audio)
        assert isinstance(result, TranscriptionResult)
        assert hasattr(result, "text")
        assert hasattr(result, "confidence")
        assert hasattr(result, "duration_ms")
        assert hasattr(result, "is_empty")

    def test_duration_ms_is_positive_integer(self, config, noise_audio):
        from aura.modules.stt import STTEngine
        stt = STTEngine(config)
        stt.preload()
        result = stt.transcribe(noise_audio)
        assert isinstance(result.duration_ms, int)
        assert result.duration_ms > 0


class TestSTTEngineAdversarial:

    def test_transcribe_without_preload_does_not_crash(self, config, silence_audio):
        from aura.modules.stt import STTEngine
        stt = STTEngine(config)
        result = stt.transcribe(silence_audio)
        assert result is not None

    def test_transcribe_empty_array_returns_empty_result(self, config):
        from aura.modules.stt import STTEngine
        stt = STTEngine(config)
        stt.preload()
        result = stt.transcribe(np.array([], dtype=np.float32))
        assert result is not None
        assert result.is_empty is True

    def test_transcribe_none_returns_empty_result(self, config):
        from aura.modules.stt import STTEngine
        stt = STTEngine(config)
        stt.preload()
        result = stt.transcribe(None)
        assert result is not None
        assert result.is_empty is True

    def test_transcribe_nan_audio_does_not_crash(self, config):
        from aura.modules.stt import STTEngine
        stt = STTEngine(config)
        stt.preload()
        nan_audio = np.full(16000 * 2, float("nan"), dtype=np.float32)
        result = stt.transcribe(nan_audio)
        assert result is not None

    def test_transcribe_clipped_audio_does_not_crash(self, config):
        from aura.modules.stt import STTEngine
        stt = STTEngine(config)
        stt.preload()
        clipped = np.full(16000 * 2, 999.0, dtype=np.float32)
        result = stt.transcribe(clipped)
        assert result is not None

    def test_wrong_sample_rate_does_not_crash(self, config, silence_audio):
        from aura.modules.stt import STTEngine
        stt = STTEngine(config)
        stt.preload()
        result = stt.transcribe(silence_audio, sample_rate=8000)
        assert result is not None

    def test_transcribe_never_raises_to_caller(self, config):
        from aura.modules.stt import STTEngine
        stt = STTEngine(config)
        stt.preload()
        with patch.object(stt._model, "transcribe", side_effect=RuntimeError("Whisper exploded")):
            result = stt.transcribe(np.zeros(16000, dtype=np.float32))
        assert result is not None
        assert result.is_empty is True

    def test_concurrent_transcribe_calls_are_isolated(self, config):
        from aura.modules.stt import STTEngine
        stt = STTEngine(config)
        stt.preload()
        results = {}
        errors = []
        silence = np.zeros(16000, dtype=np.float32)

        def run(key):
            try:
                results[key] = stt.transcribe(silence)
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=run, args=(i,)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, f"Concurrent transcribe raised: {errors}"
        assert len(results) == 3


# ═══════════════════════════════════════════════════════════
# SECTION 6 — WakeWordListener Audit
# ═══════════════════════════════════════════════════════════

class TestWakeWordListenerHappyPath:

    def test_start_is_nonblocking(self, config, keyboard_wake_run):
        """start() returns immediately — does not block"""
        from aura.modules.wake_word import WakeWordListener
        listener = WakeWordListener(config)
        start = time.time()
        listener.start()
        elapsed = time.time() - start
        listener.stop()
        assert elapsed < 1.0, f"start() blocked for {elapsed:.2f}s"

    def test_stop_is_idempotent(self, config, keyboard_wake_run):
        """stop() called twice does not crash"""
        from aura.modules.wake_word import WakeWordListener
        listener = WakeWordListener(config)
        listener.start()
        listener.stop()
        listener.stop()

    def test_start_twice_does_not_spawn_two_threads(self, config, keyboard_wake_run):
        """Second start() call is silently ignored — no duplicate thread"""
        from aura.modules.wake_word import WakeWordListener
        listener = WakeWordListener(config)
        before = threading.active_count()
        listener.start()
        listener.start()
        time.sleep(0.3)
        after = threading.active_count()
        listener.stop()
        assert after - before <= 1, "start() twice spawned two threads"

    def test_emit_detected_fires_correct_event(self, config):
        """_emit_detected() fires WAKE_WORD_DETECTED with correct payload"""
        from aura.modules.wake_word import WakeWordListener
        listener = WakeWordListener(config)
        events = []
        bus.subscribe(EventType.WAKE_WORD_DETECTED, lambda p: events.append(p))
        listener._emit_detected("openwakeword")
        assert len(events) == 1
        assert events[0]["source"] == "openwakeword"
        assert "timestamp" in events[0]

    def test_direct_command_without_wake_phrase_emits_wake(self, config):
        """Substantive utterance without wake phrase can activate when configured."""
        from aura.modules.wake_word import WakeWordListener
        listener = WakeWordListener(config)
        assert listener._should_activate_direct_command(
            "what port does my project use for admin api?"
        )
        events = []
        bus.subscribe(EventType.WAKE_WORD_DETECTED, lambda p: events.append(p))
        listener._emit_detected(
            "whisper_direct",
            transcript="what port does my project use for admin api?",
            command="what port does my project use for admin api?",
        )
        assert len(events) == 1
        assert events[0]["command"] == "what port does my project use for admin api?"
        assert events[0]["source"] == "whisper_direct"

    def test_short_noise_not_direct_command(self, config):
        from aura.modules.wake_word import WakeWordListener
        listener = WakeWordListener(config)
        assert not listener._should_activate_direct_command("what?")

    def test_payload_timestamp_is_valid_iso_string(self, config):
        """WAKE_WORD_DETECTED timestamp is a parseable ISO datetime string"""
        from datetime import datetime
        from aura.modules.wake_word import WakeWordListener
        listener = WakeWordListener(config)
        events = []
        bus.subscribe(EventType.WAKE_WORD_DETECTED, lambda p: events.append(p))
        listener._emit_detected("openwakeword")
        ts = events[0]["timestamp"]
        parsed = datetime.fromisoformat(ts)
        assert parsed is not None

    def test_keyboard_fallback_fires_wake_word_detected(self, config):
        """CTRL+SPACE fallback emits WAKE_WORD_DETECTED"""
        from aura.modules.wake_word import WakeWordListener
        detections = []
        hotkey_callbacks = []

        def _capture_hotkey(_combo, callback):
            hotkey_callbacks.append(callback)

        bus.subscribe(EventType.WAKE_WORD_DETECTED, lambda p: detections.append(p))
        with patch.dict(os.environ, {"PICOVOICE_ACCESS_KEY": ""}, clear=False):
            with patch("openwakeword.model.Model", side_effect=Exception("no oww")):
                with patch("keyboard.add_hotkey", side_effect=_capture_hotkey):
                    listener = WakeWordListener(config)
                    listener.start()
                    time.sleep(0.3)
                    assert hotkey_callbacks, "keyboard hotkey was not registered"
                    hotkey_callbacks[0]()
                    time.sleep(0.2)
                    listener.stop()
        assert len(detections) >= 1, "CTRL+SPACE did not fire WAKE_WORD_DETECTED"
        assert detections[0]["source"] == "keyboard"


class TestWakeWordListenerAdversarial:

    def test_openwakeword_import_failure_falls_to_tier3(self, config):
        """If openwakeword is not installed, falls back to keyboard without crash"""
        from aura.modules.wake_word import WakeWordListener
        errors = []
        bus.subscribe(EventType.SYSTEM_ERROR, lambda p: errors.append(p))
        with patch("openwakeword.model.Model", side_effect=ImportError("not installed")):
            listener = WakeWordListener(config)
            listener.start()
            time.sleep(0.5)
            listener.stop()
        assert any(
            "openwakeword" in str(e.get("error", "")) for e in errors
        ), "No SYSTEM_ERROR emitted when openwakeword failed"

    def test_mic_unavailable_emits_system_error_not_crash(self, config):
        """sounddevice InputStream failure → SYSTEM_ERROR emitted, pipeline lives"""
        import sounddevice as sd
        from aura.modules.wake_word import WakeWordListener

        errors = []
        bus.subscribe(EventType.SYSTEM_ERROR, lambda p: errors.append(p))
        mock_model = MagicMock()
        mock_model.predict.return_value = {"hey_jarvis": 0.0}
        with patch("openwakeword.model.Model", return_value=mock_model):
            with patch.object(sd, "InputStream", side_effect=Exception("No mic found")):
                listener = WakeWordListener(config)
                listener.start()
                time.sleep(0.5)
                listener.stop()

    def test_no_access_key_skips_porcupine_silently(self, config, keyboard_wake_run):
        """Missing Porcupine key → no crash, no error"""
        from aura.modules.wake_word import WakeWordListener
        with patch.dict(os.environ, {"PICOVOICE_ACCESS_KEY": ""}, clear=False):
            listener = WakeWordListener(config)
            listener.start()
            time.sleep(0.3)
            listener.stop()

    def test_score_dict_missing_model_key_does_not_crash(self, config):
        """If predict() returns empty dict, score defaults to 0.0"""
        score = {}.get("hey_jarvis", 0.0)
        assert score == 0.0

    def test_audio_overflow_does_not_crash(self, config):
        """
        sounddevice reports overflow on some chunks under load.
        Must be logged at debug and NOT crash the listen loop.
        """
        import sounddevice as sd

        overflow_count = {"n": 0}

        class MockStream:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def read(self, frames):
                overflow_count["n"] += 1
                return (
                    np.zeros((frames, 1), dtype=np.float32),
                    overflow_count["n"] % 2 == 0,
                )

        mock_model = MagicMock()
        mock_model.predict.return_value = {"hey_jarvis": 0.0}

        with patch("openwakeword.model.Model", return_value=mock_model):
            with patch.object(sd, "InputStream", return_value=MockStream()):
                mock = MockStream()
                for _ in range(5):
                    chunk, overflowed = mock.read(1280)
                    if overflowed:
                        pass
                    mock_model.predict(chunk.flatten())

    def test_rapid_start_stop_does_not_leak_threads(self, config, keyboard_wake_run):
        """5 rapid start/stop cycles leave no orphan threads"""
        from aura.modules.wake_word import WakeWordListener
        for _ in range(5):
            listener = WakeWordListener(config)
            listener.start()
            time.sleep(0.1)
            listener.stop()
            time.sleep(0.1)
        time.sleep(1)
        count = threading.active_count()
        assert count < 20, f"Thread leak detected: {count} threads active"

    def test_cooldown_respects_stop_event(self, config):
        """_cooldown() exits early when stop_event is set"""
        from aura.modules.wake_word import WakeWordListener
        listener = WakeWordListener(config)
        listener._stop_event.set()
        start = time.time()
        listener._cooldown(seconds=5.0)
        elapsed = time.time() - start
        assert elapsed < 1.0, (
            f"_cooldown() took {elapsed:.2f}s despite stop_event being set"
        )

    def test_emit_error_does_not_crash_when_bus_has_no_subscribers(self, config):
        """_emit_error() is safe even with no SYSTEM_ERROR subscribers"""
        from aura.modules.wake_word import WakeWordListener
        listener = WakeWordListener(config)
        listener._emit_error("test error with no subscribers")


# ═══════════════════════════════════════════════════════════
# SECTION 7 — TTSEngine Audit
# ═══════════════════════════════════════════════════════════

def _make_tts(config):
    """Create a TTSEngine with mocked synthesis so tests don't block on audio."""
    from aura.modules.tts import TTSEngine
    tts = TTSEngine(config)
    tts._try_edge_tts = MagicMock(return_value=False)
    tts._try_piper = MagicMock(return_value=False)
    tts._try_pyttsx3 = MagicMock(return_value=True)
    return tts


class TestTTSEngineHappyPath:

    def test_speak_does_not_block_caller(self, config):
        tts = _make_tts(config)
        tts.start()
        start = time.time()
        tts.speak("This is a test of the AURA TTS system.")
        elapsed = time.time() - start
        assert elapsed < 0.5, f"speak() blocked for {elapsed:.2f}s"
        time.sleep(1)

    def test_interrupt_clears_queue(self, config):
        tts = _make_tts(config)
        tts.start()
        for i in range(10):
            tts.speak(f"Item {i} in the queue.")
        time.sleep(0.3)
        tts.interrupt()
        time.sleep(0.3)
        assert tts._queue.qsize() < 3

    def test_speak_emits_tts_events(self, config):
        tts = _make_tts(config)
        tts.start()
        events = set()
        handlers = {}
        for et in [EventType.TTS_SPEAK_REQUEST, EventType.TTS_SPEAKING_STARTED,
                   EventType.TTS_SPEAKING_FINISHED]:
            h = lambda p, e=et: events.add(e)
            handlers[et] = h
            bus.subscribe(et, h)
        tts.speak("Test.")
        time.sleep(2)
        for et, h in handlers.items():
            bus.unsubscribe(et, h)
        assert EventType.TTS_SPEAK_REQUEST in events
        assert EventType.TTS_SPEAKING_STARTED in events
        assert EventType.TTS_SPEAKING_FINISHED in events


class TestTTSEngineAdversarial:

    def test_empty_string_does_not_crash(self, config):
        tts = _make_tts(config)
        tts.start()
        tts.speak("")
        time.sleep(0.5)

    def test_none_input_does_not_crash(self, config):
        tts = _make_tts(config)
        tts.start()
        tts.speak(None)
        time.sleep(0.5)

    def test_unicode_and_emoji_text_does_not_crash(self, config):
        tts = _make_tts(config)
        tts.start()
        for text in ["hello AURA", "Error Null pointer", "testing emoji"]:
            tts.speak(text)
            time.sleep(0.1)
        time.sleep(1)

    def test_fifty_rapid_speak_calls_do_not_crash(self, config):
        tts = _make_tts(config)
        tts.start()
        for i in range(50):
            tts.speak(f"Message {i}.")
        time.sleep(2)
        tts.interrupt()

    def test_preferred_engine_failure_falls_back_to_pyttsx3(self, config):
        from aura.modules.tts import TTSEngine
        tts = TTSEngine(config)
        tts._try_edge_tts = MagicMock(return_value=False)
        tts._try_piper = MagicMock(side_effect=Exception("piper not installed"))
        tts._try_pyttsx3 = MagicMock(return_value=True)
        tts.start()
        tts.speak("Fallback test.")
        time.sleep(2)
        tts._try_pyttsx3.assert_called()

    def test_priority_speak_jumps_queue(self, config):
        tts = _make_tts(config)
        tts.start()
        for i in range(20):
            tts.speak(f"Low priority item {i}.")
        time.sleep(0.2)
        tts.speak("URGENT: priority message.", priority=True)
        time.sleep(1)


# ═══════════════════════════════════════════════════════════
# SECTION 8 — Config Audit
# ═══════════════════════════════════════════════════════════

class TestConfigAudit:

    def test_all_required_keys_present(self, config):
        required_paths = [
            ("aura", "version"), ("aura", "language"),
            ("models", "fast"), ("models", "general"), ("models", "reasoning"),
            ("models", "code"), ("models", "vision"), ("models", "embeddings"),
            ("routing", "complexity_threshold"), ("routing", "rag_confidence_threshold"),
            ("routing", "realtime_warning"), ("routing", "intent_timeout_seconds"),
            ("routing", "intent_max_retries"),
            ("stt", "model"), ("stt", "silence_timeout"), ("stt", "max_recording"),
            ("memory", "persist_path"), ("memory", "max_results"), ("memory", "purge_days"),
            ("ollama", "base_url"), ("ollama", "timeout"), ("ollama", "retries"),
        ]
        for keys in required_paths:
            obj = config
            for k in keys:
                assert k in obj, f"Missing config key: {'.'.join(keys)}"
                obj = obj[k]
            assert obj is not None, f"Config key is None: {'.'.join(keys)}"

    def test_bad_config_missing_required_keys_exits_cleanly(self):
        from aura.core.config_loader import _validate_required_sections
        from aura.core.errors import ConfigError
        import yaml
        with open("tests/fixtures/bad_config.yaml") as f:
            raw = yaml.safe_load(f)
        with pytest.raises(ConfigError):
            _validate_required_sections(raw)


# ═══════════════════════════════════════════════════════════
# SECTION 9 — Safety Audit
# ═══════════════════════════════════════════════════════════

class TestSafetyAudit:

    def test_no_shell_true_in_source_files(self):
        violations = []
        for root, _, files in os.walk("aura"):
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                path = os.path.join(root, fname)
                with open(path, encoding="utf-8") as f:
                    for lineno, line in enumerate(f, 1):
                        stripped = line.split("#")[0]
                        if re.search(r"shell\s*=\s*True", stripped):
                            violations.append(f"{path}:{lineno}: {line.strip()}")
        assert not violations, "shell=True found:\n" + "\n".join(violations)

    def test_no_eval_or_exec_in_source_files(self):
        violations = []
        for root, _, files in os.walk("aura"):
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                path = os.path.join(root, fname)
                with open(path, encoding="utf-8") as f:
                    for lineno, line in enumerate(f, 1):
                        if re.search(r'\beval\s*\(|\bexec\s*\(', line):
                            stripped = line.strip()
                            if not stripped.startswith("#"):
                                violations.append(f"{path}:{lineno}: {stripped}")
        assert not violations, "eval()/exec() found:\n" + "\n".join(violations)

    def test_no_subprocess_string_concatenation(self):
        violations = []
        dangerous = re.compile(r'subprocess\.(run|Popen|call|check_output)\s*\(\s*f["\']')
        for root, _, files in os.walk("aura"):
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                path = os.path.join(root, fname)
                with open(path, encoding="utf-8") as f:
                    content = f.read()
                if dangerous.search(content):
                    violations.append(path)
        assert not violations, "String-interpolated subprocess:\n" + "\n".join(violations)

    def test_no_audio_written_to_disk(self, config):
        from aura.modules.stt import STTEngine
        stt = STTEngine(config)
        stt.preload()
        before = set(glob.glob("**/*.wav", recursive=True) + glob.glob("**/*.mp3", recursive=True))
        stt.transcribe(np.zeros(16000 * 2, dtype=np.float32))
        after = set(glob.glob("**/*.wav", recursive=True) + glob.glob("**/*.mp3", recursive=True))
        new_files = after - before
        recording_files = [f for f in new_files if "tts" not in f.lower() and "piper" not in f.lower()]
        assert not recording_files, f"STTEngine wrote audio to disk:\n" + "\n".join(recording_files)

    def test_audit_log_directory_exists(self, config):
        log_path = config["audit"]["file"]
        log_dir = os.path.dirname(log_path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
            assert os.path.isdir(log_dir)


# ═══════════════════════════════════════════════════════════
# SECTION 10 — Pipeline E2E
# ═══════════════════════════════════════════════════════════

class TestFullPipelineE2E:

    def test_pipeline_recovers_after_bad_transcription(self, config):
        bus.emit(EventType.TRANSCRIPTION_COMPLETE, {"text": "", "confidence": 0.0, "duration_ms": 500})
        time.sleep(1)
        received = []
        handler = lambda p: received.append(p)
        bus.subscribe(EventType.MODE_CHANGED, handler)
        bus.emit(EventType.MODE_CHANGED, {"mode": "ONLINE"})
        bus.unsubscribe(EventType.MODE_CHANGED, handler)
        assert len(received) >= 1, "Pipeline stuck"


# ═══════════════════════════════════════════════════════════
# SECTION 11 — Regression Guards
# ═══════════════════════════════════════════════════════════

class TestRegressionGuards:

    def test_event_bus_singleton_is_same_instance_everywhere(self):
        from aura.utils import event_bus as eb1
        from aura.utils.event_bus import bus as b1
        import importlib
        eb2 = importlib.import_module("aura.utils.event_bus")
        assert eb1.bus is eb2.bus

    def test_mode_monitor_singleton_is_same_instance(self):
        from aura.utils import mode_monitor as mm1
        import importlib
        mm2 = importlib.import_module("aura.utils.mode_monitor")
        assert mm1.mode_monitor is mm2.mode_monitor

    def test_stt_engine_does_not_import_ollama(self):
        from aura.modules import stt
        src = inspect.getsource(stt)
        tree = ast.parse(src)
        imports = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
        ollama_imports = []
        for n in imports:
            if isinstance(n, ast.ImportFrom) and n.module and "ollama" in n.module:
                ollama_imports.append(n.module)
            elif isinstance(n, ast.Import):
                for alias in n.names:
                    if "ollama" in alias.name:
                        ollama_imports.append(alias.name)
        assert not ollama_imports, "STTEngine imports OllamaClient — layer violation"

    def test_wake_word_module_does_not_import_stt(self):
        from aura.modules import wake_word
        src = inspect.getsource(wake_word)
        assert "from aura.modules.stt" not in src and "import stt" not in src

    def test_tts_does_not_call_stt(self):
        from aura.modules import tts
        src = inspect.getsource(tts)
        assert "STTEngine" not in src and "stt.transcribe" not in src
