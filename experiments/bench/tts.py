"""Experiment 3: text-to-speech latency on CPU.

Time to first audio is approximated by the time to synthesise the first sentence,
since Locus speaks sentence by sentence. Candidates:

    piper:<voice.onnx>                          (voice config next to it as .onnx.json)
    kokoro:<kokoro.onnx>,<voices.bin>,<voice>   e.g. kokoro:models/kokoro-v1.0.onnx,models/voices-v1.0.bin,af_heart
    sapi                                        Windows built-in voice via pyttsx3

    uv run --group tts python -m bench.tts -c piper:models/en_US-lessac-medium.onnx --save-audio
"""

from __future__ import annotations

import argparse
import re
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any, Protocol

import psutil

from bench.results import fmt, make_run_dir, percentile, write_json, write_jsonl

TARGET_FIRST_AUDIO_MS = 300

TEXTS = (
    "Your CPU is at 23 percent.",
    "You have 6.2 gigabytes of RAM free.",
    "Opening Chrome.",
    "I'm about to delete notes.txt from Documents. It will go to the Recycle Bin.",
    "I can't look that up yet.",
    "Python is a popular programming language known for its readable syntax. "
    "It's used for web development, data analysis and automation. "
    "Most people start with the official tutorial.",
)

_SENTENCE = re.compile(r"(?<=[.!?])\s+")


class Engine(Protocol):
    def synth(self, text: str) -> tuple[Any, int]:
        """Return (int16 or float32 samples, sample_rate)."""
        ...


class Piper:
    def __init__(self, arg: str) -> None:
        from piper import PiperVoice

        self._voice = PiperVoice.load(arg)

    def synth(self, text: str) -> tuple[Any, int]:
        import numpy as np

        voice = self._voice
        if hasattr(voice, "synthesize_stream_raw"):  # piper-tts 1.2.x
            raw = b"".join(voice.synthesize_stream_raw(text))
            rate = voice.config.sample_rate
        else:  # piper-tts 1.3+ (OHF-Voice/piper1-gpl)
            chunks = list(voice.synthesize(text))
            raw = b"".join(c.audio_int16_bytes for c in chunks)
            rate = chunks[0].sample_rate if chunks else voice.config.sample_rate
        return np.frombuffer(raw, dtype=np.int16), rate


class Kokoro:
    def __init__(self, arg: str) -> None:
        from kokoro_onnx import Kokoro as KokoroOnnx

        model, voices, self._voice = (part.strip() for part in arg.split(","))
        self._tts = KokoroOnnx(model, voices)

    def synth(self, text: str) -> tuple[Any, int]:
        samples, rate = self._tts.create(text, voice=self._voice, speed=1.0, lang="en-us")
        return samples, rate


class Sapi:
    """Windows SAPI via pyttsx3. Renders to a temp WAV, so timing includes file I/O."""

    def __init__(self, _arg: str) -> None:
        import pyttsx3

        self._engine = pyttsx3.init()

    def synth(self, text: str) -> tuple[Any, int]:
        import soundfile as sf

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.wav"
            self._engine.save_to_file(text, str(path))
            self._engine.runAndWait()
            samples, rate = sf.read(str(path), dtype="int16")
        return samples, rate


ENGINES = {"piper": Piper, "kokoro": Kokoro, "sapi": Sapi}


def bench_candidate(spec: str, save_dir: Path | None) -> tuple[dict[str, Any],
                                                                list[dict[str, Any]]]:
    name, _, arg = spec.partition(":")
    if name not in ENGINES:
        raise SystemExit(f"Bad candidate '{spec}'. Engines: {list(ENGINES)}")
    proc = psutil.Process()
    rss_before = proc.memory_info().rss
    t0 = time.perf_counter()
    engine: Engine = ENGINES[name](arg)
    load_s = time.perf_counter() - t0
    engine.synth("Warming up.")  # not scored
    ram_mb = (proc.memory_info().rss - rss_before) / 1e6

    rows = []
    for i, text in enumerate(TEXTS):
        first_sentence = _SENTENCE.split(text)[0]
        t0 = time.perf_counter()
        engine.synth(first_sentence)
        first_ms = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        samples, rate = engine.synth(text)
        full_ms = (time.perf_counter() - t0) * 1000
        audio_s = len(samples) / rate if rate else 0
        if save_dir is not None:
            import soundfile as sf

            sf.write(save_dir / f"{name}-{i}.wav", samples, rate)
        rows.append({"text": text, "first_audio_ms": round(first_ms, 1),
                     "full_ms": round(full_ms, 1), "audio_s": round(audio_s, 2),
                     "rtf": round(full_ms / 1000 / audio_s, 3) if audio_s else None})
    firsts = [r["first_audio_ms"] for r in rows]
    summary = {
        "candidate": spec,
        "load_seconds": round(load_s, 2),
        "ram_mb": round(ram_mb),
        "first_audio_ms_median": statistics.median(firsts),
        "first_audio_ms_p95": percentile(firsts, 95),
        "rtf_median": statistics.median(r["rtf"] for r in rows if r["rtf"]),
        "pass_latency": (percentile(firsts, 95) or 1e9) <= TARGET_FIRST_AUDIO_MS,
    }
    return summary, rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-c", "--candidate", action="append", required=True)
    parser.add_argument("--save-audio", action="store_true",
                        help="save WAVs so you can judge voice quality by ear")
    parser.add_argument("--label", default="local")
    args = parser.parse_args()

    run_dir = make_run_dir(args.label, "tts")
    print(f"Results: {run_dir}")
    summaries = []
    for spec in args.candidate:
        print(f"\n== {spec} ==")
        save_dir = run_dir / "audio" if args.save_audio else None
        if save_dir:
            save_dir.mkdir(exist_ok=True)
        summary, rows = bench_candidate(spec, save_dir)
        write_jsonl(run_dir / f"{spec.split(':')[0]}.rows.jsonl", rows)
        summaries.append(summary)
        print(f"first audio p95 {fmt(summary['first_audio_ms_p95'])} ms  "
              f"RTF {summary['rtf_median']}  RAM +{summary['ram_mb']} MB")
    write_json(run_dir / "summary.json", summaries)

    lines = ["# Experiment 3: text-to-speech results", "",
             f"Target: first audio ≤ {TARGET_FIRST_AUDIO_MS} ms (p95). Judge quality by "
             "listening to the saved WAVs.", "",
             "| Candidate | First audio median ms | First audio p95 ms | RTF | RAM MB | Load s "
             "| Pass |", "|---|---|---|---|---|---|---|"]
    for s in summaries:
        lines.append(f"| {s['candidate']} | {fmt(s['first_audio_ms_median'])} "
                     f"| {fmt(s['first_audio_ms_p95'])} | {s['rtf_median']} | {s['ram_mb']} "
                     f"| {s['load_seconds']} | {'✅' if s['pass_latency'] else '❌'} |")
    (run_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nWrote {run_dir / 'summary.md'}")


if __name__ == "__main__":
    main()
