"""Verification test for enhanced desktop, fallback, and perception capabilities."""

from unittest.mock import MagicMock, patch
import pytest

from apps.gateway.policy import Policy, DEFAULT_POLICY
from apps.gateway.tools.desktop import app_launch, desktop_window_control


@pytest.fixture
def mock_ctx(tmp_path):
    ctx = MagicMock()
    ctx.settings.data_dir = str(tmp_path)
    ctx.policy = Policy(DEFAULT_POLICY)
    ctx.state = {}
    return ctx


@patch("webbrowser.open")
def test_app_launch_web_fallback(mock_open, mock_ctx):
    with patch("os.startfile", side_effect=OSError("Not installed")):
        out = app_launch(mock_ctx, "whatsapp")
        assert out.ok
        assert "browser" in out.text.lower() or "opened" in out.text.lower()
        mock_open.assert_called_once()
        assert "whatsapp" in mock_open.call_args[0][0].lower()


@patch("webbrowser.open")
def test_app_launch_unknown_service_search_fallback(mock_open, mock_ctx):
    out = app_launch(mock_ctx, "super_obscure_app_xyz")
    assert out.ok
    assert "browser" in out.text.lower()
    mock_open.assert_called_once()
    assert "super_obscure_app_xyz" in mock_open.call_args[0][0]


def test_desktop_window_control_active(mock_ctx):
    out = desktop_window_control(mock_ctx, action="active")
    assert out.ok
    assert "window" in out.text.lower()
