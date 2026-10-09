"""The MVP action set, plus the JSON schema and system prompt built from it.

Single source of truth for the router benchmark: the schema constrains the
model's output (llama.cpp grammar), and the prompt is generated from the same
specs so the two can't drift apart.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

Tier = Literal["read_only", "reversible", "check_first", "destructive"]

LOCATIONS = ("Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos")


@dataclass(frozen=True)
class Param:
    name: str
    enum: tuple[str, ...] = ()  # empty = free-form string


@dataclass(frozen=True)
class Action:
    name: str
    tier: Tier
    description: str
    params: tuple[Param, ...] = field(default_factory=tuple)


ACTIONS: tuple[Action, ...] = (
    Action("system.cpu", "read_only", "current CPU usage"),
    Action("system.ram", "read_only", "current memory/RAM usage"),
    Action("system.disk", "read_only", "free disk space", (Param("drive"),)),
    Action("system.battery", "read_only", "battery level or charging state"),
    Action(
        "system.processes",
        "read_only",
        "list the top running processes",
        (Param("sort", ("cpu", "memory")),),
    ),
    Action("system.datetime", "read_only", "current time or date"),
    Action("app.open", "reversible", "open/launch an application", (Param("app"),)),
    Action("app.close", "check_first", "close an application", (Param("app"),)),
    Action("folder.open", "reversible", "open a folder in Explorer", (Param("path"),)),
    Action(
        "folder.create",
        "reversible",
        "create a folder",
        (Param("name"), Param("location")),
    ),
    Action(
        "file.create",
        "reversible",
        "create a file",
        (Param("name"), Param("location")),
    ),
    Action(
        "file.rename",
        "reversible",
        "rename a file or folder",
        (Param("path"), Param("new_name")),
    ),
    Action(
        "file.move",
        "reversible",
        "move a file or folder",
        (Param("path"), Param("destination")),
    ),
    Action("file.delete", "destructive", "delete a file or folder", (Param("path"),)),
    Action(
        "file.search",
        "read_only",
        "find files by name or type",
        (Param("query"), Param("location")),
    ),
    Action("power.lock", "reversible", "lock this PC"),
    Action("power.shutdown", "destructive", "shut down this PC"),
    Action("power.restart", "destructive", "restart this PC"),
    Action(
        "qa.answer",
        "read_only",
        "answer a question, explain something or write code, including any "
        '"how do I..." or "why does..." question about the topics above',
    ),
    Action("assistant.help", "read_only", "say what Locus can do"),
    Action("assistant.cancel", "read_only", "stop, cancel, never mind"),
    Action(
        "assistant.needs_online",
        "read_only",
        "needs live internet information (weather, news, prices, scores, latest versions)",
        (Param("feature", ("web_search",)),),
    ),
    Action(
        "assistant.unsupported",
        "read_only",
        "anything else Locus can't do (email, messages, music playback, alarms, volume, git, "
        'shell commands, formatting drives); reason "multi_step" if the user asks for more '
        "than one action",
        (Param("reason", ("not_supported", "multi_step")),),
    ),
)

BY_NAME: dict[str, Action] = {a.name: a for a in ACTIONS}
DESTRUCTIVE: frozenset[str] = frozenset(a.name for a in ACTIONS if a.tier == "destructive")


def _param_schema(p: Param) -> dict[str, Any]:
    value: dict[str, Any] = {"type": "string", "enum": list(p.enum)} if p.enum else {
        "type": "string"
    }
    # Every param may be null: the model must not invent values the user didn't say.
    return {"anyOf": [value, {"type": "null"}]}


def build_schema() -> dict[str, Any]:
    """One object variant per action, so params are checked per action."""
    variants = []
    for a in ACTIONS:
        variants.append(
            {
                "type": "object",
                "properties": {
                    "action": {"const": a.name},
                    "params": {
                        "type": "object",
                        "properties": {p.name: _param_schema(p) for p in a.params},
                        "required": [p.name for p in a.params],
                        "additionalProperties": False,
                    },
                },
                "required": ["action", "params"],
                "additionalProperties": False,
            }
        )
    return {"anyOf": variants}


_RULES = f"""\
Rules:
- Pick a PC action only if the user wants it done now on this PC. Questions about how \
something works, or how to do it, are qa.answer.
- Exactly one action. If the user asks for two or more things, use assistant.unsupported \
with reason "multi_step".
- Paths: a known folder ({", ".join(LOCATIONS)}), then "/" and the file name, \
e.g. "Documents/notes.txt". Use a drive path like "E:/backup.zip" if the user names a drive.
- Use null for any parameter the user did not say. Never guess file or app names.
- Reply with JSON only."""

# Few-shot examples. Deliberately NOT taken from the catalog so the eval stays fair.
FEW_SHOT: tuple[tuple[str, dict[str, Any]], ...] = (
    ("open firefox", {"action": "app.open", "params": {"app": "firefox"}}),
    ("how do I open a terminal in vs code", {"action": "qa.answer", "params": {}}),
    (
        "delete draft.docx from my documents",
        {"action": "file.delete", "params": {"path": "Documents/draft.docx"}},
    ),
    (
        "what's apple's stock price",
        {"action": "assistant.needs_online", "params": {"feature": "web_search"}},
    ),
    (
        "check the ram and close spotify",
        {"action": "assistant.unsupported", "params": {"reason": "multi_step"}},
    ),
    (
        "rename that one",
        {"action": "file.rename", "params": {"path": None, "new_name": None}},
    ),
)


def build_system_prompt() -> str:
    lines = ["You are Locus, a desktop assistant's command router.",
             "Map the user's message to exactly one action.", "", "Actions:"]
    for a in ACTIONS:
        params = f" (params: {', '.join(p.name for p in a.params)})" if a.params else ""
        lines.append(f"- {a.name}: {a.description}{params}")
    return "\n".join(lines) + "\n\n" + _RULES


def build_messages(text: str, *, few_shot: bool) -> list[dict[str, str]]:
    messages = [{"role": "system", "content": build_system_prompt()}]
    if few_shot:
        for user, answer in FEW_SHOT:
            messages.append({"role": "user", "content": user})
            messages.append({"role": "assistant", "content": json.dumps(answer)})
    messages.append({"role": "user", "content": text})
    return messages
