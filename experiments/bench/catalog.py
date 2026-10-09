"""Load the command catalog and score router predictions against it."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bench.actions import DESTRUCTIVE

DEFAULT_CATALOG = Path(__file__).resolve().parent.parent / "data" / "catalog.jsonl"


@dataclass(frozen=True)
class Case:
    id: str
    group: str
    text: str
    action: str | None  # None = no action expected (e.g. empty transcript)
    # key -> acceptable values (any match passes), or None = must be null/absent.
    # Keys not listed are not checked.
    params: dict[str, tuple[str, ...] | None]
    tags: frozenset[str]

    @property
    def skip_llm(self) -> bool:
        return "skip_llm" in self.tags


def load_catalog(path: Path = DEFAULT_CATALOG) -> list[Case]:
    cases: list[Case] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        raw = json.loads(line)
        params: dict[str, tuple[str, ...] | None] = {}
        for key, value in raw.get("params", {}).items():
            if value is None:
                params[key] = None
            elif isinstance(value, list):
                params[key] = tuple(value)
            else:
                params[key] = (value,)
        try:
            cases.append(
                Case(
                    id=raw["id"],
                    group=raw["group"],
                    text=raw["text"],
                    action=raw["action"],
                    params=params,
                    tags=frozenset(raw.get("tags", [])),
                )
            )
        except KeyError as exc:
            raise ValueError(f"{path}:{lineno}: missing field {exc}") from exc
    return cases


_SPACES = re.compile(r"\s+")


def norm(value: Any) -> str | None:
    """Normalise a param value: case, quotes, slashes and whitespace don't count."""
    if value is None:
        return None
    text = str(value).strip().strip("'\"").replace("\\", "/").rstrip("/").lower()
    text = _SPACES.sub(" ", text)
    return text or None


@dataclass(frozen=True)
class Score:
    action_ok: bool
    params_ok: bool  # True when no params are checked for this case
    destructive_false_positive: bool  # predicted a destructive action the user didn't ask for
    destructive_miss: bool  # user asked for a destructive action, model picked something else


def score(case: Case, predicted: dict[str, Any] | None) -> Score:
    pred_action = predicted.get("action") if predicted else None
    pred_params = (predicted.get("params") or {}) if predicted else {}
    action_ok = pred_action == case.action

    params_ok = action_ok
    if action_ok:
        for key, accepted in case.params.items():
            got = norm(pred_params.get(key))
            if accepted is None:
                ok = got is None
            else:
                ok = got in {norm(v) for v in accepted}
            params_ok = params_ok and ok

    expected_destructive = case.action in DESTRUCTIVE
    predicted_destructive = pred_action in DESTRUCTIVE
    return Score(
        action_ok=action_ok,
        params_ok=params_ok,
        destructive_false_positive=predicted_destructive and not expected_destructive,
        destructive_miss=expected_destructive and not action_ok,
    )
