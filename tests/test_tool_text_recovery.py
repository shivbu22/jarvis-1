"""Recovering a tool call that the model wrote out as text.

Small local models sometimes print the JSON they were shown in the prompt instead
of using the tool-calling API. The observed failure: a greeting reply came out of
the speaker as ``{"name":"ask_user","parameters":"How are you?"}`` -- tool syntax,
spoken aloud, to a person. Either execute what the model plainly asked for, or
refuse to say it; never read the JSON to the master.
"""

from __future__ import annotations

import pytest
from apps.gateway.brain.base import (
    BrainReply,
    clean_text,
    looks_like_text_tool_call,
    normalize_recovered_arguments,
    recover_text_tool_call,
)
from apps.gateway.service import JarvisService
from tests.conftest import tool_call


@pytest.mark.parametrize(
    "text",
    [
        '{"name":"ask_user","parameters":"How are you?"}',
        '{"tool":"system_info","arguments":{}}',
        '{"tool_name": "get_weather", "parameters": {"city": "Mumbai"}}',
        '```json\n{"name":"system_info","parameters":{}}\n```',
        'Sure! {"name":"system_info","parameters":{}}',
        '{"name": "system_info", }',
        '{"name": "get_weather", "parameters": {"city": "Mumbai", }, }',
        'music_play(query="lofi")',
        'music_play, query="music", service="youtube"',
        "I'll be right there. *desktop_hotkey*keys:win+d*",
        'app_launch {"app": "notepad"}',
        'desktop_window_control, {"action": "minimize"}',
    ],
)
def test_a_written_out_tool_call_is_recovered(text: str) -> None:
    recovered = recover_text_tool_call(
        text,
        {"ask_user", "system_info", "get_weather", "music_play", "desktop_hotkey", "app_launch", "desktop_window_control"}
    )
    assert recovered is not None, f"not recovered from {text!r}"
    name, raw = recovered
    assert name in {"ask_user", "system_info", "get_weather", "music_play", "desktop_hotkey", "app_launch", "desktop_window_control"}
    assert raw is not None or name in {"system_info"}


def test_an_unknown_tool_is_never_invented() -> None:
    """Recovery must not conjure a tool the registry does not have."""
    assert recover_text_tool_call('{"name":"rm_rf","parameters":{}}', {"system_info"}) is None


def test_ordinary_prose_is_not_a_tool_call() -> None:
    assert recover_text_tool_call("It is light drizzle in Mumbai.", {"get_weather"}) is None
    assert recover_text_tool_call("", {"get_weather"}) is None
    assert looks_like_text_tool_call("The answer is 42.") is False


def test_tool_syntax_naming_nothing_is_still_not_speakable() -> None:
    """Even unrecoverable tool JSON must be recognised as not-a-sentence."""
    assert recover_text_tool_call('{"name":"rm_rf","parameters":{}}', {"system_info"}) is None
    assert looks_like_text_tool_call('{"name":"rm_rf","parameters":{}}') is True


def test_a_bare_string_binds_to_the_single_required_argument() -> None:
    """`"parameters": "How are you?"` means the tool's one string argument."""
    assert normalize_recovered_arguments("How are you?", ["question"]) == {
        "question": "How are you?"
    }
    # With more than one candidate the string cannot be placed, so it is parsed
    # as text and left to fail validation with a readable error.
    assert normalize_recovered_arguments("plain words", ["a", "b"]) == {}


def test_arguments_as_an_object_pass_through() -> None:
    assert normalize_recovered_arguments({"city": "Mumbai"}, ["city"]) == {"city": "Mumbai"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("assistant\n\nI am well, sir.", "I am well, sir."),
        ("Jarvis: Good morning.", "Good morning."),
        ("assistant, I can check the calendar.", "I can check the calendar."),
        ("assistant\n\nassistant\n\nTwice prefixed.", "Twice prefixed."),
        # A leading word that merely starts like a role must survive.
        ("systemd is running.", "systemd is running."),
        ("assistant is running on this machine.", "assistant is running on this machine."),
        # A reply that is nothing but a label is not a sentence at all.
        ("assistant", ""),
        ("Assistant:", ""),
        ("assistant. is", "assistant. is"),
    ],
)
def test_role_prefixes_are_not_spoken(raw: str, expected: str) -> None:
    """A model that continues the transcript must not have its label read aloud."""
    assert clean_text(raw) == expected


# --- end to end through the agent ----------------------------------------


class TextCallingBrain:
    """A brain that answers with tool syntax written as text, once."""

    name = "text-calling"
    model = "text-calling-1"

    def __init__(self, script: list[BrainReply]) -> None:
        self.script = script
        self.calls: list[list[dict]] = []

    async def generate(self, messages, tools):
        self.calls.append(list(messages))
        index = min(len(self.calls) - 1, len(self.script) - 1)
        return self.script[index]


async def test_the_recovered_call_actually_runs(settings) -> None:
    """A written-out call must be executed, not spoken."""
    service = JarvisService(settings)
    brain = TextCallingBrain(
        [
            BrainReply(text='{"name":"system_info","parameters":{}}'),
            BrainReply(text="This machine is fine."),
        ]
    )
    service.agent.brain = brain
    try:
        result = await service.agent.run_turn("how is this machine", session_id="t")
    finally:
        service.memory.close()

    assert [attempt.name for attempt in result.tools] == ["system_info"]
    assert result.reply == "This machine is fine."
    assert "{" not in result.reply


async def test_a_completed_action_is_never_reported_as_a_failure(settings) -> None:
    """When the tools ran but the model cannot phrase it, say what happened.

    Observed: memory_remember succeeded, then the answering round produced nothing
    usable, and the reply was "I could not put that into words" -- which denies an
    action that was in fact carried out.
    """
    service = JarvisService(settings)
    brain = TextCallingBrain(
        [
            BrainReply(text="", tool_calls=[tool_call("memory_remember", {"text": "workshop Friday"})]),
            BrainReply(text=""),
        ]
    )
    service.agent.brain = brain
    try:
        result = await service.agent.run_turn("remember the workshop", session_id="t")
        stored = service.memory.count()
    finally:
        service.memory.close()

    assert stored == 1, "the note must actually have been saved"
    assert "could not" not in result.reply.lower()
    assert result.reply.strip()


async def test_a_suspending_call_written_as_text_is_not_executed(settings) -> None:
    """Honouring a text-written ask_user would stall the turn for the whole window.

    A small model writes ask_user as prose to make conversation. The call is never
    executed, but the words it wrapped -- what the model actually wanted said --
    are salvaged and become the reply, so the turn neither stalls nor lies.
    """
    service = JarvisService(settings)
    brain = TextCallingBrain(
        [
            BrainReply(text='{"name":"ask_user","parameters":"I am working, sir."}'),
        ]
    )
    service.agent.brain = brain
    try:
        result = await service.agent.run_turn("hello jarvis, how are you", session_id="t")
    finally:
        service.memory.close()

    assert result.tools == [], "a suspending call must not be recovered from text"
    assert result.reply == "I am working, sir."
    assert service.interactions.pending() is False


async def test_a_discarded_json_round_announces_a_retract(settings) -> None:
    """The transport is told to take back a round that turned out to be JSON.

    When the brain streams, the JSON reaches the master's screen as live deltas
    before the agent can classify the round; the only way it comes off again is
    an explicit signal. Without this, a greeting left
    ``{"name":"ask_user",...}`` sitting in the orb's transcript forever.
    """
    service = JarvisService(settings)
    brain = TextCallingBrain(
        [
            BrainReply(text='{"name":"ask_user","parameters":"How are you?"}'),
        ]
    )
    service.agent.brain = brain
    rounds: list[tuple[str, int]] = []

    async def on_round(kind: str, iteration: int) -> None:
        rounds.append((kind, iteration))

    try:
        await service.agent.run_turn("hello jarvis", session_id="t", on_round=on_round)
    finally:
        service.memory.close()

    assert ("retract", 1) in rounds, "the junk round was never retracted"
    assert [kind for kind, _ in rounds].count("retract") == 1, "retracted twice for one junk round"
    assert service.interactions.pending() is False, "the turn must never wait on anyone"


async def test_unusable_syntax_also_announces_a_retract(settings) -> None:
    service = JarvisService(settings)
    brain = TextCallingBrain(
        [
            BrainReply(text='{"name":"launch_the_missiles","parameters":{}}'),
            BrainReply(text="I cannot do that."),
        ]
    )
    service.agent.brain = brain
    kinds: list[str] = []

    async def on_round(kind: str, _iteration: int) -> None:
        kinds.append(kind)

    try:
        await service.agent.run_turn("do something else", session_id="t", on_round=on_round)
    finally:
        service.memory.close()

    assert "retract" in kinds


async def test_unusable_tool_syntax_is_never_spoken(settings) -> None:
    """When the named tool does not exist, ask again rather than read the JSON."""
    service = JarvisService(settings)
    brain = TextCallingBrain(
        [
            BrainReply(text='{"name":"launch_the_missiles","parameters":{}}'),
            BrainReply(text="I cannot do that."),
        ]
    )
    service.agent.brain = brain
    try:
        result = await service.agent.run_turn("do something else", session_id="t")
    finally:
        service.memory.close()

    assert result.reply == "I cannot do that."
    assert "launch_the_missiles" not in result.reply
    assert result.tools == []
