"""Unit tests for Desktop and Browser Automation and Security Hardening."""

from unittest.mock import MagicMock, patch
import pytest
from pathlib import Path

from apps.gateway.policy import Policy, DEFAULT_POLICY
from apps.gateway.tools import build_registry
from apps.gateway.tools.desktop import (
    desktop_hotkey,
    desktop_type_text,
    desktop_mouse_action,
    desktop_window_control,
    desktop_screenshot,
)
from apps.gateway.tools.browser import (
    browser_scroll,
    browser_press_key,
    browser_navigate,
    browser_eval_js,
    browser_get_elements,
)
from apps.gateway.tools.ultimate import (
    ultimate_exec,
    ultimate_system_control,
)


@pytest.fixture
def mock_ctx(tmp_path):
    ctx = MagicMock()
    ctx.settings.data_dir = str(tmp_path)
    ctx.policy = Policy(DEFAULT_POLICY)
    ctx.state = {}
    return ctx


# --------------------------------------------------------------------------
# Security Vulnerability Tests
# --------------------------------------------------------------------------


def test_ultimate_exec_blocks_dangerous_patterns(mock_ctx):
    out = ultimate_exec(mock_ctx, "rm -rf /")
    assert not out.ok
    assert "Refused by safety backstop" in out.text

    out_format = ultimate_exec(mock_ctx, "format c: /fs:ntfs")
    assert not out_format.ok
    assert "Refused by safety backstop" in out_format.text


def test_ultimate_system_control_sanitization(mock_ctx):
    # Malicious injection attempt in service name
    with patch("subprocess.run") as mock_sub:
        mock_sub.return_value = MagicMock(returncode=0, stdout="service running")
        out = ultimate_system_control(mock_ctx, action="services_list", target="test'; Drop-Table #")
        assert out.ok
        # Ensure single quotes or injection symbols were sanitized out
        called_cmd = mock_sub.call_args[0][0][3]
        assert "Drop-Table" not in called_cmd or "';" not in called_cmd
        assert "'; " not in called_cmd


# --------------------------------------------------------------------------
# Desktop Automation Tests
# --------------------------------------------------------------------------


def test_desktop_hotkey_dispatch(mock_ctx):
    with patch("apps.gateway.tools.desktop.ctypes.windll.user32.keybd_event") as mock_kb:
        out = desktop_hotkey(mock_ctx, "ctrl+c")
        assert out.ok
        assert mock_kb.called
        assert "ctrl+c" in out.text


def test_desktop_type_text(mock_ctx):
    with patch("apps.gateway.tools.desktop._clipboard") as mock_clip, \
         patch("apps.gateway.tools.desktop.desktop_hotkey") as mock_hotkey:
        mock_hotkey.return_value = MagicMock(ok=True)
        out = desktop_type_text(mock_ctx, "Hello J.A.R.V.I.S.")
        assert out.ok
        mock_clip.assert_called_with(get=False, text="Hello J.A.R.V.I.S.")
        mock_hotkey.assert_called_with(mock_ctx, "ctrl+v")


def test_desktop_mouse_action(mock_ctx):
    with patch("apps.gateway.tools.desktop.ctypes.windll.user32.SetCursorPos") as mock_pos, \
         patch("apps.gateway.tools.desktop.ctypes.windll.user32.mouse_event") as mock_event:
        out = desktop_mouse_action(mock_ctx, action="click", x=200, y=300)
        assert out.ok
        mock_pos.assert_called_with(200, 300)
        assert mock_event.called


def test_desktop_window_control_list(mock_ctx):
    with patch("subprocess.run") as mock_sub:
        mock_sub.return_value = MagicMock(returncode=0, stdout="1234 Chrome ChromeBrowser")
        out = desktop_window_control(mock_ctx, action="list")
        assert out.ok
        assert "Active Desktop Windows" in out.text


def test_desktop_screenshot(mock_ctx, tmp_path):
    dest = tmp_path / "shot.png"
    with patch("subprocess.run") as mock_sub:
        mock_sub.return_value = MagicMock(returncode=0)
        # Create a mock file so stat succeeds
        dest.write_bytes(b"\x89PNG\r\n\x1a\nfake")
        out = desktop_screenshot(mock_ctx, path=str(dest))
        assert out.ok
        assert "saved" in out.text.lower()


# --------------------------------------------------------------------------
# Browser Automation Tests
# --------------------------------------------------------------------------


def test_browser_scroll(mock_ctx):
    with patch("apps.gateway.tools.browser._on_browser_thread") as mock_action:
        mock_action.return_value = {"url": "https://example.com", "direction": "down", "amount": 500}
        out = browser_scroll(mock_ctx, direction="down", amount=500)
        assert out.ok
        assert "Scrolled down" in out.text


def test_browser_press_key(mock_ctx):
    with patch("apps.gateway.tools.browser._on_browser_thread") as mock_action:
        mock_action.return_value = {"url": "https://example.com", "key": "Enter"}
        out = browser_press_key(mock_ctx, key="Enter")
        assert out.ok
        assert "Pressed 'Enter'" in out.text


def test_browser_navigate(mock_ctx):
    with patch("apps.gateway.tools.browser._on_browser_thread") as mock_action:
        mock_action.return_value = {"url": "https://example.com", "title": "Example", "action": "reload"}
        out = browser_navigate(mock_ctx, action_name="reload")
        assert out.ok
        assert "reload" in out.text


def test_browser_eval_js(mock_ctx):
    with patch("apps.gateway.tools.browser._on_browser_thread") as mock_action:
        mock_action.return_value = "Title: J.A.R.V.I.S."
        out = browser_eval_js(mock_ctx, script="document.title")
        assert out.ok
        assert "Title: J.A.R.V.I.S." in out.text


def test_browser_get_elements(mock_ctx):
    with patch("apps.gateway.tools.browser._on_browser_thread") as mock_action:
        mock_action.return_value = [
            {"tag": "button", "text": "Submit", "id": "submit-btn", "href": ""},
            {"tag": "a", "text": "Home", "id": "", "href": "/home"},
        ]
        out = browser_get_elements(mock_ctx)
        assert out.ok
        assert "Found 2 interactive elements" in out.text


# --------------------------------------------------------------------------
# Vision & Perception Tests (PRD Epic 4 / FR-6.1)
# --------------------------------------------------------------------------


def test_camera_capture_ram_only(mock_ctx):
    from apps.gateway.tools.vision import camera_capture
    import numpy as np

    fake_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    with patch("cv2.VideoCapture") as mock_vc:
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        mock_cap.read.return_value = (True, fake_frame)
        mock_vc.return_value = mock_cap

        out = camera_capture(mock_ctx, save=False)
        assert out.ok
        assert "RAM" in out.text
        assert mock_cap.release.called


def test_camera_describe(mock_ctx):
    from apps.gateway.tools.vision import camera_describe
    with patch("apps.gateway.tools.vision.camera_capture") as mock_cap:
        mock_cap.return_value = MagicMock(ok=True, data={"width": 640, "height": 480})
        out = camera_describe(mock_ctx)
        assert out.ok
        assert "Camera visual captured" in out.text


def test_screen_describe(mock_ctx):
    from apps.gateway.tools.vision import screen_describe
    with patch("apps.gateway.tools.vision.desktop_screenshot") as mock_shot:
        mock_shot.return_value = MagicMock(ok=True, data={"path": "C:/fake/shot.png"})
        out = screen_describe(mock_ctx)
        assert out.ok
        assert "Desktop screen captured" in out.text

