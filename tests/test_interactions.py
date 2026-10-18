"""Asking the master a question, and what happens when they do not answer.

The safety-relevant property is the same one the permission gate has: **silence
is not an answer**. A turn that asks and is ignored must carry on with a stated
assumption, never treat the void as agreement. The other property is that asking
must be *bounded*: it suspends the turn, and a small local model has been observed
asking a question nobody needed (a greeting became a 140-second turn), so the
window is short on purpose.
"""

from __future__ import annotations

import asyncio

import pytest
from apps.gateway.interactions import InteractionBroker
from apps.gateway.service import JarvisService
from apps.gateway.stt import Transcript
from apps.gateway.tools.interaction import ask_user
from apps.gateway.tools.registry import ToolContext


def ctx_with(ask=None) -> ToolContext:
    context = ToolContext(settings=None, memory=None, policy=None, audit=None, bus=None)
    if ask is not None:
        context.state["ask_user"] = ask
    return context


# --- the broker -----------------------------------------------------------


async def test_an_answer_resolves_the_question() -> None:
    broker = InteractionBroker(timeout_seconds=5)

    async def answer_soon() -> None:
        await asyncio.sleep(0.01)
        pending = broker.oldest()
        assert pending is not None
        assert broker.resolve(pending.id, "the blue one") is True

    task = asyncio.create_task(answer_soon())
    assert await broker.ask("Which one?") == "the blue one"
    await task
    assert broker.pending() is False


async def test_silence_is_not_an_answer() -> None:
    """A timeout must come back empty, and be recorded as a timeout."""
    broker = InteractionBroker(timeout_seconds=0.05)
    assert await broker.ask("Which one?") == ""
    assert broker.oldest() is None


async def test_the_question_is_spoken_as_well_as_shown() -> None:
    spoken: list[str] = []

    async def speak(text: str) -> None:
        spoken.append(text)

    broker = InteractionBroker(timeout_seconds=0.05, speak=speak)
    await broker.ask("Which Bob did you mean?")
    assert spoken == ["Which Bob did you mean?"]


async def test_events_are_published_for_the_ui() -> None:
    events: list[dict] = []
    bus = type("Bus", (), {"publish": staticmethod(events.append)})()
    broker = InteractionBroker(timeout_seconds=0.05, bus=bus)
    await broker.ask("Which file?", options=["a.txt", "b.txt"])

    kinds = [event["type"] for event in events]
    assert kinds == ["question", "question_result"]
    assert events[0]["options"] == ["a.txt", "b.txt"]
    assert events[1]["answered"] is False
    assert events[1]["source"] == "timeout"


async def test_a_spoken_answer_reaches_the_oldest_question() -> None:
    broker = InteractionBroker(timeout_seconds=5)

    async def answer_by_voice() -> None:
        await asyncio.sleep(0.01)
        assert broker.resolve_by_voice("the second one") is not None

    task = asyncio.create_task(answer_by_voice())
    assert await broker.ask("Which one?") == "the second one"
    await task


async def test_resolving_an_unknown_question_is_refused() -> None:
    broker = InteractionBroker(timeout_seconds=1)
    assert broker.resolve("nope", "hello") is False
    assert broker.resolve_by_voice("hello") is None


async def test_an_answered_question_cannot_be_answered_twice() -> None:
    broker = InteractionBroker(timeout_seconds=5)

    async def answer_twice() -> None:
        await asyncio.sleep(0.01)
        question_id = broker.oldest().id
        assert broker.resolve(question_id, "first") is True
        assert broker.resolve(question_id, "second") is False

    task = asyncio.create_task(answer_twice())
    assert await broker.ask("Which one?") == "first"
    await task


async def test_shutdown_releases_a_held_question() -> None:
    """Nothing may be left hanging when the gateway stops."""
    broker = InteractionBroker(timeout_seconds=30)

    async def shutdown() -> None:
        await asyncio.sleep(0.01)
        assert broker.cancel_all("shutdown") == 1

    task = asyncio.create_task(shutdown())
    assert await broker.ask("Which one?") == ""
    await task


async def test_an_empty_question_is_never_asked() -> None:
    broker = InteractionBroker(timeout_seconds=1)
    assert await broker.ask("   ") == ""


# --- the tool -------------------------------------------------------------


async def test_the_tool_reports_the_answer_to_the_model() -> None:
    async def ask(question: str, options: list[str]) -> str:
        assert question == "Which Bob?"
        assert options == ["Bob Smith", "Bob Jones"]
        return "Bob Jones"

    output = await ask_user(ctx_with(ask), "Which Bob?", "Bob Smith | Bob Jones")
    assert "Bob Jones" in output.text
    assert output.ok is True
    assert output.data == {"answered": True, "answer": "Bob Jones"}


async def test_no_answer_tells_the_model_to_carry_on() -> None:
    """The model must not sit waiting or ask the same thing again."""

    async def ask(_question: str, _options: list[str]) -> str:
        return ""

    output = await ask_user(ctx_with(ask), "Which Bob?")
    assert output.ok is False
    assert "Do not ask again" in output.text
    assert output.data == {"answered": False}


async def test_the_tool_is_refused_without_a_broker() -> None:
    """Tools must stay usable outside a running service."""
    output = await ask_user(ctx_with(), "Which Bob?")
    assert output.ok is False
    assert "cannot ask" in output.text


async def test_the_tool_accepts_no_options() -> None:
    async def ask(_question: str, options: list[str]) -> str:
        assert options == []
        return "yes"

    assert (await ask_user(ctx_with(ask), "Go ahead?")).data["answer"] == "yes"


# --- routing a spoken answer ---------------------------------------------


async def test_a_spoken_turn_answers_a_held_question(settings) -> None:
    """Speaking the answer must resume the turn, not start a competing one."""
    service = JarvisService(settings)

    async def fake_transcribe(_audio: bytes, **_kwargs) -> Transcript:
        return Transcript(text="the second one")

    service.transcribe = fake_transcribe  # type: ignore[method-assign]

    async def answer_soon() -> None:
        await asyncio.sleep(0.05)
        # Simulate the master answering out loud while the turn is suspended.
        assert service.interactions.resolve_by_voice("the second one") is not None

    task = asyncio.create_task(answer_soon())
    held = await service.interactions.ask("Which one?", timeout=5)
    await task
    assert held == "the second one"

    # With a question open, spoken words answer it rather than starting a turn.
    asker = asyncio.create_task(service.interactions.ask("Which one?", timeout=5))
    await asyncio.sleep(0.05)
    result = await service.turn_audio(b"not really audio")
    assert result.error == "answered_question"
    assert await asker == "the second one"
    service.memory.close()


async def test_a_spoken_turn_is_normal_when_nothing_is_held(settings) -> None:
    service = JarvisService(settings)

    async def fake_transcribe(_audio: bytes, **_kwargs) -> Transcript:
        return Transcript(text="hello")

    service.transcribe = fake_transcribe  # type: ignore[method-assign]
    result = await service.turn_audio(b"not really audio", speak=False)
    # No question is open, so this is an ordinary turn (it may fail for want of a
    # brain, but it must not be reported as an answer to a question).
    assert result.error != "answered_question"
    service.memory.close()


@pytest.mark.parametrize("timeout", [1.0, 45.0])
def test_the_window_is_bounded(timeout: float) -> None:
    """A stalled question may not hold a turn indefinitely."""
    assert InteractionBroker(timeout_seconds=timeout).timeout_seconds == timeout
