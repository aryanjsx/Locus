"""Where results go: experiments/results/<label>/<timestamp>-<kind>/."""

from __future__ import annotations

import json
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any

from bench.hw import fingerprint, tier_for

RESULTS_ROOT = Path(__file__).resolve().parent.parent / "results"


def make_run_dir(label: str, kind: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = RESULTS_ROOT / label / f"{stamp}-{kind}"
    run_dir.mkdir(parents=True, exist_ok=False)
    hw = fingerprint()
    hw["tier"] = tier_for(hw["ram_total_gb"])
    write_json(run_dir / "hardware.json", hw)
    return run_dir


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[int(pct) - 1]


def fmt(value: float | None, digits: int = 0, suffix: str = "") -> str:
    return "—" if value is None else f"{value:.{digits}f}{suffix}"


def pct(fraction: float | None) -> str:
    return "—" if fraction is None else f"{fraction * 100:.1f}%"
