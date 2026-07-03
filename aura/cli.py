"""
AURA — CLI entry point.

Boot sequence (HARDENED):

1. Load + validate configuration (fails hard on missing required keys).
2. Acquire the event-bus singleton.
3. Configure the structured logger and attach it to the bus.
4. Install centralised error subscribers + audit-log subscriber.
5. Spawn the isolated execution worker (:mod:`aura.worker`) and receive
   its advertised action schema.
6. Build the :class:`CommandRegistry` with :class:`WorkerClient` as its
   dispatcher; import action metadata from the worker's schema.
7. Create the router with per-source rate limiter, permission
   validator, non-blocking safety gate, and the in-process intent
   parsers (:mod:`aura.intents`).
8. Run either a one-shot command (``python -m aura "cpu"``) or the REPL.

Isolation invariants
--------------------
The main process NEVER imports ``plugins.*``.  A compromised plugin
therefore cannot reach the event bus, the audit log, the rate limiter,
or the safety gate of the main process — it can only corrupt its own
worker, which the client will respawn on crash.
"""

from __future__ import annotations

import atexit
import sys
from pathlib import Path

import os

from aura.security.audit_events import get_audit_event_registry
from aura.security.audit_log import AuditLogger
from aura.runtime.command_registry import CommandRegistry
from aura.core.config_loader import get as get_config
from aura.core.config_loader import load_config
from aura.core.error_handler import handle_error, install_default_subscribers
from aura.core.errors import AuraError
from aura.core.event_bus import get_event_bus
from aura.core.io import InputSource, OutputSink, StdinInput, StdoutOutput
from aura.core.logger import attach_event_bus_logger, get_logger
from aura.security.permissions import PermissionLevel, PermissionValidator
from aura.security.plugin_manifest import (
    PluginManifest,
    default_manifest_path,
    manifest_sha256,
)
from aura.security.rate_limiter import RateLimiter
from aura.runtime.router import Router
from aura.security.safety_gate import AutoConfirmGate, SafetyGate
from aura.runtime.worker_client import WorkerClient
from aura.intents import default_intent_parsers

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

BANNER = r"""
    ___   __  ______  ___
   /   | / / / / __ \/   |
  / /| |/ / / / /_/ / /| |
 / ___ / /_/ / _, _/ ___ |
/_/  |_\____/_/ |_/_/  |_|

Autonomous Unified Response Architecture
"""


# Short, single-line usage examples per action.  Shown in `help` so
# users don't have to guess the accepted phrasing.  Both the natural
# language form and the canonical action-id form are supported by the
# default intent parser chain; we show the shorter of the two here.
_USAGE_EXAMPLES: dict[str, str] = {
    "system.cpu":    "cpu  |  system.cpu",
    "system.ram":    "ram  |  system.ram",
    "system.health": "system health  |  system.health",
    "process.list":  "list processes  |  process.list",
    "process.kill":  "kill process <name>  |  process.kill process_name=<name>",
    "process.shell": "run command <cmd>  |  process.shell command=\"<cmd>\"",
    "file.create":   "create file <path>  |  file.create path=<path>",
    "file.delete":   "delete file <path>  |  file.delete path=<path>",
    "file.rename":   "rename file <old> <new>  |  file.rename old_name=<o> new_name=<n>",
    "file.move":     "move file <src> <dst>  |  file.move source=<s> destination=<d>",
    "file.search":   "search files <dir> <pattern>  |  file.search directory=<d> pattern=<p>",
    "project.create": "create project <path> [--stack python|node|react|fastapi]",
    "log.show":       "show logs <file> [n]  |  log.show filepath=<f> lines=<n>",
    "npm.install":   "npm install [cwd]  |  npm.install cwd=<dir>",
    "npm.run":       "npm run <script> [cwd]  |  npm.run script=<s> cwd=<d>",
}


def _build_help(registry: CommandRegistry) -> str:
    lines = ["Available commands:"]
    for entry in sorted(registry.list(), key=lambda e: e["action"]):
        flag = " [destructive]" if entry["destructive"] else ""
        level = entry.get("permission_level", "?")
        desc = entry["description"] or ""
        lines.append(
            f"  {entry['action']:<18} [{entry['plugin']}] "
            f"({level})  {desc}{flag}"
        )
        example = _USAGE_EXAMPLES.get(entry["action"])
        if example:
            lines.append(f"    usage: {example}")
    lines.append(
        "\nAction ids (e.g. 'system.ram', 'file.create path=foo.txt') "
        "are accepted in addition to the natural-language phrases above."
    )
    lines.append("Type 'exit' or 'quit' to leave, 'help' to repeat this list.")
    return "\n".join(lines)


def bootstrap(
    *,
    auto_confirm: bool | None = None,
) -> tuple[Router, CommandRegistry]:
    """Build and wire every subsystem.  Fails fast on misconfiguration.

    **Lockdown note**: the worker is deliberately NOT returned.  The
    only supported execution path is ``Router → CommandRegistry.execute
    → Dispatcher → Worker``.  Exposing the worker to the caller would
    re-open the very backdoor the Phase-2 lockdown eliminated.
    """
    load_config()

    bus = get_event_bus()
    logger = get_logger("aura")
    attach_event_bus_logger(bus, logger)
    install_default_subscribers(bus, logger)

    audit = AuditLogger(bus)
    audit.subscribe()

    # Verify the existing audit chain at startup.  Only warn when the
    # log genuinely exists with entries and the chain is broken — fresh
    # installs and empty logs should never alarm the user.
    try:
        from aura.security.audit_log import verify_chain
        _audit_path = audit.path
        if _audit_path.exists() and _audit_path.stat().st_size > 0:
            ok, bad_line = verify_chain(_audit_path)
            if not ok:
                bus.emit("audit.chain_break", {"path": str(_audit_path), "bad_line": bad_line})
                sys.stderr.write(
                    "[AURA] SECURITY WARNING: Audit log integrity check failed. "
                    "Log may have been tampered with. See logs/audit.log.\n"
                )
    except Exception:
        pass

    # Load the plugin safety manifest BEFORE spawning the worker.  We
    # bind its SHA-256 to the worker via an environment variable so
    # the worker can refuse to start if its local manifest differs.
    manifest_path = default_manifest_path(_PROJECT_ROOT)
    manifest = PluginManifest.load(manifest_path)
    manifest_hash = manifest_sha256(manifest_path)
    os.environ["AURA_MANIFEST_SHA256"] = manifest_hash

    worker = WorkerClient(bus, project_root=_PROJECT_ROOT)
    # Pass the hash down so it lands in the worker's restricted env
    # regardless of the caller's own environment.
    worker._bind_manifest_for_worker(manifest_path)
    schema = worker.start()
    atexit.register(worker.shutdown)

    audit_events = get_audit_event_registry()

    if auto_confirm is None:
        auto_confirm = bool(get_config("safety.auto_confirm", False))

    safety_gate: SafetyGate = (
        AutoConfirmGate(bus) if auto_confirm else SafetyGate(bus)
    )
    rate_limiter = RateLimiter()
    permission_validator = PermissionValidator()

    # Security policy is fixed at construction.  There is no
    # ``attach_security`` after this point — the registry is locked.
    registry = CommandRegistry(
        bus,
        worker,
        manifest=manifest,
        rate_limiter=rate_limiter,
        permission_validator=permission_validator,
        safety_gate=safety_gate,
        auto_confirm=auto_confirm,
    )
    for entry in schema:
        action = entry["action"]
        plugin_name = entry.get("plugin", "system")
        destructive = bool(entry.get("destructive", False))
        level = PermissionLevel.parse(entry.get("permission_level", "MEDIUM"))

        manifest_entry = manifest.check(
            plugin=plugin_name,
            action=action,
            permission_level=level,
            destructive=destructive,
        )
        audit_events.register_action_coverage(
            action, manifest_entry.audit_events
        )
        if destructive:
            audit_events.require_coverage(action)

        registry.register_metadata(
            action,
            plugin=plugin_name,
            description=entry.get("description", ""),
            destructive=destructive,
            permission_level=level,
        )

    # Re-subscribe the audit logger now that plugin-specific events
    # may have been registered (idempotent per-event).
    audit.subscribe()

    router = Router(
        bus,
        registry,
        default_intent_parsers(),
    )
    return router, registry


def _mode_line() -> str:
    """Return a human-readable mode indicator line."""
    try:
        from aura.utils.mode_monitor import ModeMonitor
        online = ModeMonitor.is_online()
    except Exception:
        online = False
    return "  Mode: ONLINE \u2705" if online else "  Mode: OFFLINE \U0001f534"


_STACK_CHOICES = ("python", "node", "react", "fastapi")


def _prompt_stack(src: InputSource, sink: OutputSink) -> str | None:
    """Ask the user to choose a tech stack interactively.

    Returns the stack name, or ``None`` if the user cancels.
    """
    sink.send("\nSelect a tech stack:")
    for idx, name in enumerate(_STACK_CHOICES, 1):
        sink.send(f"  [{idx}] {name}")
    sink.send("")

    while True:
        sink.send("Enter number (or 'cancel'): ")
        choice = src.get_command()
        if choice is None or (choice and choice.strip().lower() == "cancel"):
            return None
        if choice and choice.strip().isdigit():
            num = int(choice.strip())
            if 1 <= num <= len(_STACK_CHOICES):
                return _STACK_CHOICES[num - 1]
        sink.send(f"  Please enter 1-{len(_STACK_CHOICES)} or 'cancel'.")


def _needs_stack_prompt(text: str) -> bool:
    """Return True when the command is ``create project <path>`` without
    an explicit ``--stack`` flag."""
    tokens = text.strip().split()
    if len(tokens) < 3:
        return False
    if tokens[0].lower() != "create" or tokens[1].lower() != "project":
        return False
    return "--stack" not in tokens


def run_repl(
    router: Router,
    registry: CommandRegistry,
    *,
    input_source: InputSource | None = None,
    output_sink: OutputSink | None = None,
) -> None:
    src = input_source or StdinInput()
    sink = output_sink or StdoutOutput()

    sink.send(BANNER)
    sink.send(_mode_line())
    sink.send(_build_help(registry))

    while True:
        text = src.get_command()
        if text is None:
            sink.send("\nExiting Kommy.")
            return
        if not text:
            continue
        low = text.lower()
        if low in ("exit", "quit"):
            sink.send("Goodbye.")
            return
        if low == "help":
            sink.send(_build_help(registry))
            continue

        if _needs_stack_prompt(text):
            stack = _prompt_stack(src, sink)
            if stack is None:
                sink.send("Cancelled.")
                continue
            text = f"{text} --stack {stack}"

        result = router.route(text, source="cli")
        sink.send(result.message)


def print_help(registry: CommandRegistry) -> None:
    """Print the help text (shared between REPL and --yes mode)."""
    print(_build_help(registry))


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="aura",
        description="AURA \u2014 Autonomous Unified Response Architecture",
    )
    parser.add_argument(
        "--version", action="store_true",
        help="Print AURA version and exit",
    )
    parser.add_argument(
        "--yes", metavar="COMMAND",
        help="Run a single command non-interactively (auto-confirm) and exit",
    )
    parser.add_argument(
        "--test", action="store_true",
        help=argparse.SUPPRESS,
    )
    args, remaining = parser.parse_known_args(
        sys.argv[1:] if argv is None else argv,
    )

    if args.version:
        from aura import __version__
        print(f"AURA {__version__}")
        return 0

    if args.test and os.environ.get("AURA_DEV") == "1":
        text = " ".join(remaining) if remaining else "(no input)"
        print(f"Received input: {text}")
        return 0

    auto_confirm_flag = args.yes is not None

    if args.yes is not None:
        if not args.yes.strip():
            print("[AURA] No command provided. Use --yes \"<command>\".")
            return 1

    try:
        router, registry = bootstrap(auto_confirm=auto_confirm_flag or None)
    except AuraError as exc:
        result = handle_error(exc, context={"phase": "bootstrap"})
        print(result.message, file=sys.stderr)
        return 2

    if args.yes is not None:
        text = args.yes.strip()
        low = text.lower()
        if low == "help":
            print_help(registry)
            return 0
        if low in ("exit", "quit"):
            print("Goodbye.")
            return 0
        result = router.route(text, source="cli")
        print(result.message)
        return 0 if result.success else 1

    if remaining:
        text = " ".join(remaining)
        result = router.route(text, source="cli")
        print(result.message)
        return 0 if result.success else 1

    try:
        run_repl(router, registry)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
