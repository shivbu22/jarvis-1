"""Agent turn-loop tests with a scripted brain.

These are the integration tests for the promise "the brain never executes
anything": a scripted model asks for a tool, and what happens next is decided by
the gate, not by the model.
"""

from __future__ import annotations

import asyncio

from apps.gateway.brain.base import BrainReply
from tests.conftest import tool_call, tool_reply


async def test_plain_answer_needs_no_tools(agent, harness) -> None:
    result = await agent.run_turn("hello")
    assert result.reply == "Very good, sir."
    assert result.tools == []
    assert result.iterations == 1


async def test_auto_tool_runs_and_feeds_back_into_the_conversation(make_agent, harness) -> None:
    agent, brain = make_agent(
        [
            tool_reply("memory_remember", {"text": "the workshop is Friday"}),
            BrainReply(text="Noted, sir."),
        ]
    )
    result = await agent.run_turn("remember the workshop is Friday")

    assert result.reply == "Noted, sir."
    assert result.used_tools == ["memory_remember"]
    assert result.tools[0].decision == "auto"
    assert harness.memory.count() == 1
    # The tool result must be visible to the model on the next round trip.
    follow_up = brain.calls[1]
    assert any(message.get("role") == "tool" for message in follow_up)


async def test_confirm_tool_is_held_and_reported(make_agent, harness) -> None:
    agent, _ = make_agent(
        [
            tool_reply("fs_write", {"path": "C:\\Users\\ahiva\\OneDrive\\Desktop\\jarvis\\x.txt", "content": "hi"}),
            BrainReply(text="I need your permission."),
        ]
    )
    task = asyncio.create_task(agent.run_turn("write a file"))
    await asyncio.sleep(0.02)

    pending = harness.broker.oldest()
    assert pending is not None, "the write should be held, not executed"
    assert pending.tool == "fs_write"

    harness.broker.resolve(pending.id, False, source="test")
    result = await task

    attempt = result.tools[0]
    assert attempt.decision == "declined"
    assert attempt.ok is False


async def test_approved_confirmation_lets_the_tool_run(make_agent, harness, tmp_path) -> None:
    target = tmp_path / "approved-by-master.txt"
    agent, _ = make_agent(
        [
            tool_reply("fs_write", {"path": str(target), "content": "written"}),
            BrainReply(text="Written."),
        ]
    )
    task = asyncio.create_task(agent.run_turn("write it"))
    await asyncio.sleep(0.02)

    pending = harness.broker.oldest()
    assert pending is not None
    harness.broker.resolve(pending.id, True, source="voice")
    result = await task

    assert result.tools[0].decision == "confirmed"
    assert target.read_text(encoding="utf-8") == "written"


async def test_a_spoken_yes_approves_a_held_write(make_agent, harness, tmp_path) -> None:
    target = tmp_path / "spoken-yes.txt"
    agent, _ = make_agent(
        [
            tool_reply("fs_write", {"path": str(target), "content": "ok"}),
            BrainReply(text="Done."),
        ]
    )
    task = asyncio.create_task(agent.run_turn("write it"))
    await asyncio.sleep(0.02)

    assert harness.broker.resolve_by_voice("yes please") is not None
    await task
    assert target.exists()


async def test_declined_tool_tells_the_model_not_to_retry(make_agent, harness) -> None:
    agent, brain = make_agent(
        [
            tool_reply("fs_delete", {"path": "C:\\Users\\ahiva\\OneDrive\\Desktop\\jarvis\\data\\gone.txt"}),
            BrainReply(text="Understood, I left it alone."),
        ]
    )
    task = asyncio.create_task(agent.run_turn("delete that"))
    await asyncio.sleep(0.02)
    harness.broker.resolve(harness.broker.oldest().id, False)
    await task

    tool_messages = [m for m in brain.calls[1] if m.get("role") == "tool"]
    assert tool_messages
    assert "NOT PERFORMED" in tool_messages[0]["content"]


async def test_no_confirmation_path_means_no_execution(make_agent, harness, tmp_path) -> None:
    """HTTP callers that opt out of confirmations cannot execute confirm-tier tools."""
    target = tmp_path / "should-not-exist.txt"
    agent, _ = make_agent(
        [
            tool_reply("fs_write", {"path": str(target), "content": "nope"}),
            BrainReply(text="I would need permission for that."),
        ]
    )
    result = await agent.run_turn("write it", allow_confirmation=False)

    assert result.tools[0].decision == "awaiting"
    assert not target.exists()
    assert harness.broker.oldest() is None


async def test_destructive_command_is_refused_by_the_gate(make_agent, harness) -> None:
    agent, _ = make_agent(
        [
            tool_reply("shell_run", {"command": "rm -rf /"}),
            BrainReply(text="I will not do that."),
        ]
    )
    result = await agent.run_turn("wipe the disk")

    assert result.tools[0].decision == "denied"
    assert result.tools[0].ok is False
    assert harness.broker.oldest() is None, "a denied tool must never even ask"


async def test_deny_tier_tool_is_refused(make_agent, harness) -> None:
    agent, _ = make_agent(
        [tool_reply("shell_run_admin", {"command": "whoami"}), BrainReply(text="No.")]
    )
    result = await agent.run_turn("run that as admin")
    assert result.tools[0].decision == "denied"


async def test_unknown_tool_is_reported_to_the_model(make_agent) -> None:
    agent, brain = make_agent(
        [tool_reply("launch_the_missiles", {}), BrainReply(text="I cannot do that.")]
    )
    result = await agent.run_turn("do it")
    assert result.tools[0].decision == "error"
    tool_messages = [m for m in brain.calls[1] if m.get("role") == "tool"]
    assert "No tool named" in tool_messages[0]["content"]


async def test_iteration_cap_ends_with_prose(make_agent) -> None:
    """A looping model must produce a sentence, not an unbounded tool budget."""
    looping = [
        BrainReply(text="", tool_calls=[tool_call("system_info", {}, call_id=f"c{i}")])
        for i in range(10)
    ]
    agent, brain = make_agent(looping)

    result = await agent.run_turn("keep going")

    assert result.iterations == agent.settings.max_tool_iterations
    assert result.reply  # something speakable came out
    # The final call must be made with tools disabled.
    assert brain.tool_sets[-1] == []


async def test_brain_error_is_spoken_not_raised(make_agent) -> None:
    agent, _ = make_agent([BrainReply(text="", error="Ollama is not running.")])
    result = await agent.run_turn("hello")

    assert result.error == "Ollama is not running."
    assert "Ollama is not running" in result.reply
    assert result.reply.endswith(".")


async def test_empty_transcript_is_handled(agent) -> None:
    result = await agent.run_turn("   ")
    assert result.error == "empty_transcript"
    assert result.reply


async def test_conversation_is_persisted(agent, harness) -> None:
    await agent.run_turn("remember I like filter coffee")
    turns = harness.memory.history("default", limit=10)
    assert [t["role"] for t in turns] == ["user", "assistant"]


async def test_history_is_isolated_by_session(agent, harness) -> None:
    await agent.run_turn("first session", session_id="a")
    await agent.run_turn("second session", session_id="b")
    assert harness.memory.history("a")[0]["content"] == "first session"
    assert harness.memory.history("b")[0]["content"] == "second session"


async def test_system_prompt_states_the_tier_rules(agent) -> None:
    prompt = agent.system_prompt()
    assert "web_search" in prompt
    assert "shell_run" in prompt
    assert "permission" in prompt.lower()
    assert "shell_run_admin" in prompt


async def test_turn_is_audited(make_agent, harness) -> None:
    agent, _ = make_agent(
        [tool_reply("memory_remember", {"text": "audit me"}), BrainReply(text="Saved.")]
    )
    await agent.run_turn("remember this")

    events = [e for e in harness.audit.tail(50) if e.get("event") == "turn"]
    assert events
    assert events[-1]["transcript"] == "remember this"
    assert events[-1]["tools"][0]["name"] == "memory_remember"


async def test_bus_publishes_turn_events(make_agent, harness) -> None:
    agent, _ = make_agent(
        [tool_reply("memory_remember", {"text": "events"}), BrainReply(text="Saved.")]
    )
    queue = await harness.bus.subscribe()
    await agent.run_turn("remember this", session_id="ws")

    kinds = []
    while not queue.empty():
        kinds.append(queue.get_nowait()["type"])
    assert "transcript" in kinds
    assert "tool_start" in kinds
    assert "turn_complete" in kinds


async def test_tool_call_ids_are_preserved_for_parallel_calls(make_agent, harness) -> None:
    """Providers match tool results to requests by id, so ids must round-trip."""
    reply = BrainReply(
        text="",
        tool_calls=[
            tool_call("system_info", {}, call_id="alpha"),
            tool_call("memory_list", {}, call_id="beta"),
        ],
    )
    agent, brain = make_agent([reply, BrainReply(text="Two calls done.")])
    await agent.run_turn("check things")

    ids = [m["tool_call_id"] for m in brain.calls[1] if m.get("role") == "tool"]
    assert ids == ["alpha", "beta"]


# ---------------------------------------------------------------------------
# an empty answer is a specific failure worth one retry
# ---------------------------------------------------------------------------


async def test_empty_answer_is_retried_without_tools(make_agent) -> None:
    """A reasoning model can spend its whole budget thinking and say nothing.

    Measured here: qwen3 does exactly that when its output cap is tight, and the
    turn then reported a bare "Done." to a question it had understood. The retry
    takes the tools away and asks for one sentence.
    """
    agent, brain = make_agent([BrainReply(text=""), BrainReply(text="I am working, sir.")])

    result = await agent.run_turn("confirm you are working")

    assert result.reply == "I am working, sir."
    assert len(brain.calls) == 2
    # The retry must offer no tools, so there is nothing left to deliberate about.
    assert brain.tool_sets[1] == []
    # And it must say so plainly in the prompt.
    assert "one short spoken sentence" in brain.calls[1][-1]["content"]


async def test_retry_still_offers_no_tools_after_a_tool_ran(make_agent) -> None:
    agent, _ = make_agent(
        [
            tool_reply("get_weather", {"city": "Mumbai"}),
            BrainReply(text=""),
            BrainReply(text="It is overcast in Mumbai."),
        ]
    )

    result = await agent.run_turn("what is the weather in Mumbai")

    assert result.reply == "It is overcast in Mumbai."
    assert [a.name for a in result.tools] == ["get_weather"]


async def test_an_understandable_failure_when_the_retry_is_also_empty(make_agent) -> None:
    """Never a bare "Done." -- say plainly what did not happen.

    No tool ran here, so there is no completed action to report and the wording is
    the "could not work it out" one. A turn whose tools *succeeded* but whose
    model then failed to phrase it says "That is done." instead, which is the
    honest reading of that situation: the action did happen.
    """
    agent, _ = make_agent([BrainReply(text=""), BrainReply(text="")])

    result = await agent.run_turn("confirm you are working")

    assert result.reply == "I could not work that out."
    assert result.reply != "Done."
