"""
Full-pipeline-context TTS diagnostic — mirrors live RAG test speak/wait pattern.
"""

from __future__ import annotations

import logging
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("diagnose_tts_pipeline")

RESPONSE = (
    "Your project uses TCP port 7742 for the admin API, "
    "which is not publicly documented."
)


def _split_sentences_like_main(text: str) -> list[str]:
    from main import _has_complete_sentence, _split_first_sentence

    sentences = []
    buf = ""
    for ch in text:
        buf += ch
        while _has_complete_sentence(buf):
            sentence, buf = _split_first_sentence(buf)
            sentence = sentence.strip()
            if sentence:
                sentences.append(sentence)
    if buf.strip():
        sentences.append(buf.strip())
    return sentences


def run_once(timeout: float = 120.0) -> dict:
    import aura.core.config_loader as cl
    from aura.core.command_engine import CommandEngine
    from aura.core.event_bus import bus
    from aura.core.llm_brain import BrainController
    from aura.core.ollama_client import OllamaClient
    from aura.core.intent_router import IntentRouter
    from aura.modules.tts import TTSEngine
    from aura.security.safety_gate import SafetyGate
    from aura.utils.mode_monitor import mode_monitor
    from main import _VOICE_SYSTEM_PROMPT

    cl._cache = None
    config = load_config()
    mode_monitor.start()

    ollama = OllamaClient(config)
    router = IntentRouter(config, ollama, event_bus=bus)
    tts = TTSEngine(config)

    backend_log: list[str] = []
    orig_edge = tts._try_edge_tts
    orig_piper = tts._try_piper
    orig_pyttsx3 = tts._try_pyttsx3

    def _wrap(name, fn):
        def _inner(text):
            t0 = time.perf_counter()
            log.info("SYNTH_START backend=%s len=%s", name, len(text))
            ok = fn(text)
            log.info(
                "SYNTH_END backend=%s ok=%s ms=%s",
                name,
                ok,
                int((time.perf_counter() - t0) * 1000),
            )
            if ok:
                backend_log.append(name)
            return ok

        return _inner

    tts._try_edge_tts = _wrap("edge-tts", orig_edge)
    tts._try_piper = _wrap("piper", orig_piper)
    tts._try_pyttsx3 = _wrap("pyttsx3", orig_pyttsx3)
    tts.start()

    SafetyGate(bus, tts_engine=tts, stt_engine=None, config=config)
    BrainController(config, bus, ollama)
    CommandEngine(config, bus, SafetyGate(bus, tts_engine=tts, stt_engine=None, config=config))

    sentences = _split_sentences_like_main(RESPONSE)
    log.info("MODE=%s sentences=%s", mode_monitor.current_mode, sentences)

    for i, sentence in enumerate(sentences):
        log.info("SPEAK_ENQUEUE #%s: %r", i, sentence[:60])
        tts.speak(sentence)

    wait_t0 = time.perf_counter()
    tts.wait_until_idle(timeout=timeout)
    wait_ms = int((time.perf_counter() - wait_t0) * 1000)

    result = {
        "mode": mode_monitor.current_mode,
        "backends": list(backend_log),
        "sentence_count": len(sentences),
        "wait_ms": wait_ms,
        "timed_out": wait_ms >= int(timeout * 1000) - 500,
        "speaking_at_end": tts._speaking.is_set(),
        "queue_empty": tts._queue.empty(),
    }
    log.info("RESULT %s", result)
    return result


def main() -> int:
    from aura.core.config_loader import load_config

    load_config()
    results = []
    for i in range(5):
        log.info("=== PIPELINE RUN %s/5 ===", i + 1)
        results.append(run_once())
        time.sleep(1)

    print("\n=== PIPELINE SUMMARY ===")
    for i, r in enumerate(results, 1):
        print(
            f"run {i}: mode={r['mode']} backends={r['backends']} "
            f"sentences={r['sentence_count']} wait_ms={r['wait_ms']} "
            f"timed_out={r['timed_out']} speaking={r['speaking_at_end']}"
        )
    timeouts = sum(1 for r in results if r["timed_out"])
    return 1 if timeouts else 0


if __name__ == "__main__":
    raise SystemExit(main())
