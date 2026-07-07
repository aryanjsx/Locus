# tests/test_destructive_gate.py
# Verifies that CommandEngine ALWAYS routes destructive actions through
# SafetyGate regardless of the is_destructive flag on the incoming plan.
#
# This test catches the exact bug from Violation #1: BrainController could
# emit is_destructive=False for actions like shutdown/restart, and the old
# code would skip SafetyGate entirely.

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from aura.core.event_bus import bus
from aura.schemas.command import (
    CommandPlan,
    DESTRUCTIVE_ACTIONS,
    ExecutionResult,
    ExecutorType,
)


@pytest.fixture()
def engine():
    """Build a CommandEngine with a mocked SafetyGate that always denies."""
    from aura.core.command_engine import CommandEngine

    config = {
        "safety": {"confirmation_timeout": 1, "audit_log": "/tmp/test_audit.log"},
        "executors": {"shell_timeout": 5},
    }
    mock_gate = MagicMock()
    mock_gate.check = MagicMock(return_value=False)

    eng = CommandEngine(config, event_bus=bus, safety_gate=mock_gate)
    return eng, mock_gate


class TestDestructiveActionsAlwaysGated:
    """For every action in DESTRUCTIVE_ACTIONS, even with is_destructive=False
    artificially forced, CommandEngine must still call SafetyGate.check()."""

    @pytest.mark.parametrize(
        "executor,action",
        sorted(DESTRUCTIVE_ACTIONS, key=lambda t: (t[0].name, t[1])),
        ids=[f"{ex.name}.{act}" for ex, act in sorted(DESTRUCTIVE_ACTIONS, key=lambda t: (t[0].name, t[1]))],
    )
    def test_safety_gate_called_despite_false_flag(self, executor, action, engine):
        eng, mock_gate = engine

        plan = CommandPlan(
            executor=executor,
            action=action,
            params={},
            is_destructive=False,
            requires_confirm=False,
        )

        result = eng.execute(plan)

        mock_gate.check.assert_called_once()
        called_plan = mock_gate.check.call_args[0][0]
        assert called_plan.is_destructive is True, (
            f"{executor.name}.{action}: is_destructive was not re-derived to True"
        )
        assert result.success is False
        assert result.output == "Cancelled."


class TestNonDestructiveSkipsGate:
    """Actions NOT in DESTRUCTIVE_ACTIONS should NOT trigger SafetyGate
    unless requires_confirm is explicitly True."""

    @pytest.mark.parametrize(
        "executor,action",
        [
            (ExecutorType.SYSTEM, "open_app"),
            (ExecutorType.SYSTEM, "screenshot"),
            (ExecutorType.MONITOR, "get_stats"),
            (ExecutorType.LLM_ONLY, "llm_response"),
        ],
    )
    def test_non_destructive_skips_safety_gate(self, executor, action, engine):
        eng, mock_gate = engine

        plan = CommandPlan(
            executor=executor,
            action=action,
            params={},
            is_destructive=False,
            requires_confirm=False,
        )

        eng.execute(plan)
        mock_gate.check.assert_not_called()
