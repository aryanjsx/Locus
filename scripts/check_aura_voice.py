"""Full voice/wake stack self-check. Run: python scripts/check_aura_voice.py"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import sounddevice as sd

FAILURES: list[str] = []
WARNINGS: list[str] = []
OK: list[str] = []


def ok(msg: str) -> None:
    OK.append(msg)
    print(f"  OK  {msg}")


def warn(msg: str) -> None:
    WARNINGS.append(msg)
    print(f"  WARN {msg}")


def fail(msg: str) -> None:
    FAILURES.append(msg)
    print(f"  FAIL {msg}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> int:
    section("Audio devices")
    try:
        default_in, default_out = sd.default.device
        ok(f"Default input device index: {default_in}")
        inputs = []
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                inputs.append((i, d["name"]))
                tag = " (DEFAULT)" if i == default_in else ""
                print(f"       [{i}] {d['name'][:72]}{tag}")
        if not inputs:
            fail("No input devices found")
        elif default_in is None or default_in < 0:
            fail(f"Invalid default input: {default_in}")
    except Exception as e:
        fail(f"sounddevice query failed: {e}")

    # Tier 2 fallback uses openWakeWord's stock hey_jarvis model — not "Hey Kommy".
    section("openWakeWord models")
    try:
        import openwakeword
        from openwakeword.utils import download_models

        models_dir = os.path.join(
            os.path.dirname(openwakeword.__file__), "resources", "models"
        )
        needed = ["hey_jarvis_v0.1.onnx", "embedding_model.onnx", "melspectrogram.onnx"]
        missing = [n for n in needed if not os.path.isfile(os.path.join(models_dir, n))]
        if missing:
            warn(f"Missing models {missing} — downloading...")
            download_models()
            missing = [n for n in needed if not os.path.isfile(os.path.join(models_dir, n))]
        if missing:
            fail(f"Still missing after download: {missing}")
        else:
            ok("hey_jarvis + preprocessor ONNX models present")
    except Exception as e:
        fail(f"openwakeword: {e}")

    section("Config")
    try:
        from aura.core.config_loader import load_config

        config = load_config()
        ww = config.get("wake_word", {})
        ok(f"oww_model={ww.get('oww_model')}")
        ok(f"oww_threshold={ww.get('oww_threshold')}")
        ok(f"oww_patience={ww.get('oww_patience')}")
        ok(f"input_device={ww.get('input_device')}")
        threshold = float(ww.get("oww_threshold", 0.35))
        patience = int(ww.get("oww_patience", 2))
    except Exception as e:
        fail(f"config load: {e}")
        return 1

    section("Model inference (silence)")
    try:
        from openwakeword.model import Model

        fw = "onnx"
        try:
            import tflite_runtime  # noqa: F401
            fw = "tflite"
        except ImportError:
            warn("tflite-runtime not installed — using ONNX (expected on Python 3.14)")

        # hey_jarvis is the OWW stock model filename — unrelated to product wake phrase.
        model = Model(wakeword_models=["hey_jarvis"], inference_framework=fw)
        silence = np.zeros(1280, dtype=np.int16)
        for _ in range(25):
            model.predict(
                silence,
                patience={"hey_jarvis": patience},
                threshold={"hey_jarvis": threshold},
            )
        raw = list(model.prediction_buffer.get("hey_jarvis", []))
        peak = max(raw) if raw else 0.0
        triggered = model.predict(
            silence,
            patience={"hey_jarvis": patience},
            threshold={"hey_jarvis": threshold},
        )
        if triggered.get("hey_jarvis", 0) > 0:
            fail("Silence triggered wake word — threshold too low or model broken")
        elif peak >= threshold:
            warn(f"Silence peak {peak:.3f} near threshold {threshold} — may false-trigger")
        else:
            ok(f"Silence peak {peak:.3f} < threshold {threshold} (no false trigger)")
    except Exception as e:
        fail(f"model inference: {e}")

    section("Live microphone (5 seconds — stay quiet, then speak)")
    try:
        from openwakeword.model import Model

        # hey_jarvis is the OWW stock model filename — unrelated to product wake phrase.
        model = Model(wakeword_models=["hey_jarvis"], inference_framework=fw)
        sr = 16000
        chunk = 1280
        patience_map = {"hey_jarvis": patience}
        threshold_map = {"hey_jarvis": threshold}
        from aura.utils.audio_input import resolve_input_device

        device = resolve_input_device(config)
        stream_kw = dict(
            samplerate=sr, channels=1, dtype="float32", blocksize=chunk, device=device
        )
        print(f"       Using mic [{device}] (auto-resolve)")

        rms_max = 0.0
        raw_peak = 0.0
        triggers = 0
        t_end = time.time() + 5.0

        with sd.InputStream(**stream_kw) as stream:
            while time.time() < t_end:
                data, _ = stream.read(chunk)
                flat = data.flatten()
                rms = float(np.sqrt(np.mean(flat**2)))
                rms_max = max(rms_max, rms)
                pcm = (np.clip(flat, -1.0, 1.0) * 32767).astype(np.int16)
                pred = model.predict(
                    pcm, patience=patience_map, threshold=threshold_map
                )
                buf = model.prediction_buffer.get("hey_jarvis")
                if buf:
                    raw_peak = max(raw_peak, float(buf[-1]))
                if pred.get("hey_jarvis", 0) > 0:
                    triggers += 1
                    model.reset()

        if rms_max < 0.0001:
            fail(
                f"Mic RMS max {rms_max:.6f} — no audio detected (wrong/disabled mic?)"
            )
        elif rms_max < 0.001:
            warn(f"Mic RMS very low ({rms_max:.6f}) — speak louder or check mic level")
        else:
            ok(f"Mic receiving audio (rms peak {rms_max:.4f})")

        # "Hey Jarvis" is openWakeWord's built-in hey_jarvis ONNX model name —
        # NOT the product wake phrase ("Hey Kommy"). This script exercises Tier 2
        # OWW fallback only; main.py Tier 1 uses Whisper + "hey kommy".
        ok(f"Live raw score peak (5s): {raw_peak:.3f} (need ~{threshold}+ for Hey Jarvis)")
        if triggers > 0:
            ok(f"Wake triggered {triggers} time(s) during test")
        elif raw_peak < threshold * 0.5:
            warn(
                # Intentionally "Hey Jarvis" — matches hey_jarvis model above, not Kommy.
                "No wake trigger and low scores — say 'Hey Jarvis' while running this "
                "OWW diagnostic (not main.py); or lower oww_threshold / fix input_device"
            )
        elif raw_peak >= threshold * 0.8:
            warn(
                # Intentionally "Hey Jarvis" — matches hey_jarvis model above, not Kommy.
                f"Scores near threshold ({raw_peak:.3f}) but patience not met — "
                "try clearer 'Hey Jarvis' + short pause (OWW Tier 2 test phrase)"
            )
        else:
            ok("No false trigger in 5s sample")
    except Exception as e:
        fail(f"live mic test: {e}")

    section("WakeWordListener + EventBus")
    try:
        from unittest.mock import patch
        from aura.modules.wake_word import WakeWordListener
        from aura.utils.event_bus import EventType, bus

        events = []
        bus.subscribe(EventType.WAKE_WORD_DETECTED, lambda p: events.append(p.data))

        listener = WakeWordListener(config)
        ok(f"threshold={listener._oww_threshold} patience={listener._oww_patience}")
        listener._emit_detected("test")
        if events and events[0].get("source") == "test":
            ok("WAKE_WORD_DETECTED event payload correct")
        else:
            fail("Event bus wake payload wrong")

        listener.start()
        time.sleep(2)
        alive = listener._thread.is_alive() if listener._thread else False
        listener.stop()
        if alive:
            ok("WakeWordListener thread started and stopped cleanly")
        else:
            fail("Wake thread died immediately — check logs for Tier 1 failure")
    except Exception as e:
        fail(f"WakeWordListener: {e}")

    section("Ollama + Whisper preload")
    try:
        from aura.core.ollama_client import OllamaClient
        from aura.modules.stt import STTEngine

        ollama = OllamaClient(config)
        if ollama.health_check():
            ok("Ollama reachable")
        else:
            fail("Ollama not running (ollama serve)")

        stt = STTEngine(config)
        stt.preload()
        ok("Whisper preloaded")
    except Exception as e:
        fail(f"Ollama/Whisper: {e}")

    section("Keyboard fallback (Tier 3)")
    try:
        import keyboard
        ok("keyboard package importable")
    except ImportError:
        fail("keyboard package missing — pip install keyboard")

    print("\n" + "=" * 50)
    print(f"OK: {len(OK)}  WARN: {len(WARNINGS)}  FAIL: {len(FAILURES)}")
    if FAILURES:
        print("\nFailures:")
        for f in FAILURES:
            print(f"  - {f}")
    if WARNINGS:
        print("\nWarnings:")
        for w in WARNINGS:
            print(f"  - {w}")
    print("=" * 50)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
