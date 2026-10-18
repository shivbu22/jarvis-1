"""Self-Evolution & Dynamic Skill Synthesis Tools for J.A.R.V.I.S.

Allows J.A.R.V.I.S. to:
1. Dynamically author and register new SKILL.md packages on the fly (`skill_create`).
2. Record, list, and delete learned behavioral rules (`record_learned_rule`, `list_evolution_rules`).
3. Trigger proactive executive daily briefings (`trigger_daily_briefing`).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .registry import ToolContext, ToolOutput, ToolSpec, object_schema, param

logger = logging.getLogger("jarvis.tools.evolution")

CUSTOM_SKILLS_DIR = Path("skills")


def skill_create(
    ctx: ToolContext,
    name: str,
    description: str,
    instructions: str,
    script_code: str = "",
) -> ToolOutput:
    """Author and install a brand new SKILL.md package in J.A.R.V.I.S.'s dynamic skill library."""
    clean_name = "".join(c if c.isalnum() or c in ("-", "_") else "-" for c in name.strip().lower()).strip("-")
    if not clean_name:
        return ToolOutput.from_error("Invalid skill name provided.")

    skill_dir = CUSTOM_SKILLS_DIR / clean_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"

    # Construct standard YAML frontmatter + markdown
    content = f"""---
name: {clean_name}
description: {description.strip()}
license: MIT
---

# {clean_name.replace('-', ' ').title()} Skill

{instructions.strip()}
"""
    try:
        skill_file.write_text(content, encoding="utf-8")

        # Optional companion script
        if script_code.strip():
            scripts_dir = skill_dir / "scripts"
            scripts_dir.mkdir(parents=True, exist_ok=True)
            script_path = scripts_dir / "run.py"
            script_path.write_text(script_code.strip(), encoding="utf-8")

        return ToolOutput.from_ok({
            "status": "created",
            "skill_name": clean_name,
            "path": str(skill_file),
            "description": description.strip(),
            "message": f"Skill '{clean_name}' successfully authored and installed into skills/{clean_name}/SKILL.md.",
        })
    except Exception as exc:
        logger.error("Failed creating skill %s: %s", clean_name, exc)
        return ToolOutput.from_error(f"Failed creating skill: {exc}")


def record_learned_rule(
    ctx: ToolContext,
    rule: str,
    trigger_pattern: str = "*",
    category: str = "general",
) -> ToolOutput:
    """Explicitly record a new self-evolved behavioral rule to guide future decisions."""
    from ..evolution import EvolutionEngine

    engine = EvolutionEngine()
    created = engine.add_rule(
        rule_text=rule,
        category=category,
        trigger_pattern=trigger_pattern,
        source="explicit_teach",
    )
    return ToolOutput.from_ok({
        "status": "recorded",
        "rule_id": created.rule_id,
        "rule_text": created.rule_text,
        "category": created.category,
        "trigger_pattern": created.trigger_pattern,
        "message": f"Rule successfully recorded and saved to evolution ledger ({created.rule_id}).",
    })


def list_evolution_rules(ctx: ToolContext) -> ToolOutput:
    """List all currently active self-evolved behavioral rules and corrections."""
    from ..evolution import EvolutionEngine

    engine = EvolutionEngine()
    rules = engine.list_rules()
    return ToolOutput.from_ok({
        "total": len(rules),
        "rules": rules,
    })


def delete_evolution_rule(ctx: ToolContext, rule_id: str) -> ToolOutput:
    """Delete an obsolete or incorrect evolution rule by id."""
    from ..evolution import EvolutionEngine

    engine = EvolutionEngine()
    ok = engine.delete_rule(rule_id.strip())
    if ok:
        return ToolOutput.from_ok({"status": "deleted", "rule_id": rule_id})
    return ToolOutput.from_error(f"Rule id '{rule_id}' not found.")


async def trigger_daily_briefing(
    ctx: ToolContext,
    deliver_toast: bool = True,
    speak: bool = False,
) -> ToolOutput:
    """Generate and deliver an executive daily morning briefing."""
    from ..evolution import EvolutionEngine
    from ..notify import NotificationManager

    engine = EvolutionEngine()
    service = getattr(ctx, "service", None)
    briefing = await engine.generate_daily_briefing(service=service)

    # Deliver Windows toast if requested
    if deliver_toast:
        try:
            notifier = NotificationManager()
            notifier.notify_sync(
                title=briefing["title"],
                message=briefing["text"][:240] + ("..." if len(briefing["text"]) > 240 else ""),
                urgency="normal",
            )
        except Exception as exc:
            logger.debug("Toast delivery for briefing skipped: %s", exc)

    return ToolOutput.from_ok(briefing)


def build_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="skill_create",
            description="Author and dynamically install a brand new SKILL.md package in J.A.R.V.I.S.'s skill library.",
            parameters=object_schema(
                properties={
                    "name": param("string", "Unique alphanumeric name for the skill (e.g. 'github-release-manager')."),
                    "description": param("string", "One-line description of what this skill does and when to use it."),
                    "instructions": param("string", "Full Markdown instructions, step-by-step workflow, and execution doctrine."),
                    "script_code": param("string", "Optional Python companion script code to store in scripts/run.py.", default=""),
                },
                required=["name", "description", "instructions"],
            ),
            handler=skill_create,
            category="evolution",
        ),
        ToolSpec(
            name="record_learned_rule",
            description="Explicitly record a permanent self-evolved rule or user preference that J.A.R.V.I.S. must follow.",
            parameters=object_schema(
                properties={
                    "rule": param("string", "The exact behavioral rule or guideline to strictly follow."),
                    "trigger_pattern": param("string", "Context or keywords when this rule applies (default '*' for all turns).", default="*"),
                    "category": param("string", "Category: 'preference', 'tool_usage', 'tone', 'accuracy', or 'general'.", default="general"),
                },
                required=["rule"],
            ),
            handler=record_learned_rule,
            category="evolution",
        ),
        ToolSpec(
            name="list_evolution_rules",
            description="List all active self-evolved rules and behavioral guidelines.",
            parameters=object_schema(properties={}),
            handler=list_evolution_rules,
            category="evolution",
        ),
        ToolSpec(
            name="delete_evolution_rule",
            description="Delete an obsolete or unwanted self-evolved rule by its ID.",
            parameters=object_schema(
                properties={
                    "rule_id": param("string", "The rule_id to remove (e.g. 'rule-a1b2c3d4')."),
                },
                required=["rule_id"],
            ),
            handler=delete_evolution_rule,
            category="evolution",
        ),
        ToolSpec(
            name="trigger_daily_briefing",
            description="Generate and deliver an executive daily morning briefing (weather, markets, ventures, pending tasks).",
            parameters=object_schema(
                properties={
                    "deliver_toast": param("boolean", "Whether to deliver a Windows Action Center toast alert.", default=True),
                    "speak": param("boolean", "Whether to speak the briefing out loud.", default=False),
                },
            ),
            handler=trigger_daily_briefing,
            category="evolution",
        ),
    ]
