"""
AURA — Brain Controller (Phase 2).

Plan builder and model selector. Receives a classified IntentObject from
the IntentRouter, selects the appropriate LLM model from config, and
builds a CommandPlan describing what should happen next.

For LLM-backed intents (GENERAL_KNOWLEDGE, CODE_GENERATION, PROJECT_CONTEXT,
UNKNOWN, REALTIME_QUERY): BrainController sets executor=LLM_ONLY and packs
the selected model + prompt into params. The actual Ollama streaming call
happens downstream in main.py's _stream_to_tts() pipeline.

For executor-backed intents (SYSTEM_COMMAND, DEV_TASK, VISION_TASK):
BrainController resolves the action via heuristic entity extraction and
routes directly to the appropriate executor. No LLM step is needed here —
the spec's routing table (§4.4) specifies direct dispatch for these intents.

Model selection rules:
  SYSTEM_COMMAND / DEV_TASK  → config.models.fast
  CODE_GENERATION            → config.models.code
  PROJECT_CONTEXT / UNKNOWN  → config.models.general (+ requires_rag=True)
  GENERAL_KNOWLEDGE          → config.models.general
  VISION_TASK                → config.models.vision
  REALTIME_QUERY             → config.models.general
"""

from __future__ import annotations

import logging
from typing import Any

from aura.schemas.command import CommandPlan, ExecutorType

logger = logging.getLogger("aura.brain")

# IntentType name → (config models key, ExecutorType, requires_rag)
_INTENT_DISPATCH: dict[str, tuple[str, ExecutorType, bool]] = {
    "SYSTEM_COMMAND":     ("fast",    ExecutorType.SYSTEM,   False),
    "DEV_TASK":           ("fast",    ExecutorType.SHELL,    False),
    "CODE_GENERATION":    ("code",    ExecutorType.LLM_ONLY, False),
    "PROJECT_CONTEXT":    ("general", ExecutorType.LLM_ONLY, True),
    "UNKNOWN":            ("general", ExecutorType.LLM_ONLY, True),
    "GENERAL_KNOWLEDGE":  ("general", ExecutorType.LLM_ONLY, False),
    "VISION_TASK":        ("vision",  ExecutorType.VISION,   False),
    "REALTIME_QUERY":     ("general", ExecutorType.LLM_ONLY, False),
}


class BrainController:
    """Plan builder: selects model, resolves action, builds CommandPlan.

    Does NOT call Ollama directly — LLM streaming happens downstream in
    the main pipeline worker via _stream_to_tts().
    """

    def __init__(self, config: dict[str, Any], event_bus: Any, ollama_client: Any) -> None:
        self._config = config
        self._event_bus = event_bus
        # Retained for Phase 3 multi-step plan generation; streaming
        # calls currently happen in main.py's _stream_to_tts() pipeline.
        self._ollama = ollama_client
        self._models: dict[str, str] = config.get("models", {})

    def handle_intent(self, intent_object: Any) -> CommandPlan:
        """Build a CommandPlan from the classified intent and emit COMMAND_PLAN_READY.

        Parameters
        ----------
        intent_object : IntentObject
            The classified intent from IntentRouter.

        Returns
        -------
        CommandPlan
            The plan ready for CommandEngine.execute().
        """
        intent_type = intent_object.intent_type
        intent_type_str = intent_type.name if hasattr(intent_type, "name") else str(intent_type)

        model_key, executor, requires_rag = _INTENT_DISPATCH.get(
            intent_type_str, ("general", ExecutorType.LLM_ONLY, True)
        )

        model = self._models.get(model_key, self._models.get("general", ""))
        if intent_object.model_override:
            model = intent_object.model_override

        # Determine action from entities or intent type
        action = self._resolve_action(intent_type_str, intent_object.entities, intent_object.cleaned_text)

        # Build params
        params: dict[str, Any] = {
            "model": model,
            "prompt": intent_object.cleaned_text,
            "raw_text": intent_object.raw_text,
            "requires_rag": requires_rag or intent_object.requires_rag,
        }
        params.update(intent_object.entities)

        plan = CommandPlan(
            executor=executor,
            action=action,
            params=params,
            timeout_seconds=self._config.get("ollama", {}).get("timeout", 60),
            intent_ref=intent_object,
        )

        logger.info(
            "CommandPlan: executor=%s action=%s model=%s destructive=%s",
            plan.executor, plan.action, model, plan.is_destructive,
        )

        from aura.core.event_bus import EventType
        self._event_bus.emit(EventType.COMMAND_PLAN_READY, {"command_plan": plan})
        return plan

    @staticmethod
    def _resolve_action(intent_type_str: str, entities: dict[str, Any], text: str) -> str:
        """Derive a concrete action string from intent + entities.

        For executor-backed intents (SYSTEM_COMMAND, DEV_TASK, VISION_TASK)
        this performs keyword-based entity extraction — per spec §4.4, these
        intent types route to executors via entity matching, not LLM reasoning.

        For LLM-backed intents (GENERAL_KNOWLEDGE, CODE_GENERATION, etc.),
        returns a generic action string; the real work happens downstream
        when main.py streams from Ollama.
        """
        if entities.get("action"):
            return str(entities["action"])

        # Fallback heuristics based on intent type
        if intent_type_str == "SYSTEM_COMMAND":
            lower = text.lower()
            if any(kw in lower for kw in ("open", "launch", "start")):
                return "open_app"
            if any(kw in lower for kw in ("kill", "stop", "close", "end")):
                return "kill_process"
            if any(kw in lower for kw in ("cpu", "ram", "memory", "battery", "disk")):
                return "get_stats"
            if any(kw in lower for kw in ("create", "make")):
                return "create_file"
            if any(kw in lower for kw in ("delete", "remove")):
                return "delete_file"
            return "system_generic"
        elif intent_type_str == "DEV_TASK":
            lower = text.lower()
            if "push" in lower:
                return "git_push"
            if "pull" in lower:
                return "git_pull"
            if "commit" in lower:
                return "git_commit"
            return "dev_generic"
        elif intent_type_str == "CODE_GENERATION":
            return "generate_code"
        elif intent_type_str == "VISION_TASK":
            return "vision_analyze"
        else:
            return "llm_respond"
