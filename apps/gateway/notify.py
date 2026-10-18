"""Notification delivery channels for J.A.R.V.I.S.

Pushes alerts, reminders, and proactive notices to the user through
multiple channels:

* **Windows Toast** — native OS notifications using ``plyer`` or fallback PowerShell.
* **EventBus** — internal broadcast for the Orb UI.
* **Console** — logged output for headless / CLI operation.

Channels are tried in priority order; failures gracefully fall through.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("jarvis.notify")


@dataclass
class Notification:
    title: str
    message: str
    urgency: str = "normal"  # "low" | "normal" | "high" | "critical"
    channel: str = ""  # which channel delivered it
    timestamp: float = field(default_factory=time.time)


class NotificationManager:
    """Multi-channel notification dispatcher."""

    def __init__(self, bus: Any = None, speak_fn: Any = None) -> None:
        self._bus = bus
        self._speak_fn = speak_fn
        self._history: list[Notification] = []
        self._max_history = 200
        self._toast_available: bool | None = None

    @property
    def history(self) -> list[Notification]:
        """Return copy of recent notification history."""
        return list(self._history)

    async def send(
        self, title: str, message: str, urgency: str = "normal",
        speak: bool = False,
    ) -> Notification:
        """Deliver a notification through all available channels."""
        notif = Notification(title=title, message=message, urgency=urgency)

        # 1. Windows Toast (native OS notification)
        if await self._try_toast(title, message, urgency):
            notif.channel = "toast"
        # 2. EventBus (Orb UI gets it)
        elif self._try_bus(title, message, urgency):
            notif.channel = "bus"
        # 3. Console fallback
        else:
            notif.channel = "console"
            logger.info("NOTIFICATION [%s]: %s — %s", urgency, title, message)

        # Optionally speak the notification
        if speak and self._speak_fn:
            try:
                await self._speak_fn(message)
            except Exception:
                pass

        self._history.append(notif)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        return notif

    async def _try_toast(self, title: str, message: str, urgency: str) -> bool:
        """Try Windows toast notification via plyer, then PowerShell fallback."""
        if sys.platform != "win32":
            return False

        # 1. Native Windows WinRT Toast via PowerShell (Modern Windows 10/11 Action Center)
        try:
            safe_title = title.replace("'", "''").replace('"', '`"')
            safe_msg = message.replace("'", "''").replace('"', '`"')
            ps_script = (
                "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null; "
                "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime] | Out-Null; "
                f"$xml = New-Object Windows.Data.Xml.Dom.XmlDocument; "
                f"$xml.LoadXml('<toast><visual><binding template=\"ToastText02\"><text id=\"1\">{safe_title}</text><text id=\"2\">{safe_msg}</text></binding></visual></toast>'); "
                "$toast = [Windows.UI.Notifications.ToastNotification]::new($xml); "
                "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('J.A.R.V.I.S.').Show($toast)"
            )
            proc = await asyncio.create_subprocess_exec(
                "powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=4)
            if proc.returncode == 0:
                return True
        except Exception:
            pass

        # 2. Secondary fallback via plyer if available
        if self._toast_available is not False:
            try:
                from plyer import notification as plyer_notif
                plyer_notif.notify(
                    title=title,
                    message=message,
                    app_name="J.A.R.V.I.S.",
                    timeout=10 if urgency != "critical" else 30,
                )
                self._toast_available = True
                return True
            except Exception:
                self._toast_available = False

        return False

    def _try_bus(self, title: str, message: str, urgency: str) -> bool:
        """Push to the internal EventBus for Orb UI consumption."""
        if not self._bus:
            return False
        try:
            self._bus.publish({
                "type": "notification",
                "title": title,
                "message": message,
                "urgency": urgency,
                "timestamp": time.time(),
            })
            return True
        except Exception:
            return False

    def get_history(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent notification history."""
        recent = self._history[-limit:]
        return [
            {
                "title": n.title,
                "message": n.message,
                "urgency": n.urgency,
                "channel": n.channel,
                "timestamp": n.timestamp,
            }
            for n in reversed(recent)
        ]

    def get_telemetry(self) -> dict[str, Any]:
        return {
            "total_sent": len(self._history),
            "toast_available": self._toast_available,
            "channels": ["toast", "bus", "console"],
        }
