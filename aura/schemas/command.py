# aura/schemas/command.py
# AURA Command Schema — CommandPlan and ExecutionResult.

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

from aura.schemas.intent import IntentObject


class ExecutorType(Enum):
    SYSTEM   = auto()   # open/close apps, shutdown, restart, screenshot, volume
    SHELL    = auto()   # allowlisted shell commands
    MONITOR  = auto()   # CPU, RAM, battery, process list
    SESSION  = auto()   # session control (end session via voice command)
    GIT      = auto()   # Phase 3
    DOCKER   = auto()   # Phase 3
    NPM      = auto()   # Phase 3
    BROWSER  = auto()   # Phase 7
    VISION   = auto()   # Phase 4
    LLM_ONLY = auto()   # No executor — response goes straight to TTS


@dataclass
class CommandPlan:
    executor:          ExecutorType
    action:            str
    params:            dict[str, Any]  = field(default_factory=dict)
    requires_confirm:  bool            = False
    is_destructive:    bool            = False
    timeout_seconds:   int             = 30
    intent_ref:        IntentObject | None = None


@dataclass
class ExecutionResult:
    success:       bool
    output:        str              # human-readable — goes to TTS
    data:          Any              = None
    error:         str | None       = None
    executor:      ExecutorType | None = None
    duration_ms:   int              = 0
    was_confirmed: bool             = False


# ---------------------------------------------------------------------------
# Canonical destructive-action registry.
#
# This is the SINGLE source of truth for which (executor, action) pairs are
# destructive.  CommandEngine re-derives is_destructive from this set before
# calling SafetyGate — upstream flags are advisory only.
#
# Cross-referenced against AURA_ENGINEERING_SPEC.md §4.2 and §5.1.
# ---------------------------------------------------------------------------

DESTRUCTIVE_ACTIONS: frozenset[tuple[ExecutorType, str]] = frozenset({
    # SYSTEM — power/process management (§5.1 + code extensions)
    (ExecutorType.SYSTEM, "shutdown"),
    (ExecutorType.SYSTEM, "restart"),
    (ExecutorType.SYSTEM, "log_off"),
    (ExecutorType.SYSTEM, "kill_process"),
    (ExecutorType.SYSTEM, "close_app"),
    # SYSTEM — file destruction (spec §4.2 FILE.delete/rmdir mapped here)
    (ExecutorType.SYSTEM, "delete_file"),
    (ExecutorType.SYSTEM, "delete_folder"),
    (ExecutorType.SYSTEM, "rmdir"),
    # SHELL — arbitrary commands always confirm (defense-in-depth)
    (ExecutorType.SHELL, "run_command"),
    # GIT — spec §4.2 destructive actions
    (ExecutorType.GIT, "push"),
    (ExecutorType.GIT, "branch_delete"),
    (ExecutorType.GIT, "force_push"),
    (ExecutorType.GIT, "reset_hard"),
    # DOCKER — spec §4.2 destructive actions
    (ExecutorType.DOCKER, "build"),
    (ExecutorType.DOCKER, "remove"),
    (ExecutorType.DOCKER, "prune"),
})
