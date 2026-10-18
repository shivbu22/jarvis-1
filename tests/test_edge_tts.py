from __future__ import annotations

from apps.gateway.config import load_settings
from apps.gateway.tts import EdgeTTSEngine, build_tts


def test_edge_tts_engine_available() -> None:
    settings = load_settings()
    engine = EdgeTTSEngine(settings)
    assert engine.available() is True
    desc = engine.describe()
    assert desc["engine"] == "edge"
    assert desc["voice"] == "en-GB-RyanNeural"


def test_build_tts_prefers_edge_when_auto(monkeypatch) -> None:
    settings = load_settings()
    settings.tts_engine = "auto"
    engine = build_tts(settings)
    assert engine.name == "edge"


def test_build_tts_explicit_engine_selection() -> None:
    settings = load_settings()
    settings.tts_engine = "sapi"
    assert build_tts(settings).name == "sapi"

    settings.tts_engine = "edge"
    assert build_tts(settings).name == "edge"

    settings.tts_engine = "chatterbox"
    assert build_tts(settings).name == "chatterbox"
