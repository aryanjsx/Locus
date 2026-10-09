"""Experiment 2: speech-to-text speed and accuracy on CPU.

Each candidate is `engine:model`, e.g. `faster-whisper:base.en`, `faster-whisper:small.en`,
`moonshine:moonshine/base`. Clips come from data/audio/manifest.jsonl (see bench.record).

    uv run --group stt python -m bench.stt -c faster-whisper:base.en -c faster-whisper:small.en
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import time
from pathlib import Path
from typing import Any, Protocol

import psutil

from bench.record import AUDIO_DIR
from bench.results import fmt, make_run_dir, percentile, write_json, write_jsonl

TARGET_MS_PER_3S = 1000  # scope doc: a 3 s clip transcribed in under 1 s


class Engine(Protocol):
    def transcribe(self, path: Path) -> str: ...


class FasterWhisper:
    def __init__(self, model: str, threads: int | None) -> None:
        from faster_whisper import WhisperModel

        kwargs: dict[str, Any] = {"device": "cpu", "compute_type": "int8"}
        if threads:
            kwargs["cpu_threads"] = threads
        self._model = WhisperModel(model, **kwargs)

    def transcribe(self, path: Path) -> str:
        # beam_size=1 (greedy) is what we'd ship for latency; vad_filter trims silence.
        segments, _info = self._model.transcribe(str(path), language="en", beam_size=1,
                                                 vad_filter=True)
        return " ".join(s.text.strip() for s in segments)


class Moonshine:
    """Experimental adapter for `useful-moonshine-onnx`. Install it separately (see README)."""

    def __init__(self, model: str, threads: int | None) -> None:
        from moonshine_onnx import MoonshineOnnxModel, load_tokenizer

        self._model = MoonshineOnnxModel(model_name=model)
        self._tokenizer = load_tokenizer()

    def transcribe(self, path: Path) -> str:
        import soundfile as sf

        audio, rate = sf.read(str(path), dtype="float32")
        if rate != 16_000:
            raise ValueError(f"{path.name}: expected 16 kHz audio, got {rate}")
        tokens = self._model.generate(audio[None, :])
        return self._tokenizer.decode_batch(tokens)[0]


ENGINES = {"faster-whisper": FasterWhisper, "moonshine": Moonshine}

_PUNCT = re.compile(r"[^\w\s']")


def words(text: str) -> list[str]:
    return _PUNCT.sub(" ", text.lower()).split()


def wer(reference: str, hypothesis: str) -> float:
    """Word error rate: word-level edit distance / reference length."""
    ref, hyp = words(reference), words(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1] / len(ref)


def load_clips(manifest: Path) -> list[dict[str, Any]]:
    clips = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines()
             if line.strip()]
    if not clips:
        raise SystemExit(f"No clips in {manifest}. Record some with: python -m bench.record")
    return clips


def bench_candidate(spec: str, clips: list[dict[str, Any]], audio_dir: Path,
                    threads: int | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    engine_name, _, model = spec.partition(":")
    if engine_name not in ENGINES or not model:
        raise SystemExit(f"Bad candidate '{spec}'. Use engine:model, engines: {list(ENGINES)}")

    proc = psutil.Process()
    rss_before = proc.memory_info().rss
    start = time.perf_counter()
    engine: Engine = ENGINES[engine_name](model, threads)
    load_s = time.perf_counter() - start
    engine.transcribe(audio_dir / clips[0]["file"])  # warm-up, not scored
    rss_loaded = proc.memory_info().rss

    rows = []
    for clip in clips:
        path = audio_dir / clip["file"]
        t0 = time.perf_counter()
        text = engine.transcribe(path)
        ms = (time.perf_counter() - t0) * 1000
        seconds = clip.get("seconds") or _duration(path)
        rows.append({
            "file": clip["file"],
            "reference": clip["text"],
            "hypothesis": text,
            "wer": round(wer(clip["text"], text), 3),
            "audio_s": seconds,
            "ms": round(ms, 1),
            "ms_per_3s": round(ms * 3 / seconds, 1) if seconds else None,
            "rtf": round(ms / 1000 / seconds, 3) if seconds else None,
        })
    per3 = [r["ms_per_3s"] for r in rows if r["ms_per_3s"]]
    summary = {
        "candidate": spec,
        "clips": len(rows),
        "load_seconds": round(load_s, 2),
        "ram_mb": round((rss_loaded - rss_before) / 1e6),
        "wer_mean": round(statistics.mean(r["wer"] for r in rows), 3),
        "ms_per_3s_median": percentile(per3, 50),
        "ms_per_3s_p95": percentile(per3, 95),
        "pass_latency": (percentile(per3, 95) or 1e9) <= TARGET_MS_PER_3S,
    }
    return summary, rows


def _duration(path: Path) -> float:
    import soundfile as sf

    return sf.info(str(path)).duration


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-c", "--candidate", action="append", required=True,
                        help="engine:model (repeatable)")
    parser.add_argument("--manifest", type=Path, default=AUDIO_DIR / "manifest.jsonl")
    parser.add_argument("--threads", type=int)
    parser.add_argument("--label", default="local")
    args = parser.parse_args()

    clips = load_clips(args.manifest)
    run_dir = make_run_dir(args.label, "stt")
    print(f"Results: {run_dir}  ({len(clips)} clips)")
    summaries = []
    for spec in args.candidate:
        print(f"\n== {spec} ==")
        summary, rows = bench_candidate(spec, clips, args.manifest.parent, args.threads)
        write_jsonl(run_dir / f"{spec.replace(':', '_').replace('/', '-')}.rows.jsonl", rows)
        summaries.append(summary)
        print(f"WER {summary['wer_mean']:.1%}  per-3s p95 {fmt(summary['ms_per_3s_p95'])} ms  "
              f"RAM +{summary['ram_mb']} MB")
    write_json(run_dir / "summary.json", summaries)

    lines = ["# Experiment 2: speech-to-text results", "",
             f"Target: a 3 s clip in ≤ {TARGET_MS_PER_3S} ms (p95).", "",
             "| Candidate | WER | ms per 3 s (median) | ms per 3 s (p95) | RAM MB | Load s | Pass |",
             "|---|---|---|---|---|---|---|"]
    for s in summaries:
        lines.append(f"| {s['candidate']} | {s['wer_mean']:.1%} | {fmt(s['ms_per_3s_median'])} "
                     f"| {fmt(s['ms_per_3s_p95'])} | {s['ram_mb']} | {s['load_seconds']} "
                     f"| {'✅' if s['pass_latency'] else '❌'} |")
    (run_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nWrote {run_dir / 'summary.md'}")


if __name__ == "__main__":
    main()
