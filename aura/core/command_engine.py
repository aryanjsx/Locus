# aura/core/command_engine.py
# AURA Command Engine — maps IntentObjects to CommandPlans and dispatches
# them to the correct executor. The SafetyGate is called here — never skip it.

from __future__ import annotations
import logging
import time
from typing import Any

from aura.schemas.intent import IntentObject, IntentType
from aura.schemas.command import CommandPlan, DESTRUCTIVE_ACTIONS, ExecutionResult, ExecutorType
from aura.security.safety_gate import SafetyGate
from aura.core.event_bus import bus, EventType
from aura.executors.system_executor import SystemExecutor
from aura.executors.system_monitor import SystemMonitor
from aura.executors.shell_executor import ShellExecutor
from aura.executors.browser_executor import BrowserExecutor

logger = logging.getLogger("aura.command_engine")


class CommandEngine:
    """
    Central dispatcher.

    Flow:
        IntentObject → CommandPlan → SafetyGate.check() → Executor.run() → ExecutionResult
    """

    def __init__(self, config: dict[str, Any], event_bus: Any = None, safety_gate: Any = None) -> None:
        self._config = config
        self._bus = event_bus  # kept for external callers; internal code uses module-level bus
        self._safety  = safety_gate if safety_gate is not None else SafetyGate(bus, config=config)
        self._system  = SystemExecutor(config)
        self._monitor = SystemMonitor(config)
        self._shell   = ShellExecutor(config)
        self._browser = BrowserExecutor(config)

    def receive_safety_confirmation(self, spoken_text: str) -> None:
        """
        Called by STTEngine when it captures a response during
        the AWAITING_CONFIRM pipeline state.
        Forwards to SafetyGate.
        """
        self._safety.receive_confirmation(spoken_text)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def execute(self, intent_or_plan: Any) -> ExecutionResult:
        """
        Accepts either an IntentObject (builds a CommandPlan internally)
        or a CommandPlan directly (from BrainController).

        Runs through SafetyGate if needed, then dispatches to the executor.
        """
        start = time.monotonic()

        # Support both IntentObject and CommandPlan inputs
        if isinstance(intent_or_plan, IntentObject):
            plan = self._build_plan(intent_or_plan)
            if plan is None:
                return ExecutionResult(
                    success=False,
                    output="I understood what you want, but I don't have an executor for that yet.",
                    executor=None,
                )
        else:
            plan = intent_or_plan

        executor_name = plan.executor.name if isinstance(plan.executor, ExecutorType) else str(plan.executor)

        # Re-derive is_destructive from the canonical set — NEVER trust
        # upstream flags alone.  This is the last line of defense.
        executor_for_check = plan.executor if isinstance(plan.executor, ExecutorType) else None
        if executor_for_check is not None:
            actually_destructive = (executor_for_check, plan.action) in DESTRUCTIVE_ACTIONS
        else:
            actually_destructive = plan.is_destructive
        plan.is_destructive = actually_destructive
        plan.requires_confirm = plan.requires_confirm or actually_destructive

        bus.emit(EventType.COMMAND_PLAN_READY, {
            "executor": executor_name,
            "action": plan.action,
            "is_destructive": plan.is_destructive,
        })

        # 2. Safety gate — MUST run before any destructive action
        if plan.is_destructive or plan.requires_confirm:
            bus.emit(EventType.EXECUTION_STARTED, {
                "executor": executor_name,
                "action": plan.action,
                "state": "AWAITING_CONFIRM",
            })
            approved = self._safety.check(plan)
            if not approved:
                result = ExecutionResult(
                    success=False,
                    output="Cancelled.",
                    executor=plan.executor,
                    duration_ms=int((time.monotonic() - start) * 1000),
                )
                bus.emit(EventType.EXECUTION_COMPLETE, {"success": False, "output": "Cancelled."})
                return result
        else:
            bus.emit(EventType.EXECUTION_STARTED, {
                "executor": executor_name,
                "action": plan.action,
            })

        # 3. Dispatch to executor
        result = self._dispatch(plan)
        result.duration_ms = int((time.monotonic() - start) * 1000)

        result_executor_name = None
        if result.executor:
            result_executor_name = result.executor.name if isinstance(result.executor, ExecutorType) else str(result.executor)

        bus.emit(EventType.EXECUTION_COMPLETE, {
            "success": result.success,
            "output": result.output,
            "executor": result_executor_name,
            "duration_ms": result.duration_ms,
        })

        return result

    # ------------------------------------------------------------------
    # Plan builder — maps IntentType + entities → CommandPlan
    # ------------------------------------------------------------------

    def _build_plan(self, intent: IntentObject) -> CommandPlan | None:
        itype    = intent.intent_type
        entities = intent.entities

        # ── SESSION CONTROL ───────────────────────────────────────────
        if itype == IntentType.DEACTIVATE_SESSION:
            return self._make_plan(
                ExecutorType.SESSION,
                "end_session",
                {},
            )

        # ── SYSTEM_COMMAND ────────────────────────────────────────────
        if itype == IntentType.SYSTEM_COMMAND:
            action: str = entities.get("action", "").lower().replace(" ", "_")

            # App launching
            if action in ("open", "open_app", "launch", "start"):
                app = entities.get("app_name", entities.get("target", ""))
                return self._make_plan(
                    ExecutorType.SYSTEM, "open_app",
                    {"app_name": app},
                )
            # URL opening
            if action in ("open_url", "browse", "go_to", "navigate"):
                url = entities.get("url", entities.get("target", ""))
                return self._make_plan(
                    ExecutorType.SYSTEM, "open_url",
                    {"url": url},
                )
            # Closing apps
            if action in ("close", "close_app", "quit", "exit"):
                app = entities.get("app_name", entities.get("process_name", ""))
                return self._make_plan(
                    ExecutorType.SYSTEM, "close_app",
                    {"process_name": app},
                    requires_confirm=True,
                )
            # Screenshot
            if action in ("screenshot", "take_screenshot", "capture_screen"):
                return self._make_plan(
                    ExecutorType.SYSTEM, "screenshot", {}
                )
            # Volume
            if action in ("set_volume", "volume"):
                level = int(entities.get("level", entities.get("volume", 50)))
                return self._make_plan(
                    ExecutorType.SYSTEM, "set_volume", {"level": level}
                )
            if action == "mute":
                return self._make_plan(ExecutorType.SYSTEM, "mute", {})

            # Power management — ALL destructive
            if action in ("shutdown", "shut_down", "turn_off", "power_off"):
                return self._make_plan(
                    ExecutorType.SYSTEM, "shutdown", {},
                    is_destructive=True, requires_confirm=True,
                )
            if action in ("restart", "reboot"):
                return self._make_plan(
                    ExecutorType.SYSTEM, "restart", {},
                    is_destructive=True, requires_confirm=True,
                )
            if action in ("log_off", "logoff", "sign_out", "logout"):
                return self._make_plan(
                    ExecutorType.SYSTEM, "log_off", {},
                    is_destructive=True, requires_confirm=True,
                )
            if action in ("sleep", "hibernate"):
                return self._make_plan(ExecutorType.SYSTEM, "sleep", {})
            if action in ("lock", "lock_screen", "lock_pc"):
                return self._make_plan(ExecutorType.SYSTEM, "lock", {})
            if action in ("minimize_all", "show_desktop"):
                return self._make_plan(ExecutorType.SYSTEM, "minimize_all", {})
            if action in ("kill_process", "kill", "force_quit"):
                return self._make_plan(
                    ExecutorType.SYSTEM, "kill_process",
                    {
                        "process_name": entities.get("process_name", ""),
                        "pid": entities.get("pid"),
                    },
                    is_destructive=True, requires_confirm=True,
                )

            # ── SYSTEM MONITORING ─────────────────────────────────────
            if action in ("get_stats", "system_stats", "status", "health"):
                return self._make_plan(ExecutorType.MONITOR, "get_stats", {})
            if action in ("cpu", "cpu_usage", "processor"):
                return self._make_plan(ExecutorType.MONITOR, "get_cpu", {})
            if action in ("ram", "memory", "ram_usage"):
                return self._make_plan(ExecutorType.MONITOR, "get_ram", {})
            if action in ("battery", "battery_level"):
                return self._make_plan(ExecutorType.MONITOR, "get_battery", {})
            if action in ("disk", "disk_space", "storage"):
                return self._make_plan(ExecutorType.MONITOR, "get_disk", {})
            if action in ("processes", "list_processes", "running"):
                return self._make_plan(ExecutorType.MONITOR, "list_processes", {})

        # ── LLM_ONLY intents — no executor needed ─────────────────────
        if itype in (
            IntentType.GENERAL_KNOWLEDGE,
            IntentType.CODE_GENERATION,
            IntentType.PROJECT_CONTEXT,
            IntentType.UNKNOWN,
        ):
            return self._make_plan(ExecutorType.LLM_ONLY, "llm_response", {})

        return None

    # ------------------------------------------------------------------
    # Executor dispatcher
    # ------------------------------------------------------------------

    def _dispatch(self, plan: CommandPlan) -> ExecutionResult:
        executor = plan.executor

        if executor == ExecutorType.LLM_ONLY:
            return ExecutionResult(
                success=True,
                output="",
                executor=ExecutorType.LLM_ONLY,
                data={
                    "mode": "llm_stream",
                    "model": plan.params.get("model", ""),
                    "prompt": plan.params.get("prompt", ""),
                    "requires_rag": plan.params.get("requires_rag", False),
                    "staleness_warning": plan.params.get("staleness_warning", False),
                },
            )
        if executor == ExecutorType.BROWSER:
            return self._browser.run(plan.action, plan.params)
        if executor == ExecutorType.SESSION:
            bus.emit(EventType.SESSION_ENDED, {"reason": "manual_voice_command"})
            return ExecutionResult(
                success=True,
                output="Goodbye. Say Hey Kommy when you need me again.",
                executor=ExecutorType.SESSION,
            )
        if executor == ExecutorType.SYSTEM:
            return self._system.run(plan.action, plan.params)
        if executor == ExecutorType.MONITOR:
            return self._monitor.run(plan.action, plan.params)
        if executor == ExecutorType.SHELL:
            return self._shell.run(plan.action, plan.params)

        executor_name = executor.name if isinstance(executor, ExecutorType) else str(executor)
        return ExecutionResult(
            success=False,
            output=f"Executor {executor_name} not implemented yet.",
            executor=executor,
        )

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    @staticmethod
    def _make_plan(
        executor: ExecutorType,
        action: str,
        params: dict[str, Any],
        is_destructive: bool = False,
        requires_confirm: bool = False,
        timeout: int = 30,
    ) -> CommandPlan:
        key = (executor, action)
        is_dest = is_destructive or key in DESTRUCTIVE_ACTIONS
        needs_confirm = requires_confirm or is_dest
        return CommandPlan(
            executor=executor,
            action=action,
            params=params,
            is_destructive=is_dest,
            requires_confirm=needs_confirm,
            timeout_seconds=timeout,
        )
