"""
AURA — Configuration Loader with strict required-key validation.

Loads ``config.yaml`` (user-local, gitignored) with fallback to
``config.example.yaml`` (tracked template), deep-merges into built-in
defaults, applies environment overrides, and then **validates that every
key in :data:`REQUIRED_KEYS` is present and non-empty**.

Missing keys raise :class:`~aura.core.errors.ConfigError` — there is no
silent fallback for required values.
"""

from __future__ import annotations

import copy
import logging
import os
from pathlib import Path
from typing import Any

from aura.core.errors import ConfigError

logger = logging.getLogger("aura.config")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

_CONFIG_PATH = _PROJECT_ROOT / "config.yaml"
_FALLBACK_PATH = _PROJECT_ROOT / "config.example.yaml"

# Keys that MUST be present (dot-notation).  Loader fails hard if any
# is missing or resolves to ``None`` / empty string / empty list.
REQUIRED_KEYS: tuple[str, ...] = (
    "aura.name",
    "aura.version",
    "aura.mode",
    "sandbox.base_dir",
    "paths.protected",
    "logging.file",
    "logging.level",
    "shell.timeout",
    "shell.allowed_commands",
    "permissions.source_limits",
    "rate_limit.max_per_minute",
    "rate_limit.repeat_threshold",
    "safety.confirmation_timeout",
    "audit.file",
    # Phase 2 — Voice Pipeline
    "models.fast",
    "models.general",
    "models.code",
    "routing.intent_timeout_seconds",
    "routing.intent_max_retries",
    "stt.model",
    "stt.silence_timeout",
    "stt.max_recording",
    "tts.offline_engine",
    "ollama.base_url",
    "ollama.timeout",
    "ollama.retries",
)

# Built-in defaults merged under user config (never used as required-key fallback).
_DEFAULTS: dict[str, Any] = {
    "aura": {
        "name": "AURA",
        "version": "0.2.0",
        "mode": "offline",
    },
    "sandbox": {
        "base_dir": str(Path.home() / "AURA_SANDBOX"),
    },
    "paths": {
        "protected": [
            "C:/Windows",
            "C:/Windows/System32",
            "C:/Program Files",
            "C:/Program Files (x86)",
            "/bin",
            "/usr",
            "/etc",
            "/sbin",
            "/boot",
        ],
    },
    "logging": {
        "file": "logs/aura.log",
        "level": "INFO",
        "format": "json",
        "max_bytes": 5_242_880,
        "backup_count": 3,
        "benchmark": False,
        "trace": False,
    },
    "shell": {
        "timeout": 120,
        "allowed_commands": ["git", "npm", "docker", "echo"],
    },
    "system_check": {
        "tools": ["git", "node", "docker", "npm"],
    },
    "permissions": {
        "source_limits": {
            "cli": "CRITICAL",
            "llm": "MEDIUM",
            "planner": "HIGH",
            "auto": "LOW",
        },
    },
    "rate_limit": {
        "max_per_minute": 60,
        "repeat_threshold": 10,
    },
    "safety": {
        "confirmation_timeout": 8,
        "auto_confirm": False,
        "audit_log": "logs/safety_audit.log",
    },
    "audit": {
        "file": "logs/audit.log",
        "max_bytes": 5_242_880,
        "backup_count": 5,
    },
}

_cache: dict[str, Any] | None = None


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _apply_env_overrides(cfg: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(cfg)

    log_path = os.environ.get("AURA_LOG_PATH", "").strip()
    if log_path:
        merged.setdefault("logging", {})["file"] = log_path

    timeout_raw = os.environ.get("AURA_SHELL_TIMEOUT", "").strip()
    if timeout_raw:
        try:
            merged.setdefault("shell", {})["timeout"] = int(timeout_raw)
        except ValueError:
            raise ConfigError(
                f"AURA_SHELL_TIMEOUT is not an integer: {timeout_raw!r}"
            )

    protected_raw = os.environ.get("AURA_PROTECTED_PATHS", "").strip()
    if protected_raw:
        merged.setdefault("paths", {})["protected"] = [
            p.strip() for p in protected_raw.split(",") if p.strip()
        ]

    sandbox_raw = os.environ.get("AURA_SANDBOX_DIR", "").strip()
    if sandbox_raw:
        merged.setdefault("sandbox", {})["base_dir"] = sandbox_raw

    return merged


def _dig(cfg: dict[str, Any], dotted: str) -> Any:
    cur: Any = cfg
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


_REQUIRED_SECTIONS: tuple[str, ...] = (
    "aura", "sandbox", "paths", "logging", "shell",
    "permissions", "rate_limit", "safety", "audit",
)


def _normalize_aliases(cfg: dict[str, Any]) -> None:
    """Map legacy / alternate keys to their canonical names."""
    safety = cfg.get("safety")
    if isinstance(safety, dict) and "confirm_timeout" in safety:
        safety["confirmation_timeout"] = safety.pop("confirm_timeout")


def _validate_required_sections(raw: dict[str, Any]) -> None:
    """Fail if any required top-level section is absent from the raw config."""
    missing = [s for s in _REQUIRED_SECTIONS if s not in raw]
    if missing:
        raise ConfigError(
            f"Required top-level section(s) missing from config.yaml: "
            f"{', '.join(sorted(missing))}. "
            f"Run `cp config.example.yaml config.yaml` to reset."
        )


def _validate_required(cfg: dict[str, Any]) -> None:
    missing: list[str] = []
    empty: list[str] = []
    for key in REQUIRED_KEYS:
        value = _dig(cfg, key)
        if value is None:
            missing.append(key)
            continue
        if isinstance(value, str) and not value.strip():
            empty.append(key)
        elif isinstance(value, (list, tuple, dict)) and not value:
            empty.append(key)

    if missing or empty:
        parts: list[str] = []
        if missing:
            parts.append(f"missing: {sorted(missing)}")
        if empty:
            parts.append(f"empty: {sorted(empty)}")
        raise ConfigError(
            "Configuration invalid — " + "; ".join(parts)
            + f". Provide these keys in {_CONFIG_PATH.name} "
            f"(or {_FALLBACK_PATH.name})."
        )


# ---------------------------------------------------------------------------
# Range / sanity validation.
#
# Required by Phase-2 readiness: no silent DoS via misconfiguration.  Each
# numeric value has a lower bound below which the system cannot operate
# and an upper bound beyond which the value is almost certainly a typo.
# Values outside the bounds raise ConfigError *before* the cache is set,
# so the process never starts in a broken state.
# ---------------------------------------------------------------------------

# key -> (expected_type, lower_bound_exclusive, upper_bound_inclusive, label)
_NUMERIC_BOUNDS: tuple[tuple[str, tuple[type, ...], float, float, str], ...] = (
    ("shell.timeout",              (int, float), 0, 3600, "seconds"),
    ("rate_limit.max_per_minute",  (int,),       0, 10000, "calls/min"),
    ("rate_limit.repeat_threshold",(int,),       1, 10000, "repeats"),
    ("safety.confirmation_timeout", (int, float), 0, 600,   "seconds"),
    ("logging.max_bytes",          (int,),       0, 1024 * 1024 * 1024, "bytes"),
    ("logging.backup_count",       (int,),      -1, 1000,  "files"),
    ("audit.max_bytes",            (int,),       0, 1024 * 1024 * 1024, "bytes"),
    ("audit.backup_count",         (int,),      -1, 1000,  "files"),
)


def _validate_numeric(cfg: dict[str, Any], key: str, types: tuple[type, ...],
                      low_excl: float, high_incl: float, unit: str) -> str | None:
    value = _dig(cfg, key)
    if value is None:
        return None  # covered by required-key check if required
    # bool is an int subclass — refuse it explicitly.
    if isinstance(value, bool):
        return f"{key} must be numeric, got bool"
    if not isinstance(value, types):
        return (
            f"{key} must be {'/'.join(t.__name__ for t in types)}, "
            f"got {type(value).__name__}"
        )
    numeric = float(value)
    if numeric <= low_excl:
        return f"{key} must be > {low_excl} ({unit}), got {value}"
    if numeric > high_incl:
        return f"{key} must be <= {high_incl} ({unit}), got {value}"
    return None


def _validate_source_overrides(cfg: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    overrides = _dig(cfg, "rate_limit.sources")
    if overrides is None:
        return errors
    if not isinstance(overrides, dict):
        return [f"rate_limit.sources must be a mapping, got {type(overrides).__name__}"]
    for src, entry in overrides.items():
        if not isinstance(entry, dict):
            errors.append(
                f"rate_limit.sources.{src} must be a mapping, "
                f"got {type(entry).__name__}"
            )
            continue
        mpm = entry.get("max_per_minute")
        if mpm is not None:
            if isinstance(mpm, bool) or not isinstance(mpm, int) or mpm <= 0:
                errors.append(
                    f"rate_limit.sources.{src}.max_per_minute must be int > 0, "
                    f"got {mpm!r}"
                )
        rt = entry.get("repeat_threshold")
        if rt is not None:
            if isinstance(rt, bool) or not isinstance(rt, int) or rt <= 1:
                errors.append(
                    f"rate_limit.sources.{src}.repeat_threshold must be int > 1, "
                    f"got {rt!r}"
                )
    return errors


def _validate_ranges(cfg: dict[str, Any]) -> None:
    problems: list[str] = []
    for key, types, low, high, unit in _NUMERIC_BOUNDS:
        err = _validate_numeric(cfg, key, types, low, high, unit)
        if err:
            problems.append(err)
    problems.extend(_validate_source_overrides(cfg))
    if problems:
        raise ConfigError(
            "Configuration out of range — " + "; ".join(problems)
        )


def load_config() -> dict[str, Any]:
    """Load, validate, and cache configuration.

    Raises
    ------
    ConfigError
        If any required key is missing or empty after merge + overrides.
    """
    global _cache
    if _cache is not None:
        return _cache

    raw: dict[str, Any] = {}
    path = _CONFIG_PATH if _CONFIG_PATH.exists() else _FALLBACK_PATH

    if path.exists():
        try:
            import yaml  # noqa: PLC0415 — deferred so missing PyYAML is non-fatal
            with path.open("r", encoding="utf-8") as fh:
                raw = yaml.safe_load(fh) or {}
        except ImportError as exc:
            raise ConfigError(
                "PyYAML is required to load config.yaml. "
                "Install it with: pip install pyyaml"
            ) from exc
        except Exception as exc:
            raise ConfigError(f"Failed to parse {path.name}: {exc}") from exc
    else:
        raise ConfigError(
            f"No configuration file found. Expected {_CONFIG_PATH.name} "
            f"or {_FALLBACK_PATH.name} at {_PROJECT_ROOT}."
        )

    _validate_required_sections(raw)
    merged = _deep_merge(_DEFAULTS, raw)
    merged = _apply_env_overrides(merged)
    _normalize_aliases(merged)
    _validate_required(merged)
    _validate_ranges(merged)
    _cache = merged
    return _cache


def get(key: str, default: Any = None) -> Any:
    """Retrieve a config value using dot-separated keys."""
    value = _dig(load_config(), key)
    return default if value is None else value


def reload() -> dict[str, Any]:
    """Force-reload configuration from disk."""
    global _cache
    _cache = None
    return load_config()
