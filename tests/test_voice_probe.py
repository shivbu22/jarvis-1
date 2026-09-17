"""The cloned voice must earn its place at conversation speed.

A CPU-only torch build can take a minute to speak one sentence. That is not a
voice, it is a pause. These tests pin the contract: the probe measures, keeps a
fast clone, degrades a slow one to the Windows voice, and explains itself in
/health notes either way.
"""

from __future__ import annotations

import asyncio

from apps.gateway.service import JarvisService
from tests.conftest import ScriptedBrain


class TimedFakeVoice:
    """A chatterbox stand-in whose synthesize takes a configurable time."""

    def __init__(self, seconds: float, fail: bool = False) -> None:
        self.seconds = seconds
        self.fail = fail
        self.calls: list[str] = []

    name = "chatterbox"

    def available(self) -> bool:
        return True

    def describe(self) -> dict:
        return {"engine": "chatterbox", "available": True}

    def synthesize(self, text: str):
        import time as _time

        self.calls.append(text)
        if self.fail:
            raise RuntimeError("voice exploded")
        _time.sleep(self.seconds)
        return object()  # the probe never inspects the payload


class AlwaysAvailableSapi:
    name = "sapi"

    def __init__(self, settings) -> None:
        self.settings = settings

    def available(self) -> bool:
        return True

    def synthesize(self, text: str):
        return object()


def make_service(settings, policy, monkeypatch, voice) -> JarvisService:
    """A real service with the TTS swapped for a controllable fake.

    The patch targets the service module because service.py binds ``build_tts``
    at import time -- patching apps.gateway.tts would leave the real engine (and
    its ~1.5 GB warm-up) silently in the path.
    """
    import apps.gateway.service as service_module

    monkeypatch.setattr(service_module, "build_tts", lambda s: voice)
    service = JarvisService(settings)
    service.agent.brain = ScriptedBrain([])
    return service


def start_and_probe(service: JarvisService) -> None:
    """Run start() and let the voice probe finish deterministically.

    The probe is deliberately a background task in production -- the gateway must
    answer /health while the weights warm up -- so the test awaits its handle
    rather than sleeping and hoping.
    """

    async def run() -> None:
        await service.start()
        if service._voice_probe is not None:
            await service._voice_probe

    asyncio.run(run())


def test_a_fast_cloned_voice_stays_live(settings, policy, monkeypatch) -> None:
    voice = TimedFakeVoice(seconds=0.05)
    service = make_service(settings, policy, monkeypatch, voice)
    start_and_probe(service)

    assert service.tts.name == "chatterbox"
    assert any("cloned voice is live" in note for note in service.notes)


def test_a_slow_cloned_voice_degrades_to_the_windows_voice(
    settings, policy, monkeypatch
) -> None:
    voice = TimedFakeVoice(seconds=0.4)
    # The probe threshold is a module constant, so the test shrinks it instead of
    # sleeping the real 10 seconds.
    from apps.gateway import service as service_module

    monkeypatch.setattr(service_module, "VOICE_SPEED_LIMIT_SECONDS", 0.2)
    sapi_calls = {"n": 0}

    class CountingSapi(AlwaysAvailableSapi):
        def __init__(self, s) -> None:
            super().__init__(s)
            sapi_calls["n"] += 1

    monkeypatch.setattr("apps.gateway.tts.SapiEngine", CountingSapi)
    service = make_service(settings, policy, monkeypatch, voice)
    start_and_probe(service)

    assert service.tts.name == "sapi", "a slow clone must be replaced"
    assert sapi_calls["n"] == 1
    assert any("Windows voice" in note for note in service.notes)
    assert any("force it" in note for note in service.notes)


def test_a_failing_cloned_voice_is_reported_not_fatal(settings, policy, monkeypatch) -> None:
    voice = TimedFakeVoice(seconds=0.0, fail=True)
    service = make_service(settings, policy, monkeypatch, voice)
    start_and_probe(service)  # must not raise

    assert service.tts.name == "chatterbox"  # left in place; speak() has its own fallback
    assert any("warm-up" in note for note in service.notes)
