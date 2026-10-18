"""Talking to the master: asking a question when the request is unclear.

This exists because guessing is the expensive failure. A spoken request arrives
without punctuation or certainty -- "send it to him", "move that meeting", "the
usual one" -- and a model that fills in the blank invents either the wrong
recipient or the wrong file, then reports success. One short question costs two
seconds of the master's time; a wrong action costs considerably more.

The question is put through :class:`~apps.gateway.interactions.InteractionBroker`,
which suspends the turn, speaks the question, and waits for an answer in words.
"""

from __future__ import annotations

from .registry import ToolContext, ToolOutput, ToolSpec, object_schema, param


async def ask_user(ctx: ToolContext, question: str, options: str = "") -> ToolOutput:
    """Put one question to the master and wait for the answer."""
    ask = ctx.state.get("ask_user")
    if ask is None:
        return ToolOutput(
            text=(
                "I cannot ask the master a question in this session, so nothing was "
                "asked. Make the safest reasonable assumption and say what you assumed."
            ),
            ok=False,
        )

    # Options are a convenience for the UI, not a constraint on the answer: the
    # master may be speaking, and a spoken answer will not match a button label.
    choices = [part.strip() for part in (options or "").split("|") if part.strip()][:4]
    answer = await ask(question.strip(), choices)

    if not answer:
        return ToolOutput(
            text=(
                "No reply came back, so nothing was decided. Do not ask again this "
                "turn. Carry on with the most reasonable interpretation and mention "
                "the assumption in one short sentence."
            ),
            ok=False,
            data={"answered": False},
        )
    return ToolOutput(
        text=f"The master answered: {answer}",
        data={"answered": True, "answer": answer},
    )


def build_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="ask_user",
            description=(
                "Ask the master one question and WAIT for their reply, which blocks "
                "the turn. Use it only to obtain one missing fact needed to complete "
                "a task, which only they know: which person, which file, which of two "
                "clients. Never use it to greet, to make conversation, to reply "
                "politely, or to ask what the master wants in general -- answer those "
                "directly. When no reply comes, keep going and state your assumption."
            ),
            parameters=object_schema(
                {
                    "question": param(
                        "question",
                        "string",
                        "The question, phrased for the ear in one short sentence. "
                        "Say what you need and why in as few words as possible.",
                    ),
                    "options": param(
                        "options",
                        "string",
                        "Optional likely answers, separated by ' | '. Shown as buttons "
                        "in the UI. The master may answer with anything instead.",
                        default="",
                    ),
                },
                required=["question"],
            ),
            handler=ask_user,
            # Asking is not executing: a question that happens to contain the word
            # "delete" must not be refused by the destructive-pattern scanner.
            executing=False,
            category="system",
        )
    ]
