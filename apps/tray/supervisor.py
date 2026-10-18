"""Sidecar supervisor for J.A.R.V.I.S. Gateway and Ollama brain processes."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger("jarvis.supervisor")

ROOT = Path(__file__).resolve().parent.parent.parent
GATEWAY_URL = "http://127.0.0.1:8000/health"
OLLAMA_URL = "http://127.0.0.1:11434/api/tags"


class SidecarSupervisor:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = root
        self.python_bin = sys.executable
        self.ollama_bin = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe")
        self.gateway_process: subprocess.Popen | None = None
        self.ollama_process: subprocess.Popen | None = None

    def is_gateway_alive(self) -> bool:
        try:
            with urllib.request.urlopen(GATEWAY_URL, timeout=3) as resp:
                return resp.status == 200
        except Exception:
            return False

    def is_ollama_alive(self) -> bool:
        try:
            with urllib.request.urlopen(OLLAMA_URL, timeout=3) as resp:
                return resp.status == 200
        except Exception:
            return False

    def ensure_ollama(self) -> bool:
        if self.is_ollama_alive():
            return True
        if not Path(self.ollama_bin).exists():
            logger.warning("Ollama binary not found at %s", self.ollama_bin)
            return False
        logger.info("Starting Ollama daemon in background...")
        try:
            self.ollama_process = subprocess.Popen(
                [self.ollama_bin, "serve"],
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            for _ in range(15):
                time.sleep(1)
                if self.is_ollama_alive():
                    logger.info("Ollama is now online.")
                    return True
        except Exception as exc:
            logger.error("Failed to start Ollama: %s", exc)
        return self.is_ollama_alive()

    def ensure_gateway(self) -> bool:
        if self.is_gateway_alive():
            return True
        logger.info("Starting J.A.R.V.I.S. Gateway daemon in background...")
        cmd = [
            self.python_bin,
            "-m",
            "uvicorn",
            "apps.gateway.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ]
        try:
            self.gateway_process = subprocess.Popen(
                cmd,
                cwd=str(self.root),
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            for _ in range(20):
                time.sleep(1)
                if self.is_gateway_alive():
                    logger.info("J.A.R.V.I.S. Gateway is now online.")
                    return True
        except Exception as exc:
            logger.error("Failed to start Gateway: %s", exc)
        return self.is_gateway_alive()

    def restart_all(self) -> dict[str, bool]:
        """Terminate existing instances and restart fresh."""
        self.stop_all()
        time.sleep(2)
        ollama_ok = self.ensure_ollama()
        gateway_ok = self.ensure_gateway()
        return {"ollama": ollama_ok, "gateway": gateway_ok}

    def stop_all(self) -> None:
        if self.gateway_process:
            try:
                self.gateway_process.terminate()
            except Exception:
                pass
            self.gateway_process = None
        if self.ollama_process:
            try:
                self.ollama_process.terminate()
            except Exception:
                pass
            self.ollama_process = None
