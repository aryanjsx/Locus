"""
AURA — Intelligence Router (Phase 2).

Every voice command passes through this router. It classifies intent,
selects the right model, and decides whether RAG memory retrieval is needed.

Uses a two-tier classification strategy:
  1. Fast regex patterns for obvious intents (instant, no LLM call)
  2. LLM-based classification for ambiguous commands (with retry + fallback)
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from aura.core.ollama_client import OllamaClient
from aura.core.event_bus import EventType, bus
from aura.schemas.intent import IntentObject, IntentType

logger = logging.getLogger("aura.router")


# Exact system prompt per spec — do NOT modify
ROUTER_CLASSIFY_V1_PROMPT = """\
You are Kommy's intent classifier. Classify the user's command.

Return ONLY valid JSON. No explanation. No markdown. No preamble.

Schema:
{
  "intent_type": "<INTENT_TYPE>",
  "confidence": <float 0.0-1.0>,
  "entities": { "<key>": "<value>" },
  "requires_rag": <true|false>
}

Valid intent types: GENERAL_KNOWLEDGE, CODE_GENERATION, SYSTEM_COMMAND,
DEV_TASK, PROJECT_CONTEXT, VISION_TASK, REALTIME_QUERY, UNKNOWN"""


# Model selection map: IntentType -> config key under "models"
_MODEL_MAP: dict[IntentType, str] = {
    IntentType.GENERAL_KNOWLEDGE: "general",
    IntentType.CODE_GENERATION: "code",
    IntentType.SYSTEM_COMMAND: "fast",
    IntentType.DEV_TASK: "fast",
    IntentType.PROJECT_CONTEXT: "general",
    IntentType.VISION_TASK: "vision",
    IntentType.REALTIME_QUERY: "general",
    IntentType.UNKNOWN: "general",
}


_FAST_PATTERNS: list[tuple[re.Pattern, IntentType, dict[str, str]]] = [
    # System commands with entity extraction
    (re.compile(r"\b(create|make|delete|remove|rename|move|copy)\b.+\b(file|folder|directory)\b", re.I),
     IntentType.SYSTEM_COMMAND, {}),
    (re.compile(r"\b(open|launch|start)\b\s+(.+)", re.I),
     IntentType.SYSTEM_COMMAND, {"action": "open_app"}),
    (re.compile(r"\b(shutdown|shut down|power off|turn off)\b", re.I),
     IntentType.SYSTEM_COMMAND, {"action": "shutdown"}),
    (re.compile(r"\b(restart|reboot)\b", re.I),
     IntentType.SYSTEM_COMMAND, {"action": "restart"}),
    (re.compile(r"\b(log off|logoff|sign out|logout)\b", re.I),
     IntentType.SYSTEM_COMMAND, {"action": "log_off"}),
    (re.compile(r"\b(close|quit|exit)\s+(?:the\s+)?(?:app\s+)?(.+)", re.I),
     IntentType.SYSTEM_COMMAND, {"action": "close_app"}),
    (re.compile(r"\b(kill|terminate|force quit)\s+(?:the\s+)?(?:process\s+)?(.+)", re.I),
     IntentType.SYSTEM_COMMAND, {"action": "kill_process"}),
    (re.compile(r"\b(screenshot|volume|brightness)\b", re.I),
     IntentType.SYSTEM_COMMAND, {}),
    # CPU/RAM/system stats
    (re.compile(r"\b(cpu|processor|ram|memory|battery|disk)\b", re.I),
     IntentType.SYSTEM_COMMAND, {"action": "get_stats"}),
    # Dev tasks — before CODE_GENERATION so "push my code" matches push, not code
    (re.compile(r"\b(git |docker |npm |pip |yarn |push|pull|commit|deploy|build)\b", re.I),
     IntentType.DEV_TASK, {}),
    # Code generation
    (re.compile(r"\b(write|code|function|class|implement|refactor|debug|fix bug|script)\b.*\b(in|for|using|with)?\b", re.I),
     IntentType.CODE_GENERATION, {}),
    # Vision
    (re.compile(r"\b(screen|see|look at|what.s on my|describe my)\b", re.I),
     IntentType.VISION_TASK, {}),
    # Project context
    (re.compile(r"\b(what routes|my project|this project|codebase|in my repo|project have)\b", re.I),
     IntentType.PROJECT_CONTEXT, {}),
    # Realtime
    (re.compile(r"\b(latest|current|today|price|stock|weather|news)\b", re.I),
     IntentType.REALTIME_QUERY, {}),
    # General knowledge
    (re.compile(r"\b(what is|who is|explain|how does|why does|tell me about|define|meaning of|difference between)\b", re.I),
     IntentType.GENERAL_KNOWLEDGE, {}),
]


class IntentRouter:
    """Classifies user intent via fast regex then LLM fallback."""

    def __init__(
        self,
        config: dict,
        ollama_client: OllamaClient,
        event_bus: Any = None,
    ) -> None:
        self._config = config
        self._ollama = ollama_client
        self._event_bus = event_bus or bus
        self._models: dict[str, str] = config.get("models", {})
        routing_cfg = config.get("routing", {})
        self._intent_timeout: int = routing_cfg.get("intent_timeout_seconds", 10)
        self._max_retries: int = routing_cfg.get("intent_max_retries", 3)
        self._fast_confidence: float = float(routing_cfg.get("fast_confidence", 0.85))
        self._fallback_confidence: float = float(routing_cfg.get("fallback_confidence", 0.3))

    def classify(self, raw_text: str) -> IntentObject:
        """Classify a voice-transcribed command using two-tier strategy.

        Tier 1: Fast regex patterns for obvious intents (instant, no LLM call).
        Tier 2: LLM classification via Ollama for ambiguous input, with
                 per-attempt timeout (default 10s) and retry on invalid JSON
                 (default 3 attempts).
        Fallback: IntentType.UNKNOWN after all retries exhausted or Ollama
                  unavailable — routes into the RAG+LLM response path.
        """
        cleaned = raw_text.lower().strip()

        # Tier 1: Fast-path regex
        fast_result = self._fast_classify(raw_text, cleaned)
        if fast_result is not None:
            logger.info("Fast-classified as %s", fast_result.intent_type.name)
            return fast_result

        # Tier 2: LLM classification with retry + timeout
        llm_result = self._llm_classify(raw_text, cleaned)
        if llm_result is not None:
            logger.info(
                "LLM-classified as %s (confidence=%.2f)",
                llm_result.intent_type.name, llm_result.confidence,
            )
            self._event_bus.emit(EventType.INTENT_CLASSIFIED, {
                "intent_type": llm_result.intent_type.name,
                "confidence": llm_result.confidence,
                "raw_text": raw_text,
                "fast_path": False,
            })
            return llm_result

        # Final fallback: UNKNOWN (not GENERAL_KNOWLEDGE) per spec Section 2.3
        fallback = IntentObject(
            intent_type=IntentType.UNKNOWN,
            raw_text=raw_text,
            cleaned_text=cleaned,
            entities={},
            model_override=self._models.get("general"),
            requires_rag=True,
            confidence=self._fallback_confidence,
        )
        self._event_bus.emit(EventType.INTENT_CLASSIFIED, {
            "intent_type": fallback.intent_type.name,
            "confidence": fallback.confidence,
            "raw_text": raw_text,
            "fast_path": False,
        })
        logger.warning(
            "LLM classification failed after %d retries - falling back to UNKNOWN",
            self._max_retries,
        )
        return fallback

    def _fast_classify(self, raw_text: str, cleaned: str) -> IntentObject | None:
        """Instant regex-based classification for obvious patterns."""
        for pattern, intent_type, default_entities in _FAST_PATTERNS:
            if pattern.search(cleaned):
                model_key = _MODEL_MAP.get(intent_type, "general")
                model_override = self._models.get(model_key)

                # Merge default entities (e.g. {"action": "get_stats"} for CPU queries)
                entities = dict(default_entities)

                result = IntentObject(
                    intent_type=intent_type,
                    raw_text=raw_text,
                    cleaned_text=cleaned,
                    entities=entities,
                    model_override=model_override,
                    requires_rag=False,
                    confidence=self._fast_confidence,
                )
                self._event_bus.emit(EventType.INTENT_CLASSIFIED, {
                    "intent_type": result.intent_type.name,
                    "confidence": result.confidence,
                    "raw_text": raw_text,
                    "fast_path": True,
                })
                return result
        return None

    def _llm_classify(self, raw_text: str, cleaned: str) -> IntentObject | None:
        """Tier 2: LLM-based classification with retry and timeout.

        Calls Ollama with ROUTER_CLASSIFY_V1_PROMPT, enforcing per-attempt
        timeout of self._intent_timeout seconds. Retries up to self._max_retries
        times on invalid/unparseable JSON. Returns None if all retries fail or
        Ollama is unreachable.
        """
        from aura.core.ollama_client import OllamaUnavailableError

        model = self._models.get("fast", self._models.get("general", ""))

        for attempt in range(1, self._max_retries + 1):
            try:
                response = self._ollama.chat(
                    model=model,
                    prompt=raw_text,
                    system_prompt=ROUTER_CLASSIFY_V1_PROMPT,
                    num_predict=120,
                    timeout=self._intent_timeout,
                )
                result = self._parse_response(response.text, raw_text)
                if result is not None:
                    return result

                logger.warning(
                    "LLM returned unparseable JSON (attempt %d/%d): %r",
                    attempt, self._max_retries, response.text[:200],
                )

            except OllamaUnavailableError:
                logger.error("Ollama unavailable during classification - giving up")
                return None
            except Exception as exc:
                logger.warning(
                    "LLM classification attempt %d/%d failed: %s",
                    attempt, self._max_retries, exc,
                )

        return None

    def _parse_response(self, text: str, raw_text: str) -> IntentObject | None:
        """Parse JSON from LLM response, returning IntentObject or None."""
        # Strip markdown fences if the model included them
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            cleaned = "\n".join(lines)

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            # Try to find JSON object in the response
            start = cleaned.find("{")
            end = cleaned.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    data = json.loads(cleaned[start:end])
                except json.JSONDecodeError:
                    return None
            else:
                return None

        intent_str = data.get("intent_type", "").upper()
        try:
            intent_type = IntentType[intent_str]
        except KeyError:
            return None

        confidence = data.get("confidence", 0.5)
        if not isinstance(confidence, (int, float)):
            confidence = 0.5
        confidence = max(0.0, min(1.0, float(confidence)))

        # Pass LLM-returned entities directly — do NOT override with {}
        entities = data.get("entities", {})
        if not isinstance(entities, dict):
            entities = {}

        requires_rag = bool(data.get("requires_rag", False))
        model_key = _MODEL_MAP.get(intent_type, "general")
        model_override = self._models.get(model_key)

        return IntentObject(
            intent_type=intent_type,
            raw_text=raw_text,
            cleaned_text=raw_text.lower().strip(),
            entities=entities,
            model_override=model_override,
            requires_rag=requires_rag,
            confidence=confidence,
        )
