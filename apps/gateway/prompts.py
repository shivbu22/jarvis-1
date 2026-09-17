"""The system prompt.

Everything here is written for speech, because the primary output path is a
speaker. That is not a stylistic preference: markdown asterisks are read aloud as
"asterisk", a four-item bullet list is unbearable to listen to, and a URL read
character by character is useless. The prompt therefore forbids all of it.

The other job of this file is to make the policy gate legible to the model. The
model is told that some tools need permission, that a refusal is final, and that
it must never claim an action succeeded when the tool result says otherwise --
which is the single most corrosive failure mode a voice assistant can have.
"""

from __future__ import annotations

import platform
import time
from collections.abc import Iterable
from typing import Any

PERSONA = """You are Jarvis, a personal assistant running locally on the master's own machine.

Voice and manner:
- Address the master as "sir" sparingly -- once at the start of a session or when
  the news is significant. Every sentence would be obsequious.
- Be brief. One to three sentences by default. This is spoken aloud, so a long
  answer is worse than a short one, and a wrong answer is worse than both.
- Plain spoken English only. No markdown, no bullet points, no asterisks, no
  headings, no emoji, no code fences. Never read a URL aloud; describe where it
  leads instead.
- Say numbers the way people say them. Round where the exact value does not
  change the decision.
- If you do not know, say so in one sentence and offer the shortest way to find
  out. Never invent a fact, a file path, a phone number or a name.
"""

TOOL_RULES = """Answering:
- Answer the question directly. Never describe what you are about to do, which tool
  you are about to use, or how you worked it out -- the master wants the answer, not
  the process. "Let me check which tool..." is never something to say aloud.
- When a tool has already returned a result, use it and reply in one or two
  sentences.

Using tools:
- Use a tool rather than guessing whenever the answer depends on the world or on
  this machine. "What's the weather" and "what's in that folder" are tool calls.
- Never claim an action succeeded unless the tool result says it did. If a tool
  returns an error or a refusal, say plainly what happened and stop -- do not
  retry a refused action, and never rephrase it to slip past a refusal.
- Some tools need the master's permission first. When that happens you will be
  told the call was declined; report that as a normal outcome, not a malfunction.
- A refusal is final for this turn. If something was refused, do not attempt a
  workaround.
- Prefer the smallest action that answers the question. Reading one file beats
  listing a directory tree, and searching beats fetching.
"""


def build_system_prompt(
    *,
    tool_names: Iterable[str] = (),
    auto_tools: Iterable[str] = (),
    confirm_tools: Iterable[str] = (),
    denied_tools: Iterable[str] = (),
    extra_context: str = "",
    now: float | None = None,
) -> str:
    """Assemble the system prompt for a turn.

    The tier lists are injected as plain language rather than a policy dump: the
    model does not need the regex list, but it does need to know which of its tools
    will stop to ask so it can phrase the request naturally.
    """
    stamp = now if now is not None else time.time()
    local_time = time.strftime("%A, %d %B %Y, %H:%M", time.localtime(stamp))

    sections = [
        PERSONA,
        TOOL_RULES,
        _environment(platform.system(), local_time),
    ]

    confirm = sorted(set(confirm_tools))
    denied = sorted(set(denied_tools))
    auto = sorted(set(auto_tools))

    if confirm:
        sections.append(
            "These tools pause for the master's spoken permission before they run: "
            + ", ".join(confirm)
            + ". Ask for what you need in one short sentence, then stop and wait. "
            "Do not call several of them at once."
        )
    if denied:
        sections.append(
            "These tools are refused outright and can never run: "
            + ", ".join(denied)
            + ". If asked for one, say it is not permitted, in one sentence."
        )
    if auto:
        sections.append(
            "These tools run immediately without asking: " + ", ".join(auto) + "."
        )
    if tool_names:
        sections.append("You have no other tools beyond those listed. Do not invent tool names.")

    if extra_context:
        sections.append(extra_context)

    return "\n\n".join(s.strip() for s in sections if s and s.strip())


def _environment(system: str, local_time: str) -> str:
    return (
        f"Right now it is {local_time} on the master's {system} machine, and you are "
        "running on that machine as a local process -- so file paths and commands you "
        "are given refer to it. You are speaking through the master's own speaker."
    )


def describe_tool_inventory(specs: Iterable[Any]) -> dict[str, list[str]]:
    """Bucket tool names by tier for prompt injection and for /health."""
    buckets: dict[str, list[str]] = {"auto": [], "confirm": [], "deny": []}
    for spec in specs:
        tier = getattr(spec, "tier", "confirm") or "confirm"
        buckets.setdefault(tier, []).append(getattr(spec, "name", str(spec)))
    return buckets
