"""
Phase-2 hardening: config_loader MUST refuse zero, negative, or
wrong-type values for numeric safety knobs.  Fail-fast on misconfig
prevents a silently-broken subsystem (e.g. a rate limiter with
``max_per_minute=0`` would reject every command).
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from aura.core import config_loader
from aura.core.errors import ConfigError


def _write(path: Path, body: str) -> None:
    path.write_text(textwrap.dedent(body), encoding="utf-8")


def _reload_from(tmp_path: Path, body: str, monkeypatch: pytest.MonkeyPatch):
    cfg = tmp_path / "config.yaml"
    _write(cfg, body)
    example = tmp_path / "config.example.yaml"
    _write(example, body)  # so reload never falls back elsewhere

    monkeypatch.setattr(config_loader, "_CONFIG_PATH", cfg)
    monkeypatch.setattr(config_loader, "_FALLBACK_PATH", example)
    return config_loader.reload()


_BASELINE = """
aura:
  name: AURA
  version: "2.0.0"
  mode: offline
sandbox:
  base_dir: "./sandbox"
paths:
  protected:
    - "~/.ssh"
    - "/etc"
logging:
  level: INFO
  file: "logs/aura.log"
  max_bytes: 1048576
  backup_count: 3
audit:
  file: "logs/audit.log"
  max_bytes: 1048576
  backup_count: 3
shell:
  allowed_commands: ["echo"]
  timeout: 10
permissions:
  source_limits:
    cli: CRITICAL
    llm: MEDIUM
rate_limit:
  max_per_minute: 60
  repeat_threshold: 10
safety:
  confirm_timeout: 15
  auto_confirm: false
models:
  fast: "llama3.2:1b"
  general: "llama3.2:1b"
  code: "deepseek-coder:6.7b"
routing:
  intent_timeout_seconds: 10
  intent_max_retries: 3
stt:
  model: "tiny"
  silence_timeout: 1.0
  max_recording: 10
tts:
  offline_engine: "piper"
ollama:
  base_url: "http://localhost:11434"
  timeout: 120
  retries: 2
"""


def _variant(**overrides: str) -> str:
    """Return the baseline YAML with named lines replaced."""
    lines = _BASELINE.strip("\n").splitlines()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        for key, repl in overrides.items():
            if stripped.startswith(f"{key}:"):
                line = line.split(key + ":")[0] + f"{key}: {repl}"
                break
        out.append(line)
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------
# Zero / negative rejections
# ------------------------------------------------------------------
@pytest.mark.parametrize(
    "key, value",
    [
        ("max_per_minute", "0"),
        ("max_per_minute", "-1"),
        ("repeat_threshold", "1"),   # must be > 1
        ("repeat_threshold", "0"),
        ("timeout", "0"),
        ("timeout", "-5"),
        ("max_bytes", "0"),
        ("confirm_timeout", "0"),
        ("confirm_timeout", "-1"),
    ],
)
def test_zero_or_negative_numeric_values_rejected(
    tmp_path, monkeypatch, key, value
):
    with pytest.raises(ConfigError) as excinfo:
        _reload_from(tmp_path, _variant(**{key: value}), monkeypatch)
    assert "out of range" in str(excinfo.value) or ">" in str(excinfo.value)


# ------------------------------------------------------------------
# Wrong type rejection (bool is NOT int for config purposes)
# ------------------------------------------------------------------
@pytest.mark.parametrize(
    "key, value",
    [
        ("max_per_minute", "true"),     # bool
        ("max_per_minute", '"sixty"'),  # string
        ("timeout", "true"),
        ("max_bytes", "true"),
    ],
)
def test_wrong_type_rejected(tmp_path, monkeypatch, key, value):
    with pytest.raises(ConfigError):
        _reload_from(tmp_path, _variant(**{key: value}), monkeypatch)


# ------------------------------------------------------------------
# Happy path — legitimate values still load.
# ------------------------------------------------------------------
def test_valid_config_loads(tmp_path, monkeypatch):
    cfg = _reload_from(tmp_path, _BASELINE, monkeypatch)
    assert cfg["rate_limit"]["max_per_minute"] == 60
    assert cfg["shell"]["timeout"] == 10
    assert cfg["logging"]["max_bytes"] == 1048576


# ------------------------------------------------------------------
# Per-source overrides are range-checked too
# ------------------------------------------------------------------
def test_source_override_zero_rejected(tmp_path, monkeypatch):
    body = _BASELINE + textwrap.dedent(
        """\
        rate_limit:
          sources:
            llm:
              max_per_minute: 0
              repeat_threshold: 5
        """
    )
    with pytest.raises(ConfigError) as excinfo:
        _reload_from(tmp_path, body, monkeypatch)
    assert "llm" in str(excinfo.value)


def test_source_override_repeat_threshold_too_low(tmp_path, monkeypatch):
    body = _BASELINE + textwrap.dedent(
        """\
        rate_limit:
          sources:
            llm:
              max_per_minute: 30
              repeat_threshold: 1
        """
    )
    with pytest.raises(ConfigError):
        _reload_from(tmp_path, body, monkeypatch)
