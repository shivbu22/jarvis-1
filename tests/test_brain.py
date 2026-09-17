"""Brain-layer tests: model resolution, request shape, and failover.

These cover the three failure modes that cost the most to find on this machine, and
none of them are visible from the HTTP tests alone:

1. **Model resolution.** Ollama 0.34 loads ``qwen3:4b`` with a 65536-token context.
   On a 4 GB GPU that inflates the model to ~13 GB, pushes ~84% of it onto the CPU
   and drops generation to ~6 tokens/s, so every spoken turn exceeds its timeout.
   The tuned ``jarvis-brain:4b`` (same weights, 8192 context) must therefore win when
   it is installed -- while a deliber00ately chosen *different* model must still be
   respected.
2. **A failover that crashes.** The failover bookkeeping once referenced a ``reply``
   that does not exist on the exception path, so the error handler raised
   ``UnboundLocalError`` and the turn returned an empty 500 instead of the fallback
   brain's answer. A failover must never be able to break the turn it is saving.
3. **An under-sized token cap.** A local 4B model spends 250-700 hidden reasoning
   tokens before it answers. Capping too low truncates the turn mid-thought, the
   visible answer comes back empty, and the agent reports a bare "Done."
"""

from __future__ import annotations

import json

import pytest
from apps.gateway.audit import AuditLog
from apps.gateway.brain import BrainRouter
from apps.gateway.brain.base import Brain, BrainError, BrainReply, ToolCall
from apps.gateway.brain.ollama import JARVIS_BRAIN_MODEL, TUNED_VARIANTS, OllamaBrain
from apps.gateway.brain.openai_compat import OpenAICompatBrain

# ---------------------------------------------------------------------------
# 1. model resolution
# ---------------------------------------------------------------------------


def _ollama_with(installed: list[str], model: str = "qwen3:4b") -> OllamaBrain:
    """An Ollama brain whose "what is installed" answer is scripted."""
    brain = OllamaBrain(host="http://127.0.0.1:11434", model=model)

    async def installed_models() -> list[str]:
        return installed

    async def is_running() -> bool:
        return True

    brain.installed_models = installed_models  # type: ignore[method-assign]
    brain.is_running = is_running  # type: ignore[method-assign]
    return brain


async def test_tuned_model_wins_over_the_stock_base() -> None:
    """Leaving the default in place must not select the slow stock model."""
    stock = next(
        stock_name
        for stock_name, tuned_name in TUNED_VARIANTS.items()
        if tuned_name == JARVIS_BRAIN_MODEL
    )
    brain = _ollama_with([stock, JARVIS_BRAIN_MODEL], model=stock)
    try:
        assert await brain.resolve_model() == JARVIS_BRAIN_MODEL
        assert brain.model == JARVIS_BRAIN_MODEL
        assert brain.model_note, "the substitution must be recorded, not silent"
    finally:
        await brain.aclose()


async def test_every_stock_base_maps_to_its_own_tuned_variant() -> None:
    """Each stock model gets *its* tuned brain, not whatever the default is.

    Regression guard: the preference once pointed everything at a single tuned name,
    which would have loaded the wrong weights entirely when the master configured
    the other family.
    """
    for stock, tuned in TUNED_VARIANTS.items():
        brain = _ollama_with([stock, tuned], model=stock)
        try:
            assert await brain.resolve_model() == tuned
            assert brain.model == tuned
        finally:
            await brain.aclose()


async def test_a_stock_base_without_its_tuned_variant_is_respected() -> None:
    """No tuned brain on disk means the stock model runs as configured."""
    stock = next(iter(TUNED_VARIANTS))
    brain = _ollama_with([stock], model=stock)
    try:
        assert await brain.resolve_model() == stock
        assert brain.model_note is None
    finally:
        await brain.aclose()


async def test_tuned_model_used_when_it_is_the_only_thing_installed() -> None:
    brain = _ollama_with([JARVIS_BRAIN_MODEL])
    try:
        assert await brain.resolve_model() == JARVIS_BRAIN_MODEL
    finally:
        await brain.aclose()


async def test_an_explicitly_chosen_model_is_respected() -> None:
    """A master who names a different model gets that model, not our preference.

    ``jarvis-brain:4b`` is derived from ``qwen3:4b``, so it is only a better choice
    for that base. Any other deliberate selection must pass straight through.
    """
    brain = _ollama_with(["qwen3:8b", JARVIS_BRAIN_MODEL], model="qwen3:8b")
    try:
        assert await brain.resolve_model() == "qwen3:8b"
        assert brain.model_note is None
    finally:
        await brain.aclose()


async def test_missing_model_falls_back_to_something_installed() -> None:
    brain = _ollama_with(["gemma3:4b"], model="gemma3:27b")
    try:
        assert await brain.resolve_model() == "gemma3:4b"
        assert "gemma3:27b" in (brain.model_note or "")
    finally:
        await brain.aclose()


async def test_no_models_reports_what_to_type() -> None:
    """An empty install must produce an actionable sentence, not a traceback."""
    brain = _ollama_with([])
    try:
        with pytest.raises(BrainError) as caught:
            await brain.resolve_model()
        assert "ollama pull" in str(caught.value)
    finally:
        await brain.aclose()


# ---------------------------------------------------------------------------
# 2. request shape
# ---------------------------------------------------------------------------


class _FakeResponse:
    status_code = 200
    text = "{}"

    def json(self) -> dict:
        return {"choices": [{"message": {"content": "Very good, sir.", "tool_calls": []}}]}


class _RecordingClient:
    """Stands in for httpx.AsyncClient, capturing exactly what was posted."""

    def __init__(self) -> None:
        self.paths: list[str] = []
        self.payloads: list[dict] = []

    async def post(self, path: str, json: dict | None = None) -> _FakeResponse:
        self.paths.append(path)
        self.payloads.append(json or {})
        return _FakeResponse()

    async def aclose(self) -> None:
        return None


async def test_local_brain_caps_output_tokens_generously() -> None:
    """The cap exists to bound latency, but must leave room for hidden reasoning.

    A 700-token cap measured as *too small*: qwen3 truncated mid-thought and the
    visible answer came back empty, which the agent loop then reported as "Done."
    """
    brain = OllamaBrain(host="http://127.0.0.1:11434", model="qwen3:4b")
    assert brain.max_tokens >= 1024

    recorder = _RecordingClient()
    brain._client = recorder  # type: ignore[assignment]
    try:
        await brain.generate([{"role": "user", "content": "hi"}], [])
    finally:
        await brain.aclose()

    payload = recorder.payloads[0]
    assert payload["max_tokens"] == brain.max_tokens
    assert recorder.paths[0] == "/chat/completions"


async def test_cloud_brains_leave_the_cap_to_the_provider() -> None:
    """Only the local brain overrides max_tokens; a cloud brain sends none."""
    brain = OpenAICompatBrain(name="Groq", base_url="https://example.invalid/v1", model="m")
    assert brain.max_tokens == 0

    recorder = _RecordingClient()
    brain._client = recorder  # type: ignore[assignment]
    try:
        await brain.generate([{"role": "user", "content": "hi"}], [])
    finally:
        await brain.aclose()

    assert "max_tokens" not in recorder.payloads[0]


async def test_tools_are_sent_with_auto_choice() -> None:
    brain = OpenAICompatBrain(name="Groq", base_url="https://example.invalid/v1", model="m")
    recorder = _RecordingClient()
    brain._client = recorder  # type: ignore[assignment]
    tools = [{"type": "function", "function": {"name": "t", "description": "d", "parameters": {}}}]
    try:
        await brain.generate([{"role": "user", "content": "hi"}], tools)
    finally:
        await brain.aclose()

    payload = recorder.payloads[0]
    assert payload["tools"] == tools
    assert payload["tool_choice"] == "auto"


async def test_provider_timing_is_carried_onto_the_reply() -> None:
    """Ollama reports its own timings; they must survive into the BrainReply."""
    brain = OpenAICompatBrain(name="Groq", base_url="https://example.invalid/v1", model="m")

    class TimedResponse(_FakeResponse):
        def json(self) -> dict:
            return {
                "choices": [{"message": {"content": "hi", "tool_calls": []}}],
                "total_duration": 1_500_000_000,
                "prompt_eval_count": 42,
                "eval_count": 7,
            }

    class TimedClient(_RecordingClient):
        async def post(self, path: str, json: dict | None = None) -> _FakeResponse:
            self.paths.append(path)
            self.payloads.append(json or {})
            return TimedResponse()

    brain._client = TimedClient()  # type: ignore[assignment]
    try:
        reply = await brain.generate([{"role": "user", "content": "hi"}], [])
    finally:
        await brain.aclose()

    assert reply.duration_ns == 1_500_000_000
    assert reply.prompt_eval_count == 42
    assert reply.eval_count == 7


# ---------------------------------------------------------------------------
# 3. failover
# ---------------------------------------------------------------------------


class _ExplodingBrain(Brain):
    name = "Exploding"
    model = "boom-1"

    async def generate(self, messages, tools):  # type: ignore[override]
        raise BrainError("the provider fell over")


class _GoodBrain(Brain):
    name = "Good"
    model = "good-1"

    async def generate(self, messages, tools):  # type: ignore[override]
        return BrainReply(text="Answered by the fallback.", model=self.model)


async def test_failover_answers_and_is_recorded(tmp_path) -> None:
    audit = AuditLog(tmp_path / "audit.jsonl", True)
    router = BrainRouter([_ExplodingBrain(), _GoodBrain()], audit=audit)

    reply = await router.generate([{"role": "user", "content": "hi"}], [])

    assert reply.text == "Answered by the fallback."
    assert reply.error is None
    assert router.last_used == "Good"

    events = [entry["event"] for entry in audit.tail()]
    assert "brain_failover" in events
    assert "brain_fallback" in events


async def test_failover_still_answers_when_bookkeeping_is_broken() -> None:
    """The bookkeeping must not be able to break the answer it is recording.

    Two regressions are guarded here. First, referencing a reply that does not exist
    on the exception path turned a recoverable provider outage into an empty HTTP
    500. Second, a failing audit write did the same -- and a failover is exactly
    when the system is already degraded, so it must not also be when the spoken
    answer disappears.
    """

    class HostileAudit:
        def record(self, *args, **kwargs):
            raise RuntimeError("the audit trail is broken too")

    router = BrainRouter([_ExplodingBrain(), _GoodBrain()], audit=HostileAudit())

    reply = await router.generate([{"role": "user", "content": "hi"}], [])
    assert reply.text == "Answered by the fallback."
    assert reply.error is None


async def test_all_brains_failing_reports_every_reason(tmp_path) -> None:
    audit = AuditLog(tmp_path / "audit.jsonl", True)
    router = BrainRouter([_ExplodingBrain(), _ExplodingBrain()], audit=audit)

    reply = await router.generate([{"role": "user", "content": "hi"}], [])

    assert reply.error
    assert "provider fell over" in reply.error


async def test_no_brains_configured_is_reported_not_crashed() -> None:
    reply = await BrainRouter([]).generate([{"role": "user", "content": "hi"}], [])
    assert reply.error == "No brain is configured."


def test_tool_reply_serialises_for_the_next_round_trip() -> None:
    reply = BrainReply(
        text="",
        tool_calls=[ToolCall(id="call_1", name="get_weather", arguments={"city": "Mumbai"})],
    )
    message = reply.as_message()
    assert message["role"] == "assistant"
    assert message["tool_calls"][0]["id"] == "call_1"
    # Arguments must travel as a JSON *string*, which is the wire format every
    # OpenAI-compatible provider expects on the way back in.
    assert json.loads(message["tool_calls"][0]["function"]["arguments"]) == {"city": "Mumbai"}
