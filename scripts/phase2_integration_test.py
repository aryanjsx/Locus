"""
AURA Phase 2 — Full Integration Test.

Requires:
  - Ollama running (`ollama serve`)
  - At minimum, the fast model pulled
  - Microphone connected (for STT test)

Run with: python scripts/phase2_integration_test.py
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aura.core.config_loader import load_config
from aura.core.intent_router import IntentRouter
from aura.schemas.intent import IntentType
from aura.core.ollama_client import OllamaClient
from aura.modules.stt import STTEngine
from aura.modules.tts import TTSEngine
from aura.utils.mode_monitor import mode_monitor


def main() -> None:
    config = load_config()
    ollama = OllamaClient(config)
    router = IntentRouter(config, ollama)
    stt = STTEngine(config)
    tts = TTSEngine(config)

    mode_monitor.start()
    stt.preload()
    tts.start()

    print("=" * 50)
    print("AURA Phase 2 — Integration Test")
    print("=" * 50)

    print(f"\n[1] Mode: {mode_monitor.current_mode}")

    assert ollama.health_check(), "Ollama not running"
    print("[2] Ollama reachable")

    cases = [
        ("Open VS Code", IntentType.SYSTEM_COMMAND),
        ("Write a bubble sort in Python", IntentType.CODE_GENERATION),
        ("What is dependency injection?", IntentType.GENERAL_KNOWLEDGE),
        ("Push my code to GitHub", IntentType.DEV_TASK),
        ("What's on my screen?", IntentType.VISION_TASK),
    ]
    passed = 0
    for text, expected in cases:
        result = router.classify(text)
        status = "PASS" if result.intent_type == expected else "FAIL"
        print(
            f"  [{status}] '{text}' -> {result.intent_type} "
            f"(expected: {expected}, confidence: {result.confidence:.2f})"
        )
        if result.intent_type == expected:
            passed += 1

    print(f"[3] Router: {passed}/{len(cases)} correct")
    assert passed >= 4, f"Router accuracy too low: {passed}/{len(cases)}"

    tts.speak("Phase 2 integration test complete. AURA is online.")
    time.sleep(5)
    print("[4] TTS spoke — confirm you heard audio")

    print("\nAll Phase 2 checks passed.")
    mode_monitor.stop()


if __name__ == "__main__":
    main()
