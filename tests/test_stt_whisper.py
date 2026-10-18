from __future__ import annotations

import io

import numpy as np
import pytest
import soundfile as sf
from apps.gateway.config import load_settings
from apps.gateway.stt import WhisperSTT


def test_whisper_faint_audio_transcription() -> None:
  settings = load_settings()
  stt = WhisperSTT(settings)
  if not stt.available():
    pytest.skip("faster-whisper not available")

  # Generate 1.0s low amplitude synthetic audio
  sr = 16000
  t = np.linspace(0, 1.0, sr)
  audio = (0.005 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)

  buf = io.BytesIO()
  sf.write(buf, audio, sr, format="WAV")
  raw_wav = buf.getvalue()

  transcript = stt.transcribe(raw_wav)
  assert isinstance(transcript.text, str)
