"""Regression tests closing Violation #2 gaps (RAG + REALTIME routing)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from aura.core.command_engine import CommandEngine
from aura.core.event_bus import bus
from aura.core.intent_router import IntentRouter
from aura.core.llm_brain import BrainController
from aura.memory.context_retriever import augment_prompt_with_rag
from aura.schemas.command import ExecutorType
from aura.schemas.intent import IntentType


@pytest.fixture()
def config():
    return {
        "safety": {"confirmation_timeout": 1, "audit_log": "/tmp/test_audit.log"},
        "executors": {"shell_timeout": 5},
        "models": {
            "fast": "llama3",
            "code": "codellama",
            "general": "llama3",
            "embeddings": "nomic-embed-text",
        },
        "ollama": {"timeout": 60},
        "routing": {
            "realtime_warning": True,
            # 0.50 — nomic-embed-text good matches land ~0.55–0.65; 0.72 silently filtered all results
            "rag_confidence_threshold": 0.50,
            "rag_rank_margin": 0.03,
        },
        "memory": {
            "persist_path": ".aura/memory",
            "max_results": 3,
        },
        "browser": {"timeout": 5},
    }


class TestProjectContextRagFlag:
    def test_fast_path_project_context_sets_requires_rag(self, config):
        router = IntentRouter(config, ollama_client=MagicMock(), event_bus=bus)
        intent = router.classify("What routes does my project have?")
        assert intent.intent_type == IntentType.PROJECT_CONTEXT
        assert intent.requires_rag is True

    def test_brain_propagates_requires_rag(self, config):
        router = IntentRouter(config, ollama_client=MagicMock(), event_bus=bus)
        brain = BrainController(config, bus, MagicMock())
        intent = router.classify("What routes does my project have?")
        plan = brain.handle_intent(intent)
        assert plan.executor == ExecutorType.LLM_ONLY
        assert plan.params["requires_rag"] is True


class TestRagAugmentation:
    def test_augment_prompt_with_rag(self):
        result = augment_prompt_with_rag("What is auth?", ["JWT used", "OAuth planned"])
        assert "JWT used" in result
        assert "What is auth?" in result

    def test_llm_stream_carries_requires_rag(self, config):
        mock_gate = MagicMock()
        mock_gate.check = MagicMock(return_value=True)
        eng = CommandEngine(config, event_bus=bus, safety_gate=mock_gate)

        plan = eng._make_plan(  # noqa: SLF001 — test helper
            ExecutorType.LLM_ONLY,
            "llm_response",
            {
                "model": "llama3",
                "prompt": "What did we decide about auth?",
                "requires_rag": True,
            },
        )
        with patch(
            "aura.memory.context_retriever.retrieve_context",
            return_value=["We chose JWT for API tokens."],
        ):
            result = eng.execute(plan)

        assert result.data["requires_rag"] is True
        prompt = augment_prompt_with_rag(
            result.data["prompt"],
            ["We chose JWT for API tokens."],
        )
        assert "JWT" in prompt


class TestRealtimeQueryRouting:
    @patch("aura.core.llm_brain.mode_monitor")
    def test_online_routes_to_browser(self, mock_mode, config):
        mock_mode.is_online.return_value = True
        brain = BrainController(config, bus, MagicMock())
        intent = MagicMock()
        intent.intent_type = IntentType.REALTIME_QUERY
        intent.cleaned_text = "what is the latest node.js version"
        intent.raw_text = intent.cleaned_text
        intent.entities = {}
        intent.model_override = None
        intent.requires_rag = False

        plan = brain.handle_intent(intent)
        assert plan.executor == ExecutorType.BROWSER
        assert plan.action == "search"
        assert plan.params["query"] == intent.cleaned_text
        assert plan.params["requires_rag"] is False

    @patch("aura.core.llm_brain.mode_monitor")
    def test_offline_routes_to_llm_with_staleness(self, mock_mode, config):
        mock_mode.is_online.return_value = False
        brain = BrainController(config, bus, MagicMock())
        intent = MagicMock()
        intent.intent_type = IntentType.REALTIME_QUERY
        intent.cleaned_text = "what is the latest node.js version"
        intent.raw_text = intent.cleaned_text
        intent.entities = {}
        intent.model_override = None
        intent.requires_rag = False

        plan = brain.handle_intent(intent)
        assert plan.executor == ExecutorType.LLM_ONLY
        assert plan.params["staleness_warning"] is True
        assert "offline" in plan.params["prompt"].lower()

    @patch("aura.core.llm_brain.mode_monitor")
    def test_online_browser_reaches_tts_output(self, mock_mode, config):
        mock_mode.is_online.return_value = True
        brain = BrainController(config, bus, MagicMock())
        intent = MagicMock()
        intent.intent_type = IntentType.REALTIME_QUERY
        intent.cleaned_text = "latest node.js version"
        intent.raw_text = intent.cleaned_text
        intent.entities = {}
        intent.model_override = None
        intent.requires_rag = False

        plan = brain.handle_intent(intent)
        mock_gate = MagicMock()
        mock_gate.check = MagicMock(return_value=True)
        eng = CommandEngine(config, event_bus=bus, safety_gate=mock_gate)

        from aura.schemas.command import ExecutionResult

        fake_result = ExecutionResult(
            success=True,
            output="Node.js 22 is the current LTS.",
            executor=ExecutorType.BROWSER,
        )

        with patch.object(eng._browser, "run", return_value=fake_result):  # noqa: SLF001
            result = eng.execute(plan)

        assert result.output == "Node.js 22 is the current LTS."
        assert result.executor == ExecutorType.BROWSER

    @patch("aura.core.llm_brain.mode_monitor")
    def test_offline_llm_stream_carries_staleness_flag(self, mock_mode, config):
        mock_mode.is_online.return_value = False
        brain = BrainController(config, bus, MagicMock())
        intent = MagicMock()
        intent.intent_type = IntentType.REALTIME_QUERY
        intent.cleaned_text = "latest node.js version"
        intent.raw_text = intent.cleaned_text
        intent.entities = {}
        intent.model_override = None
        intent.requires_rag = False

        plan = brain.handle_intent(intent)
        mock_gate = MagicMock()
        mock_gate.check = MagicMock(return_value=True)
        eng = CommandEngine(config, event_bus=bus, safety_gate=mock_gate)
        result = eng.execute(plan)

        assert result.data["mode"] == "llm_stream"
        assert result.data["staleness_warning"] is True


class TestUnknownRagPath:
    def test_unknown_fallback_sets_requires_rag(self, config):
        router = IntentRouter(config, ollama_client=MagicMock(), event_bus=bus)
        intent = router.classify("xyzzy plugh nonsense command")
        assert intent.intent_type == IntentType.UNKNOWN
        assert intent.requires_rag is True

        brain = BrainController(config, bus, MagicMock())
        plan = brain.handle_intent(intent)
        assert plan.params["requires_rag"] is True
        assert plan.executor == ExecutorType.LLM_ONLY
