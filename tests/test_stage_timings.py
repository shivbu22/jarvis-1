"""Stage timings: where a turn's time actually goes.

A turn is four costs the master experiences as one silence -- capture (client),
transcribe, think, first_sound (voice). Reporting only a total hides which stage
to fix, so each stage is measured where it happens and surfaced in the orb.
"""

from __future__ import annotations

import asyncio

from apps.gateway.brain.base import BrainReply
from apps.gateway.service import JarvisService
from tests.conftest import ScriptedBrain


class FakeVoice:
    name = "fake"

    def available(self) -> bool:
        return True

    def synthesize(self, text: str):
        from apps.gateway.tts import Speech

        return Speech(data=b"RIFF" + b"\x00" * 32, engine=self.name, sample_rate=24000)

    def describe(self) -> dict:
        return {"engine": self.name}


async def test_a_text_turn_reports_think_and_first_sound(settings, monkeypatch) -> None:
    import apps.gateway.service as service_module

    monkeypatch.setattr(service_module, "build_tts", lambda _settings: FakeVoice())
    service = JarvisService(settings)
    service.agent.brain = ScriptedBrain([BrainReply(text="Two plus two is four.")])
    try:
        result = await service.turn_text("what is 2 plus 2", session_id="t")
    finally:
        service.memory.close()

    assert "think" in result.stages, "the brain's time was never measured"
    assert "first_sound" in result.stages, "the voice's first cost was never measured"
    assert result.stages["think"] >= 0
    assert result.stages["first_sound"] >= 0
    # A text turn has no ears and no capture stage.
    assert "transcribe" not in result.stages
    assert "capture" not in result.stages


async def test_an_audio_turn_reports_the_transcribe_stage(settings, monkeypatch) -> None:
    service = JarvisService(settings)
    service.agent.brain = ScriptedBrain([BrainReply(text="Fine.")])

    async def fake_transcribe(_audio: bytes, **_kwargs):
        from apps.gateway.stt import Transcript

        await asyncio.sleep(0.01)
        return Transcript(text="status report")

    service.transcribe = fake_transcribe  # type: ignore[method-assign]
    try:
        result = await service.turn_audio(b"not really audio", speak=False)
    finally:
        service.memory.close()

    assert result.stages.get("transcribe", 0) >= 0.01, "whisper's time was not measured"
    assert "think" in result.stages


async def test_a_silent_turn_reports_transcribe_but_no_think(settings) -> None:
    service = JarvisService(settings)

    async def fake_transcribe(_audio: bytes, **_kwargs):
        from apps.gateway.stt import Transcript

        return Transcript(text="   ")

    service.transcribe = fake_transcribe  # type: ignore[method-assign]
    try:
        result = await service.turn_audio(b"not really audio")
    finally:
        service.memory.close()

    assert result.error == "no_speech"
    assert "transcribe" in result.stages, "even a failed turn shows what its ears cost"
    assert "think" not in result.stages, "the brain never ran, so it cannot have a cost"


def test_stages_reach_the_wire_rounded() -> None:
    """The orb receives stages inside turn_result, rounded to hundredths."""
    from apps.gateway.service import UtteranceResult

    result = UtteranceResult(
        transcript="", reply="", stages={"think": 1.23456, "first_sound": 0.123456}
    )
    assert result.describe()["stages"] == {"think": 1.23, "first_sound": 0.12}
