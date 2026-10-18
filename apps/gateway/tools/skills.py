"""Skills system for J.A.R.V.I.S. (Track 12).

Allows J.A.R.V.I.S. to discover, inspect, and dynamically ingest SKILL.md packages
from installed skill repositories (e.g. Antigravity/Gemini skills, project skills).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .registry import ToolContext, ToolOutput, ToolSpec, object_schema, param

logger = logging.getLogger("jarvis.tools.skills")

DEFAULT_SKILL_PATHS = [
    Path("skills"),
    Path(r"C:\Users\ahiva\.gemini\config\skills"),
    Path(r"C:\Users\ahiva\.gemini\config\plugins\agent-skills\skills"),
]


def _discover_skills() -> dict[str, Path]:
    discovered: dict[str, Path] = {}
    for base in DEFAULT_SKILL_PATHS:
        if not base.exists():
            continue
        try:
            for skill_file in base.glob("**/SKILL.md"):
                skill_name = skill_file.parent.name
                if skill_name not in discovered:
                    discovered[skill_name] = skill_file
        except Exception as exc:
            logger.debug("Error scanning %s: %s", base, exc)
    return discovered


def skills_list(ctx: ToolContext, filter: str = "") -> ToolOutput:
    """List all installed SKILL.md packages available in J.A.R.V.I.S.'s skill library."""
    all_skills = _discover_skills()
    results = []
    kw = filter.lower().strip()
    for name, path in sorted(all_skills.items()):
        if kw and kw not in name.lower():
            continue
        desc = ""
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
            lines = content.splitlines()
            for line in lines[:10]:
                if line.startswith("description:"):
                    desc = line.split(":", 1)[1].strip()
                    break
        except Exception:
            pass
        results.append({"name": name, "path": str(path), "description": desc})
    return ToolOutput.from_ok({
        "total": len(results),
        "count_returned": len(results[:60]),
        "skills": results[:60],
    })


def skills_read(ctx: ToolContext, name: str) -> ToolOutput:
    """Read the instructions and execution doctrine of a specific SKILL.md package."""
    all_skills = _discover_skills()
    target = all_skills.get(name.strip())
    if not target or not target.exists():
        return ToolOutput.from_error(f"Skill '{name}' not found in installed libraries.")
    try:
        content = target.read_text(encoding="utf-8", errors="ignore")
        if len(content) > 12000:
            content = content[:12000] + "\n...[truncated for context efficiency]"
        return ToolOutput.from_ok({
            "skill": name,
            "path": str(target),
            "instructions": content,
        })
    except Exception as exc:
        return ToolOutput.from_error(f"Failed reading skill '{name}': {exc}")


def build_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="skills_list",
            description="List installed SKILL.md packages across Antigravity and Jarvis registries.",
            parameters=object_schema(
                properties={
                    "filter": param("string", "Optional keyword to filter skill names.", default=""),
                },
            ),
            handler=skills_list,
            category="skills",
        ),
        ToolSpec(
            name="skills_read",
            description="Read the instructions and full execution doctrine of an installed SKILL.md package.",
            parameters=object_schema(
                properties={
                    "name": param("string", "Name of the skill to read"),
                },
                required=["name"],
            ),
            handler=skills_read,
            category="skills",
        ),
    ]
