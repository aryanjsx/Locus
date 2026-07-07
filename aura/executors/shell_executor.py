# aura/executors/shell_executor.py
# AURA Shell Executor — runs ONLY allowlisted shell commands.
#
# CRITICAL (Engineering Spec §5.2):
#   - Voice input is NEVER passed to shell directly
#   - ALL commands use subprocess list form — shell=False always
#   - Only commands in COMMAND_ALLOWLIST can be executed
#   - No pipes, redirects, or shell metacharacters allowed

from __future__ import annotations
import logging
import subprocess
import time
from typing import Any

from aura.schemas.command import ExecutionResult, ExecutorType

logger = logging.getLogger("aura.shell_executor")

# -----------------------------------------------------------------------
# ALLOWLIST: The ONLY commands AURA can run via this executor.
# Free-form shell commands from voice input are REJECTED.
# Add entries here to expand capability — never bypass this list.
# -----------------------------------------------------------------------
COMMAND_ALLOWLIST: set[str] = {
    "git",
    "docker",
    "npm",
    "node",
    "python",
    "pip",
    "code",      # VS Code CLI
    "rg",        # ripgrep
    "ls", "dir",
    "pwd",
    "echo",
    "ping",
    "curl",
    "wget",
    "cat",
    "type",
    "find",
}


class ShellExecutor:

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        self._timeout_default: int = int(
            config.get("shell", {}).get("timeout", 120)
        )

    def run(self, action: str, params: dict[str, Any]) -> ExecutionResult:
        ALLOWED = {
            "run_command":      self.run_command,
            "capture_output":   self.capture_output,
        }
        if action not in ALLOWED:
            return ExecutionResult(
                success=False,
                output=f"Unknown shell action: {action}.",
                executor=ExecutorType.SHELL,
            )
        start = time.monotonic()
        result = ALLOWED[action](params)
        result.duration_ms = int((time.monotonic() - start) * 1000)
        return result

    def run_command(self, params: dict[str, Any]) -> ExecutionResult:
        """
        Run a command from the allowlist.

        params:
            command: list[str]   — e.g. ["git", "status"]
            cwd: str (optional)  — working directory
            timeout: int         — seconds, default from config
        """
        command: list[str] = params.get("command", [])
        cwd: str | None    = params.get("cwd")
        timeout: int       = int(params.get("timeout", self._timeout_default))

        if not command:
            return ExecutionResult(
                success=False,
                output="No command provided.",
                executor=ExecutorType.SHELL,
            )
        if not isinstance(command, list):
            return ExecutionResult(
                success=False,
                output="Command must be a list of strings, not a raw string.",
                error="shell injection prevention: raw string rejected",
                executor=ExecutorType.SHELL,
            )

        # Validate first token against allowlist
        base_cmd = command[0].strip().lower()
        if base_cmd not in COMMAND_ALLOWLIST:
            logger.warning(f"ShellExecutor: blocked '{base_cmd}' — not in allowlist")
            return ExecutionResult(
                success=False,
                output=(
                    f"I'm not allowed to run '{base_cmd}' directly. "
                    "That command is not in my allowlist."
                ),
                error=f"'{base_cmd}' not in COMMAND_ALLOWLIST",
                executor=ExecutorType.SHELL,
            )

        # Reject any shell metacharacters in any argument
        for arg in command[1:]:
            if any(c in str(arg) for c in [";", "&&", "||", "|", ">", "<", "`", "$", "\n"]):
                logger.warning(f"ShellExecutor: shell metachar in arg '{arg}' — rejected")
                return ExecutionResult(
                    success=False,
                    output="That command contains unsafe characters. I won't run it.",
                    error=f"Shell metachar in arg: {arg}",
                    executor=ExecutorType.SHELL,
                )

        logger.info(f"ShellExecutor running: {command} cwd={cwd}")
        try:
            result = subprocess.run(
                command,                  # list form — NEVER shell=True
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
                # shell=False is the default — do NOT change this
            )
            success = result.returncode == 0
            output_text = (result.stdout or result.stderr or "Command completed.").strip()
            return ExecutionResult(
                success=success,
                output=output_text[:500] if output_text else "Done.",   # truncate for TTS
                data={"stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode},
                executor=ExecutorType.SHELL,
            )
        except subprocess.TimeoutExpired:
            return ExecutionResult(
                success=False,
                output=f"Command timed out after {timeout} seconds.",
                error="TimeoutExpired",
                executor=ExecutorType.SHELL,
            )
        except FileNotFoundError as exc:
            return ExecutionResult(
                success=False,
                output=f"Command not found: {command[0]}. Is it installed?",
                error=str(exc),
                executor=ExecutorType.SHELL,
            )

    def capture_output(self, params: dict[str, Any]) -> ExecutionResult:
        """Same as run_command but always returns full stdout in data."""
        return self.run_command(params)
