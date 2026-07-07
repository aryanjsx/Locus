"""Voice pipeline path: IntentRouter → BrainController → CommandEngine → SafetyGate."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from aura.core.command_engine import CommandEngine
from aura.core.config_loader import load_config
from aura.core.event_bus import bus
from aura.core.intent_router import IntentRouter
from aura.core.llm_brain import BrainController
from aura.schemas.command import DESTRUCTIVE_ACTIONS, ExecutorType


@pytest.fixture()
def voice_pipeline():
    config = load_config()
    router = IntentRouter(config, MagicMock())
    brain = BrainController(config, bus, MagicMock())
    mock_gate = MagicMock()
    mock_gate.check = MagicMock(return_value=False)
    engine = CommandEngine(config, event_bus=bus, safety_gate=mock_gate)
    return router, brain, engine, mock_gate


@pytest.mark.parametrize(
    "utterance,expected_action",
    [
        ("shutdown the computer", "shutdown"),
        ("restart my pc", "restart"),
        ("log off", "log_off"),
        ("close app chrome", "close_app"),
    ],
)
def test_voice_path_triggers_safety_gate(voice_pipeline, utterance, expected_action):
    router, brain, engine, mock_gate = voice_pipeline
    intent = router.classify(utterance)
    plan = brain.handle_intent(intent)
    assert plan.action == expected_action
    assert (ExecutorType.SYSTEM, expected_action) in DESTRUCTIVE_ACTIONS
    engine.execute(plan)
    mock_gate.check.assert_called_once()
