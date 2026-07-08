"""
Minimal TTSEngine idle diagnostic — verbose logging for wait_until_idle() investigation.

Usage:
    python scripts/diagnose_tts_idle.py [--runs 5] [--text "Hello test"]
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

# Project root on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aura.core.config_loader import load_config
from aura.modules.tts import TTSEngine
from aura.utils.mode_monitor import mode_monitor

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s.%(msecs)03d %(levelname)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("diagnose_tts_idle")


def _install_probes(tts: TTSEngine) -> dict:
    """Wrap synthesis paths to record which backend ran and how long it took."""
    stats: dict = {
        "backend": None,
        "backend_ms": 0,
        "play_returned": False,
        "synth_success": False,
    }
    orig_synth = tts._synthesize_and_play
    orig_edge = tts._try_edge_tts
    orig_piper = tts._try_piper
    orig_pyttsx3 = tts._try_pyttsx3
    orig_play = tts._play_file

    def _wrap(name: str, fn):
        def _inner(*args, **kwargs):
            t0 = time.perf_counter()
            log.info("BACKEND_ENTER %s", name)
            try:
                ok = fn(*args, **kwargs)
            finally:
                elapsed = int((time.perf_counter() - t0) * 1000)
                log.info("BACKEND_EXIT %s elapsed_ms=%s", name, elapsed)
            if ok:
                stats["backend"] = name
                stats["backend_ms"] = elapsed
                stats["synth_success"] = True
            return ok

        return _inner

    tts._try_edge_tts = _wrap("edge-tts", orig_edge)
    tts._try_piper = _wrap("piper", orig_piper)
    tts._try_pyttsx3 = _wrap("pyttsx3", orig_pyttsx3)

    def _play_probe(filepath: str) -> None:
        t0 = time.perf_counter()
        log.info("PLAY_FILE_ENTER path=%s", filepath)
        orig_play(filepath)
        elapsed = int((time.perf_counter() - t0) * 1000)
        stats["play_returned"] = True
        log.info("PLAY_FILE_EXIT elapsed_ms=%s", elapsed)

    tts._play_file = _play_probe
    tts._synthesize_and_play = orig_synth  # use real dispatch
    return stats


def run_once(text: str, timeout: float) -> dict:
    import aura.core.config_loader as cl

    cl._cache = None
    config = load_config()

    mode_monitor.start()
    mode = mode_monitor.current_mode
    log.info("MODE_MONITOR mode=%s", mode)

    tts = TTSEngine(config)
    stats = _install_probes(tts)
    tts.start()

    t0 = time.perf_counter()
    q_before = tts._queue.qsize()
    speaking_before = tts._speaking.is_set()
    log.info(
        "PRE_SPEAK queue_size=%s speaking=%s worker_alive=%s",
        q_before,
        speaking_before,
        tts._worker_thread.is_alive() if tts._worker_thread else None,
    )

    tts.speak(text)
    q_after = tts._queue.qsize()
    log.info("POST_SPEAK queue_size=%s speaking=%s", q_after, tts._speaking.is_set())

    wait_t0 = time.perf_counter()
    tts.wait_until_idle(timeout=timeout)
    wait_ms = int((time.perf_counter() - wait_t0) * 1000)
    total_ms = int((time.perf_counter() - t0) * 1000)

    timed_out = wait_ms >= int(timeout * 1000) - 100
    log.info(
        "POST_WAIT wait_ms=%s total_ms=%s queue_empty=%s speaking=%s timed_out=%s",
        wait_ms,
        total_ms,
        tts._queue.empty(),
        tts._speaking.is_set(),
        timed_out,
    )

    return {
        "mode": mode,
        "backend": stats["backend"],
        "backend_ms": stats["backend_ms"],
        "play_returned": stats["play_returned"],
        "synth_success": stats["synth_success"],
        "wait_ms": wait_ms,
        "total_ms": total_ms,
        "timed_out": timed_out,
        "queue_empty": tts._queue.empty(),
        "speaking_set": tts._speaking.is_set(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--text", default="TTS idle probe.")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    results = []
    for i in range(args.runs):
        log.info("=== RUN %s/%s ===", i + 1, args.runs)
        results.append(run_once(args.text, args.timeout))
        time.sleep(0.5)

    print("\n=== SUMMARY ===")
    for i, r in enumerate(results, 1):
        print(
            f"run {i}: mode={r['mode']} backend={r['backend']} "
            f"backend_ms={r['backend_ms']} play_returned={r['play_returned']} "
            f"wait_ms={r['wait_ms']} timed_out={r['timed_out']} "
            f"speaking_at_end={r['speaking_set']}"
        )

    timeouts = sum(1 for r in results if r["timed_out"])
    print(f"timeouts: {timeouts}/{args.runs}")
    return 1 if timeouts else 0


if __name__ == "__main__":
    raise SystemExit(main())
