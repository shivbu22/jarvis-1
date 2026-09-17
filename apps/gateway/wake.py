"""Wake word detection: "Hey Jarvis", fully locally.

openWakeWord runs the pretrained ``hey_jarvis`` model on ONNX Runtime on the CPU.
No account, no API key, no audio leaving the machine -- which is the whole reason
this is not a cloud wake-word service.

Callers may push arbitrary chunk sizes: audio from a browser arrives in whatever
increments the microphone delivers, while the model needs fixed 1280-sample
frames, so this buffers. Detections are debounced, because a single utterance
otherwise fires on two or three consecutive frames.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

from .config import Settings

logger = logging.getLogger(__name__)

FRAME_SAMPLES = 1280  # 80 ms at 16 kHz, as openWakeWord expects
FRAME_BYTES = FRAME_SAMPLES * 2
REFRACTORY_SECONDS = 1.5


@dataclass
class WakeDetection:
    model: str
    score: float
    at: float = 0.0

    def describe(self) -> dict[str, Any]:
        return {"model": self.model, "score": round(self.score, 3), "at": self.at or time.time()}


class WakeWordDetector:
    def __init__(self, settings: Settings, bus: Any = None) -> None:
        self.settings = settings
        self.bus = bus
        self._model: Any = None
        self._buffer = bytearray()
        self._lock = threading.Lock()
        self._last_detection = 0.0
        self._load_error: str | None = None

    # -- availability -----------------------------------------------------

    def available(self) -> bool:
        if not self.settings.wake_enabled:
            return False
        try:
            import openwakeword  # noqa: F401
        except ImportError:
            return False
        return True

    @property
    def loaded(self) -> bool:
        return self._model is not None

    @property
    def load_error(self) -> str | None:
        return self._load_error

    def describe(self) -> dict[str, Any]:
        return {
            "enabled": self.settings.wake_enabled,
            "available": self.available(),
            "loaded": self.loaded,
            "model": self.settings.wake_model,
            "threshold": self.settings.wake_threshold,
            "error": self._load_error,
        }

    # -- model ------------------------------------------------------------

    def load(self) -> Any:
        """Load the ONNX model. Downloads the weights once, on first use."""
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:
                return self._model
            if not self.settings.wake_enabled:
                self._load_error = "wake word is disabled (WAKE_ENABLED=0)"
                return None
            try:
                from openwakeword.model import Model
            except ImportError:
                self._load_error = (
                    "Wake word needs openwakeword. Install the voice extra with "
                    "'uv pip install -r requirements-voice.txt'."
                )
                return None
            try:
                self._model = Model(
                    wakeword_models=[self.settings.wake_model],
                    inference_framework=self.settings.wake_inference_framework,
                )
                self._load_error = None
                logger.info("wake word ready: %s", self.settings.wake_model)
            except Exception as exc:
                self._load_error = (
                    f"I could not load the '{self.settings.wake_model}' wake word "
                    f"({exc}). It downloads its weights on first use, so this needs "
                    "internet once."
                )
                logger.warning(self._load_error)
                return None
            return self._model

    # -- streaming --------------------------------------------------------

    def feed(self, pcm16: bytes) -> WakeDetection | None:
        """Push raw 16 kHz mono int16 audio. Returns a detection, or None."""
        if not self.settings.wake_enabled:
            return None
        model = self._model or self.load()
        if model is None:
            return None

        import numpy as np

        with self._lock:
            self._buffer.extend(pcm16)
            detection: WakeDetection | None = None
            while len(self._buffer) >= FRAME_BYTES:
                frame = bytes(self._buffer[:FRAME_BYTES])
                del self._buffer[:FRAME_BYTES]
                samples = np.frombuffer(frame, dtype="<i2")
                try:
                    scores = model.predict(samples)
                except Exception as exc:
                    logger.warning("wake word predict failed: %s", exc)
                    continue

                for name, score in (scores or {}).items():
                    if float(score) < self.settings.wake_threshold:
                        continue
                    now = time.time()
                    if now - self._last_detection < REFRACTORY_SECONDS:
                        continue
                    self._last_detection = now
                    detection = WakeDetection(model=str(name), score=float(score), at=now)
                    with contextlib.suppress(Exception):
                        model.reset()
                    break
                if detection is not None:
                    break

        if detection is not None and self.bus is not None:
            event = detection.describe()
            event["type"] = "wake_detected"
            self.bus.publish(event)
        return detection

    def reset(self) -> None:
        with self._lock:
            self._buffer.clear()
            self._last_detection = 0.0
            if self._model is not None:
                with contextlib.suppress(Exception):
                    self._model.reset()
