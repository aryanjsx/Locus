"""Run a local llama.cpp ``llama-server`` and talk to it over its OpenAI-style API.

The server runs as a child process (the same setup the installer will use), so a
crash in the model can't take the benchmark down with it.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import psutil

EXPERIMENTS_DIR = Path(__file__).resolve().parent.parent
MODELS_TOML = EXPERIMENTS_DIR / "config" / "models.toml"


@dataclass(frozen=True)
class ModelEntry:
    id: str
    tier: str
    path: Path
    repo: str | None
    file: str | None


def load_models(path: Path = MODELS_TOML) -> dict[str, ModelEntry]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    models: dict[str, ModelEntry] = {}
    for raw in data.get("model", []):
        model_path = Path(raw["path"])
        if not model_path.is_absolute():
            model_path = EXPERIMENTS_DIR / model_path
        models[raw["id"]] = ModelEntry(
            id=raw["id"],
            tier=raw["tier"],
            path=model_path,
            repo=raw.get("repo") or None,
            file=raw.get("file") or None,
        )
    return models


def ensure_downloaded(entry: ModelEntry) -> Path:
    """Download the GGUF from Hugging Face if it's missing (needs the `download` group)."""
    if entry.path.exists():
        return entry.path
    if not (entry.repo and entry.file):
        raise FileNotFoundError(
            f"{entry.path} not found and models.toml has no repo/file for '{entry.id}'"
        )
    from huggingface_hub import hf_hub_download

    entry.path.parent.mkdir(parents=True, exist_ok=True)
    downloaded = Path(hf_hub_download(entry.repo, entry.file, local_dir=entry.path.parent))
    if downloaded.resolve() != entry.path.resolve():
        downloaded.replace(entry.path)
    return entry.path


def find_server(explicit: str | None) -> Path:
    candidate = explicit or os.environ.get("LOCUS_LLAMA_SERVER")
    if not candidate:
        raise SystemExit(
            "Path to llama-server not given. Pass --server or set LOCUS_LLAMA_SERVER "
            "(see experiments/README.md)."
        )
    path = Path(candidate)
    if not path.exists():
        raise SystemExit(f"llama-server not found at {path}")
    return path


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LlamaServer:
    def __init__(
        self,
        server_bin: Path,
        model_path: Path,
        *,
        log_path: Path,
        ctx: int = 4096,
        threads: int | None = None,
    ) -> None:
        self.server_bin = server_bin
        self.model_path = model_path
        self.log_path = log_path
        self.ctx = ctx
        self.threads = threads
        self.port = _free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.load_seconds: float | None = None
        self._proc: subprocess.Popen[bytes] | None = None
        self._log = None
        self._client = httpx.Client(base_url=self.base_url, timeout=300)

    @property
    def pid(self) -> int:
        assert self._proc is not None
        return self._proc.pid

    def __enter__(self) -> LlamaServer:
        args = [
            str(self.server_bin),
            "-m", str(self.model_path),
            "--host", "127.0.0.1",
            "--port", str(self.port),
            "-c", str(self.ctx),
            "-np", "1",  # one slot: Locus serves one user, one request at a time
            "--jinja",  # use the model's own chat template (needed for chat_template_kwargs)
        ]
        if self.threads:
            args += ["-t", str(self.threads)]
        self._log = self.log_path.open("wb")
        start = time.perf_counter()
        self._proc = subprocess.Popen(args, stdout=self._log, stderr=subprocess.STDOUT)
        self._wait_ready(timeout=600)
        self.load_seconds = time.perf_counter() - start
        return self

    def _wait_ready(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            assert self._proc is not None
            if self._proc.poll() is not None:
                tail = self.log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
                raise RuntimeError(f"llama-server exited early:\n{tail}")
            try:
                if self._client.get("/health", timeout=2).status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(0.25)
        raise TimeoutError("llama-server did not become ready in time")

    def __exit__(self, *exc: object) -> None:
        self._client.close()
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        if self._log:
            self._log.close()

    def memory(self) -> psutil.Process:
        return psutil.Process(self.pid)

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int,
        schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Non-streaming chat call. Returns content, wall time and llama.cpp timings."""
        body: dict[str, Any] = {
            "messages": messages,
            "temperature": 0,
            "max_tokens": max_tokens,
            "cache_prompt": True,  # reuse the KV cache for the unchanged system prompt
            "chat_template_kwargs": {"enable_thinking": False},  # ignored by most templates
        }
        if schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "route", "schema": schema},
            }
        start = time.perf_counter()
        resp = self._client.post("/v1/chat/completions", json=body)
        wall_ms = (time.perf_counter() - start) * 1000
        resp.raise_for_status()
        data = resp.json()
        return {
            "content": data["choices"][0]["message"].get("content") or "",
            "wall_ms": wall_ms,
            "timings": data.get("timings", {}),
            "usage": data.get("usage", {}),
        }

    def stream(self, messages: list[dict[str, str]], *, max_tokens: int) -> Iterator[str]:
        """Streaming chat call; yields text deltas as they arrive."""
        body = {
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": max_tokens,
            "stream": True,
            "cache_prompt": True,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        with self._client.stream("POST", "/v1/chat/completions", json=body) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[len("data: "):]
                if payload.strip() == "[DONE]":
                    return
                chunk = json.loads(payload)
                choices = chunk.get("choices") or []
                if choices:
                    delta = choices[0].get("delta", {}).get("content")
                    if delta:
                        yield delta
