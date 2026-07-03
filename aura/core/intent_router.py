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
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from aura.core.ollama_client import OllamaClient, OllamaUnavailableError
from aura.core.event_bus import EventType, bus

logger = logging.getLogger("aura.router")


class IntentType(str, Enum):
    """All recognized user intent categories."""

    GENERAL_KNOWLEDGE = "GENERAL_KNOWLEDGE"
    CODE_GENERATION = "CODE_GENERATION"
    SYSTEM_COMMAND = "SYSTEM_COMMAND"
    DEV_TASK = "DEV_TASK"
    PROJECT_CONTEXT = "PROJECT_CONTEXT"
    VISION_TASK = "VISION_TASK"
    REALTIME_QUERY = "REALTIME_QUERY"
    UNKNOWN = "UNKNOWN"


@dataclass
class IntentObject:
    """Structured classification result from the router."""

    intent_type: IntentType
    raw_text: str
    cleaned_text: str
    entities: dict[str, Any]
    model_override: str | None
    requires_rag: bool
    confidence: float
    timestamp: datetime = field(default_factory=datetime.now)


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
    (re.compile(r"\b(kill|stop|close|end)\b\s+(?:the\s+)?(?:process\s+)?(.+)", re.I),
     IntentType.SYSTEM_COMMAND, {"action": "kill_process"}),
    (re.compile(r"\b(screenshot|volume|brightness)\b", re.I),
     IntentType.SYSTEM_COMMAND, {}),
    # CPU/RAM/system stats
    (re.compile(r"\b(cpu|processor|ram|memory|battery|disk)\b", re.I),
     IntentType.SYSTEM_COMMAND, {"action": "get_stats"}),
    # Code generation
    (re.compile(r"\b(write|code|function|class|implement|refactor|debug|fix bug|script)\b.*\b(in|for|using|with)?\b", re.I),
     IntentType.CODE_GENERATION, {}),
    # Dev tasks
    (re.compile(r"\b(git |docker |npm |pip |yarn |push|pull|commit|deploy|build)\b", re.I),
     IntentType.DEV_TASK, {}),
    # Vision
    (re.compile(r"\b(screen|see|look at|what.s on my|describe my)\b", re.I),
     IntentType.VISION_TASK, {}),
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

    def classify(self, raw_text: str) -> IntentObject:
        """Classify a voice-transcribed command.

        Regex-only classification with GENERAL_KNOWLEDGE fallback (no LLM call).
        """
        cleaned = raw_text.lower().strip()

        # Tier 1: Fast-path regex
        fast_result = self._fast_classify(raw_text, cleaned)
        if fast_result is not None:
            logger.info("Fast-classified as %s", fast_result.intent_type.value)
            return fast_result

        # Fallback to GENERAL_KNOWLEDGE (no LLM call)
        fallback = IntentObject(
            intent_type=IntentType.GENERAL_KNOWLEDGE,
            raw_text=raw_text,
            cleaned_text=cleaned,
            entities={},
            model_override=self._models.get("general"),
            requires_rag=True,
            confidence=0.7,
        )
        self._event_bus.emit(EventType.INTENT_CLASSIFIED, {
            "intent_type": fallback.intent_type.value,
            "confidence": fallback.confidence,
            "raw_text": raw_text,
            "fast_path": True,
        })
        logger.info("Unrecognized input — fallback to GENERAL_KNOWLEDGE")
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
                    confidence=0.85,
                )
                self._event_bus.emit(EventType.INTENT_CLASSIFIED, {
                    "intent_type": result.intent_type.value,
                    "confidence": result.confidence,
                    "raw_text": raw_text,
                    "fast_path": True,
                })
                return result
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
            intent_type = IntentType(intent_str)
        except ValueError:
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
            timestamp=datetime.now(),
        )
