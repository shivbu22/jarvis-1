"""Voice tests that load real speech models.

Skipped unless JARVIS_RUN_VOICE_TESTS=1, because they download and load gigabytes
of weights and take minutes on CPU. When enabled they are the strongest evidence
that the assistant actually hears and speaks.

The STT test needs no microphone. It has the Windows voice synthesise a known
sentence, then feeds that WAV to whisper and checks the words come back -- a
genuine round trip through the real audio path, which is exactly what cannot be
verified by reading code.
"""

from __future__ import annotations

import platform

import pytest
from apps.gateway.config import load_settings
from apps.gateway.tts import SapiEngine, build_tts

pytestmark = pytest.mark.voice

SENTENCE = "The quick brown fox jumps over the lazy dog in Mumbai."


@pytest.mark.skipif(platform.system() != "Windows", reason="the fallback voice is Windows-only")
def test_windows_voice_produces_a_playable_wav() -> None:
    engine = SapiEngine(load_settings())
    if not engine.available():
        pytest.skip("no Windows speech engine")

    speech = engine.synthesize(SENTENCE)
    assert speech.data[:4] == b"RIFF"
    assert len(speech.data) > 4000
    assert speech.sample_rate > 8000


@pytest.mark.skipif(platform.system() != "Windows", reason="needs the Windows voice as a source")
def test_whisper_transcribes_synthesised_speech() -> None:
    """Speech out, speech back in: the loop the orb depends on."""
    settings = load_settings()
    speaker = SapiEngine(settings)
    if not speaker.available():
        pytest.skip("no Windows speech engine to generate the sample")

    from apps.gateway.stt import WhisperSTT

    stt = WhisperSTT(settings)
    if not stt.available():
        pytest.skip("faster-whisper is not installed")

    audio = speaker.synthesize(SENTENCE).data
    transcript = stt.transcribe(audio)

    assert transcript.text, "whisper returned nothing for clear synthesised speech"
    cleaned = transcript.text.lower()
    # Check for the distinctive content words rather than an exact match: whisper
    # punctuates and casing differs, and this must not be a brittle string test.
    for word in ("fox", "lazy", "dog", "mumbai"):
        assert word in cleaned, f"{word!r} missing from {transcript.text!r}"


@pytest.mark.slow
@pytest.mark.skipif(platform.system() != "Windows", reason="the reference clip comes from SAPI")
def test_chatterbox_speaks_in_the_cloned_voice(tmp_path) -> None:
    """A real synthesis: loads the ~1.5 GB weights, ~60 s per sentence on CPU.

    Marked slow so the normal gate stays fast -- run it explicitly with
    `pytest -m slow` when touching the voice stack.
    """
    settings = load_settings()
    if not settings.tts_allow_download:
        from apps.gateway.tts import ChatterboxEngine

        engine = ChatterboxEngine(settings)
        if not engine._weights_cached():
            pytest.skip(
                "Chatterbox weights are not local and TTS_ALLOW_DOWNLOAD=0. "
                "Run scripts/build_jarvis_voice.py to fetch them."
            )

    engine = build_tts(settings)
    if engine.name != "chatterbox":
        pytest.skip(f"chatterbox is not the active engine ({engine.name})")

    speech = engine.synthesize("Yes sir, the weather in Mumbai is clear.")
    assert speech.data[:4] == b"RIFF"
    assert speech.engine == "chatterbox"
    assert len(speech.data) > 8000


def test_reference_voice_is_validated(tmp_path) -> None:
    from apps.gateway.tts import TTSError, build_and_save_conds

    missing = tmp_path / "absent.wav"
    with pytest.raises(TTSError):
        build_and_save_conds(missing, tmp_path / "out.wav")

    # Too short: a one-second clip cannot define a voice.
    import wave

    short = tmp_path / "short.wav"
    with wave.open(str(short), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(b"\x01\x02" * 24000)
    with pytest.raises(TTSError, match="at least"):
        build_and_save_conds(short, tmp_path / "out.wav")


@pytest.mark.skipif(platform.system() != "Windows", reason="needs a speech source")
def test_reference_voice_is_normalised(tmp_path) -> None:
    """A real reference clip becomes mono 24 kHz PCM plus a manifest."""
    speaker = SapiEngine(load_settings())
    if not speaker.available():
        pytest.skip("no Windows speech engine")

    raw = tmp_path / "ref.wav"
    raw.write_bytes(speaker.synthesize(SENTENCE * 3).data)

    from apps.gateway.tts import build_and_save_conds

    out = tmp_path / "jarvis-ref-24k.wav"
    manifest = build_and_save_conds(raw, out)

    assert out.exists()
    assert manifest["sample_rate"] == 24000
    assert manifest["seconds"] >= 5
    assert (tmp_path / "jarvis-conds.json").exists()

    import soundfile as sf

    data, rate = sf.read(str(out))
    assert rate == 24000
    assert data.ndim == 1  # mono
