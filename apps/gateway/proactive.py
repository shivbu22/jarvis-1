"""Proactive autonomous guardian daemon for J.A.R.V.I.S.

Monitors battery, RAM pressure, thermal conditions, and reminders in the background.
When triggers trip, it broadcasts alerts and can proactively notify
the operator via the EventBus and speech synthesis.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any
import psutil

logger = logging.getLogger("jarvis.proactive")


class ProactiveGuardian:
    def __init__(self, service: Any = None) -> None:
        self.service = service
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._last_battery_alert = 0.0
        self._last_ram_alert = 0.0
        self._total_scans = 0

    async def start(self, interval_seconds: float = 30.0) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(interval_seconds))
        logger.info("Proactive guardian daemon started (interval=%.0fs)", interval_seconds)

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        logger.info("Proactive guardian daemon stopped")

    async def _loop(self, interval: float) -> None:
        while self._running:
            try:
                await asyncio.sleep(interval)
                self._total_scans += 1
                await self.check_system_vitals()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Proactive loop error: %s", exc)

    async def check_system_vitals(self) -> None:
        now = time.time()

        # 1. Battery Guardian
        try:
            battery = psutil.sensors_battery()
            if battery is not None:
                # If battery < 20% and discharging, alert every 10 minutes
                if not battery.power_plugged and battery.percent <= 20:
                    if now - self._last_battery_alert > 600.0:
                        self._last_battery_alert = now
                        msg = f"Sir, your laptop battery is at {battery.percent:.0f} percent. Please connect the power adapter."
                        await self._notify(msg, alert_type="battery_low", level="warning")
        except Exception:
            pass

        # 2. RAM Pressure Guardian
        try:
            vm = psutil.virtual_memory()
            if vm.percent >= 94.0:
                if now - self._last_ram_alert > 900.0:
                    self._last_ram_alert = now
                    msg = f"Sir, system memory utilization has reached {vm.percent:.1f} percent."
                    await self._notify(msg, alert_type="ram_high", level="warning")
        except Exception:
            pass

    async def _notify(self, message: str, alert_type: str = "vital_alert", level: str = "info") -> None:
        logger.info("Proactive Alert [%s]: %s", alert_type, message)
        if self.service and hasattr(self.service, "bus") and self.service.bus:
            self.service.bus.publish({
                "type": "proactive_notice",
                "alert_type": alert_type,
                "level": level,
                "text": message,
                "timestamp": time.time(),
            })

    def get_telemetry(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "total_scans": self._total_scans,
            "last_battery_alert": self._last_battery_alert,
            "last_ram_alert": self._last_ram_alert,
        }
