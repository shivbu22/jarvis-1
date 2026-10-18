"""Text to speech: the cloned Jarvis voice, with a floor underneath it.

Two engines, chosen automatically:

* **Chatterbox** (``chatterbox-tts``) clones the voice from a reference WAV, which
  is what makes it sound like *Jarvis* rather than a narrator. It is
  autoregressive and heavy, so on a 4 GB laptop GPU it runs on the CPU, and its
  first call per process pays a real load cost.
* **Windows SAPI** via PowerShell, which needs no dependency at all.

The fallback is the point. A voice assistant that goes mute when an optional
2.5 GB dependency is missing is worse than one that speaks in a plainer voice, so
``TTS_ENGINE=auto`` degrades instead of failing, reports which voice is in use,
and keeps the conversation going.
"""

from __future__ import annotations

import importlib.util
import inspect
import io
import json
import logging
import re
import subprocess
import tempfile
import threading
import wave
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .config import Settings

logger = logging.getLogger(__name__)

REFERENCE_TARGET_RATE = 24000
MIN_REFERENCE_SECONDS = 5.0

# Preference order for the fallback voice: en-GB first, because that is the
# register a Jarvis impression lives in.
SAPI_VOICE_PREFERENCE = (
    "Microsoft George",
    "Microsoft Ryan",
    "Microsoft Daniel",
    "Microsoft Mark",
    "Microsoft David",
)


class TTSError(RuntimeError):
    """Raised when no engine could produce audio."""


@dataclass
class Speech:
    data: bytes  # a complete WAV file
    engine: str
    sample_rate: int
    voice: str = ""

    def describe(self) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "voice": self.voice,
            "sample_rate": self.sample_rate,
            "bytes": len(self.data),
        }


#: Smallest chunk worth its own synthesis call.
#:
#: Measured on this machine (16-core CPU, chatterbox turbo): the clone costs about
#: 6 seconds of *fixed* work per call plus roughly 1.5 seconds per second of audio.
#: Four clauses in four calls took 23.9 s; the same words in one call took 12.7 s.
#: So a call per clause would nearly double how long a reply takes to say. This
#: bound is what stops "start speaking sooner" from meaning "speak for twice as
#: long overall" -- and three parallel workers measured 1.00x, so extra calls
#: cannot be bought back with concurrency either.
MIN_SPEECH_CHARS = 96

#: The first chunk is allowed to be much shorter, and that is the whole point of
#: streaming: the cost of one extra call (~6 s) buys an earlier first word, and
#: the first word is what the master actually experiences as latency. Later
#: chunks use the larger bound so a long answer does not pay that cost per clause.
FIRST_SPEECH_CHARS = 24

#: Hard limit, so a reply with no punctuation in it still starts being spoken.
MAX_SPEECH_CHARS = 240

#: A place worth pausing at: a sentence end, or a comma/semicolon/colon, all of
#: which are real pauses in speech rather than arbitrary splits.
_BOUNDARY = re.compile(r"(?<=[.!?;:])\s+|(?<=,)\s+")


class SpeechChunker:
    """Turns a stream of answer tokens into pieces worth synthesising.

    Both costs are real and they pull in opposite directions: a long answer must
    *start* speaking quickly, but every synthesis call carries a large fixed cost.
    The policy resolves them by waiting for a boundary **and** a minimum length,
    which keeps the call count near the sentence count while letting the first
    sentence begin while the rest is still being written.
    """

    def __init__(
        self,
        minimum: int = MIN_SPEECH_CHARS,
        maximum: int = MAX_SPEECH_CHARS,
        first_minimum: int = FIRST_SPEECH_CHARS,
    ) -> None:
        self.minimum = max(1, int(minimum))
        self.maximum = max(self.minimum, int(maximum))
        self.first_minimum = max(1, min(int(first_minimum), self.minimum))
        self._buffer = ""
        self._emitted = 0

    def push(self, piece: str) -> list[str]:
        """Add streamed text; return any chunks that are ready to speak."""
        self._buffer += piece or ""
        ready: list[str] = []
        while True:
            cut = self._cut_point()
            if cut <= 0:
                break
            chunk = self._buffer[:cut].strip()
            self._buffer = self._buffer[cut:].lstrip()
            if chunk:
                ready.append(chunk)
                self._emitted += 1
        return ready

    def flush(self) -> list[str]:
        """Whatever is left, for the end of the reply."""
        chunk = self._buffer.strip()
        self._buffer = ""
        return [chunk] if chunk else []

    @property
    def pending(self) -> str:
        return self._buffer

    def _cut_point(self) -> int:
        """Index to cut at, or 0 when the buffer should keep accumulating."""
        threshold = self.first_minimum if self._emitted == 0 else self.minimum
        if len(self._buffer) >= self.maximum:
            # No usable punctuation: cut at the last space so no word is split.
            space = self._buffer.rfind(" ", 0, self.maximum)
            return space if space > 0 else self.maximum
        for match in _BOUNDARY.finditer(self._buffer):
            if match.end() >= threshold:
                return match.end()
        return 0


class TTSEngine(ABC):
    name = "tts"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = threading.Lock()

    @abstractmethod
    def available(self) -> bool:
        """Whether the engine can be used at all. Must not be expensive."""

    @abstractmethod
    def _synthesize(self, text: str) -> Speech:
        """Produce audio. Called with the lock held."""

    def synthesize(self, text: str) -> Speech:
        text = self._prepare(text)
        if not text:
            raise TTSError("there was nothing to say")
        with self._lock:
            return self._synthesize(text)

    def _prepare(self, text: str) -> str:
        cleaned = " ".join((text or "").split())
        limit = max(80, int(self.settings.tts_max_chars))
        if len(cleaned) > limit:
            # Cut at a sentence boundary so the voice does not stop mid-clause.
            window = cleaned[:limit]
            for separator in (". ", "! ", "? "):
                index = window.rfind(separator)
                if index > limit * 0.5:
                    return window[: index + 1]
            cleaned = window
        return cleaned

    def describe(self) -> dict[str, Any]:
        return {"engine": self.name, "available": self.available()}


# --------------------------------------------------------------------------
# Chatterbox: the cloned voice
# --------------------------------------------------------------------------


class ChatterboxEngine(TTSEngine):
    name = "chatterbox"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._model: Any = None
        self._sample_rate = REFERENCE_TARGET_RATE
        self._load_error: str | None = None
        self._voice_label = "default voice"

    def available(self) -> bool:
        """Usable right now: the package is installed AND the weights are local.

        The weights are ~1.5 GB. If this method only checked for the package, the
        first spoken reply would silently trigger that download inside a
        conversation -- five minutes of silence with no explanation. Instead the
        model is fetched deliberately (scripts/build_jarvis_voice.py) or on an
        explicit TTS_ALLOW_DOWNLOAD=1, and until then the Windows voice is used.
        """
        if importlib.util.find_spec("chatterbox") is None:
            return False
        if self.settings.tts_allow_download:
            return True
        return self._weights_cached()

    def _weights_cached(self) -> bool:
        """True when the model snapshot is fully present in the local HF cache."""
        try:
            from huggingface_hub import snapshot_download

            snapshot_download(
                repo_id=_chatterbox_repo(self.settings.tts_backend),
                local_files_only=True,
            )
            return True
        except Exception:
            return False

    @property
    def load_error(self) -> str | None:
        return self._load_error

    def reference_path(self) -> Path | None:
        """Prefer the normalised reference built by scripts/build_jarvis_voice.py."""
        candidates = [
            self.settings.voices_dir / "jarvis-ref-24k.wav",
            self.settings.voice_ref,
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    def _ensure(self) -> Any:
        if self._model is not None:
            return self._model

        module_name, class_name = _chatterbox_target(self.settings.tts_backend)
        try:
            module = __import__(module_name, fromlist=[class_name])
            factory = getattr(module, class_name)
        except (ImportError, AttributeError) as exc:
            self._load_error = (
                f"Chatterbox is not installed ({exc}). Install the voice extra with "
                "'uv pip install -r requirements-voice.txt'."
            )
            raise TTSError(self._load_error) from exc

        try:
            self._model = factory.from_pretrained(device=self.settings.tts_device)
        except Exception as exc:
            self._load_error = (
                f"I could not load the Chatterbox voice on {self.settings.tts_device} "
                f"({exc}). Try TTS_DEVICE=cpu in .env."
            )
            raise TTSError(self._load_error) from exc

        self._sample_rate = int(getattr(self._model, "sr", REFERENCE_TARGET_RATE) or REFERENCE_TARGET_RATE)
        reference = self.reference_path()
        self._voice_label = reference.name if reference else "Chatterbox default voice"
        logger.info("chatterbox ready on %s, voice=%s", self.settings.tts_device, self._voice_label)
        return self._model

    def _synthesize(self, text: str) -> Speech:
        model = self._ensure()
        kwargs: dict[str, Any] = {}

        reference = self.reference_path()
        if reference is not None:
            kwargs["audio_prompt_path"] = str(reference)

        # Only pass options this build of the library actually accepts.
        try:
            params = inspect.signature(model.generate).parameters
            if "exaggeration" in params:
                kwargs["exaggeration"] = float(self.settings.tts_exaggeration)
        except (TypeError, ValueError):
            pass

        try:
            wav = model.generate(text, **kwargs)
        except Exception as exc:
            raise TTSError(f"Chatterbox could not speak that: {exc}") from exc

        samples = _to_numpy(wav)
        if samples.size == 0:
            raise TTSError("Chatterbox produced no audio")

        return Speech(
            data=_wav_bytes(samples, self._sample_rate),
            engine=self.name,
            sample_rate=self._sample_rate,
            voice=self._voice_label,
        )

    def describe(self) -> dict[str, Any]:
        return {
            "engine": self.name,
            "available": self.available(),
            "loaded": self._model is not None,
            "backend": self.settings.tts_backend,
            "device": self.settings.tts_device,
            "reference": str(self.reference_path() or ""),
            "voice": self._voice_label,
            "error": self._load_error,
        }


def _chatterbox_repo(backend: str) -> str:
    """The Hugging Face repo backing the selected backend."""
    preferred = (backend or "turbo").lower()
    if preferred == "turbo":
        return "ResembleAI/chatterbox-turbo"
    return "ResembleAI/chatterbox"


def _chatterbox_target(backend: str) -> tuple[str, str]:
    """Module and class for the requested backend, falling back to the plain one."""
    preferred = (backend or "turbo").lower()
    if preferred == "turbo":
        for module_name, class_name in (
            ("chatterbox.tts_turbo", "ChatterboxTurboTTS"),
            ("chatterbox.tts", "ChatterboxTTS"),
        ):
            if importlib.util.find_spec(module_name.split(".tts")[0]) is None:
                continue
            try:
                module = __import__(module_name, fromlist=[class_name])
            except ImportError:
                continue
            if hasattr(module, class_name):
                return module_name, class_name
    return "chatterbox.tts", "ChatterboxTTS"


# --------------------------------------------------------------------------
# Microsoft Edge Neural TTS: Studio-Quality Human British Voice
# --------------------------------------------------------------------------


class EdgeTTSEngine(TTSEngine):
    """Ultra-low latency, studio-quality neural voice synthesis.

    Provides realistic human intonation and authentic British inflection (e.g.
    `en-GB-RyanNeural`) with sub-second response times and zero GPU load.
    """

    name = "edge"

    def __init__(self, settings: Settings, voice: str = "en-GB-RyanNeural") -> None:
        super().__init__(settings)
        self._voice = getattr(settings, "tts_edge_voice", None) or voice

    def available(self) -> bool:
        return importlib.util.find_spec("edge_tts") is not None

    def _synthesize(self, text: str) -> Speech:
        if not self.available():
            raise TTSError("edge-tts is not installed in the environment")

        import asyncio

        import edge_tts
        import soundfile as sf
        from .hinglish import resolve_tts_voice

        hinglish_voice = getattr(self.settings, "tts_voice_hinglish", None) or "en-IN-PrabhatNeural"
        selected_voice = resolve_tts_voice(text, default_voice=self._voice, hinglish_voice=hinglish_voice)

        async def _generate() -> bytes:
            comm = edge_tts.Communicate(text, selected_voice)
            chunks = []
            async for chunk in comm.stream():
                if chunk.get("type") == "audio" and "data" in chunk:
                    chunks.append(chunk["data"])
            return b"".join(chunks)

        try:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    raw_mp3 = executor.submit(asyncio.run, _generate()).result(timeout=25)
            else:
                raw_mp3 = asyncio.run(_generate())
        except Exception as exc:
            raise TTSError(f"Edge TTS synthesis failed: {exc}") from exc

        if not raw_mp3:
            raise TTSError("Edge TTS generated empty audio")

        try:
            data, sample_rate = sf.read(io.BytesIO(raw_mp3), dtype="float32")
            mono = _to_numpy(data)
            wav_bytes = _wav_bytes(mono, sample_rate)
            return Speech(
                data=wav_bytes,
                engine=self.name,
                sample_rate=int(sample_rate),
                voice=selected_voice,
            )
        except Exception as exc:
            raise TTSError(f"failed to decode Edge TTS audio stream: {exc}") from exc

    def describe(self) -> dict[str, Any]:
        return {
            "engine": self.name,
            "available": self.available(),
            "voice": self._voice,
        }


# --------------------------------------------------------------------------
# Windows SAPI: the fallback
# --------------------------------------------------------------------------


class SapiEngine(TTSEngine):
    name = "sapi"

    def available(self) -> bool:
        import platform

        return platform.system() == "Windows"

    def _synthesize(self, text: str) -> Speech:
        if not self.available():
            raise TTSError("the Windows fallback voice is only available on Windows")

        with tempfile.TemporaryDirectory(prefix="jarvis-tts-") as folder:
            text_path = Path(folder) / "say.txt"
            wav_path = Path(folder) / "say.wav"
            text_path.write_text(text, encoding="utf-8")

            # The text is passed as a FILE, never interpolated into the script:
            # that removes quoting and quoting-escape problems entirely.
            script = _sapi_script(text_path, wav_path)
            try:
                completed = subprocess.run(
                    [
                        "powershell",
                        "-NoProfile",
                        "-NonInteractive",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-Command",
                        script,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise TTSError(f"the Windows voice failed: {exc}") from exc

            if completed.returncode != 0 or not wav_path.exists():
                detail = (completed.stderr or completed.stdout or "").strip()[:300]
                raise TTSError(f"the Windows voice failed: {detail}")

            data = wav_path.read_bytes()

        sample_rate, voice = _wav_info(data)
        return Speech(data=data, engine=self.name, sample_rate=sample_rate, voice=voice)

    def describe(self) -> dict[str, Any]:
        return {"engine": self.name, "available": self.available(), "platform": "windows"}


def _sapi_script(text_path: Path, wav_path: Path) -> str:
    preferences = ", ".join(f"'{name}'" for name in SAPI_VOICE_PREFERENCE)
    return (
        "Add-Type -AssemblyName System.Speech;"
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        f"$preferred = @({preferences});"
        "$voices = $s.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo };"
        "foreach ($name in $preferred) {"
        "  $match = $voices | Where-Object { $_.Name -like \"*$name*\" } | Select-Object -First 1;"
        "  if ($match) { $s.SelectVoice($match.Name); break }"
        "};"
        "$s.Rate = 0;"
        f"$s.SetOutputToWaveFile('{wav_path}');"
        f"$s.Speak([IO.File]::ReadAllText('{text_path}', [Text.Encoding]::UTF8));"
        "$s.Dispose();"
    )


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _to_numpy(wav: Any) -> np.ndarray:
    """Flatten a torch tensor, numpy array or list into float32 mono."""
    if hasattr(wav, "detach"):
        wav = wav.detach()
    if hasattr(wav, "cpu"):
        wav = wav.cpu()
    if hasattr(wav, "numpy"):
        wav = wav.numpy()
    samples = np.asarray(wav, dtype=np.float32)
    if samples.ndim > 1:
        samples = samples.reshape(-1) if samples.shape[0] == 1 else samples.mean(axis=0)
    return samples.astype(np.float32)


def _wav_bytes(samples: np.ndarray, sample_rate: int) -> bytes:
    clipped = np.clip(samples, -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(int(sample_rate))
        handle.writeframes(pcm.tobytes())
    return buffer.getvalue()


def _wav_info(data: bytes) -> tuple[int, str]:
    try:
        with wave.open(io.BytesIO(data), "rb") as handle:
            return int(handle.getframerate()), "Windows SAPI"
    except (wave.Error, OSError):
        return 0, "Windows SAPI"


def build_tts(settings: Settings) -> TTSEngine:
    """Pick an engine.

    - ``edge``: Microsoft Neural Studio British voice (fast, natural human inflection).
    - ``chatterbox``: Voice clone.
    - ``sapi``: Offline Windows fallback voice.
    - ``auto``: Prefers Edge Neural voice for human-like British JARVIS dialogue
      with sub-second latency; falls back to Chatterbox/SAPI.
    """
    requested = (settings.tts_engine or "auto").lower()
    edge = EdgeTTSEngine(settings)
    chatterbox = ChatterboxEngine(settings)
    sapi = SapiEngine(settings)

    if requested == "edge":
        return edge
    if requested == "chatterbox":
        return chatterbox
    if requested == "sapi":
        return sapi

    if edge.available():
        logger.info("using the neural British Jarvis voice (edge-tts: %s)", edge._voice)
        return edge
    if chatterbox.available():
        logger.info("using the cloned Jarvis voice (chatterbox)")
        return chatterbox
    if sapi.available():
        if importlib.util.find_spec("chatterbox") is not None:
            logger.info(
                "chatterbox is installed but its ~1.5 GB voice model is not local; "
                "using the Windows voice. Run scripts/build_jarvis_voice.py to fetch it."
            )
        else:
            logger.info("chatterbox not installed; using the Windows voice")
        return sapi
    return edge


def build_and_save_conds(
    ref_wav: Path,
    out_path: Path,
    *,
    device: str = "auto",
    backend: str = "turbo",
    exaggeration: float = 0.5,
) -> dict[str, Any]:
    """Validate, normalise and register a reference voice.

    Chatterbox conditions on the reference audio at generation time, so there is
    no separate conditioning tensor to persist. What this does instead is the
    part that actually matters for reproducibility: it checks the reference is
    long enough and audible, rewrites it as mono 24 kHz PCM that every code path
    can read, and records a manifest next to it so it is obvious which file is in
    use and where it came from.
    """
    import soundfile as sf

    ref_wav = Path(ref_wav)
    if not ref_wav.exists():
        raise TTSError(f"no reference audio at {ref_wav}")

    data, rate = sf.read(str(ref_wav), dtype="float32", always_2d=True)
    mono = data.mean(axis=1) if data.shape[1] > 1 else data[:, 0]
    seconds = len(mono) / float(rate or 1)
    if seconds < MIN_REFERENCE_SECONDS:
        raise TTSError(
            f"the reference clip is {seconds:.1f}s; at least {MIN_REFERENCE_SECONDS:g}s "
            "of clear speech is needed for a stable voice clone"
        )

    peak = float(np.max(np.abs(mono))) if mono.size else 0.0
    if peak < 0.01:
        raise TTSError("the reference clip is silent")

    if rate != REFERENCE_TARGET_RATE:
        duration = len(mono) / float(rate)
        target_count = int(duration * REFERENCE_TARGET_RATE)
        positions = np.linspace(0.0, duration, num=len(mono), endpoint=False)
        target = np.linspace(0.0, duration, num=target_count, endpoint=False)
        mono = np.interp(target, positions, mono).astype(np.float32)

    mono = (mono / max(peak, 1e-6) * 0.95).astype(np.float32)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out_path), mono, REFERENCE_TARGET_RATE, subtype="PCM_16")

    manifest = {
        "source": str(ref_wav),
        "normalised": str(out_path),
        "seconds": round(len(mono) / REFERENCE_TARGET_RATE, 2),
        "sample_rate": REFERENCE_TARGET_RATE,
        "backend": backend,
        "device": device,
        "exaggeration": exaggeration,
        "built_at": __import__("time").time(),
    }
    manifest_path = out_path.with_name("jarvis-conds.json")
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
