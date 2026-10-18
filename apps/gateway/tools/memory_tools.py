"""Memory tools -- the reason Jarvis improves instead of restarting every session.

Store is additive and therefore tier ``auto``: remembering something can never
destroy anything. Recall is a search, not a dump, so a vague question does not
flood the model's context with every note ever saved.
"""

from __future__ import annotations

import time

from .registry import ToolContext, ToolOutput, ToolSpec, object_schema, param


def memory_remember(ctx: ToolContext, text: str, tags: str = "") -> ToolOutput:
    memory_id = ctx.memory.add(text, tags=tags, source="voice")
    if memory_id < 0:
        # A near-duplicate: nothing new was stored, and the original note's
        # seen-count went up instead. Saying so plainly stops the model from
        # claiming a fresh memory was created -- and from re-saving the same
        # fact in different words every few turns.
        return ToolOutput(
            text=f"I already have this noted ('{text}' or similar), so nothing new was saved.",
            data={"id": -memory_id, "text": text, "tags": tags, "duplicate": True},
        )
    return ToolOutput(
        text=f"Remembered: {text}",
        data={"id": memory_id, "text": text, "tags": tags},
    )


def memory_recall(ctx: ToolContext, query: str, limit: int = 5) -> ToolOutput:
    limit = max(1, min(int(limit or 5), 25))
    rows = ctx.memory.search(query, limit=limit)
    if not rows:
        return ToolOutput(
            text=f"I have nothing saved about '{query}'.",
            data={"query": query, "results": []},
        )
    lines = [f"Saved notes matching '{query}':"]
    for row in rows:
        when = time.strftime("%d %B", time.localtime(float(row["created_at"])))
        tags = f" [{row['tags']}]" if row.get("tags") else ""
        lines.append(f"- {row['text']}{tags} (noted {when})")
    return ToolOutput(text="\n".join(lines), data={"query": query, "results": rows})


def memory_list(ctx: ToolContext, limit: int = 10) -> ToolOutput:
    limit = max(1, min(int(limit or 10), 50))
    rows = ctx.memory.recent(limit=limit)
    if not rows:
        return ToolOutput(text="I have nothing saved yet.", data={"results": []})
    lines = ["Your most recent notes:"]
    for row in rows:
        when = time.strftime("%d %B", time.localtime(float(row["created_at"])))
        lines.append(f"- {row['text']} (noted {when})")
    return ToolOutput(text="\n".join(lines), data={"results": rows})


def build_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="memory_remember",
            description=(
                "Save a durable note for later. Use whenever the master says "
                "'remember', or states a fact, preference, deadline or plan worth "
                "keeping. This persists across restarts."
            ),
            parameters=object_schema(
                {
                    "text": param("text", "string", "The fact to remember, in one clear sentence."),
                    "tags": param(
                        "tags", "string", "Optional space-separated tags for later filtering.",
                        default="",
                    ),
                },
                ["text"],
            ),
            handler=memory_remember,
            executing=False,
            category="memory",
        ),
        ToolSpec(
            name="memory_recall",
            description=(
                "Search previously saved notes. Use before saying you do not know "
                "something the master may have told you before."
            ),
            parameters=object_schema(
                {
                    "query": param("query", "string", "What to search for."),
                    "limit": param(
                        "limit", "integer", "Maximum notes to return.",
                        default=5, minimum=1, maximum=25,
                    ),
                },
                ["query"],
            ),
            handler=memory_recall,
            executing=False,
            category="memory",
        ),
        ToolSpec(
            name="memory_list",
            description="List the most recently saved notes.",
            parameters=object_schema(
                {
                    "limit": param(
                        "limit", "integer", "How many notes to list.",
                        default=10, minimum=1, maximum=50,
                    )
                },
                [],
            ),
            handler=memory_list,
            executing=False,
            category="memory",
        ),
    ]
