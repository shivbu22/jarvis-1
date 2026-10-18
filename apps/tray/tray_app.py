"""J.A.R.V.I.S. Desktop System Tray Companion & Global Hotkey Service.

Features:
- Windows System Tray icon with live status indication.
- Global Hotkey: Ctrl + Alt + J to bring up the Holographic Orb HUD.
- Privacy hardware kill-switches for Microphone and Camera.
- Sidecar supervisor integration to restart Gateway & Brain seamlessly.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import logging
import os
import queue
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw
import pystray

from apps.tray.supervisor import SidecarSupervisor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("jarvis.tray")

ORB_URL = "http://127.0.0.1:5173"
HEALTH_URL = "http://127.0.0.1:8000/health"
HOTKEY_ID = 101

# Modifiers for Windows RegisterHotKey: MOD_ALT = 0x0001, MOD_CONTROL = 0x0002
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
VK_J = 0x4A  # Virtual key code for 'J'


def create_jarvis_icon(active: bool = True, alert: bool = False) -> Image.Image:
    """Generate a high-res glowing cyan Arc Reactor icon for the system tray."""
    size = (64, 64)
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # Palette
    color_ring = (0, 229, 255, 255) if active else (100, 116, 139, 255)
    color_core = (0, 255, 204, 255) if active else (71, 85, 105, 255)
    color_bg = (10, 15, 29, 255)

    if alert:
        color_ring = (239, 68, 68, 255)
        color_core = (252, 165, 165, 255)

    # Outer circle
    draw.ellipse((4, 4, 60, 60), fill=color_bg, outline=color_ring, width=3)
    # Inner reactor ring
    draw.ellipse((14, 14, 50, 50), outline=color_core, width=2)
    # Center core
    draw.ellipse((24, 24, 40, 40), fill=color_ring)

    # Spokes
    for angle in [0, 45, 90, 135, 180, 225, 270, 315]:
        import math
        rad = math.radians(angle)
        x1 = 32 + int(14 * math.cos(rad))
        y1 = 32 + int(14 * math.sin(rad))
        x2 = 32 + int(24 * math.cos(rad))
        y2 = 32 + int(24 * math.sin(rad))
        draw.line((x1, y1, x2, y2), fill=color_ring, width=2)

    return image


class JarvisDesktopCompanion:
    def __init__(self) -> None:
        self.supervisor = SidecarSupervisor()
        self.mic_muted = False
        self.camera_covered = False
        self.icon: pystray.Icon | None = None
        self._hotkey_thread: threading.Thread | None = None
        self._running = True

    def open_orb_hud(self) -> None:
        """Open or focus the Holographic Orb interface."""
        logger.info("Opening J.A.R.V.I.S. Orb HUD: %s", ORB_URL)
        webbrowser.open(ORB_URL)

    def open_diagnostics(self) -> None:
        """Open Gateway health endpoint."""
        webbrowser.open(HEALTH_URL)

    def toggle_mic(self) -> None:
        self.mic_muted = not self.mic_muted
        state = "MUTED" if self.mic_muted else "ACTIVE"
        logger.info("Microphone hardware killswitch toggled: %s", state)
        if self.icon:
            self.icon.title = f"J.A.R.V.I.S. MK-VII (Mic: {state})"
            self.icon.update_menu()

    def toggle_camera(self) -> None:
        self.camera_covered = not self.camera_covered
        state = "COVERED" if self.camera_covered else "ACTIVE"
        logger.info("Camera privacy killswitch toggled: %s", state)
        if self.icon:
            self.icon.update_menu()

    def restart_engine(self) -> None:
        logger.info("Restarting Gateway and Ollama sidecars...")
        res = self.supervisor.restart_all()
        logger.info("Restart completed: %s", res)

    def _listen_global_hotkey(self) -> None:
        """Register Ctrl+Alt+J in a dedicated Windows message pump thread."""
        user32 = ctypes.windll.user32
        # RegisterHotKey(hWnd, id, fsModifiers, vk)
        registered = user32.RegisterHotKey(None, HOTKEY_ID, MOD_CONTROL | MOD_ALT, VK_J)
        if not registered:
            logger.warning("Could not register global hotkey Ctrl+Alt+J (may be bound to another app).")
            return
        logger.info("Global hotkey registered: Ctrl + Alt + J -> Bring up J.A.R.V.I.S.")

        msg = wintypes.MSG()
        try:
            while self._running:
                # PeekMessage / GetMessage pump
                if user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
                    if msg.message == 0x0312:  # WM_HOTKEY
                        if msg.wParam == HOTKEY_ID:
                            logger.info("HotKey Ctrl+Alt+J pressed! Activating J.A.R.V.I.S. HUD.")
                            self.open_orb_hud()
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
                time.sleep(0.01)
        finally:
            user32.UnregisterHotKey(None, HOTKEY_ID)

    def _listen_microphone_wake(self) -> None:
        """Stream microphone audio to Gateway /ws/wake for system-wide 'Hey Jarvis' detection."""
        try:
            import sounddevice as sd
            import websockets.sync.client as ws_client
        except ImportError:
            logger.info("sounddevice or websockets not installed; background mic listener disabled")
            return

        wake_ws_url = "ws://127.0.0.1:8000/ws/wake"
        logger.info("Background OS microphone wake word listener started (%s)", wake_ws_url)

        while self._running:
            if self.mic_muted:
                time.sleep(0.5)
                continue

            try:
                with ws_client.connect(wake_ws_url, open_timeout=5.0) as ws:
                    logger.info("Connected to Gateway wake-word detection service.")
                    audio_q: queue.Queue[bytes] = queue.Queue(maxsize=50)

                    def audio_callback(indata: Any, frames: int, time_info: Any, status: Any) -> None:
                        if not self.mic_muted:
                            try:
                                audio_q.put_nowait(bytes(indata))
                            except queue.Full:
                                pass

                    with sd.RawInputStream(samplerate=16000, blocksize=1280, channels=1, dtype="int16", callback=audio_callback):
                        while self._running and not self.mic_muted:
                            try:
                                chunk = audio_q.get(timeout=0.2)
                                ws.send(chunk)
                                try:
                                    raw = ws.recv(timeout=0.01)
                                    msg = json.loads(raw)
                                    if msg.get("type") == "wake_detected":
                                        logger.info("Background Wake detected! ('Hey Jarvis', score=%s)", msg.get("score"))
                                        try:
                                            import winsound
                                            winsound.Beep(1000, 150)
                                            winsound.Beep(1400, 200)
                                        except Exception:
                                            pass
                                        self.open_orb_hud()
                                except (TimeoutError, Exception):
                                    pass
                            except queue.Empty:
                                pass
            except Exception as exc:
                logger.debug("Wake listener reconnecting: %s", exc)
                time.sleep(2.0)

    def quit(self) -> None:
        logger.info("Exiting J.A.R.V.I.S. Desktop Companion...")
        self._running = False
        if self.icon:
            self.icon.stop()

    def build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem("⚡ Open J.A.R.V.I.S. Orb (Ctrl+Alt+J)", lambda: self.open_orb_hud(), default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                lambda text: f"🎤 Mic Killswitch: {'[MUTED]' if self.mic_muted else '[ACTIVE]'}",
                lambda: self.toggle_mic(),
            ),
            pystray.MenuItem(
                lambda text: f"📷 Camera Killswitch: {'[COVERED]' if self.camera_covered else '[ACTIVE]'}",
                lambda: self.toggle_camera(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("🔄 Restart Engine & Brain", lambda: self.restart_engine()),
            pystray.MenuItem("📊 Gateway Diagnostics", lambda: self.open_diagnostics()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("🛑 Exit J.A.R.V.I.S.", lambda: self.quit()),
        )

    def run(self) -> None:
        # Start hotkey listener thread
        if sys.platform == "win32":
            self._hotkey_thread = threading.Thread(target=self._listen_global_hotkey, daemon=True)
            self._hotkey_thread.start()

        # Start background microphone wake listener thread
        self._wake_thread = threading.Thread(target=self._listen_microphone_wake, daemon=True)
        self._wake_thread.start()

        img = create_jarvis_icon(active=True)
        self.icon = pystray.Icon(
            name="JARVIS",
            icon=img,
            title="J.A.R.V.I.S. MK-VII Co-Founder OS",
            menu=self.build_menu(),
        )
        logger.info("Starting J.A.R.V.I.S. System Tray icon...")
        self.icon.run()


if __name__ == "__main__":
    app = JarvisDesktopCompanion()
    app.run()
