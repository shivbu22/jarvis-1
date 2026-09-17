"""JarvisService -- everything the gateway owns, assembled in one place.

Keeping construction and lifecycle here (rather than in the FastAPI module) means
the whole assistant can be started and exercised without an HTTP server, which is
what the CLI and most of the tests do. main.py is then only transport.
"""

from __future__ import annotations

import asyncio
import logging
import platform
import time
from dataclasses import dataclass
from typing import Any

from . import __version__
from .agent import Agent, TurnResult
from .audit import AuditLog
from .brain import BrainRouter, OllamaBrain, build_brain_chain
from .config import Settings, load_settings
from .confirm import ConfirmationBroker, classify_reply
from .events import EventBus
from .mcp_client import MCPManager
from .memory import MemoryStore
from .policy import Policy
from .stt import STTUnavailable, Transcript, WhisperSTT
from .tts import Speech, TTSEngine, TTSError, build_tts
from .wake import WakeDetection, WakeWordDetector

#: Warm per-sentence seconds the cloned voice is allowed before it is demoted to
#: the Windows voice. Generous: the point is to refuse conversation-killing
#: latency, not to punish merely slow machines.
VOICE_SPEED_LIMIT_SECONDS = 10.0

logger = logging.getLogger(__name__)


@dataclass
class UtteranceResult:
    transcript: str
    reply: str
    audio: bytes | None = None
    turn: TurnResult | None = None
    error: str | None = None

    def describe(self) -> dict[str, Any]:
        return {
            "transcript": self.transcript,
            "reply": self.reply,
            "has_audio": bool(self.audio),
            "error": self.error,
            "turn": self.turn.as_dict() if self.turn is not None else None,
        }


class JarvisService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        self.started_at = time.time()
        self.notes: list[str] = []

        self.audit = AuditLog(self.settings.audit_path, self.settings.audit_enabled)
        self.bus = EventBus()
        self.policy = Policy.load(self.settings.resolved_policy_path())
        self.memory = MemoryStore(self.settings.db_path)
        self.broker = ConfirmationBroker(
            self.settings.confirm_timeout_seconds, bus=self.bus, audit=self.audit
        )

        from .tools import ToolContext, build_registry

        self.tools_ctx = ToolContext(
            settings=self.settings,
            memory=self.memory,
            policy=self.policy,
            audit=self.audit,
            bus=self.bus,
        )
        self.registry = build_registry(self.policy, self.tools_ctx)

        self.brain: BrainRouter = build_brain_chain(self.settings, audit=self.audit)
        self.agent = Agent(
            settings=self.settings,
            brain=self.brain,
            registry=self.registry,
            broker=self.broker,
            audit=self.audit,
            bus=self.bus,
            memory=self.memory,
        )

        self.stt = WhisperSTT(self.settings)
        self.tts: TTSEngine = build_tts(self.settings)
        self.wake = WakeWordDetector(self.settings, self.bus)
        # Handle to the in-flight voice-speed probe, so tests can await it and
        # shutdown can cancel it instead of leaving a stray task.
        self._voice_probe: asyncio.Task[None] | None = None

        # External MCP servers contribute tools through the same registry, so
        # they inherit the same tiers, confirmation and audit trail.
        self.mcp: MCPManager | None = (
            MCPManager(self.settings.resolved_mcp_config_path(), self.registry, self.audit)
            if self.settings.mcp_enabled
            else None
        )

        if not self.policy.loaded_from_file:
            self.notes.append(
                f"policy file not loaded ({self.policy.source}); using built-in defaults"
            )

    # -- lifecycle --------------------------------------------------------

    async def start(self) -> None:
        """Pre-flight checks. Nothing here is fatal: the gateway must still serve."""
        self._resolve_ollama_model()
        self.audit.record(
            "startup",
            version=__version__,
            brain=self.brain.describe(),
            policy_source=self.policy.source,
            tools=len(self.registry.names()),
        )

        if self.mcp is not None:
            self.notes.extend(await self.mcp.start())

        if self.settings.offline_mode:
            self.notes.append("offline mode: only local brains, STT and TTS are used")
        if not self.stt.available():
            self.notes.append(
                "speech recognition is unavailable (install requirements-voice.txt); "
                "text input still works"
            )
        if not self.tts.available():
            self.notes.append("no text-to-speech engine is available; replies will be text only")

        logger.info(
            "jarvis ready | brain=%s | tools=%d | stt=%s | tts=%s",
            (self.brain.describe().get("active") or "none"),
            len(self.registry.names()),
            self.stt.available(),
            self.tts.name,
        )

        # The cloned voice must earn its place: on a CPU-only torch build a single
        # sentence can take a minute, which would silently destroy the turn latency
        # everything else works to protect. Measured, not assumed -- and the master
        # can always force it with TTS_ENGINE=chatterbox.
        if self.tts.name == "chatterbox":
            self._voice_probe = asyncio.create_task(self._probe_voice_speed())

    async def _probe_voice_speed(self) -> None:
        """Time the cloned voice on a warm model; degrade to SAPI if it is too slow.

        The first call only loads the weights; the second is the real measurement.
        The threshold is generous (a GPU box passes in a second or two) because the
        point is not to punish slow machines but to refuse *conversation-killing*
        latency while still explaining exactly why and how to get the clone back.
        """
        try:
            await asyncio.to_thread(self.tts.synthesize, "Ready.")
            started = time.perf_counter()
            await asyncio.to_thread(self.tts.synthesize, "Standing by, sir.")
            elapsed = time.perf_counter() - started
        except Exception as exc:
            self.notes.append(f"the cloned voice failed its warm-up: {exc}")
            return
        if elapsed <= VOICE_SPEED_LIMIT_SECONDS:
            self.notes.append(f"the cloned voice is live ({elapsed:.1f}s per sentence)")
            return
        try:
            from .tts import SapiEngine

            fallback = SapiEngine(self.settings)
            if fallback.available():
                self.tts = fallback
                self.notes.append(
                    f"the cloned voice needed {elapsed:.0f}s to speak one short sentence "
                    "on this machine (CPU-only torch), so replies use the Windows voice "
                    "to stay responsive. Install a CUDA build of PyTorch to unlock the "
                    "clone, or set TTS_ENGINE=chatterbox to force it."
                )
                logger.warning("cloned voice too slow (%.0fs); switched to sapi", elapsed)
        except Exception as exc:
            self.notes.append(f"voice speed probe could not fall back: {exc}")

    def _resolve_ollama_model(self) -> None:
        """If Ollama is in the chain, check the configured model really exists.

        Scheduling this as a task rather than awaiting it keeps startup fast: the
        probe has a 5 s timeout and must not delay the first /health response.
        """
        ollama = next((b for b in self.brain.brains if isinstance(b, OllamaBrain)), None)
        if ollama is None:
            return
        self._ollama_probe = asyncio.create_task(self._probe_ollama(ollama))

    async def _probe_ollama(self, ollama: OllamaBrain) -> None:
        try:
            await ollama.resolve_model()
            if ollama.model_note:
                self.notes.append(f"Ollama: {ollama.model_note}")
        except Exception as exc:
            self.notes.append(f"Ollama: {exc}")

    async def stop(self) -> None:
        cancelled = self.broker.cancel_all("shutdown")
        if cancelled:
            self.notes.append(f"refused {cancelled} unconfirmed action(s) on shutdown")
        if self.mcp is not None:
            await self.mcp.stop()
        try:
            from .tools.browser import close_browser_session

            close_browser_session(self.tools_ctx)
        except Exception:
            pass
        await self.brain.aclose()
        self.memory.close()
        self.audit.record("shutdown", uptime_seconds=round(time.time() - self.started_at, 1))

    # -- speech -----------------------------------------------------------

    async def transcribe(self, audio: bytes, *, prompt: str | None = None) -> Transcript:
        if not self.stt.available():
            raise STTUnavailable(
                "Speech recognition is not installed. Install the voice extra with "
                "'uv pip install -r requirements-voice.txt'."
            )
        return await asyncio.to_thread(self.stt.transcribe, audio, prompt=prompt)

    async def speak(self, text: str) -> bytes | None:
        """Synthesise a reply. Returns WAV bytes, or None when it cannot speak."""
        if not text.strip():
            return None
        try:
            speech: Speech = await asyncio.to_thread(self.tts.synthesize, text)
        except TTSError as exc:
            logger.warning("tts failed: %s", exc)
            if self.tts.name != "sapi":
                # One retry on the OS voice: a mute assistant is the worst outcome.
                try:
                    from .tts import SapiEngine

                    fallback = SapiEngine(self.settings)
                    if fallback.available():
                        speech = await asyncio.to_thread(fallback.synthesize, text)
                        self.notes.append(f"the cloned voice failed, used the Windows voice: {exc}")
                        return speech.data
                except TTSError:
                    pass
            self.notes.append(f"text to speech failed: {exc}")
            return None
        if self.bus is not None:
            self.bus.publish({"type": "speaking", **speech.describe()})
        return speech.data

    # -- turns ------------------------------------------------------------

    async def turn_text(
        self,
        text: str,
        *,
        session_id: str = "default",
        allow_confirmation: bool = True,
        speak: bool = True,
        on_token: Any | None = None,
    ) -> UtteranceResult:
        turn = await self.agent.run_turn(
            text,
            session_id=session_id,
            allow_confirmation=allow_confirmation,
            on_token=on_token,
        )
        audio = await self.speak(turn.reply) if speak else None
        return UtteranceResult(
            transcript=turn.transcript, reply=turn.reply, audio=audio, turn=turn, error=turn.error
        )

    async def turn_audio(
        self,
        audio: bytes,
        *,
        session_id: str = "default",
        allow_confirmation: bool = True,
        speak: bool = True,
        on_token: Any | None = None,
    ) -> UtteranceResult:
        try:
            transcript = await self.transcribe(audio)
        except STTUnavailable as exc:
            return UtteranceResult(transcript="", reply=str(exc), error="stt_unavailable")

        text = transcript.text.strip()
        if not text:
            return UtteranceResult(
                transcript="", reply="", error="no_speech",
                turn=TurnResult(transcript="", reply="", session_id=session_id),
            )
        return await self.turn_text(
            text,
            session_id=session_id,
            allow_confirmation=allow_confirmation,
            speak=speak,
            on_token=on_token,
        )

    def resolve_confirmation(self, confirmation_id: str, approved: bool, source: str = "ui") -> bool:
        return self.broker.resolve(confirmation_id, approved, source=source)

    def answer_pending_by_voice(self, text: str) -> dict[str, Any] | None:
        """Route a spoken yes/no to a held action. Returns the resolved request."""
        pending = self.broker.oldest()
        if pending is None:
            return None
        if classify_reply(text) == "unclear":
            return None
        self.broker.resolve_by_voice(text)
        return pending.as_dict()

    # -- introspection ----------------------------------------------------

    def health(self) -> dict[str, Any]:
        brain = self.brain.describe()
        active = brain.get("active")
        mode = "local" if self.settings.offline_mode or brain.get("local_only") else "cloud brain"
        return {
            "status": "ok",
            "version": __version__,
            "uptime_seconds": round(time.time() - self.started_at, 1),
            "mode": mode,
            "offline": self.settings.offline_mode,
            "brain": brain,
            "active_brain": active,
            "tools": self.registry.describe(),
            "tool_count": len(self.registry.names()),
            "stt": self.stt.describe(),
            "tts": self.tts.describe(),
            "wake": self.wake.describe(),
            # Taken from the policy itself rather than rebuilt here: the hand-built
            # copy silently drifted (it had lost sensitive_paths), and /health is
            # exactly where a human goes to check what the gate really enforces.
            "policy": self.policy.describe(),
            "mcp": self.mcp.describe() if self.mcp is not None else {"enabled": False},
            "memory": self.memory.stats(),
            "confirmations_pending": self.broker.snapshot(),
            "notes": list(self.notes),
            "platform": {
                "system": platform.system(),
                "release": platform.release(),
                "root": str(self.settings.root),
                "data_dir": str(self.settings.data_dir),
                "host": self.settings.host,
                "port": self.settings.port,
            },
        }

    def transcript(self, text: str) -> str:
        return " ".join((text or "").split())


__all__ = [
    "JarvisService",
    "Speech",
    "Transcript",
    "TurnResult",
    "UtteranceResult",
    "WakeDetection",
]
