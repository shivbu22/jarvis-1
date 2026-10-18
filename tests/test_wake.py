"""Wake-word tests.

The unit tests here need no models and always run. The important one is
``test_missing_weights_are_not_reported_as_ready``: ``openwakeword`` ships
*without* its ONNX weights, so "the import works" is not the same as "this can
detect anything", and a health check that confuses the two is worse than none.

``test_the_wake_word_fires_on_synthesised_speech`` is the real thing -- the
Windows voice says "Hey Jarvis", those samples go through the detector, and the
detection is asserted. It is opt-in (JARVIS_RUN_VOICE_TESTS=1), like the other
real-model tests, because a cold machine downloads ~10 MB of weights first.
"""

from __future__ import annotations

import dataclasses
import io
import platform

import numpy as np
import pytest
import soundfile as sf
from apps.gateway import wake
from apps.gateway.config import Settings, load_settings
from apps.gateway.wake import WakeWordDetector, required_weights

WAKE_PHRASE = "Hey Jarvis, what's the weather in Mumbai?"


def _disabled(settings: Settings) -> Settings:
    return dataclasses.replace(settings, wake_enabled=False)


def _sixteen_k(pcm: bytes) -> bytes:
    """Any WAV's samples as the 16 kHz mono int16 frames the detector wants."""
    samples, rate = sf.read(io.BytesIO(pcm), dtype="float32")
    if samples.ndim > 1:
        samples = samples.mean(axis=1)
    if rate != wake.RATE:
        samples = np.interp(
            np.linspace(0, len(samples) - 1, int(len(samples) * wake.RATE / rate)),
            np.arange(len(samples)),
            samples,
        ).astype("float32")
    peak = float(np.max(np.abs(samples))) or 1.0
    return (samples / peak * 0.7 * 32767).astype("<i2").tobytes()


# --- always run: the honesty of the health check --------------------------


def test_required_weights_include_the_feature_models() -> None:
    """Audio features come from shared models, not from the wake model."""
    names = required_weights("hey_jarvis")
    assert "hey_jarvis_v0.1.onnx" in names
    for shared in ("melspectrogram.onnx", "embedding_model.onnx"):
        assert shared in names, f"{shared} is needed to compute features"


def test_missing_weights_are_not_reported_as_ready(monkeypatch, tmp_path) -> None:
    """An empty model directory must not read as ready.

    This is the observed bug: a fresh ``pip install openwakeword`` has no
    ``resources/models`` at all and its ``Model`` constructor never fetches one,
    so the detector could only raise ``NoSuchFile`` -- while the old
    ``available()`` returned True on the strength of the import alone.
    """
    monkeypatch.setattr(wake, "weights_dir", lambda: tmp_path)

    detector = WakeWordDetector(load_settings())
    assert detector.available() is True, "the package is installed, so offer the feature"
    assert detector.weights_present is False
    assert detector.ready is False
    assert detector.missing_weights()  # and it can name what is absent

    described = detector.describe()
    assert described["ready"] is False
    # The error must exist before any load was attempted, and must say how to fix it.
    assert described["error"], "an unready detector must explain itself up front"
    assert "download_models" in described["error"]


def test_present_weights_are_reported_as_ready(monkeypatch, tmp_path) -> None:
    """With every required file on disk, readiness is honest again."""
    for name in required_weights("hey_jarvis"):
        (tmp_path / name).write_bytes(b"")
    monkeypatch.setattr(wake, "weights_dir", lambda: tmp_path)

    detector = WakeWordDetector(load_settings())
    assert detector.weights_present is True
    assert detector.ready is True
    assert detector.missing_weights() == []
    assert detector.describe()["error"] is None


def test_disabled_wake_word_is_never_ready(settings: Settings) -> None:
    detector = WakeWordDetector(_disabled(settings))
    assert detector.available() is False
    assert detector.ready is False


def test_feeding_a_disabled_detector_is_a_no_op(settings: Settings) -> None:
    detector = WakeWordDetector(_disabled(settings))
    assert detector.feed(b"\x00\x00" * wake.FRAME_SAMPLES) is None


def test_frame_constants_match_the_model() -> None:
    """1280 samples at 16 kHz is what openWakeWord's model expects; do not drift."""
    assert wake.RATE == 16000
    assert wake.FRAME_SAMPLES == 1280
    assert wake.FRAME_BYTES == wake.FRAME_SAMPLES * 2


# --- opt-in: the detector actually fires ----------------------------------


@pytest.mark.voice
@pytest.mark.skipif(platform.system() != "Windows", reason="the sample comes from SAPI")
def test_the_wake_word_fires_on_synthesised_speech(settings: Settings) -> None:
    """Say "Hey Jarvis" with the Windows voice and expect a detection.

    A microphone is the one input no test can provide, so the voice stands in
    for it: the same 16 kHz mono int16 frames the orb sends over ``/ws/wake``.
    """
    from apps.gateway.tts import SapiEngine

    speaker = SapiEngine(settings)
    if not speaker.available():
        pytest.skip("no Windows speech engine to generate the sample")

    detector = WakeWordDetector(settings)
    if not detector.available():
        pytest.skip("openwakeword is not installed")
    if not detector.weights_present:
        pytest.skip("wake weights are not cached; the detector fetches them on first use")

    pcm16 = _sixteen_k(speaker.synthesize(WAKE_PHRASE).data)

    # Exercise the production path: a per-connection session, exactly what a
    # websocket connection receives from open_session().
    session = detector.open_session()
    hits = []
    for offset in range(0, len(pcm16) - wake.FRAME_BYTES + 1, wake.FRAME_BYTES):
        detection = session.feed(pcm16[offset : offset + wake.FRAME_BYTES])
        if detection is not None:
            hits.append(detection)

    assert detector.loaded, f"detector never loaded: {detector.load_error}"
    assert hits, "no wake-word detection for clear synthesised speech"
    assert hits[0].model
    assert hits[0].score >= settings.wake_threshold


@pytest.mark.voice
def test_silence_never_trips_the_detector(settings: Settings) -> None:
    """Silence must not fire: a false wake would open the microphone unasked."""
    detector = WakeWordDetector(settings)
    if not detector.available() or not detector.weights_present:
        pytest.skip("wake weights are not cached; the detector fetches them on first use")

    silence = b"\x00\x00" * wake.FRAME_SAMPLES
    for _ in range(40):
        assert detector.feed(silence) is None
    assert detector.load_error is None
