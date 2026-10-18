"""Unit tests for Speaker Identification & Voiceprint Tools."""

from __future__ import annotations

import io
import wave
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
from apps.gateway.speaker_id import SpeakerRecognizer
from apps.gateway.tools.registry import ToolContext
from apps.gateway.tools.speaker import (
    speaker_delete,
    speaker_list,
)


def _generate_synthetic_pcm16(freq: float = 440.0, duration: float = 1.0, sr: int = 16000) -> bytes:
    """Generate a clean synthetic sine wave PCM16 audio bytes."""
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    # Add harmonics for distinct spectral fingerprint
    audio = 0.6 * np.sin(2 * np.pi * freq * t) + 0.3 * np.sin(2 * np.pi * (freq * 2) * t)
    pcm = (audio * 32767).astype(np.int16)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


@pytest.fixture
def temp_speaker_recognizer(tmp_path: Path) -> SpeakerRecognizer:
    rec = SpeakerRecognizer(data_dir=tmp_path / "speakers")
    return rec


def test_voiceprint_extraction(temp_speaker_recognizer: SpeakerRecognizer) -> None:
    audio1 = _generate_synthetic_pcm16(freq=220.0, duration=1.0)
    emb1 = temp_speaker_recognizer.extract_embedding(audio1)
    assert isinstance(emb1, np.ndarray)
    assert emb1.shape == (64,)
    assert not np.all(emb1 == 0)
    assert np.isclose(np.linalg.norm(emb1), 1.0, atol=1e-3)


def test_speaker_enroll_and_identify(temp_speaker_recognizer: SpeakerRecognizer) -> None:
    # Speaker A (lower pitch ~180Hz)
    audio_a = _generate_synthetic_pcm16(freq=180.0, duration=1.5)
    # Speaker B (higher pitch ~520Hz)
    audio_b = _generate_synthetic_pcm16(freq=520.0, duration=1.5)

    # Enroll Speaker A as Master
    ok_a, _msg_a = temp_speaker_recognizer.enroll("Master", audio_a, description="Primary owner")
    assert ok_a is True

    # Enroll Speaker B as Guest
    ok_b, _msg_b = temp_speaker_recognizer.enroll("Guest", audio_b, description="Visiting friend")
    assert ok_b is True

    # Test Identify on Speaker A's voice
    speaker, conf, _scores = temp_speaker_recognizer.identify(audio_a)
    assert speaker == "Master"
    assert conf > 0.70

    # Test Identify on Speaker B's voice
    speaker_b, conf_b, _scores_b = temp_speaker_recognizer.identify(audio_b)
    assert speaker_b == "Guest"
    assert conf_b > 0.70

    # Test List profiles
    profs = temp_speaker_recognizer.list_profiles()
    assert len(profs) == 2
    names = {p["name"] for p in profs}
    assert "Master" in names
    assert "Guest" in names

    # Test Delete
    del_ok = temp_speaker_recognizer.delete_profile("Guest")
    assert del_ok is True
    assert len(temp_speaker_recognizer.list_profiles()) == 1


def test_speaker_tools_handlers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    rec = SpeakerRecognizer(data_dir=tmp_path / "speakers_tools")
    monkeypatch.setattr(SpeakerRecognizer, "get_instance", lambda *args, **kwargs: rec)

    audio = _generate_synthetic_pcm16(freq=300.0, duration=1.0)
    rec.enroll("Master", audio, description="System Master")

    ctx = MagicMock(spec=ToolContext)

    # speaker_list tool
    res_list = speaker_list(ctx)
    assert res_list.ok is True
    assert "Master" in res_list.text

    # speaker_delete tool
    res_del = speaker_delete(ctx, name="Master")
    assert res_del.ok is True
    assert "Successfully deleted" in res_del.text
