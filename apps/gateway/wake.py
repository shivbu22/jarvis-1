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
from pathlib import Path
from typing import Any

from .config import Settings

logger = logging.getLogger(__name__)

RATE = 16000  # the only rate the model accepts; the browser resamples to it
FRAME_SAMPLES = 1280  # 80 ms at 16 kHz, as openWakeWord expects
FRAME_BYTES = FRAME_SAMPLES * 2
REFRACTORY_SECONDS = 1.5


class WakeUnavailable(RuntimeError):
    """The wake word cannot run: disabled, weights unobtainable, or unloadable."""


def weights_dir() -> Path | None:
    """Where openWakeWord looks for its ONNX weights, or None if unimportable."""
    try:
        import openwakeword
    except ImportError:
        return None
    return Path(openwakeword.__file__).resolve().parent / "resources" / "models"


def required_weights(model: str) -> list[str]:
    """The ONNX files the ONNX detector needs on disk for ``model``.

    Derived from openWakeWord's own tables rather than hardcoded, so a version
    bump cannot leave this list stale and quietly wrong. The shared feature
    models are always needed: audio features come from them, not from the wake
    model itself.
    """
    import openwakeword

    def onnx_name(url: str) -> str:
        return url.split("/")[-1].replace(".tflite", ".onnx")

    feature = [onnx_name(spec["download_url"]) for spec in openwakeword.FEATURE_MODELS.values()]
    wake = [
        onnx_name(spec["download_url"])
        for spec in openwakeword.MODELS.values()
        if model in spec["download_url"]
    ]
    return feature + [name for name in wake if name not in feature]


@dataclass
class WakeDetection:
    model: str
    score: float
    at: float = 0.0

    def describe(self) -> dict[str, Any]:
        return {"model": self.model, "score": round(self.score, 3), "at": self.at or time.time()}


class WakeSession:
    """One independent wake-word stream, owned by a single websocket connection.

    The ONNX model keeps rolling feature buffers between ``predict`` calls, so
    sharing one model between connections interleaves their audio and neither
    stream detects reliably -- the exact failure seen when the orb's live mic
    and a smoke test fed the endpoint at the same time. A session owns its own
    model instance, buffer, and refractory timer; only the weights on disk are
    shared, which is the expensive part.
    """

    def __init__(
        self,
        settings: Settings,
        model: Any,
        bus: Any = None,
    ) -> None:
        self.settings = settings
        self._model = model
        self.bus = bus
        self._buffer = bytearray()
        self._last_detection = 0.0

    def feed(self, pcm16: bytes) -> WakeDetection | None:
        """Push raw 16 kHz mono int16 audio. Returns a detection, or None."""
        if not self.settings.wake_enabled or self._model is None:
            return None

        import numpy as np

        detection: WakeDetection | None = None
        self._buffer.extend(pcm16)
        while len(self._buffer) >= FRAME_BYTES:
            frame = bytes(self._buffer[:FRAME_BYTES])
            del self._buffer[:FRAME_BYTES]
            samples = np.frombuffer(frame, dtype="<i2")
            try:
                scores = self._model.predict(samples)
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
                    self._model.reset()
                break
            if detection is not None:
                break

        if detection is not None and self.bus is not None:
            event = detection.describe()
            event["type"] = "wake_detected"
            self.bus.publish(event)
        return detection

    def reset(self) -> None:
        self._buffer.clear()
        self._last_detection = 0.0
        if self._model is not None:
            with contextlib.suppress(Exception):
                self._model.reset()


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
        """Whether the wake word could work at all: enabled, and the package present."""
        if not self.settings.wake_enabled:
            return False
        return weights_dir() is not None

    @property
    def ready(self) -> bool:
        """Whether the detector can run *now*, with no one-time fetch first.

        ``available`` and ``ready`` differ on a fresh install, and conflating
        them is a specific, observed bug: the ``openwakeword`` wheel ships
        *without* its ``resources/models`` directory and its ``Model``
        constructor never fetches it, so a first run indexes a model file that
        does not exist and raises ``NoSuchFile``. The orb used to read
        ``available`` alone, so it showed a green "wake ready" pill over a
        detector that could only ever fail.
        """
        return self.available() and self.weights_present

    @property
    def weights_present(self) -> bool:
        """Whether every ONNX file this model needs is already on disk."""
        directory = weights_dir()
        if directory is None:
            return False
        names = self._required()
        return bool(names) and all((directory / name).exists() for name in names)

    def missing_weights(self) -> list[str]:
        """Which required files are absent -- the actionable part of the error."""
        directory = weights_dir()
        if directory is None:
            return []
        return [name for name in self._required() if not (directory / name).exists()]

    def _required(self) -> list[str]:
        try:
            return required_weights(self.settings.wake_model)
        except Exception:  # pragma: no cover - only with a broken install
            return []

    def weights_hint(self) -> str:
        """The one-line fix for absent weights, shown in errors and /health."""
        model = self.settings.wake_model
        return (
            f"I have no '{model}' wake-word weights yet. They download on first "
            "use, so this needs internet once; if that is blocked, run: "
            f'python -c "import openwakeword.utils as u; u.download_models([\'{model}\'])"'
        )

    def download_weights(self) -> None:
        """Fetch the weights the wheel does not ship.

        ``openwakeword.utils.download_models`` is the library's own entry point;
        it also grabs the shared melspectrogram and embedding models, which the
        wake model cannot run without.
        """
        from openwakeword.utils import download_models

        download_models([self.settings.wake_model])

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
            "ready": self.ready,
            "model": self.settings.wake_model,
            "threshold": self.settings.wake_threshold,
            "weights_present": self.weights_present,
            "missing_weights": self.missing_weights(),
            # A load error only exists once something tried to load, which may
            # never have happened. Say why it cannot work regardless of that.
            "error": self._load_error or (None if self.weights_present else self.weights_hint()),
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
                import openwakeword  # noqa: F401 -- availability check
            except ImportError:
                self._load_error = (
                    "Wake word needs openwakeword. Install the voice extra with "
                    "'uv pip install -r requirements-voice.txt'."
                )
                return None
            if not self.weights_present:
                # First run on a fresh install: the wheel ships no weights, so
                # fetch them here rather than failing with NoSuchFile later.
                logger.info(
                    "wake word weights missing (%s); fetching %s",
                    ", ".join(self.missing_weights()) or "unknown",
                    self.settings.wake_model,
                )
                try:
                    self.download_weights()
                except Exception as exc:
                    self._load_error = f"{self.weights_hint()} ({exc})"
                    logger.warning(self._load_error)
                    return None

            try:
                self._model = self._new_model()
                self._load_error = None
                logger.info("wake word ready: %s", self.settings.wake_model)
            except Exception as exc:
                self._load_error = (
                    f"I could not load the '{self.settings.wake_model}' wake word ({exc})."
                )
                logger.warning(self._load_error)
                return None
            return self._model

    def _new_model(self) -> Any:
        """Build one independent ONNX model instance.

        Construction is deliberately separate from the cached ``load()``: the
        model keeps rolling feature buffers between ``predict`` calls, so every
        wake stream needs its own instance even though they share the weights
        on disk. Measured live: sessions sharing the cached model scored 0.00
        on the smoke test while another connection fed the endpoint, because
        the two streams interleaved inside the same ONNX buffers.
        """
        from openwakeword.model import Model

        return Model(
            wakeword_models=[self.settings.wake_model],
            inference_framework=self.settings.wake_inference_framework,
        )

    # -- streaming --------------------------------------------------------

    def open_session(self) -> WakeSession:
        """Create an independent stream for one websocket connection.

        Raises ``WakeUnavailable`` with an actionable message when the wake
        word cannot run at all; the endpoint turns that into the connection's
        first frame instead of leaving it silently dead. Loading here also
        triggers the one-time weight download on a fresh install, exactly as
        feeding the shared model used to.
        """
        if not self.available():
            raise WakeUnavailable(
                "Wake word needs openwakeword. Install the voice extra with "
                "'uv pip install -r requirements-voice.txt'."
            )
        # ``load()`` first so a fresh install downloads the weights here, then
        # build an instance dedicated to this session -- sharing one model
        # between streams is the exact bug the session design exists to fix.
        if self.load() is None:
            raise WakeUnavailable(self._load_error or self.weights_hint())
        if not self.weights_present:
            raise WakeUnavailable(self.weights_hint())
        try:
            model = self._new_model()
        except Exception as exc:
            raise WakeUnavailable(
                f"I could not load the '{self.settings.wake_model}' wake word ({exc})."
            ) from exc
        return WakeSession(self.settings, model, bus=self.bus)

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
