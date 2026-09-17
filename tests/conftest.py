"""Shared fixtures.

Tests build their own Service harness rather than going through JarvisService:
that keeps the safety tests fast and independent of whether the optional voice
stack or a running Ollama happen to be present on the machine.

The policy under test is the REAL `config/policy.yaml`, not a copy. A test suite
that grades a fixture instead of the shipped configuration will happily pass while
the thing that actually runs is misconfigured.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.gateway.agent import Agent  # noqa: E402
from apps.gateway.audit import AuditLog  # noqa: E402
from apps.gateway.brain.base import Brain, BrainReply, ToolCall  # noqa: E402
from apps.gateway.config import Settings  # noqa: E402
from apps.gateway.confirm import ConfirmationBroker  # noqa: E402
from apps.gateway.events import EventBus  # noqa: E402
from apps.gateway.memory import MemoryStore  # noqa: E402
from apps.gateway.policy import Policy  # noqa: E402
from apps.gateway.tools import ToolContext, build_registry  # noqa: E402

POLICY_PATH = ROOT / "config" / "policy.yaml"


def pytest_collection_modifyitems(config, items):
    """Skip `voice` tests unless they are explicitly asked for.

    They load real whisper and Chatterbox weights (gigabytes, minutes on CPU), so
    the default suite has to stay independent of them to remain useful.
    """
    import os

    if os.environ.get("JARVIS_RUN_VOICE_TESTS") == "1":
        return
    skipper = pytest.mark.skip(reason="set JARVIS_RUN_VOICE_TESTS=1 to run voice tests")
    for item in items:
        if "voice" in item.keywords:
            item.add_marker(skipper)


class ScriptedBrain(Brain):
    """A brain that returns a fixed script, one reply per call.

    The last reply repeats if the agent asks for more, which keeps tests that
    exercise the iteration cap simple to write.
    """

    name = "scripted"
    model = "scripted-1"

    def __init__(self, replies: list[BrainReply]) -> None:
        self.replies = replies
        self.calls: list[list[dict]] = []
        self.tool_sets: list[list[dict]] = []

    async def generate(self, messages, tools):  # type: ignore[override]
        self.calls.append(list(messages))
        self.tool_sets.append(list(tools))
        index = min(len(self.calls) - 1, len(self.replies) - 1)
        return self.replies[index] if self.replies else BrainReply(text="(no script)")

    # Keep the scripted brain a minimal turn cost so tests never accidentally
    # depend on a real model. Ollama's timing fields are optional extras, so we
    # leave this bridge thin: the harness can attach timing only when a test
    # explicitly needs it.
    def with_timing(self, *, prompt_eval_count: int = 0, eval_count: int = 0, duration_ns: int = 0) -> None:
        """Set timing on all queued replies (call before a turn for a scripted brain)."""
        for reply in self.replies:
            reply.prompt_eval_count = prompt_eval_count
            reply.eval_count = eval_count
            reply.duration_ns = duration_ns


def tool_call(name: str, arguments: dict | None = None, call_id: str = "call_1") -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=arguments or {})


def tool_reply(name: str, arguments: dict | None = None) -> BrainReply:
    return BrainReply(
        text="", tool_calls=[tool_call(name, arguments)], model="scripted-1"
    )


@dataclass
class Harness:
    settings: Settings
    policy: Policy
    memory: MemoryStore
    audit: AuditLog
    bus: EventBus
    broker: ConfirmationBroker
    ctx: ToolContext
    registry: object


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Deterministic settings with all state inside the test's tmp_path."""
    data_dir = tmp_path / "data"
    return Settings(
        root=tmp_path,
        data_dir=data_dir,
        voices_dir=tmp_path / "voices",
        config_dir=ROOT / "config",
        policy_path=POLICY_PATH,
        audit_enabled=True,
        offline_mode=True,
        confirm_timeout_seconds=0.35,
        history_turns=6,
        max_tool_iterations=3,
        # Pin the cheap Windows voice: once the multi-GB Chatterbox weights are
        # cached, "auto" would resolve to chatterbox here and the real 60s
        # synthesis would run inside every API test (tests must stay fast and
        # independent of the optional voice stack; the voice tests opt in
        # explicitly with JARVIS_RUN_VOICE_TESTS=1).
        tts_engine="sapi",
    )


@pytest.fixture
def policy(tmp_path: Path) -> Policy:
    """The shipped policy, with the test's tmp_path added as a writable root.

    Without this, every write test would be refused by the real roots and the
    success path would never be exercised -- a suite that only proves refusals is
    half a suite.
    """
    import yaml

    data = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
    data["file_write_roots"] = [*(data.get("file_write_roots") or []), str(tmp_path)]
    shell = data.setdefault("shell", {})
    shell["workdir_roots"] = [*(shell.get("workdir_roots") or []), str(tmp_path)]
    return Policy(data, source=str(POLICY_PATH))


@pytest.fixture
def harness(settings: Settings, policy: Policy) -> Harness:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    memory = MemoryStore(settings.db_path)
    audit = AuditLog(settings.audit_path, True)
    bus = EventBus()
    broker = ConfirmationBroker(settings.confirm_timeout_seconds, bus=bus, audit=audit)
    ctx = ToolContext(settings=settings, memory=memory, policy=policy, audit=audit, bus=bus)
    registry = build_registry(policy, ctx)
    result = Harness(
        settings=settings,
        policy=policy,
        memory=memory,
        audit=audit,
        bus=bus,
        broker=broker,
        ctx=ctx,
        registry=registry,
    )
    yield result
    memory.close()


@pytest.fixture
def agent(harness: Harness) -> Agent:
    return Agent(
        settings=harness.settings,
        brain=ScriptedBrain([BrainReply(text="Very good, sir.")]),
        registry=harness.registry,
        broker=harness.broker,
        audit=harness.audit,
        bus=harness.bus,
        memory=harness.memory,
    )


@pytest.fixture
def make_agent(harness: Harness):
    """Build an Agent whose brain returns a given script."""

    def factory(replies: list[BrainReply]) -> tuple[Agent, ScriptedBrain]:
        brain = ScriptedBrain(replies)
        built = Agent(
            settings=harness.settings,
            brain=brain,
            registry=harness.registry,
            broker=harness.broker,
            audit=harness.audit,
            bus=harness.bus,
            memory=harness.memory,
        )
        return built, brain

    return factory
