"""Speech to text, locally, with faster-whisper.

Decoding is deliberately format-agnostic: the orb uploads whatever MediaRecorder
produced (usually WebM/Opus), while the CLI and tests hand over WAV. PyAV -- which
ships inside faster-whisper -- can decode all of it, so bytes are written to a
temp file and handed to the library's own decoder. `soundfile` is the fallback for
WAV when PyAV is unavailable.

The default device is CUDA with `int8_float16`, which is what fits a 4 GB laptop
GPU. If the CUDA runtime cannot load -- a known Windows DLL-hunt problem with
ctranslate2 -- the model is transparently reloaded on CPU with `int8` and the
transcript records that it happened, rather than the whole feature failing.
"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Ensure Windows locates cublas64_12.dll / cublasLt64_12.dll for ctranslate2 CUDA acceleration
try:
    import torch
    _torch_lib = os.path.join(os.path.dirname(torch.__file__), "lib")
    if os.path.isdir(_torch_lib):
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(_torch_lib)
        os.environ["PATH"] = _torch_lib + ";" + os.environ.get("PATH", "")
except Exception:
    pass

from .config import Settings, stt_decode_options

logger = logging.getLogger(__name__)

TARGET_SAMPLE_RATE = 16000


class STTUnavailable(RuntimeError):
    """Raised when faster-whisper is not installed or a model cannot load."""


@dataclass
class Transcript:
    text: str
    language: str = ""
    duration_seconds: float = 0.0
    segments: int = 0
    model: str = ""
    device: str = ""
    no_speech: bool = False

    def describe(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "language": self.language,
            "duration_seconds": round(self.duration_seconds, 2),
            "segments": self.segments,
            "model": self.model,
            "device": self.device,
            "no_speech": self.no_speech,
        }


class WhisperSTT:
    """A lazily loaded whisper model, safe to call from worker threads."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._model: Any = None
        self._device = ""
        self._compute_type = ""
        self._load_error: str | None = None
        self._lock = threading.Lock()

    # -- availability -----------------------------------------------------

    @property
    def loaded(self) -> bool:
        return self._model is not None

    @property
    def load_error(self) -> str | None:
        return self._load_error

    def available(self) -> bool:
        """Whether the dependency exists. Cheap: does not load the model."""
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            return False
        return True

    def describe(self) -> dict[str, Any]:
        """Report what is true now, not what was configured.

        `device` stays "not loaded" until a model has actually been probed. It
        used to echo the configured value, so /health claimed "cuda" on a machine
        where CUDA fails at the first encode and the real answer is CPU -- exactly
        the kind of reassuring-but-wrong status that makes a health endpoint
        useless.
        """
        return {
            "engine": "faster-whisper",
            "available": self.available(),
            "loaded": self.loaded,
            "model": self.settings.whisper_model,
            "device": self._device or "not loaded",
            "configured_device": self.settings.stt_device,
            "compute_type": self._compute_type or self.settings.stt_compute_type,
            "profile": self.settings.stt_profile,
            "language": self.settings.stt_language,
            "error": self._load_error,
        }

    # -- model ------------------------------------------------------------

    def _ensure(self) -> Any:
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:
                return self._model

            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                self._load_error = (
                    "Speech recognition needs faster-whisper. Install the voice extra "
                    "with 'uv pip install -r requirements-voice.txt'."
                )
                raise STTUnavailable(self._load_error) from exc

            device, compute_type = self._device_plan()
            attempts = [(device, compute_type)]
            if device != "cpu":
                attempts.append(("cpu", "int8"))

            errors: list[str] = []
            for attempt_device, attempt_compute in attempts:
                try:
                    candidate = WhisperModel(
                        self.settings.whisper_model,
                        device=attempt_device,
                        compute_type=attempt_compute,
                        download_root=str(_model_cache_dir()),
                    )
                    # Force one real encode before trusting the device. On Windows
                    # ctranslate2 happily *constructs* a CUDA model and then fails
                    # on the first encode with "Library cublas64_12.dll is not
                    # found" -- a failure that would otherwise surface in the
                    # middle of a conversation. Probing here turns it into a
                    # startup-time fallback instead.
                    _probe_device(candidate)
                    self._model = candidate
                    self._device, self._compute_type = attempt_device, attempt_compute
                    if attempt_device != device:
                        logger.warning(
                            "whisper fell back to %s/%s after %s failed (%s)",
                            attempt_device, attempt_compute, device, errors[-1] if errors else "",
                        )
                    self._load_error = None
                    return self._model
                except Exception as exc:
                    errors.append(f"{attempt_device}/{attempt_compute}: {exc}")
                    logger.warning("whisper load failed on %s: %s", attempt_device, exc)

            self._load_error = (
                "I could not load the speech model ("
                + "; ".join(errors)
                + "). Try STT_DEVICE=cpu or a smaller WHISPER_MODEL in .env."
            )
            raise STTUnavailable(self._load_error)

    def _device_plan(self) -> tuple[str, str]:
        requested = (self.settings.stt_device or "auto").lower()
        if requested == "auto":
            requested = "cuda" if _cuda_available() else "cpu"
        compute = (self.settings.stt_compute_type or "auto").lower()
        if compute == "auto":
            compute = "int8_float16" if requested == "cuda" else "int8"
        return requested, compute

    def _run(
        self, model: Any, samples: Any, language: str | None, prompt: str | None, options: dict
    ) -> tuple[list[Any], Any]:
        """One transcription pass.

        The generators are drained here, inside the caller's error handling: the
        CUDA failure this guards against is raised during iteration, not by the
        call itself, so it must not escape the try block.
        """
        vad_kwargs: dict[str, Any] = {}
        if self.settings.stt_vad_filter:
            vad_kwargs["vad_filter"] = True
            vad_kwargs["vad_parameters"] = {
                "threshold": getattr(self.settings, "stt_vad_threshold", 0.2),
                "min_speech_duration_ms": 50,
                "min_silence_duration_ms": 600,
                "speech_pad_ms": 200,
            }
        else:
            vad_kwargs["vad_filter"] = False

        effective_prompt = (
            prompt
            if prompt is not None
            else getattr(
                self.settings,
                "stt_initial_prompt",
                "Conversational speech, technical commands, quiet voice, whispered queries.",
            )
        )

        segments, info = model.transcribe(
            samples,
            language=language,
            initial_prompt=effective_prompt,
            no_speech_threshold=0.6,
            condition_on_previous_text=False,
            **vad_kwargs,
            **options,
        )
        return list(segments), info

    def _reload_on_cpu(self) -> None:
        """Swap in a CPU model after a device failure, so hearing keeps working."""
        from faster_whisper import WhisperModel

        with self._lock:
            self._model = WhisperModel(
                self.settings.whisper_model,
                device="cpu",
                compute_type="int8",
                download_root=str(_model_cache_dir()),
            )
            self._device, self._compute_type = "cpu", "int8"
            self._load_error = None

    # -- transcription ----------------------------------------------------

    def transcribe(
        self, audio: bytes | Path, *, language: str | None = None, prompt: str | None = None
    ) -> Transcript:
        model = self._ensure()
        path, temporary = _materialise(audio)

        try:
            samples = _decode(path)
        finally:
            if temporary:
                with contextlib.suppress(OSError):
                    path.unlink(missing_ok=True)

        if samples is None:
            return Transcript(text="", model=self.settings.whisper_model, device=self._device)

        options = stt_decode_options(self.settings.stt_profile)
        language = language if language is not None else (self.settings.stt_language or None)
        if language in {"", "auto"}:
            language = None

        try:
            collected, info = self._run(model, samples, language, prompt, options)
        except RuntimeError as exc:
            # Second line of defence: a device can also fail after the probe
            # (driver updates, VRAM pressure from another process). Falling back
            # to CPU keeps the assistant able to hear rather than going mute.
            if self._device == "cpu" or not _looks_like_device_failure(exc):
                raise STTUnavailable(f"speech recognition failed: {exc}") from exc
            logger.warning("speech device failed mid-transcription (%s); retrying on cpu", exc)
            self._reload_on_cpu()
            collected, info = self._run(self._model, samples, language, prompt, options)

        text = " ".join(segment.text.strip() for segment in collected).strip()

        return Transcript(
            text=text,
            language=str(getattr(info, "language", "") or ""),
            duration_seconds=float(getattr(info, "duration", 0.0) or 0.0),
            segments=len(collected),
            model=self.settings.whisper_model,
            device=self._device,
            no_speech=not text,
        )


def _probe_device(model: Any) -> None:
    """Run a throwaway encode so a broken runtime fails at load, not mid-conversation.

    The buffer is low-level noise rather than silence: whisper's VAD would filter
    pure silence out before the encoder ran, and the encoder is exactly the part
    that needs cuBLAS.
    """
    import numpy as np

    noise = np.random.default_rng(0).normal(0.0, 0.01, TARGET_SAMPLE_RATE).astype(np.float32)
    segments, _ = model.transcribe(noise, language="en", vad_filter=False, beam_size=1)
    list(segments)  # the work happens during iteration, not at call time


def _looks_like_device_failure(exc: Exception) -> bool:
    """True for CUDA/cuDNN/cuBLAS runtime problems, which CPU can recover from."""
    message = str(exc).lower()
    return any(
        marker in message
        for marker in ("cublas", "cudnn", "cuda", "not found or cannot be loaded", "gpu")
    )


def _cuda_available() -> bool:
    try:
        import ctranslate2

        return bool(ctranslate2.get_cuda_device_count())
    except Exception:
        return False


def _model_cache_dir() -> Path:
    """Keep whisper weights with the other model caches, outside OneDrive."""
    import os

    hf_home = os.environ.get("HF_HOME")
    if hf_home:
        return Path(hf_home)
    return Path.home() / ".cache" / "huggingface"


def _materialise(audio: bytes | Path) -> tuple[Path, bool]:
    """Return (path, is_temporary). Bytes are written to a temp file to decode.

    The suffix is only a hint -- PyAV sniffs the container -- so one fixed
    suffix serves both WebM/Opus from the browser and WAV from the CLI.
    """
    if isinstance(audio, Path):
        return audio, False
    import tempfile

    descriptor, name = tempfile.mkstemp(suffix=".webm", prefix="jarvis-stt-")
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(audio)
    return Path(name), True


def _decode(path: Path) -> Any | None:
    """Decode any container to 16 kHz mono float32. None when it is not audio."""
    try:
        from faster_whisper.audio import decode_audio

        return decode_audio(str(path), sampling_rate=TARGET_SAMPLE_RATE)
    except Exception as exc:
        logger.info("PyAV decode failed (%s); trying soundfile", exc)

    try:
        import numpy as np
        import soundfile as sf

        data, rate = sf.read(str(path), dtype="float32", always_2d=True)
        mono = data.mean(axis=1) if data.shape[1] > 1 else data[:, 0]
        if rate != TARGET_SAMPLE_RATE:
            mono = _resample(mono, rate, TARGET_SAMPLE_RATE)
        return mono.astype(np.float32)
    except Exception as exc:
        logger.warning("could not decode audio at %s: %s", path, exc)
        return None


def _resample(samples: Any, source_rate: int, target_rate: int) -> Any:
    """Linear-interpolation resample. Whisper is robust; this stays dependency-free."""
    import numpy as np

    if source_rate == target_rate or samples.size == 0:
        return samples
    duration = samples.size / float(source_rate)
    target_count = round(duration * target_rate)
    if target_count <= 0:
        return samples
    source_positions = np.linspace(0.0, duration, num=samples.size, endpoint=False)
    target_positions = np.linspace(0.0, duration, num=target_count, endpoint=False)
    return np.interp(target_positions, source_positions, samples).astype(np.float32)


def decode_to_pcm16(audio: bytes) -> bytes | None:
    """Decode arbitrary audio to 16 kHz mono 16-bit PCM.

    Used by the wake-word path, for clients that upload a recorded clip rather
    than streaming raw frames.
    """
    import numpy as np

    path, temporary = _materialise(audio)
    try:
        samples = _decode(path)
        if samples is None:
            return None
        clipped = np.clip(samples, -1.0, 1.0)
        return (clipped * 32767.0).astype("<i2").tobytes()
    finally:
        if temporary:
            with contextlib.suppress(OSError):
                path.unlink(missing_ok=True)


def wav_bytes_from_pcm16(pcm: bytes, sample_rate: int = TARGET_SAMPLE_RATE) -> bytes:
    """Wrap raw PCM in a RIFF header (mono, 16-bit)."""
    import io

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm)
    return buffer.getvalue()


def silence(seconds: float = 0.1) -> bytes:
    """A short silent WAV, useful for tests and as a TTS failure placeholder."""
    import io

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(TARGET_SAMPLE_RATE)
        handle.writeframes(b"\x00\x00" * int(TARGET_SAMPLE_RATE * seconds))
    return buffer.getvalue()
