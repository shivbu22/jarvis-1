"""Media and Quick Action Tools for JARVIS.

Provides fast, keyless multimedia controls and quick system actions:
- `youtube_play`: Search and play videos or music on YouTube directly in browser
- `music_play`: Play songs/artists on Spotify, YouTube Music, or resume playback
- `media_control`: Hardware media controls (play/pause, next, previous, stop, mute, volume)
- `volume_control`: Adjust or set system volume levels
- `web_open`: Launch popular web apps and services (YouTube, Netflix, Spotify, GitHub, ChatGPT, etc.)
- `system_quick_action`: Quick OS actions (lock workstation, task manager, settings, explorer)
"""

from __future__ import annotations

import ctypes
import logging
import os
import platform
import re
import time
import urllib.parse
import webbrowser
from typing import Any

import httpx

from .registry import ToolContext, ToolOutput, ToolSpec, boolean_param, object_schema, param

logger = logging.getLogger(__name__)

# Virtual-Key codes for Windows Multimedia and Volume
VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_STOP = 0xB2
VK_MEDIA_PLAY_PAUSE = 0xB3

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002

POPULAR_SERVICES: dict[str, str] = {
    "youtube": "https://www.youtube.com",
    "yt": "https://www.youtube.com",
    "music": "https://music.youtube.com",
    "ytmusic": "https://music.youtube.com",
    "youtube music": "https://music.youtube.com",
    "spotify": "https://open.spotify.com",
    "netflix": "https://www.netflix.com",
    "google": "https://www.google.com",
    "github": "https://github.com",
    "reddit": "https://www.reddit.com",
    "twitter": "https://x.com",
    "x": "https://x.com",
    "chatgpt": "https://chatgpt.com",
    "claude": "https://claude.ai",
    "gemini": "https://gemini.google.com",
    "gmail": "https://mail.google.com",
    "mail": "https://mail.google.com",
    "maps": "https://maps.google.com",
    "google maps": "https://maps.google.com",
    "whatsapp": "https://web.whatsapp.com",
    "amazon": "https://www.amazon.com",
    "wikipedia": "https://www.wikipedia.org",
    "discord": "https://discord.com/app",
    "notion": "https://www.notion.so",
    "linkedin": "https://www.linkedin.com",
    "instagram": "https://www.instagram.com",
    "twitch": "https://www.twitch.tv",
    "prime": "https://www.primevideo.com",
    "prime video": "https://www.primevideo.com",
    "disney": "https://www.disneyplus.com",
    "hotstar": "https://www.hotstar.com",
}


def _send_key(vk_code: int) -> None:
    """Send a single press and release of a virtual key on Windows."""
    if platform.system() != "Windows":
        return
    try:
        user32 = ctypes.windll.user32
        user32.keybd_event(vk_code, 0, KEYEVENTF_EXTENDEDKEY, 0)
        user32.keybd_event(vk_code, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)
    except Exception as exc:
        logger.warning("Could not send virtual key 0x%X: %s", vk_code, exc)


def _find_browser_executable() -> str | None:
    """Find installed browser executable on Windows (Brave, Chrome, Edge, Firefox)."""
    import shutil
    candidates = [
        os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe"),
        os.path.expandvars(r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%PROGRAMFILES(X86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%PROGRAMFILES%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%PROGRAMFILES%\Mozilla Firefox\firefox.exe"),
        shutil.which("brave"),
        shutil.which("chrome"),
        shutil.which("msedge"),
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def _open_url(url: str) -> bool:
    """Robust URL opener prioritizing direct browser executable launch, then shell handler."""
    import subprocess
    browser_exe = _find_browser_executable()
    if browser_exe:
        try:
            subprocess.Popen([browser_exe, url])
            logger.info("Launched URL %s directly in browser %s", url, browser_exe)
            return True
        except Exception as exc:
            logger.warning("Direct browser launch failed: %s", exc)

    if platform.system() == "Windows" and hasattr(os, "startfile"):
        try:
            os.startfile(url)
            return True
        except Exception as exc:
            logger.debug("os.startfile failed: %s", exc)

    try:
        return webbrowser.open(url)
    except Exception as exc:
        logger.error("webbrowser.open failed: %s", exc)
        return False


# --------------------------------------------------------------------------
# 1. YouTube Play / Search
# --------------------------------------------------------------------------


def youtube_play(ctx: ToolContext, query: str, direct_video: bool = True) -> ToolOutput:
    """Search and play a video on YouTube, optionally resolving the top video ID."""
    clean_query = (query or "").strip()
    if not clean_query:
        _open_url("https://www.youtube.com")
        return ToolOutput(text="Opened YouTube in your browser.", data={"url": "https://www.youtube.com"})

    # Check if query is already a YouTube URL
    if "youtube.com" in clean_query or "youtu.be" in clean_query:
        url = clean_query if clean_query.startswith("http") else f"https://{clean_query}"
        _open_url(url)
        return ToolOutput(text=f"Opened YouTube video in your browser.", data={"url": url})

    target_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(clean_query)}"

    # Try resolving top matching video ID directly
    if direct_video:
        try:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                )
            }
            with httpx.Client(timeout=3.0, follow_redirects=True) as client:
                resp = client.get(target_url, headers=headers)
                if resp.status_code == 200:
                    matches = re.findall(r'/watch\?v=([a-zA-Z0-9_-]{11})', resp.text)
                    if matches:
                        video_id = matches[0]
                        target_url = f"https://www.youtube.com/watch?v={video_id}&autoplay=1"
        except Exception as exc:
            logger.debug("Direct YouTube video scrape skipped: %s", exc)

    _open_url(target_url)
    return ToolOutput(
        text=f"Playing '{clean_query}' on YouTube.",
        data={"query": clean_query, "url": target_url},
    )


# --------------------------------------------------------------------------
# 2. Music Play
# --------------------------------------------------------------------------


def music_play(ctx: ToolContext, query: str = "", service: str = "auto") -> ToolOutput:
    """Play music on Spotify, YouTube Music, or toggle system media."""
    clean_query = (query or "").strip()
    svc = (service or "auto").lower().strip()

    # No specific query: resume or toggle whatever music was playing
    if not clean_query:
        _send_key(VK_MEDIA_PLAY_PAUSE)
        return ToolOutput(text="Resumed music playback.")

    encoded = urllib.parse.quote(clean_query)

    if svc in ("spotify", "auto"):
        # Try native Spotify URI first if on Windows
        opened_native = False
        if platform.system() == "Windows":
            try:
                os.startfile(f"spotify:search:{encoded}")
                opened_native = True
            except OSError:
                opened_native = False

        if opened_native:
            return ToolOutput(
                text=f"Searching and playing '{clean_query}' on Spotify.",
                data={"query": clean_query, "service": "spotify"},
            )

        if svc == "spotify":
            url = f"https://open.spotify.com/search/{encoded}"
            webbrowser.open(url)
            return ToolOutput(
                text=f"Opened Spotify for '{clean_query}'.",
                data={"query": clean_query, "url": url},
            )

    if svc in ("youtube_music", "ytmusic"):
        url = f"https://music.youtube.com/search?q={encoded}"
        webbrowser.open(url)
        return ToolOutput(
            text=f"Opened YouTube Music for '{clean_query}'.",
            data={"query": clean_query, "url": url},
        )

    # Fall back to YouTube video play
    return youtube_play(ctx, query=clean_query, direct_video=True)


# --------------------------------------------------------------------------
# 3. Media Controls
# --------------------------------------------------------------------------


def media_control(ctx: ToolContext, action: str) -> ToolOutput:
    """Control media playback: play, pause, next, previous, stop, mute, volume."""
    act = (action or "play_pause").lower().replace("-", "_").strip()

    if act in ("play_pause", "toggle", "play", "pause", "resume"):
        _send_key(VK_MEDIA_PLAY_PAUSE)
        return ToolOutput(text="Toggled media play/pause.")
    elif act in ("next", "next_track", "skip"):
        _send_key(VK_MEDIA_NEXT_TRACK)
        return ToolOutput(text="Skipped to next track.")
    elif act in ("previous", "prev", "prev_track", "back"):
        _send_key(VK_MEDIA_PREV_TRACK)
        return ToolOutput(text="Rewound to previous track.")
    elif act in ("stop",):
        _send_key(VK_MEDIA_STOP)
        return ToolOutput(text="Stopped media playback.")
    elif act in ("mute", "unmute", "toggle_mute"):
        _send_key(VK_VOLUME_MUTE)
        return ToolOutput(text="Toggled system mute.")
    elif act in ("volume_up", "louder", "up"):
        for _ in range(5):
            _send_key(VK_VOLUME_UP)
        return ToolOutput(text="Volume turned up.")
    elif act in ("volume_down", "quieter", "down", "lower"):
        for _ in range(5):
            _send_key(VK_VOLUME_DOWN)
        return ToolOutput(text="Volume turned down.")
    else:
        return ToolOutput(
            text=f"Unknown media action '{action}'. Options: play_pause, next, previous, stop, mute, volume_up, volume_down.",
            ok=False,
        )


# --------------------------------------------------------------------------
# 4. Volume Control
# --------------------------------------------------------------------------


def volume_control(
    ctx: ToolContext,
    action: str = "set",
    level: int = 50,
    delta: int = 0,
) -> ToolOutput:
    """Adjust system audio volume."""
    act = (action or "set").lower().strip()

    if act in ("mute", "silence"):
        _send_key(VK_VOLUME_MUTE)
        return ToolOutput(text="System audio muted.")

    if act in ("unmute",):
        # Sending volume up or down cancels mute without drastically altering volume
        _send_key(VK_VOLUME_UP)
        _send_key(VK_VOLUME_DOWN)
        return ToolOutput(text="System audio unmuted.")

    if act in ("up", "raise", "increase"):
        steps = max(1, (delta or 10) // 2)
        for _ in range(steps):
            _send_key(VK_VOLUME_UP)
        return ToolOutput(text=f"Raised volume by {steps * 2} percent.")

    if act in ("down", "lower", "decrease"):
        steps = max(1, (delta or 10) // 2)
        for _ in range(steps):
            _send_key(VK_VOLUME_DOWN)
        return ToolOutput(text=f"Lowered volume by {steps * 2} percent.")

    # Target volume level 0..100
    target_level = max(0, min(int(level), 100))
    # Reset volume to 0 by pressing volume down 50 times, then step up
    for _ in range(50):
        _send_key(VK_VOLUME_DOWN)
    steps_up = target_level // 2
    for _ in range(steps_up):
        _send_key(VK_VOLUME_UP)

    return ToolOutput(
        text=f"Set system volume to approximately {target_level} percent.",
        data={"level": target_level},
    )


# --------------------------------------------------------------------------
# 5. Open Web Service / Website
# --------------------------------------------------------------------------


def web_open(ctx: ToolContext, target: str) -> ToolOutput:
    """Open a popular website, web application, or custom URL in the default browser."""
    raw = (target or "").strip()
    if not raw:
        return ToolOutput(text="Please specify what website or service to open.", ok=False)

    key = raw.lower().strip()
    url = POPULAR_SERVICES.get(key)

    if not url:
        # Check if target is a known domain or URL
        if re.match(r"^https?://", raw, re.IGNORECASE):
            url = raw
        elif "." in raw and " " not in raw:
            url = f"https://{raw}"
        else:
            # Fall back to a Google or DuckDuckGo search
            url = f"https://www.google.com/search?q={urllib.parse.quote_plus(raw)}"

    _open_url(url)
    display_name = key.capitalize() if key in POPULAR_SERVICES else url
    return ToolOutput(
        text=f"Opened {display_name} in your browser.",
        data={"target": raw, "url": url},
    )


# --------------------------------------------------------------------------
# 6. Quick System Actions
# --------------------------------------------------------------------------


def system_quick_action(ctx: ToolContext, action: str) -> ToolOutput:
    """Perform quick Windows operating system actions."""
    act = (action or "").lower().replace("-", "_").strip()

    if act in ("lock", "lock_screen", "lock_pc", "lock_workstation"):
        if platform.system() == "Windows":
            try:
                ctypes.windll.user32.LockWorkStation()
                return ToolOutput(text="Workstation locked, sir.")
            except Exception as exc:
                return ToolOutput(text=f"Failed to lock workstation: {exc}", ok=False)
        return ToolOutput(text="Locking workstation is only supported on Windows.", ok=False)

    if act in ("task_manager", "taskmgr"):
        if platform.system() == "Windows":
            os.system("start taskmgr")
            return ToolOutput(text="Opened Task Manager.")

    if act in ("settings", "control_panel"):
        if platform.system() == "Windows":
            os.system("start ms-settings:")
            return ToolOutput(text="Opened Windows Settings.")

    if act in ("file_explorer", "explorer", "files"):
        if platform.system() == "Windows":
            os.system("start explorer")
            return ToolOutput(text="Opened File Explorer.")

    if act in ("terminal", "cmd", "powershell"):
        if platform.system() == "Windows":
            try:
                os.system("start wt")
            except Exception:
                os.system("start powershell")
            return ToolOutput(text="Opened Terminal.")

    return ToolOutput(text=f"Unknown system quick action: '{action}'.", ok=False)


def whatsapp_send(ctx: ToolContext, recipient: str, message: str, direct_send: bool = True) -> ToolOutput:
    """Send a WhatsApp message to a contact or phone number via WhatsApp Web/Desktop."""
    clean_msg = (message or "").strip()
    clean_target = (recipient or "").strip()

    if not clean_msg:
        return ToolOutput(text="Cannot send an empty WhatsApp message.", ok=False)

    digits = re.sub(r"[^\d+]", "", clean_target)
    if digits.startswith("0") and len(digits) == 11:
        digits = "44" + digits[1:]
    elif len(digits) == 10 and not digits.startswith("+"):
        digits = "91" + digits

    encoded_msg = urllib.parse.quote(clean_msg)
    if digits and len(digits) >= 10:
        url = f"https://api.whatsapp.com/send?phone={digits}&text={encoded_msg}"
    else:
        url = f"https://web.whatsapp.com/send?text={encoded_msg}"

    webbrowser.open(url)

    if direct_send and platform.system() == "Windows":
        time.sleep(1.8)
        _send_key(0x0D)  # VK_RETURN

    return ToolOutput(
        text=f"WhatsApp opened with message for '{recipient}': \"{clean_msg}\"",
        data={"recipient": recipient, "phone": digits, "message": clean_msg, "url": url},
        ok=True,
    )


# --------------------------------------------------------------------------
# Tool Specifications
# --------------------------------------------------------------------------

SPECS = [
    ToolSpec(
        name="youtube_play",
        description=(
            "Play a video or search YouTube in the default browser. Resolves and "
            "autoplays the top matching video directly (e.g. 'play bohemian rhapsody', "
            "'lofi hip hop', 'latest space news', or channel/topic)."
        ),
        parameters=object_schema(
            {
                "query": param("query", "string", "Song, video title, topic, or YouTube URL to play."),
                "direct_video": param(
                    "direct_video",
                    "boolean",
                    "Whether to resolve the top matching video and start playing immediately.",
                    default=True,
                ),
            },
            ["query"],
        ),
        handler=youtube_play,
        executing=False,
        category="media",
    ),
    ToolSpec(
        name="music_play",
        description=(
            "Play music or an artist on Spotify, YouTube Music, or resume active media playback. "
            "If query is empty, it resumes whatever song was playing."
        ),
        parameters=object_schema(
            {
                "query": param(
                    "query",
                    "string",
                    "Song name, artist, album, or genre to play (e.g. 'Queen', 'lofi beats', 'jazz').",
                    default="",
                ),
                "service": param(
                    "service",
                    "string",
                    "Music service to use: 'auto', 'spotify', 'youtube_music', 'youtube'.",
                    default="auto",
                ),
            },
            [],
        ),
        handler=music_play,
        executing=False,
        category="media",
    ),
    ToolSpec(
        name="media_control",
        description=(
            "Control media playback across system players (Spotify, YouTube, VLC, browser tabs): "
            "play_pause, next, previous, stop, mute, volume_up, volume_down."
        ),
        parameters=object_schema(
            {
                "action": param(
                    "action",
                    "string",
                    "The media action to perform.",
                    enum=[
                        "play_pause",
                        "next",
                        "previous",
                        "stop",
                        "mute",
                        "volume_up",
                        "volume_down",
                    ],
                ),
            },
            ["action"],
        ),
        handler=media_control,
        executing=False,
        category="media",
    ),
    ToolSpec(
        name="volume_control",
        description="Adjust or set system audio volume (mute, unmute, up, down, or set to 0..100%).",
        parameters=object_schema(
            {
                "action": param(
                    "action",
                    "string",
                    "Volume action: 'set', 'up', 'down', 'mute', 'unmute'.",
                    enum=["set", "up", "down", "mute", "unmute"],
                    default="set",
                ),
                "level": param(
                    "level",
                    "integer",
                    "Target volume percentage from 0 to 100 when action is 'set'.",
                    default=50,
                    minimum=0,
                    maximum=100,
                ),
                "delta": param(
                    "delta",
                    "integer",
                    "Percentage to increase or decrease when action is 'up' or 'down'.",
                    default=10,
                    minimum=1,
                    maximum=100,
                ),
            },
            [],
        ),
        handler=volume_control,
        executing=False,
        category="media",
    ),
    ToolSpec(
        name="web_open",
        description=(
            "Open any website or popular service in the default browser. Recognises names "
            "like 'youtube', 'netflix', 'spotify', 'github', 'reddit', 'twitter', 'chatgpt', "
            "'gmail', 'maps', 'whatsapp', 'amazon', 'wikipedia', or any standard URL."
        ),
        parameters=object_schema(
            {
                "target": param(
                    "target",
                    "string",
                    "Website shortcut name (e.g. 'netflix', 'github', 'youtube') or full URL.",
                ),
            },
            ["target"],
        ),
        handler=web_open,
        executing=False,
        category="media",
    ),
    ToolSpec(
        name="system_quick_action",
        description="Execute quick system actions: lock workstation, open task manager, settings, explorer, or terminal.",
        parameters=object_schema(
            {
                "action": param(
                    "action",
                    "string",
                    "System action to perform: 'lock', 'task_manager', 'settings', 'file_explorer', 'terminal'.",
                    enum=["lock", "task_manager", "settings", "file_explorer", "terminal"],
                ),
            },
            ["action"],
        ),
        handler=system_quick_action,
        executing=False,
        category="media",
    ),
    ToolSpec(
        name="whatsapp_send",
        description="Send a message to a WhatsApp contact or phone number. Opens WhatsApp Web or desktop with the pre-filled message.",
        parameters=object_schema(
            {
                "recipient": param(
                    "recipient",
                    "string",
                    "Contact name or international phone number (e.g. '+919876543210' or 'Rahul').",
                ),
                "message": param(
                    "message",
                    "string",
                    "The message text to send.",
                ),
                "direct_send": boolean_param(
                    "Whether to automatically trigger send (Enter) after opening.",
                    default=True,
                ),
            },
            ["recipient", "message"],
        ),
        handler=whatsapp_send,
        executing=True,
        category="communication",
    ),
]


def build_specs() -> list[ToolSpec]:
    """Return all ToolSpec instances provided by the media module."""
    return list(SPECS)

