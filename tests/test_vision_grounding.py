"""Tests for Visual GUI Grounding tools (screen_locate_element and screen_click_element)."""

from unittest.mock import MagicMock, patch
from pathlib import Path
import pytest

from apps.gateway.tools.registry import ToolContext, ToolOutput
from apps.gateway.tools.vision import screen_locate_element, screen_click_element, build_specs


@pytest.fixture
def mock_ctx():
    settings = MagicMock()
    settings.data_dir = "data"
    memory = MagicMock()
    policy = MagicMock()
    audit = MagicMock()
    bus = MagicMock()
    return ToolContext(settings=settings, memory=memory, policy=policy, audit=audit, bus=bus)


def test_specs_include_gui_grounding():
    specs = build_specs()
    names = {s.name for s in specs}
    assert "screen_locate_element" in names
    assert "screen_click_element" in names
    assert "camera_capture" in names
    assert "screen_describe" in names


def test_screen_locate_element_validation(mock_ctx):
    res = screen_locate_element(mock_ctx, element="")
    assert not res.ok
    assert "specify which element" in res.text


def test_screen_locate_element_preset_fallback(mock_ctx):
    with patch("apps.gateway.tools.vision.desktop_screenshot") as mock_ss:
        mock_ss.return_value = ToolOutput.from_error("fail")
        res = screen_locate_element(mock_ctx, element="start menu")
        assert res.ok
        assert res.data["found"] is True
        assert res.data["x"] == 30


def test_screen_click_element_with_coords(mock_ctx):
    with patch("pyautogui.moveTo") as mock_move, patch("pyautogui.click") as mock_click:
        res = screen_click_element(mock_ctx, element="Submit", x=500, y=300)
        assert res.ok
        assert res.data["x"] == 500
        assert res.data["y"] == 300
        mock_move.assert_called_once()
        mock_click.assert_called_once()


def test_screen_click_element_locate_and_click(mock_ctx):
    with patch("apps.gateway.tools.vision.screen_locate_element") as mock_loc, \
         patch("pyautogui.moveTo"), \
         patch("pyautogui.doubleClick"):
        mock_loc.return_value = ToolOutput.from_ok(
            {"found": True, "x": 640, "y": 480, "element": "Chrome"},
            text="Located Chrome",
        )
        res = screen_click_element(mock_ctx, element="Chrome", double_click=True)
        assert res.ok
        assert res.data["action"] == "double click"
        assert res.data["x"] == 640
        assert res.data["y"] == 480
