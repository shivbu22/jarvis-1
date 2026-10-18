"""Streaming speech: what gets spoken, when, and in how many pieces.

Two things are protected here, and both were wrong at least once:

1. **Only the answering round is spoken.** A round that calls a tool may narrate
   first; the observed failure was the model saying "How are you?" while deciding
   to ask a question, which was then spoken as though it were the answer.
2. **The chunking policy is a cost decision.** Synthesis costs ~6 s of fixed work
   per call on this machine (measured), so the first chunk is deliberately small
   and later chunks deliberately large -- otherwise starting sooner would double
   the time the whole reply takes to say.
"""

from __future__ import annotations

import pytest
from apps.gateway.brain.base import BrainReply
from apps.gateway.service import JarvisService
from apps.gateway.tts import FIRST_SPEECH_CHARS, MAX_SPEECH_CHARS, Speech, SpeechChunker
from tests.conftest import ScriptedBrain, tool_call, tool_reply


class FakeVoice:
    """An engine that records what it was asked to say."""

    name = "fake"

    def __init__(self) -> None:
        self.spoken: list[str] = []

    def available(self) -> bool:
        return True

    def synthesize(self, text: str) -> Speech:
        self.spoken.append(text)
        return Speech(data=b"RIFF" + b"\x00" * 64, engine=self.name, sample_rate=24000)

    def describe(self) -> dict:
        return {"engine": self.name}


class StreamingBrain(ScriptedBrain):
    """A scripted brain that emits its text the way a real one does: in pieces."""

    name = "streaming"
    model = "streaming-1"

    async def stream_tokens(self, messages, tools, on_piece):  # type: ignore[override]
        self.calls.append(list(messages))
        index = min(len(self.calls) - 1, len(self.replies) - 1)
        reply = self.replies[index] if self.replies else BrainReply(text="(no script)")
        for start in range(0, len(reply.text), 8):
            await on_piece(reply.text[start : start + 8])
        return reply


def _service(settings, monkeypatch, brain, voice) -> JarvisService:
    """A real service with a scripted brain and a recording voice.

    The patch targets the service module, not apps.gateway.tts: service.py binds
    ``build_tts`` at import time, so patching the tts module would leave the real
    engine (and its warm-up) in the path.
    """
    import apps.gateway.service as service_module

    monkeypatch.setattr(service_module, "build_tts", lambda _settings: voice)
    service = JarvisService(settings)
    service.agent.brain = brain
    return service


# --- the chunker ----------------------------------------------------------


def test_the_first_chunk_is_small_so_speaking_starts_early() -> None:
    """The first clause goes out alone, far below the steady-state bound.

    A boundary only counts once whitespace follows it, which is why the text is
    pushed in two pieces: the second push is what completes the clause.
    """
    chunker = SpeechChunker(minimum=96, first_minimum=24)
    assert chunker.push("It's light drizzle in Mumbai,") == []
    assert chunker.push(" so carry an umbrella.") == ["It's light drizzle in Mumbai,"]
    assert len("It's light drizzle in Mumbai,") < chunker.minimum


def test_later_chunks_wait_for_a_longer_run() -> None:
    """After the first, a short clause is held back: one call per clause is slow."""
    chunker = SpeechChunker(minimum=96, first_minimum=24)
    assert chunker.push("It's light drizzle in Mumbai, so carry an umbrella.") == [
        "It's light drizzle in Mumbai,"
    ]
    # The steady-state bound now applies, so a short clause does not cost a call.
    assert chunker.push(" Yes.") == []
    assert chunker.pending.strip().endswith("Yes.")


def test_a_runaway_sentence_is_cut_at_a_word_boundary() -> None:
    chunker = SpeechChunker(minimum=20, maximum=60, first_minimum=20)
    ready = chunker.push("word " * 40)
    assert ready, "a reply with no punctuation must still start speaking"
    assert all(len(chunk) <= 60 for chunk in ready)
    assert all(chunk.endswith("word") for chunk in ready), "a word was sliced in half"


def test_the_steady_state_bound_biases_towards_fewer_calls() -> None:
    """Two short sentences go out as one chunk, not two -- that is the cost rule."""
    chunker = SpeechChunker(minimum=20, maximum=MAX_SPEECH_CHARS, first_minimum=20)
    ready = chunker.push("First one. Second one. Third one.")
    assert ready == ["First one. Second one."]
    assert chunker.flush() == ["Third one."]


def test_nothing_empty_is_ever_emitted() -> None:
    chunker = SpeechChunker(minimum=2, first_minimum=2)
    assert chunker.push("") == []
    assert chunker.push("   ") == []
    assert chunker.flush() == []
    assert chunker.push("Ok. Fine.") == ["Ok."]
    assert chunker.flush() == ["Fine."]
    assert chunker.flush() == []


def test_flush_returns_the_tail_exactly_once() -> None:
    chunker = SpeechChunker(minimum=500, first_minimum=500)
    assert chunker.push("A sentence that never reaches the threshold.") == []
    assert chunker.flush() == ["A sentence that never reaches the threshold."]
    assert chunker.flush() == []


def test_the_first_bound_is_smaller_than_the_steady_one() -> None:
    assert SpeechChunker().minimum > FIRST_SPEECH_CHARS


# --- which round gets spoken ---------------------------------------------


async def _turn(service: JarvisService, text: str = "do the thing", **kwargs):
    spoken: list[str] = []

    async def on_speech(chunk: str) -> None:
        spoken.append(chunk)

    try:
        result = await service.turn_text(text, session_id="t", on_speech=on_speech, **kwargs)
    finally:
        await service.stop()
    return spoken, result


async def test_only_the_answering_round_is_spoken(settings, monkeypatch) -> None:
    """Narration from a tool round must never reach the speaker."""
    brain = StreamingBrain(
        [
            BrainReply(text="Let me look that up.", tool_calls=[tool_call("system_info")]),
            BrainReply(text="Here is the answer. It has two parts."),
        ]
    )
    voice = FakeVoice()
    service = _service(settings, monkeypatch, brain, voice)
    spoken, result = await _turn(service)

    heard = " ".join(spoken)
    assert "Let me look that up" not in heard, "a tool round's narration was spoken"
    assert heard.split() == result.reply.split(), "the answer was not what was spoken"
    assert voice.spoken == [], "it must not also be synthesised whole"
    assert result.speech_streamed is True
    assert result.speech_incomplete is False


async def test_a_json_round_is_retracted_and_never_spoken(settings, monkeypatch) -> None:
    """A streamed round that turns out to be tool-call JSON is taken back.

    The JSON itself is never spoken (only prose rounds speak), but the transport
    still learns the round was junk so it can clear what streamed on screen, and
    the composition must not disturb the speech chunker's own round handling.
    """
    brain = StreamingBrain(
        [
            BrainReply(text='{"name":"ask_user","parameters":"I am working, sir."}'),
        ]
    )
    voice = FakeVoice()
    service = _service(settings, monkeypatch, brain, voice)
    rounds: list[str] = []

    async def on_round(kind: str, _iteration: int) -> None:
        rounds.append(kind)

    spoken, result = await _turn(service, on_round=on_round)

    assert "retract" in rounds, "the transport was never told to take the JSON back"
    # The JSON was never streamed as speech, the salvaged words become the
    # reply, and they reach the voice through the whole-reply fallback.
    assert spoken == [], "the junk round's JSON must never be spoken"
    assert voice.spoken == ["I am working, sir."]
    assert result.reply == "I am working, sir."
    assert result.speech_streamed is False


async def test_a_long_answer_is_spoken_in_several_chunks(settings, monkeypatch) -> None:
    brain = StreamingBrain(
        [
            BrainReply(
                text=(
                    "The weather in Mumbai is light drizzle, with a high of thirty degrees. "
                    "You should carry an umbrella this afternoon."
                )
            )
        ]
    )
    voice = FakeVoice()
    service = _service(settings, monkeypatch, brain, voice)
    spoken, result = await _turn(service)

    assert len(spoken) >= 2, "a two-sentence answer should not wait as a single call"
    assert " ".join(spoken).split() == result.reply.split()


async def test_a_brain_that_cannot_stream_is_still_spoken(settings, monkeypatch) -> None:
    """Non-streaming brains must still be heard, in one piece at the end."""
    voice = FakeVoice()
    brain = ScriptedBrain([BrainReply(text="A single spoken sentence.")])
    service = _service(settings, monkeypatch, brain, voice)
    spoken, result = await _turn(service)

    assert spoken == []
    assert voice.spoken == ["A single spoken sentence."]
    assert result.audio, "the whole WAV must come back for the caller"
    assert result.speech_streamed is False


async def test_no_speech_when_speaking_is_off(settings, monkeypatch) -> None:
    voice = FakeVoice()
    service = _service(
        settings, monkeypatch, StreamingBrain([BrainReply(text="Silence.")]), voice
    )
    spoken, result = await _turn(service, speak=False)
    assert spoken == []
    assert voice.spoken == []
    assert result.audio is None


# --- the agent's round markers -------------------------------------------


async def test_rounds_are_announced_with_their_shape(settings, monkeypatch) -> None:
    """The transport needs to know which round is the answer, and is told."""
    brain = StreamingBrain([tool_reply("system_info"), BrainReply(text="Done.")])
    service = _service(settings, monkeypatch, brain, FakeVoice())
    events: list[tuple[str, int]] = []

    async def on_round(kind, iteration):
        events.append((kind, iteration))

    try:
        await service.agent.run_turn("go", session_id="t", on_round=on_round)
    finally:
        await service.stop()

    assert [kind for kind, _ in events] == ["start", "start", "prose"]
    assert events[-1] == ("prose", 2)


async def test_a_sync_round_callback_is_accepted(settings, monkeypatch) -> None:
    service = _service(
        settings, monkeypatch, StreamingBrain([BrainReply(text="Fine.")]), FakeVoice()
    )
    seen: list[str] = []
    try:
        await service.agent.run_turn("go", session_id="t", on_round=lambda kind, _i: seen.append(kind))
    finally:
        await service.stop()
    assert seen == ["start", "prose"]


# --- memory recall -------------------------------------------------------


def test_agent_recalls_relevant_notes_into_the_prompt(settings) -> None:
    service = JarvisService(settings)
    try:
        service.memory.add("the master's workshop is on Friday", tags="plans")
        recalled = service.agent.recall("what is happening on Friday")
        assert "workshop" in recalled
        prompt = service.agent.system_prompt(extra_context=recalled)
        assert "workshop" in prompt
    finally:
        service.memory.close()


def test_recall_is_silent_when_nothing_is_relevant(settings) -> None:
    """An unrelated question must not drag the notebook into the prompt."""
    service = JarvisService(settings)
    try:
        service.memory.add("the master's workshop is on Friday")
        assert service.agent.recall("hello jarvis how are you") == ""
        assert service.memory.recall("hello jarvis how are you") == []
    finally:
        service.memory.close()


@pytest.mark.parametrize("text", ["", "   ", "the and of to", "???"])
def test_recall_ignores_queries_without_substance(settings, text: str) -> None:
    service = JarvisService(settings)
    try:
        service.memory.add("the workshop is on Friday")
        assert service.memory.recall(text) == []
    finally:
        service.memory.close()


def test_recall_requires_a_shared_content_word(settings) -> None:
    """The index ORs its terms, so a shared stopword must not count as a match."""
    service = JarvisService(settings)
    try:
        service.memory.add("I am functioning properly")
        # "properly" appears only in the note; the master's question shares nothing.
        assert service.memory.recall("tell me three things you can do for me") == []
    finally:
        service.memory.close()
