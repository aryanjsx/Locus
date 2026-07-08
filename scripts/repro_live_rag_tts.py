"""Exact reproduction of live RAG TTS path with instrumentation."""

from __future__ import annotations

import logging
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("repro_rag_tts")


def run(timeout: float = 120.0) -> dict:
    import shutil
    import chromadb
    import aura.core.config_loader as cl
    from aura.core.ollama_client import OllamaClient
    from aura.core.intent_router import IntentRouter
    from aura.core.llm_brain import BrainController
    from aura.core.command_engine import CommandEngine
    from aura.core.event_bus import bus
    from aura.security.safety_gate import SafetyGate
    from aura.modules.tts import TTSEngine
    from aura.utils.mode_monitor import mode_monitor
    from aura.memory.context_retriever import retrieve_context, augment_prompt_with_rag
    from main import _VOICE_SYSTEM_PROMPT, _has_complete_sentence, _split_first_sentence

    cl._cache = None
    config = cl.load_config()
    tmp = Path(tempfile.mkdtemp(prefix="aura_live_rag_"))
    config["memory"] = dict(config.get("memory", {}))
    config["memory"]["persist_path"] = str(tmp / "chroma")
    config["routing"] = dict(config.get("routing", {}))
    config["routing"]["rag_confidence_threshold"] = 0.50
    config["routing"]["rag_rank_margin"] = 0.03

    QUERY = "what port does my project use for the admin API"
    UNIQUE_DOC = (
        "Kommy internal codename Azure Phoenix: the admin API listens exclusively on TCP port 7742 "
        "for dashboard operators; this port is not documented publicly."
    )

    ollama = OllamaClient(config)
    emb = ollama.embed(config["models"]["embeddings"], UNIQUE_DOC)
    client = chromadb.PersistentClient(path=config["memory"]["persist_path"])
    col = client.get_or_create_collection("aura_memory", metadata={"hnsw:space": "cosine"})
    col.add(ids=["azure_phoenix_port"], documents=[UNIQUE_DOC], embeddings=[emb])

    mode_monitor.start()
    router = IntentRouter(config, ollama, event_bus=bus)
    tts = TTSEngine(config)
    synth_log: list[dict] = []

    def wrap(name, fn):
        def inner(text):
            t0 = time.perf_counter()
            log.info("SYNTH %s START chars=%s", name, len(text))
            ok = fn(text)
            ms = int((time.perf_counter() - t0) * 1000)
            log.info("SYNTH %s END ok=%s ms=%s", name, ok, ms)
            synth_log.append({"backend": name, "ok": ok, "ms": ms})
            return ok

        return inner

    tts._try_edge_tts = wrap("edge-tts", tts._try_edge_tts)
    tts._try_piper = wrap("piper", tts._try_piper)
    tts._try_pyttsx3 = wrap("pyttsx3", tts._try_pyttsx3)
    tts.start()

    SafetyGate(bus, tts_engine=tts, stt_engine=None, config=config)
    brain = BrainController(config, bus, ollama)
    engine = CommandEngine(config, bus, SafetyGate(bus, tts_engine=tts, stt_engine=None, config=config))

    intent = router.classify(QUERY)
    plan = brain.handle_intent(intent)
    exec_result = engine.execute(plan)
    prompt = exec_result.data.get("prompt", "")
    model = exec_result.data.get("model", "")
    if exec_result.data.get("requires_rag"):
        chunks = retrieve_context(prompt, config, ollama)
        prompt = augment_prompt_with_rag(prompt, chunks)

    speak_count = 0
    t_stream = time.perf_counter()
    full = []
    sentence_buf = ""
    for token in ollama.chat_stream(
        model=model, prompt=prompt, system_prompt=_VOICE_SYSTEM_PROMPT, num_predict=200
    ):
        sentence_buf += token
        full.append(token)
        while _has_complete_sentence(sentence_buf):
            sentence, sentence_buf = _split_first_sentence(sentence_buf)
            sentence = sentence.strip()
            if sentence:
                speak_count += 1
                log.info("SPEAK #%s qsize=%s speaking=%s", speak_count, tts._queue.qsize(), tts._speaking.is_set())
                tts.speak(sentence)
    if sentence_buf.strip():
        speak_count += 1
        log.info("SPEAK leftover #%s", speak_count)
        tts.speak(sentence_buf.strip())

    stream_ms = int((time.perf_counter() - t_stream) * 1000)
    wait_t0 = time.perf_counter()
    tts.wait_until_idle(timeout=timeout)
    wait_ms = int((time.perf_counter() - wait_t0) * 1000)
    response = "".join(full)

    shutil.rmtree(tmp, ignore_errors=True)
    return {
        "mode": mode_monitor.current_mode,
        "speak_count": speak_count,
        "stream_ms": stream_ms,
        "wait_ms": wait_ms,
        "timed_out": wait_ms >= int(timeout * 1000) - 500,
        "speaking_at_end": tts._speaking.is_set(),
        "synth_log": synth_log,
        "has_7742": "7742" in response,
        "response_len": len(response),
    }


if __name__ == "__main__":
    results = []
    for i in range(5):
        log.info("=== REPRO %s/5 ===", i + 1)
        results.append(run())
        time.sleep(2)
    print("\n=== REPRO SUMMARY ===")
    for i, r in enumerate(results, 1):
        print(
            f"run{i}: mode={r['mode']} speaks={r['speak_count']} stream_ms={r['stream_ms']} "
            f"wait_ms={r['wait_ms']} timed_out={r['timed_out']} synth={r['synth_log']}"
        )
    sys.exit(1 if any(r["timed_out"] for r in results) else 0)
