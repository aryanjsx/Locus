"""Experiment 1: router accuracy and latency per candidate model.

Every catalog phrase is sent to the model through llama-server with the action
JSON schema enforced, then scored against the expected action and params.
With --qa it also measures streamed Q&A answers (time to first token, tokens/s).

    uv run python -m bench.router --server C:/llama/llama-server.exe --model llama3.2-3b
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from bench.actions import build_messages, build_schema
from bench.catalog import DEFAULT_CATALOG, Case, load_catalog, score
from bench.llama import LlamaServer, ensure_downloaded, find_server, load_models
from bench.results import fmt, make_run_dir, pct, percentile, write_json, write_jsonl

# Pass criteria from docs/locus/01-scope.md §8
ACCURACY_TARGET = {"8gb": 0.90, "16gb": 0.95}
ROUTE_P95_TARGET_MS = 1500

QA_PROMPTS = (
    "What is Python?",
    "Explain how DNS works in two sentences.",
    "Why is the sky blue?",
    "What's the difference between RAM and storage?",
    "Write a one-line Python list comprehension that squares numbers 1 to 10.",
)
QA_SYSTEM = "You are Locus, a concise desktop assistant. Answer in at most three sentences."


def run_router(server: LlamaServer, cases: list[Case], *, few_shot: bool) -> list[dict[str, Any]]:
    schema = build_schema()
    # Warm-up: loads weights into RAM and fills the prompt cache. Not scored.
    server.chat(build_messages("hello", few_shot=few_shot), max_tokens=64, schema=schema)

    rows = []
    for case in cases:
        if case.skip_llm:
            continue
        reply = server.chat(build_messages(case.text, few_shot=few_shot), max_tokens=96,
                            schema=schema)
        try:
            predicted = json.loads(reply["content"])
        except json.JSONDecodeError:
            predicted = None
        s = score(case, predicted)
        t = reply["timings"]
        rows.append({
            "id": case.id,
            "group": case.group,
            "text": case.text,
            "expected": case.action,
            "predicted": predicted.get("action") if predicted else None,
            "predicted_params": predicted.get("params") if predicted else None,
            "raw": None if predicted else reply["content"],
            "action_ok": s.action_ok,
            "params_checked": bool(case.params),
            "params_ok": s.params_ok,
            "destructive_fp": s.destructive_false_positive,
            "destructive_miss": s.destructive_miss,
            "tags": sorted(case.tags),
            "wall_ms": round(reply["wall_ms"], 1),
            "prompt_tokens": t.get("prompt_n"),
            "prompt_ms": t.get("prompt_ms"),
            "gen_tokens": t.get("predicted_n"),
            "gen_ms": t.get("predicted_ms"),
        })
    return rows


def summarise_router(rows: list[dict[str, Any]], tier: str) -> dict[str, Any]:
    def acc(subset: list[dict[str, Any]], key: str = "action_ok") -> float | None:
        return sum(r[key] for r in subset) / len(subset) if subset else None

    by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_group[r["group"]].append(r)
    # Params accuracy: among phrases with expected params, action AND params both right.
    with_params = [r for r in rows if r["params_checked"]]
    walls = [r["wall_ms"] for r in rows]
    gen_rates = [r["gen_tokens"] / (r["gen_ms"] / 1000) for r in rows
                 if r["gen_tokens"] and r["gen_ms"]]
    prompt_rates = [r["prompt_tokens"] / (r["prompt_ms"] / 1000) for r in rows
                    if r["prompt_tokens"] and r["prompt_ms"]]

    accuracy = acc(rows)
    p95 = percentile(walls, 95)
    fps = [r["id"] for r in rows if r["destructive_fp"]]
    target = ACCURACY_TARGET[tier]
    return {
        "cases": len(rows),
        "accuracy": accuracy,
        "params_accuracy": acc(with_params, "params_ok"),
        "near_miss_accuracy": acc([r for r in rows if "near_miss" in r["tags"]]),
        "by_group": {g: acc(rs) for g, rs in sorted(by_group.items())},
        "destructive_false_positives": fps,
        "destructive_misses": [r["id"] for r in rows if r["destructive_miss"]],
        "wall_ms_p50": percentile(walls, 50),
        "wall_ms_p95": p95,
        "prompt_tokens_per_s": statistics.median(prompt_rates) if prompt_rates else None,
        "gen_tokens_per_s": statistics.median(gen_rates) if gen_rates else None,
        "failures": [{"id": r["id"], "text": r["text"], "expected": r["expected"],
                      "predicted": r["predicted"]} for r in rows if not r["action_ok"]],
        "pass": {
            "accuracy": accuracy is not None and accuracy >= target,
            "no_destructive_false_positives": not fps,
            "latency": p95 is not None and p95 <= ROUTE_P95_TARGET_MS,
        },
        "targets": {"accuracy": target, "wall_ms_p95": ROUTE_P95_TARGET_MS},
    }


def run_qa(server: LlamaServer) -> dict[str, Any]:
    results = []
    for prompt in QA_PROMPTS:
        messages = [{"role": "system", "content": QA_SYSTEM}, {"role": "user", "content": prompt}]
        start = time.perf_counter()
        first: float | None = None
        pieces = []
        for delta in server.stream(messages, max_tokens=160):
            if first is None:
                first = time.perf_counter()
            pieces.append(delta)
        end = time.perf_counter()
        ttft_ms = (first - start) * 1000 if first else None
        gen_s = end - first if first else None
        # llama-server streams one token per chunk, so chunks/s ≈ tokens/s.
        results.append({
            "prompt": prompt,
            "answer": "".join(pieces),
            "ttft_ms": round(ttft_ms, 1) if ttft_ms else None,
            "tokens": len(pieces),
            "tokens_per_s": round(len(pieces) / gen_s, 1) if gen_s else None,
        })
    ttfts = [r["ttft_ms"] for r in results if r["ttft_ms"]]
    rates = [r["tokens_per_s"] for r in results if r["tokens_per_s"]]
    return {
        "ttft_ms_median": statistics.median(ttfts) if ttfts else None,
        "tokens_per_s_median": statistics.median(rates) if rates else None,
        "answers": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--server", help="path to llama-server(.exe); or set LOCUS_LLAMA_SERVER")
    parser.add_argument("--model", action="append", default=[],
                        help="model id from config/models.toml (repeatable)")
    parser.add_argument("--tier", choices=["8gb", "16gb"],
                        help="run every model of this tier")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--few-shot", action="store_true", help="add few-shot examples")
    parser.add_argument("--qa", action="store_true", help="also benchmark streamed Q&A")
    parser.add_argument("--threads", type=int, help="CPU threads for llama-server")
    parser.add_argument("--download", action="store_true",
                        help="download missing models from Hugging Face")
    parser.add_argument("--label", default="local", help="machine label for the results folder")
    args = parser.parse_args()

    models = load_models()
    ids = list(args.model) + [m.id for m in models.values() if args.tier and m.tier == args.tier]
    if not ids:
        parser.error("give --model or --tier")
    unknown = [i for i in ids if i not in models]
    if unknown:
        parser.error(f"unknown model id(s): {', '.join(unknown)}; see config/models.toml")

    server_bin = find_server(args.server)
    cases = load_catalog(args.catalog)
    run_dir = make_run_dir(args.label, "router")
    hw_tier = json.loads((run_dir / "hardware.json").read_text(encoding="utf-8"))["tier"]
    print(f"Results: {run_dir}")

    summaries: dict[str, dict[str, Any]] = {}
    for model_id in dict.fromkeys(ids):
        entry = models[model_id]
        model_path = ensure_downloaded(entry) if args.download else entry.path
        if not model_path.exists():
            print(f"[skip] {model_id}: {model_path} not found (use --download)")
            continue
        print(f"\n== {model_id} ({entry.tier}) ==")
        with LlamaServer(server_bin, model_path, log_path=run_dir / f"{model_id}.server.log",
                         threads=args.threads) as server:
            print(f"loaded in {server.load_seconds:.1f}s")
            rows = run_router(server, cases, few_shot=args.few_shot)
            summary = summarise_router(rows, hw_tier)
            summary["load_seconds"] = server.load_seconds
            summary["model_tier"] = entry.tier
            summary["few_shot"] = args.few_shot
            if args.qa:
                summary["qa"] = run_qa(server)
        write_jsonl(run_dir / f"{model_id}.rows.jsonl", rows)
        write_json(run_dir / f"{model_id}.summary.json", summary)
        summaries[model_id] = summary
        print(f"accuracy {pct(summary['accuracy'])}  "
              f"p95 {fmt(summary['wall_ms_p95'])} ms  "
              f"destructive FPs {len(summary['destructive_false_positives'])}")

    (run_dir / "summary.md").write_text(render_markdown(summaries, hw_tier), encoding="utf-8")
    print(f"\nWrote {run_dir / 'summary.md'}")


def render_markdown(summaries: dict[str, dict[str, Any]], hw_tier: str) -> str:
    target = ACCURACY_TARGET[hw_tier]
    lines = [
        f"# Experiment 1: router results ({hw_tier} machine)",
        "",
        f"Targets: accuracy ≥ {target:.0%}, 0 destructive false positives, "
        f"p95 ≤ {ROUTE_P95_TARGET_MS} ms.",
        "",
        "| Model | Accuracy | Near-miss | Params | Destructive FPs | p50 ms | p95 ms "
        "| Prompt tok/s | Gen tok/s | Load s | Pass |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for model_id, s in summaries.items():
        passed = all(s["pass"].values())
        lines.append(
            f"| {model_id} | {pct(s['accuracy'])} "
            f"| {pct(s['near_miss_accuracy'])} "
            f"| {pct(s['params_accuracy'])} "
            f"| {len(s['destructive_false_positives'])} "
            f"| {fmt(s['wall_ms_p50'])} | {fmt(s['wall_ms_p95'])} "
            f"| {fmt(s['prompt_tokens_per_s'])} | {fmt(s['gen_tokens_per_s'])} "
            f"| {fmt(s['load_seconds'], 1)} | {'✅' if passed else '❌'} |"
        )
    if any("qa" in s for s in summaries.values()):
        lines += ["", "## Q&A streaming", "", "| Model | Time to first token (ms) | Tokens/s |",
                  "|---|---|---|"]
        for model_id, s in summaries.items():
            if "qa" in s:
                lines.append(f"| {model_id} | {fmt(s['qa']['ttft_ms_median'])} "
                             f"| {fmt(s['qa']['tokens_per_s_median'], 1)} |")
    for model_id, s in summaries.items():
        if s["failures"]:
            lines += ["", f"## {model_id}: misrouted", "", "| ID | Phrase | Expected | Got |",
                      "|---|---|---|---|"]
            lines += [f"| {f['id']} | {f['text']} | `{f['expected']}` | `{f['predicted']}` |"
                      for f in s["failures"]]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
