"""Unit tests for media, entertainment, and quick action tools."""

from unittest.mock import MagicMock, patch
import pytest

from apps.gateway.tools import build_registry
from apps.gateway.tools.media import (
    youtube_play,
    music_play,
    media_control,
    volume_control,
    web_open,
    system_quick_action,
    SPECS,
    POPULAR_SERVICES,
)


@pytest.fixture
def dummy_ctx():
    ctx = MagicMock()
    ctx.policy.screen.return_value = "auto"
    return ctx


from apps.gateway.policy import DEFAULT_POLICY, Policy

def test_media_specs_in_registry(dummy_ctx):
    policy = Policy(DEFAULT_POLICY)
    registry = build_registry(policy, dummy_ctx)
    names = registry.names()
    expected = [
        "youtube_play",
        "music_play",
        "media_control",
        "volume_control",
        "web_open",
        "system_quick_action",
    ]
    for name in expected:
        assert name in names, f"Expected {name} to be registered in ToolRegistry"


@patch("webbrowser.open")
def test_youtube_play_url(mock_open, dummy_ctx):
    output = youtube_play(dummy_ctx, "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert output.ok
    assert "youtube.com" in output.data["url"]
    mock_open.assert_called_once()


@patch("webbrowser.open")
def test_youtube_play_query(mock_open, dummy_ctx):
    output = youtube_play(dummy_ctx, "lofi hip hop", direct_video=False)
    assert output.ok
    assert "lofi+hip+hop" in output.data["url"]
    mock_open.assert_called_once()


@patch("apps.gateway.tools.media._send_key")
def test_music_play_resume(mock_key, dummy_ctx):
    output = music_play(dummy_ctx, "")
    assert output.ok
    assert "Resumed" in output.text
    mock_key.assert_called_once()


@patch("webbrowser.open")
def test_music_play_youtube_music(mock_open, dummy_ctx):
    output = music_play(dummy_ctx, "Queen", service="youtube_music")
    assert output.ok
    assert "music.youtube.com" in output.data["url"]
    mock_open.assert_called_once()


@patch("apps.gateway.tools.media._send_key")
def test_media_controls(mock_key, dummy_ctx):
    actions = ["play_pause", "next", "previous", "stop", "mute", "volume_up", "volume_down"]
    for act in actions:
        mock_key.reset_mock()
        output = media_control(dummy_ctx, act)
        assert output.ok, f"Action {act} failed"
        assert mock_key.called


@patch("apps.gateway.tools.media._send_key")
def test_volume_control(mock_key, dummy_ctx):
    out_mute = volume_control(dummy_ctx, action="mute")
    assert out_mute.ok
    assert mock_key.called

    mock_key.reset_mock()
    out_up = volume_control(dummy_ctx, action="up", delta=10)
    assert out_up.ok
    assert mock_key.called

    mock_key.reset_mock()
    out_set = volume_control(dummy_ctx, action="set", level=60)
    assert out_set.ok
    assert out_set.data["level"] == 60


@patch("webbrowser.open")
def test_web_open_shortcuts(mock_open, dummy_ctx):
    for service in ["youtube", "netflix", "github", "reddit", "spotify"]:
        mock_open.reset_mock()
        out = web_open(dummy_ctx, service)
        assert out.ok
        assert out.data["url"] == POPULAR_SERVICES[service]
        mock_open.assert_called_once_with(POPULAR_SERVICES[service])


@patch("webbrowser.open")
def test_web_open_custom_url(mock_open, dummy_ctx):
    out = web_open(dummy_ctx, "news.ycombinator.com")
    assert out.ok
    assert out.data["url"] == "https://news.ycombinator.com"
    mock_open.assert_called_once_with("https://news.ycombinator.com")


def test_system_quick_action_unknown(dummy_ctx):
    out = system_quick_action(dummy_ctx, "fly_to_moon")
    assert not out.ok
    assert "Unknown system quick action" in out.text
