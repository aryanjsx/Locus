"""Experiment 4: peak RAM of the full chain (LLM + STT + TTS) loaded together.

Starts llama-server with the chosen model, loads the STT and TTS engines in this
process, runs one route, one transcription and one synthesis, and samples the
combined memory every 100 ms.

    uv run --group stt --group tts python -m bench.ram --model llama3.2-3b \
        --stt faster-whisper:base.en --tts piper:models/en_US-lessac-medium.onnx
"""

from __future__ import annotations

import argparse
import threading
import time
from pathlib import Path
from typing import Any

import psutil

from bench import stt as stt_bench
from bench import tts as tts_bench
from bench.actions import build_messages, build_schema
from bench.llama import LlamaServer, find_server, load_models
from bench.record import AUDIO_DIR
from bench.results import make_run_dir, write_json

BUDGET_GB = {"8gb": 3.0, "16gb": 5.0}  # scope doc §3: Locus's share of RAM


class Sampler:
    """Samples working set (rss) and private bytes of this process + the server."""

    def __init__(self) -> None:
        self.phase = "baseline"
        self.peaks: dict[str, dict[str, float]] = {}
        self._procs: list[psutil.Process] = [psutil.Process()]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def add(self, pid: int) -> None:
        self._procs.append(psutil.Process(pid))

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()

    def _run(self) -> None:
        while not self._stop.is_set():
            rss = private = 0.0
            for p in list(self._procs):
                try:
                    mem = p.memory_info()
                except psutil.Error:
                    continue
                rss += mem.rss
                private += getattr(mem, "private", mem.rss)  # 'private' exists on Windows
            peak = self.peaks.setdefault(self.phase, {"rss_gb": 0.0, "private_gb": 0.0})
            peak["rss_gb"] = max(peak["rss_gb"], rss / 1e9)
            peak["private_gb"] = max(peak["private_gb"], private / 1e9)
            time.sleep(0.1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--server")
    parser.add_argument("--model", required=True)
    parser.add_argument("--stt", required=True, help="engine:model, as in bench.stt")
    parser.add_argument("--tts", required=True, help="candidate spec, as in bench.tts")
    parser.add_argument("--clip", type=Path, help="WAV to transcribe (default: first recorded)")
    parser.add_argument("--threads", type=int)
    parser.add_argument("--label", default="local")
    args = parser.parse_args()

    entry = load_models()[args.model]
    clip = args.clip or AUDIO_DIR / stt_bench.load_clips(AUDIO_DIR / "manifest.jsonl")[0]["file"]
    run_dir = make_run_dir(args.label, "ram")
    vm = psutil.virtual_memory()
    sampler = Sampler()
    sampler.start()
    time.sleep(0.5)

    timings: dict[str, float] = {}
    with LlamaServer(find_server(args.server), entry.path, log_path=run_dir / "server.log",
                     threads=args.threads) as server:
        sampler.add(server.pid)
        sampler.phase = "llm_loaded"
        server.chat(build_messages("hello", few_shot=False), max_tokens=32, schema=build_schema())

        sampler.phase = "stt_loaded"
        engine_name, _, model = args.stt.partition(":")
        stt_engine = stt_bench.ENGINES[engine_name](model, args.threads)

        sampler.phase = "tts_loaded"
        tts_name, _, tts_arg = args.tts.partition(":")
        tts_engine = tts_bench.ENGINES[tts_name](tts_arg)

        sampler.phase = "full_turn"
        t0 = time.perf_counter()
        text = stt_engine.transcribe(clip)
        timings["stt_ms"] = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        route = server.chat(build_messages(text or "cpu", few_shot=False), max_tokens=96,
                            schema=build_schema())
        timings["route_ms"] = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        tts_engine.synth("Your CPU is at 23 percent.")
        timings["tts_ms"] = (time.perf_counter() - t0) * 1000
        time.sleep(0.5)
    sampler.stop()

    # This process + llama-server is exactly what Locus would occupy, so no baseline is
    # subtracted.
    locus_gb = max(p["rss_gb"] for p in sampler.peaks.values())
    result: dict[str, Any] = {
        "model": args.model,
        "stt": args.stt,
        "tts": args.tts,
        "system_ram_total_gb": round(vm.total / 1e9, 1),
        "system_ram_available_before_gb": round(vm.available / 1e9, 1),
        "peaks_by_phase": {k: {m: round(v, 2) for m, v in p.items()}
                           for k, p in sampler.peaks.items()},
        "peak_locus_gb": round(locus_gb, 2),
        "budget_gb": BUDGET_GB[entry.tier],
        "pass": locus_gb <= BUDGET_GB[entry.tier],
        "transcript": text,
        "route": route["content"],
        "timings_ms": {k: round(v) for k, v in timings.items()},
    }
    write_json(run_dir / "summary.json", result)
    print(f"Peak Locus RAM {locus_gb:.2f} GB (budget {BUDGET_GB[entry.tier]} GB for "
          f"{entry.tier}) -> {'PASS' if result['pass'] else 'FAIL'}")
    print(f"Full turn: STT {timings['stt_ms']:.0f} ms, route {timings['route_ms']:.0f} ms, "
          f"TTS {timings['tts_ms']:.0f} ms")
    print(f"Results: {run_dir}")


if __name__ == "__main__":
    main()
