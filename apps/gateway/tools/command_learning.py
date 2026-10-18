"""Tools for learning and executing custom user commands and voice macros."""

from __future__ import annotations

import logging
from typing import Any

from .registry import ToolContext, ToolOutput, ToolSpec, object_schema, param

logger = logging.getLogger("jarvis.tools.command_learning")


def learn_custom_command(
    ctx: ToolContext,
    trigger: str,
    action: str,
    description: str = "",
) -> ToolOutput:
    """Teach J.A.R.V.I.S. a new custom command or macro shortcut.

    When the user says or types the trigger phrase in the future, J.A.R.V.I.S. will
    automatically execute the associated action directive.
    """
    from ..command_learner import learn_command

    result = learn_command(ctx.memory, trigger=trigger, action=action, description=description)
    if not result.get("ok"):
        return ToolOutput(text=result.get("reply", "Failed to learn command."), ok=False)

    return ToolOutput(
        text=result.get("reply", f"Learned command for '{trigger}'."),
        data=result,
        ok=True,
    )


def list_custom_commands(ctx: ToolContext, filter: str = "") -> ToolOutput:
    """List all currently learned custom commands, voice shortcuts, and macros."""
    cmds = ctx.memory.list_custom_commands(limit=100)
    kw = (filter or "").strip().lower()
    if kw:
        cmds = [
            c
            for c in cmds
            if kw in c.get("trigger_phrase", "").lower() or kw in c.get("action_payload", "").lower()
        ]

    if not cmds:
        msg = f"No learned commands found{' matching ' + kw if kw else ''}."
        return ToolOutput(text=msg, data={"commands": []}, ok=True)

    lines = [f"I have {len(cmds)} learned custom command(s):"]
    for c in cmds:
        trig = c.get("trigger_phrase", "")
        act = c.get("action_payload", "")
        uses = c.get("use_count", 0)
        lines.append(f"- \"{trig}\" -> \"{act}\" (used {uses} times)")

    return ToolOutput(text="\n".join(lines), data={"commands": cmds}, ok=True)


def delete_custom_command(ctx: ToolContext, trigger: str) -> ToolOutput:
    """Remove a previously learned custom command by its trigger phrase."""
    from ..command_learner import sync_commands_to_vault

    deleted = ctx.memory.delete_custom_command(trigger)
    if not deleted:
        return ToolOutput(text=f"No custom command with trigger '{trigger}' was found.", ok=False)

    sync_commands_to_vault(ctx.memory)
    return ToolOutput(text=f"Removed learned command '{trigger}'.", ok=True)


def build_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="learn_custom_command",
            description="Teach J.A.R.V.I.S. a new custom command, voice alias, or macro so that whenever the trigger phrase is spoken, the action is executed.",
            parameters=object_schema(
                properties={
                    "trigger": param("string", "The spoken trigger phrase (e.g. 'chai time', 'deep work', 'close shop')"),
                    "action": param("string", "The action to perform when trigger is spoken (e.g. 'pause music and open browser')"),
                    "description": param("string", "Optional context or description", default=""),
                },
                required=["trigger", "action"],
            ),
            handler=learn_custom_command,
            executing=False,
            category="learning",
        ),
        ToolSpec(
            name="list_custom_commands",
            description="List all active dynamically learned voice shortcuts, custom commands, and macros.",
            parameters=object_schema(
                properties={
                    "filter": param("string", "Optional keyword to filter commands", default=""),
                }
            ),
            handler=list_custom_commands,
            executing=False,
            category="learning",
        ),
        ToolSpec(
            name="delete_custom_command",
            description="Delete a previously learned custom command by trigger phrase.",
            parameters=object_schema(
                properties={
                    "trigger": param("string", "The trigger phrase of the command to delete"),
                },
                required=["trigger"],
            ),
            handler=delete_custom_command,
            executing=False,
            category="learning",
        ),
    ]
