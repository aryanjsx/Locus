"""
AURA — Ollama HTTP Client (Phase 2).

Thin, reliable wrapper around the Ollama REST API. All LLM calls in AURA
go through this single client — nothing else talks to Ollama directly.

Supports both blocking and streaming responses.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Generator

import httpx

from aura.core.event_bus import EventType, bus

logger = logging.getLogger("aura.ollama_client")


@dataclass
class OllamaResponse:
    """Structured response from an Ollama chat/generate call."""

    text: str
    model: str
    duration_ms: int


class OllamaUnavailableError(Exception):
    """Raised when Ollama cannot be reached after all retries."""


class OllamaClient:
    """HTTP client for the local Ollama REST API with retry logic."""

    def __init__(self, config: dict) -> None:
        ollama_cfg = config.get("ollama", {})
        self._base_url: str = ollama_cfg.get("base_url", "")
        self._timeout: int = ollama_cfg.get("timeout", 60)
        self._retries: int = ollama_cfg.get("retries", 3)
        self._keep_alive: str = ollama_cfg.get("keep_alive", "10m")
        self._health_timeout: int = int(ollama_cfg.get("health_check_timeout", 5))

    def chat(
        self,
        model: str,
        prompt: str,
        system_prompt: str = "",
        num_predict: int = 150,
        timeout: float | None = None,
    ) -> OllamaResponse:
        """Send a blocking chat completion request to Ollama."""
        model = self._resolve_model(model)
        messages = self._build_messages(prompt, system_prompt)
        bus.emit(EventType.LLM_REQUEST_SENT, {"model": model, "prompt_len": len(prompt)})
        effective_timeout = timeout if timeout is not None else self._timeout

        last_exc: Exception | None = None
        for attempt in range(self._retries):
            try:
                start = time.perf_counter()
                response = httpx.post(
                    f"{self._base_url}/api/chat",
                    json={
                        "model": model,
                        "messages": messages,
                        "stream": False,
                        "keep_alive": self._keep_alive,
                        "options": {
                            "num_predict": num_predict,
                            "temperature": 0.7,
                        },
                    },
                    timeout=effective_timeout,
                )
                response.raise_for_status()
                duration_ms = int((time.perf_counter() - start) * 1000)
                text = response.json().get("message", {}).get("content", "")

                result = OllamaResponse(text=text, model=model, duration_ms=duration_ms)
                bus.emit(
                    EventType.LLM_RESPONSE_RECEIVED,
                    {"model": model, "duration_ms": duration_ms, "text_len": len(text)},
                )
                return result

            except Exception as exc:
                last_exc = exc
                if attempt < self._retries - 1:
                    backoff = 2 ** (attempt + 1)
                    logger.warning(
                        "Ollama request failed (attempt %d/%d), retrying in %ds: %s",
                        attempt + 1, self._retries, backoff, exc,
                    )
                    time.sleep(backoff)

        bus.emit(EventType.SYSTEM_ERROR, {"module": "ollama_client", "error": str(last_exc)})
        raise OllamaUnavailableError(
            f"Ollama unavailable after {self._retries} retries: {last_exc}"
        )

    def warmup(self, model: str) -> None:
        """Send a minimal request to load the model into RAM."""
        model = self._resolve_model(model)
        try:
            httpx.post(
                f"{self._base_url}/api/chat",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": False,
                    "keep_alive": self._keep_alive,
                    "options": {"num_predict": 1},
                },
                timeout=self._timeout,
            )
            logger.info("Model '%s' warmed up", model)
        except Exception as exc:
            logger.warning("Warmup failed for '%s': %s", model, exc)

    def chat_stream(
        self,
        model: str,
        prompt: str,
        system_prompt: str = "",
        num_predict: int = 80,
    ) -> Generator[str, None, None]:
        """Stream chat tokens from Ollama. Yields text chunks as they arrive."""
        model = self._resolve_model(model)
        messages = self._build_messages(prompt, system_prompt)
        bus.emit(EventType.LLM_REQUEST_SENT, {"model": model, "prompt_len": len(prompt)})

        start = time.perf_counter()
        try:
            with httpx.stream(
                "POST",
                f"{self._base_url}/api/chat",
                json={
                    "model": model,
                    "messages": messages,
                    "stream": True,
                    "keep_alive": self._keep_alive,
                    "options": {
                        "num_predict": num_predict,
                        "temperature": 0,
                    },
                },
                timeout=self._timeout,
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    token = chunk.get("message", {}).get("content", "")
                    if token:
                        yield token
                    if chunk.get("done", False):
                        break

        except Exception as exc:
            logger.error("Streaming failed: %s", exc)
            raise

        duration_ms = int((time.perf_counter() - start) * 1000)
        bus.emit(
            EventType.LLM_RESPONSE_RECEIVED,
            {"model": model, "duration_ms": duration_ms, "streamed": True},
        )

    def health_check(self) -> bool:
        """GET /api/tags — returns True if Ollama is reachable."""
        try:
            r = httpx.get(f"{self._base_url}/api/tags", timeout=self._health_timeout)
            return r.status_code == 200
        except Exception:
            return False

    def list_models(self) -> list[str]:
        """Return names of all locally available models."""
        try:
            r = httpx.get(f"{self._base_url}/api/tags", timeout=self._health_timeout)
            r.raise_for_status()
            return [m["name"] for m in r.json().get("models", [])]
        except Exception as exc:
            logger.error("Failed to list models: %s", exc)
            return []

    def _resolve_model(self, model: str) -> str:
        """Resolve the requested model to one that is actually available in Ollama."""
        try:
            available = self.list_models()
            if not available:
                return model
            if model in available:
                return model
            # Strip tags and try prefix matches
            # e.g., "llama3.2:3b-q4_0" -> base name "llama3.2:3b" or "llama3.2"
            base_requested = model.split("-")[0]
            for m in available:
                if m.startswith(base_requested) or base_requested.startswith(m.split("-")[0]):
                    return m
            # Try to match the family prefix, e.g. "llama3.2" in "llama3.2:1b"
            family_requested = model.split(":")[0]
            for m in available:
                if m.startswith(family_requested) or family_requested.startswith(m.split(":")[0]):
                    return m
            # If no match, return the original model name
            return model
        except Exception:
            return model

    @staticmethod
    def _build_messages(prompt: str, system_prompt: str) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return messages
