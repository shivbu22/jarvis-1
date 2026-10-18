"""Unit and integration tests for J.A.R.V.I.S. Autonomous Custom Command Learning."""

import pytest
from unittest.mock import MagicMock
from pathlib import Path

from apps.gateway.command_learner import parse_learning_intent, learn_command
from apps.gateway.memory import MemoryStore
from apps.gateway.tools.command_learning import (
    learn_custom_command,
    list_custom_commands,
    delete_custom_command,
)
from apps.gateway.tools.registry import ToolContext


@pytest.fixture
def mem_store(tmp_path):
    db_path = tmp_path / "test_memory.db"
    return MemoryStore(db_path)


@pytest.fixture
def tool_ctx(mem_store):
    return ToolContext(
        settings=MagicMock(),
        memory=mem_store,
        policy=MagicMock(),
        audit=MagicMock(),
        bus=MagicMock(),
    )


def test_parse_learning_intent_english():
    p1 = parse_learning_intent("learn command: when I say 'focus mode', minimize all windows and open VS Code")
    assert p1 is not None
    assert p1[0] == "focus mode"
    assert "minimize all windows and open VS Code" in p1[1]

    p2 = parse_learning_intent("whenever I say 'tea break', pause music and open browser")
    assert p2 is not None
    assert p2[0] == "tea break"
    assert "pause music and open browser" in p2[1]

    p3 = parse_learning_intent("teach command: 'market check' means fetch nifty 50 and check portfolio")
    assert p3 is not None
    assert p3[0] == "market check"
    assert "fetch nifty 50 and check portfolio" in p3[1]


def test_parse_learning_intent_hinglish():
    p1 = parse_learning_intent("naya command seekho: jab bhi main bolun 'chai time', music pause karo aur youtube chalao")
    assert p1 is not None
    assert p1[0] == "chai time"
    assert "music pause karo aur youtube" in p1[1]

    p2 = parse_learning_intent("jab main kahun 'shanti', mute system volume karo")
    assert p2 is not None
    assert p2[0] == "shanti"
    assert "mute system volume" in p2[1]


def test_parse_learning_intent_hindi():
    p1 = parse_learning_intent("नया कमांड सीखो: जब मैं कहूं 'रात हो गई', कंप्यूटर लॉक करो")
    assert p1 is not None
    assert p1[0] == "रात हो गई"
    assert "कंप्यूटर लॉक" in p1[1]

    p2 = parse_learning_intent("जब भी मैं बोलूं 'शांति', आवाज़ बंद करो")
    assert p2 is not None
    assert p2[0] == "शांति"
    assert "आवाज़ बंद" in p2[1]


def test_parse_learning_intent_negative():
    assert parse_learning_intent("open youtube") is None
    assert parse_learning_intent("what is the weather today") is None
    assert parse_learning_intent("good morning jarvis") is None


def test_learn_and_match_command(mem_store, tmp_path):
    res = learn_command(
        mem_store,
        trigger="chai time",
        action="pause music and open youtube",
        description="Relaxation macro",
        vault_dir=tmp_path / "vault",
    )
    assert res["ok"] is True
    assert "chai time" in res["reply"]

    # 1. Exact match
    matched = mem_store.match_custom_command("chai time")
    assert matched is not None
    assert matched["action_payload"] == "pause music and open youtube"
    assert matched["use_count"] == 1

    # 2. Spoken with wake word
    matched2 = mem_store.match_custom_command("Hey Jarvis, chai time")
    assert matched2 is not None
    assert matched2["use_count"] == 2

    # 3. Subphrase match
    matched3 = mem_store.match_custom_command("it is chai time now")
    assert matched3 is not None
    assert matched3["use_count"] == 3


def test_tools_command_learning(tool_ctx):
    # Learn via tool
    res = learn_custom_command(
        tool_ctx,
        trigger="focus mode",
        action="minimize all windows and open VS Code",
    )
    assert res.ok is True

    # List
    list_res = list_custom_commands(tool_ctx)
    assert list_res.ok is True
    assert "focus mode" in list_res.text

    # Delete
    del_res = delete_custom_command(tool_ctx, trigger="focus mode")
    assert del_res.ok is True

    # Verify deleted
    list_res2 = list_custom_commands(tool_ctx)
    assert "No learned commands" in list_res2.text


@pytest.mark.asyncio
async def test_agent_dynamic_command_learning_and_execution(harness):
    from apps.gateway.agent import Agent
    from apps.gateway.brain.base import Brain
    from apps.gateway.brain import BrainReply

    class EchoBrain(Brain):
        name = "mock"
        model = "mock"
        async def generate(self, messages, tools):
            user_msg = messages[-1]["content"]
            return BrainReply(text=f"Processed: {user_msg}")

    agent = Agent(
        settings=harness.settings,
        brain=EchoBrain(),
        registry=harness.registry,
        broker=harness.broker,
        audit=harness.audit,
        bus=harness.bus,
        memory=harness.memory,
    )

    # 1. Turn 1: Teach a new command
    turn1 = await agent.run_turn("Jarvis, whenever I say 'code time', open VS Code and open browser")
    assert "learned this new directive" in turn1.reply.lower()
    assert "code time" in turn1.reply.lower()

    # 2. Turn 2: Speak the learned command
    turn2 = await agent.run_turn("code time")
    assert "open" in turn2.reply.lower() and "vs code" in turn2.reply.lower() or "processed" in turn2.reply.lower()

