"""Regression tests for TTSEngine wait_until_idle() signaling."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from aura.core.config_loader import load_config
from aura.modules.tts import TTSEngine


@pytest.fixture()
def config():
    return load_config()


def _mock_tts(config, synth_fn=None):
    tts = TTSEngine(config)
    tts._try_edge_tts = MagicMock(return_value=False)
    tts._try_piper = MagicMock(return_value=False)
    if synth_fn is None:
        tts._try_pyttsx3 = MagicMock(return_value=True)
    else:
        tts._try_pyttsx3 = synth_fn
    tts.start()
    return tts


class TestWaitUntilIdle:
    def test_returns_true_when_synthesis_completes(self, config):
        tts = _mock_tts(config)
        tts.speak("Quick test.")
        assert tts.wait_until_idle(timeout=5.0) is True
        assert tts._queue.empty()
        assert not tts._speaking.is_set()

    def test_returns_false_when_synthesis_exceeds_timeout(self, config):
        def _slow(_text: str) -> bool:
            time.sleep(2.0)
            return True

        tts = _mock_tts(config, synth_fn=_slow)
        tts.speak("Slow test.")
        assert tts.wait_until_idle(timeout=0.3) is False
        assert tts._speaking.is_set()
        tts.interrupt()
        time.sleep(0.2)

    def test_timeout_log_includes_queue_and_speaking_state(self, config, caplog):
        def _slow(_text: str) -> bool:
            time.sleep(2.0)
            return True

        tts = _mock_tts(config, synth_fn=_slow)
        tts.speak("Slow test.")
        with caplog.at_level("WARNING", logger="aura.tts"):
            tts.wait_until_idle(timeout=0.2)
        assert any("queue_empty=" in r.message and "speaking=" in r.message for r in caplog.records)
