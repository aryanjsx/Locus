"""
AURA — Phase 2 Voice Pipeline Entry Point.

Ties all pipeline layers together via EventBus:
  WAKE_WORD_DETECTED  → STT (worker thread)
  TRANSCRIPTION_COMPLETE → IntentRouter
  INTENT_CLASSIFIED   → BrainController
  COMMAND_PLAN_READY  → CommandEngine
  EXECUTION_COMPLETE  → TTS / LLM streaming
  TTS_SPEAKING_FINISHED → IDLE

The Phase 1 CLI entry (aura/cli.py) remains available via `python -m aura`.
This file starts the full voice pipeline via `python main.py`.
"""

from __future__ import annotations

import logging
import sys
import threading
import time

from aura.core.config_loader import load_config
from aura.core.command_engine import CommandEngine
from aura.core.event_bus import EventType, bus
from aura.core.intent_router import IntentObject, IntentRouter, IntentType
from aura.core.llm_brain import BrainController
from aura.core.ollama_client import OllamaClient
from aura.core.pipeline_state import PipelineState, StateMachine
from aura.core.schemas import ExecutionResult
from aura.modules.stt import STTEngine
from aura.modules.tts import TTSEngine
from aura.modules.wake_word import WakeWordListener
from aura.security.safety_gate import SafetyGate
from aura.utils.mode_monitor import mode_monitor

BANNER = r"""
    ___   __  ______  ___
   /   | / / / / __ \/   |
  / /| |/ / / / /_/ / /| |
 / ___ / /_/ / _, _/ ___ |
/_/  |_\____/_/ |_/_/  |_|

 Phase 2 — Voice Pipeline Active
"""


_VOICE_SYSTEM_PROMPT = "Answer in 1 sentence. Be concise."


def _stream_to_tts(
    ollama: OllamaClient,
    tts: TTSEngine,
    model: str,
    prompt: str,
    system_prompt: str = _VOICE_SYSTEM_PROMPT,
) -> None:
    """Stream LLM tokens and send complete sentences to TTS as they form.

    This gives the user audible feedback within 1-3 seconds of the first
    generated sentence, instead of waiting for the entire response.
    """
    sentence_buf = ""
    full_text = ""
    sent_count = 0
    start = time.perf_counter()

    for token in ollama.chat_stream(
        model=model, prompt=prompt,
        system_prompt=system_prompt, num_predict=200,
    ):
        sentence_buf += token
        full_text += token

        while _has_complete_sentence(sentence_buf):
            sentence, sentence_buf = _split_first_sentence(sentence_buf)
            sentence = sentence.strip()
            if sentence:
                if sent_count == 0:
                    ttfb = int((time.perf_counter() - start) * 1000)
                    print(f"[PIPELINE] First sentence in {ttfb}ms")
                tts.speak(sentence)
                sent_count += 1

    leftover = sentence_buf.strip()
    if leftover:
        tts.speak(leftover)

    total = int((time.perf_counter() - start) * 1000)
    print(f"[PIPELINE] Streamed {len(full_text)} chars in {total}ms ({sent_count + (1 if leftover else 0)} segments)")


def _has_complete_sentence(text: str) -> bool:
    """Check if text contains at least one sentence-ending delimiter."""
    for ch in ".!?\n":
        idx = text.find(ch)
        if idx >= 0 and idx < len(text) - 1:
            return True
    return False


def _split_first_sentence(text: str) -> tuple[str, str]:
    """Split text at the first sentence boundary. Returns (sentence, remainder)."""
    best = len(text)
    for ch in ".!?\n":
        idx = text.find(ch)
        if 0 <= idx < best:
            best = idx
    split_at = best + 1
    return text[:split_at], text[split_at:]


def startup() -> None:
    """Full Phase 2 pipeline startup sequence."""
    print(BANNER)

    # 1. Load and validate config
    config = load_config()
    print("[1/8] Configuration loaded and validated")

    # 2. Start connectivity monitor
    mode_monitor.start()
    print(f"[2/8] Mode monitor started — {mode_monitor.current_mode}")

    # 3. Pre-load Whisper model
    stt = STTEngine(config)
    print("[3/8] Loading Whisper model (this may take a moment)...")
    stt.preload()
    print("[3/8] Whisper model ready")

    # 4. Ollama health check
    ollama = OllamaClient(config)
    if not ollama.health_check():
        print("\n[ERROR] Ollama not running. Start with: ollama serve")
        sys.exit(1)
    print("[4/8] Ollama reachable")

    # 5. Verify models and pre-warm the primary model
    available = ollama.list_models()
    models_cfg = config.get("models", {})
    primary_model = models_cfg.get("general", models_cfg.get("fast", ""))
    required = [models_cfg.get("fast"), models_cfg.get("general"), models_cfg.get("code")]
    for m in required:
        if m and m not in available:
            print(f"  [WARN] Model not pulled: {m} — run: ollama pull {m}")
    print(f"[5/8] Model check complete ({len(available)} models available)")
    if primary_model:
        print(f"[5/8] Pre-warming {primary_model}...")
        ollama.warmup(primary_model)
        print(f"[5/8] {primary_model} ready in RAM")

    # 6. Build pipeline components
    router = IntentRouter(config, ollama, event_bus=bus)
    tts = TTSEngine(config)
    safety_gate = SafetyGate(bus, tts_engine=tts, stt_engine=stt, config=config)
    brain = BrainController(config, bus, ollama)
    engine = CommandEngine(config, bus, safety_gate)
    state_machine = StateMachine()
    wake = WakeWordListener(config)
    wake.set_whisper_model(stt._model)
    tts.start()
    print("[6/8] Pipeline components built")

    # 7. Wire the event-driven pipeline
    # --- State machine transitions ---
    bus.subscribe(EventType.WAKE_WORD_DETECTED, lambda _: state_machine.transition(PipelineState.LISTENING))
    bus.subscribe(EventType.TRANSCRIPTION_COMPLETE, lambda _: state_machine.transition(PipelineState.CLASSIFYING))
    bus.subscribe(EventType.INTENT_CLASSIFIED, lambda _: state_machine.transition(PipelineState.THINKING))
    bus.subscribe(EventType.COMMAND_PLAN_READY, lambda _: state_machine.transition(PipelineState.EXECUTING))
    bus.subscribe(EventType.TTS_SPEAK_REQUEST, lambda _: state_machine.transition(PipelineState.SPEAKING))
    bus.subscribe(EventType.TTS_SPEAKING_FINISHED, lambda _: state_machine.transition(PipelineState.IDLE))

    # --- Wake word handler (non-blocking — spawns worker thread) ---
    def _on_wake_detected(payload) -> None:
        """Returns immediately. All blocking work in a daemon thread."""
        print("\n[PIPELINE] Wake triggered!")

        wake_data = payload.data if hasattr(payload, "data") else payload
        wake_command = wake_data.get("command", "").strip() if isinstance(wake_data, dict) else ""

        worker = threading.Thread(
            target=_run_pipeline_worker,
            args=(wake_command,),
            daemon=True,
            name="aura-pipeline-worker",
        )
        worker.start()

    def _run_pipeline_worker(wake_command: str) -> None:
        """Blocking pipeline work — runs in a daemon thread."""
        try:
            tts.interrupt()

            if wake_command:
                command_text = wake_command
                print(f'[PIPELINE] Command from wake: "{command_text}"')
            else:
                print("[PIPELINE] Recording... speak now")
                result = stt.listen_and_transcribe()
                print(
                    f'[PIPELINE] Transcription: "{result.text}" '
                    f"(empty={result.is_empty})"
                )
                if result.is_empty:
                    tts.speak("I didn't catch that. Try again.")
                    print("[PIPELINE] Ready — say 'Hey Kommy' or press CTRL+SPACE")
                    state_machine.force_idle()
                    return
                command_text = result.text

            # Classify intent
            print("[PIPELINE] Classifying intent...")
            t_classify = time.perf_counter()
            intent = router.classify(command_text)
            classify_ms = int((time.perf_counter() - t_classify) * 1000)
            print(
                f"[PIPELINE] Intent: {intent.intent_type.value} "
                f"(confidence={intent.confidence}, {classify_ms}ms)"
            )

            # Brain builds a CommandPlan (emits COMMAND_PLAN_READY internally)
            plan = brain.handle_intent(intent)
            print(f"[PIPELINE] Plan: executor={plan.executor} action={plan.action}")

            # Execute the plan
            exec_result = engine.execute(plan)

            # Handle result — either speak output or stream from LLM
            if exec_result.data and isinstance(exec_result.data, dict) and exec_result.data.get("mode") == "llm_stream":
                model = exec_result.data.get("model", "")
                prompt = exec_result.data.get("prompt", "")
                if model and prompt:
                    system_prompt = _VOICE_SYSTEM_PROMPT
                    if intent.intent_type == IntentType.CODE_GENERATION:
                        system_prompt = "Summarize the code solution in 1 sentence."
                    print(f"[PIPELINE] Streaming from {model}...")
                    _stream_to_tts(ollama, tts, model, prompt, system_prompt)
            elif exec_result.output:
                tts.speak(exec_result.output)

            tts.wait_until_idle()
            print("[PIPELINE] Ready — say 'Hey Kommy' or press CTRL+SPACE")

        except Exception as exc:
            logger = logging.getLogger("aura.main")
            logger.exception("Pipeline worker error: %s", exc)
            bus.emit(EventType.SYSTEM_ERROR, {
                "error": str(exc),
                "module": "pipeline_worker",
            })
            try:
                tts.speak("Sorry, something went wrong. Please try again.")
                tts.wait_until_idle()
            except Exception:
                pass
        finally:
            state_machine.force_idle()

    bus.subscribe(EventType.WAKE_WORD_DETECTED, _on_wake_detected)

    # --- WakeWordListener auto-arm/disarm via state ---
    def _on_wake_pause(payload) -> None:
        """Pause wake listener when pipeline is active."""
        wake.pause()

    def _on_wake_resume(payload) -> None:
        """Resume wake listener when pipeline is idle."""
        wake.resume()

    bus.subscribe(EventType.WAKE_WORD_DETECTED, _on_wake_pause)
    bus.subscribe(EventType.TTS_SPEAKING_FINISHED, _on_wake_resume)

    print("[7/8] Event pipeline wired")

    # 8. Start wake word listener
    wake.start()
    logger = logging.getLogger("aura.main")
    logger.info(
        "[AURA] Wake word: Tier 1=Whisper (hey kommy) | "
        "Tier 2=openwakeword | Tier 3=CTRL+SPACE"
    )
    wake.pause()
    tts.speak("Kommy online. Say Hey Kommy or press Control Space to activate.")
    tts.wait_until_idle()
    wake.resume()
    print("[8/8] Wake word listener active")
    print("\n" + "=" * 50)
    print("AURA Phase 2 Pipeline running.")
    print("Say 'Hey Kommy' or press CTRL+SPACE to activate.")
    print("Press CTRL+C to shut down.")
    print("=" * 50)

    # Keep main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n\nShutting down Kommy...")
        wake.stop()
        mode_monitor.stop()
        print("Goodbye.")


if __name__ == "__main__":
    startup()
