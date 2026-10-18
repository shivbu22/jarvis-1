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
from dataclasses import dataclass, field
from typing import Any

from . import __version__
from .agent import Agent, TurnResult
from .audit import AuditLog
from .brain import BrainRouter, OllamaBrain, build_brain_chain
from .config import Settings, load_settings
from .confirm import ConfirmationBroker, classify_reply
from .context import ContextWindowManager
from .events import EventBus
from .interactions import InteractionBroker
from .mcp_client import MCPManager
from .memory import MemoryStore
from .notify import NotificationManager
from .policy import Policy
from .proactive import ProactiveGuardian
from .profile import UserProfile
from .scheduler import TaskScheduler
from .stt import STTUnavailable, Transcript, WhisperSTT
from .tts import Speech, SpeechChunker, TTSEngine, TTSError, build_tts
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
    #: True when speech was handed out chunk by chunk while the answer was still
    #: being written, rather than as one WAV at the end.
    speech_streamed: bool = False
    #: Set when the streamed speech did not match the final reply (a brain
    #: failover mid-sentence), so a caller can tell that it heard a fragment.
    speech_incomplete: bool = False
    #: Where the turn's time went, in seconds: transcribe, think, speak. A
    #: stage the turn never used is simply absent. Displayed in the orb so a
    #: slow stage is visible at a glance instead of hidden inside a total.
    stages: dict[str, float] = field(default_factory=dict)

    def describe(self) -> dict[str, Any]:
        return {
            "transcript": self.transcript,
            "reply": self.reply,
            "has_audio": bool(self.audio),
            "error": self.error,
            "speech_streamed": self.speech_streamed,
            "speech_incomplete": self.speech_incomplete,
            "stages": {name: round(value, 2) for name, value in self.stages.items()},
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
        if getattr(self.settings, "ultimate_power", False):
            self.policy.grant_ultimate_permission(True)
        self.memory = MemoryStore(self.settings.db_path)
        self.broker = ConfirmationBroker(
            self.settings.confirm_timeout_seconds, bus=self.bus, audit=self.audit
        )
        # Questions are the other half of the gate: permission is yes/no, a
        # question is words. Both hold the turn open, and both refuse on silence.
        self.interactions = InteractionBroker(
            self.settings.question_timeout_seconds,
            bus=self.bus,
            audit=self.audit,
            speak=self._speak_question,
        )

        from .self_heal import SelfHealer
        from .tools import ToolContext, build_registry

        self.tools_ctx = ToolContext(
            settings=self.settings,
            memory=self.memory,
            policy=self.policy,
            audit=self.audit,
            bus=self.bus,
            service=self,
        )
        # The ask_user tool needs the broker, but tools must stay constructible
        # without a running service (the CLI and tests build them directly), so it
        # is handed over through the context instead of imported.
        self.tools_ctx.state["ask_user"] = self.interactions.ask
        self.registry = build_registry(self.policy, self.tools_ctx)

        self.self_healer = SelfHealer(service=self)
        self.guardian = ProactiveGuardian(service=self)

        # -- new subsystems: scheduler, notifications, context, profile ----
        from pathlib import Path as _Path
        _data_dir = _Path(self.settings.db_path).parent

        self.profile = UserProfile(_data_dir / "user_profile.json")
        self.context_mgr = ContextWindowManager(
            max_context_tokens=6000,
            keep_recent=6,
        )
        self.notifier = NotificationManager(bus=self.bus)

        async def _on_task_fire(task: Any) -> None:
            """Callback when a scheduled task fires."""
            if task.tool_name and hasattr(self, 'registry'):
                try:
                    await self.registry.call(task.tool_name, task.tool_args, confirmed=True)
                except Exception as exc:
                    logger.warning("Scheduled tool %s failed: %s", task.tool_name, exc)
            if task.message:
                await self.notifier.send(
                    title="J.A.R.V.I.S. Reminder",
                    message=task.message,
                    urgency="normal",
                    speak=True,
                )

        self.scheduler = TaskScheduler(
            db_path=_data_dir / "scheduler.db",
            on_fire=_on_task_fire,
        )

        from .evolution import EvolutionEngine
        self.evolution = EvolutionEngine(_data_dir / "evolution_rules.json")

        self.brain: BrainRouter = build_brain_chain(self.settings, audit=self.audit)
        self.agent = Agent(
            settings=self.settings,
            brain=self.brain,
            registry=self.registry,
            broker=self.broker,
            audit=self.audit,
            bus=self.bus,
            memory=self.memory,
            profile=self.profile,
            context_mgr=self.context_mgr,
            evolution=self.evolution,
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

        # Pre-load the whisper model so the first voice turn doesn't pay a
        # 3-8 second cold-start penalty. Runs in a thread so the health
        # endpoint is reachable immediately.
        if self.stt.available():
            try:
                await asyncio.to_thread(self.stt._ensure)
                self.notes.append(
                    f"whisper pre-loaded ({self.stt._device}/{self.stt._compute_type}, "
                    f"model={self.settings.whisper_model})"
                )
            except Exception as exc:
                self.notes.append(f"whisper pre-load failed: {exc}")

        # The cloned voice must earn its place: on a CPU-only torch build a single
        # sentence can take a minute, which would silently destroy the turn latency
        # everything else works to protect. Measured, not assumed -- and the master
        # can always force it with TTS_ENGINE=chatterbox.
        if self.tts.name == "chatterbox":
            self._voice_probe = asyncio.create_task(self._probe_voice_speed())
        elif self.tts.name == "edge":
            v = self.tts.describe().get("voice", "en-GB-RyanNeural")
            self.notes.append(f"neural British Jarvis voice is live ({v})")

        # Launch autonomous self-healing watchdog loop and proactive guardian
        await self.self_healer.start_watchdog(interval_seconds=60.0)
        await self.guardian.start(interval_seconds=30.0)
        await self.scheduler.start()
        logger.info(
            "scheduler started | profile=%s | context_window=%d tokens",
            self.profile.get("preferred_name") or "default",
            self.context_mgr.max_context_tokens,
        )

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
            if await ollama.warm_up():
                self.notes.append(f"Ollama {ollama.model} pinned in VRAM")
        except Exception as exc:
            self.notes.append(f"Ollama: {exc}")

    async def stop(self) -> None:
        await self.scheduler.stop()
        await self.guardian.stop()
        await self.self_healer.stop_watchdog()
        cancelled = self.broker.cancel_all("shutdown")
        if cancelled:
            self.notes.append(f"refused {cancelled} unconfirmed action(s) on shutdown")
        asked = self.interactions.cancel_all("shutdown")
        if asked:
            self.notes.append(f"released {asked} unanswered question(s) on shutdown")
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
        on_speech: Any | None = None,
        on_round: Any | None = None,
    ) -> UtteranceResult:
        """Run one text turn, optionally speaking it while it is still forming.

        ``on_speech`` changes the shape of the reply, not just the timing: speech
        is synthesised and handed out in chunks as the answer streams, instead of
        one WAV after the whole reply exists. That is the difference between
        silence for as long as the answer takes to write *and* say, and the master
        hearing the first sentence while the rest is still being written.
        """
        chunker = SpeechChunker() if (speak and on_speech is not None) else None
        #: Text of the round currently being generated, and of the round that was
        #: spoken. Only the prose round is ever spoken: a tool round's narration is
        #: not the answer, and saying it out loud would be worse than silence.
        round_text: list[str] = []
        spoken_text: list[str] = []
        #: Stage timings for this turn, reported to the orb so a slow stage is
        #: visible instead of hidden inside a total.
        stages: dict[str, float] = {}
        think_started = time.monotonic()

        async def speak_now(text_to_speak: str) -> None:
            nonlocal think_started
            if "think" not in stages:
                stages["think"] = time.monotonic() - think_started
            speak_started = time.monotonic()
            spoken_text.append(text_to_speak)
            for chunk in chunker.push(text_to_speak):
                await on_speech(chunk)
            stages["first_sound"] = time.monotonic() - speak_started

        async def relay(piece: str) -> None:
            if on_token is not None:
                await on_token(piece)
            if chunker is not None:
                round_text.append(piece)

        async def round_marker(kind: str, _iteration: int) -> None:
            if chunker is None:
                return
            if kind == "start":
                round_text.clear()
                return
            if kind == "retract":
                # The round's streamed text turned out to be tool-call JSON;
                # whatever of it is pending must never reach the speaker.
                round_text.clear()
                return
            if kind == "prose":
                pending = "".join(round_text)
                round_text.clear()
                if pending.strip():
                    await speak_now(pending)

        #: The transport may watch rounds itself (e.g. to retract a streamed
        #: round that turned out to be tool-call JSON); its callback is composed
        #: with the speech chunker's own round handling rather than replacing it.
        async def on_round_event(kind: str, iteration: int) -> None:
            await round_marker(kind, iteration)
            if on_round is not None:
                result = on_round(kind, iteration)
                if hasattr(result, "__await__"):
                    await result

        turn = await self.agent.run_turn(
            text,
            session_id=session_id,
            allow_confirmation=allow_confirmation,
            on_token=relay if (on_token is not None or chunker is not None) else None,
            on_round=on_round_event if (chunker is not None or on_round is not None) else None,
        )
        # Record the brain's cost even when nothing is spoken with it: a silent
        # turn still has a think stage, and hiding it would hide the very thing
        # to optimise.
        if "think" not in stages:
            stages["think"] = time.monotonic() - think_started

        if chunker is not None and spoken_text:
            for chunk in chunker.flush():
                await on_speech(chunk)

        speech_streamed = bool(spoken_text)
        # Speak the reply whole only when nothing was spoken from the stream -- a
        # brain that cannot stream, a round that hit the iteration cap, or a turn
        # whose answer arrived without any prose round at all.
        audio = None
        if speak and not speech_streamed:
            if "think" not in stages:
                stages["think"] = time.monotonic() - think_started
            speak_started = time.monotonic()
            audio = await self.speak(turn.reply)
            if audio is not None:
                stages["first_sound"] = time.monotonic() - speak_started
        spoken = " ".join(" ".join(spoken_text).split())
        incomplete = bool(speech_streamed and spoken and spoken != turn.reply.strip())
        if incomplete:
            logger.warning("spoken text did not match the reply: %r vs %r", spoken, turn.reply)
        return UtteranceResult(
            transcript=turn.transcript,
            reply=turn.reply,
            audio=audio,
            turn=turn,
            error=turn.error,
            speech_streamed=speech_streamed,
            speech_incomplete=incomplete,
            stages=stages,
        )

    async def turn_audio(
        self,
        audio: bytes,
        *,
        session_id: str = "default",
        allow_confirmation: bool = True,
        speak: bool = True,
        on_token: Any | None = None,
        on_speech: Any | None = None,
        on_round: Any | None = None,
    ) -> UtteranceResult:
        transcribe_started = time.monotonic()
        try:
            transcript = await self.transcribe(audio)
        except STTUnavailable as exc:
            return UtteranceResult(transcript="", reply=str(exc), error="stt_unavailable")
        transcribe_seconds = time.monotonic() - transcribe_started

        text = transcript.text.strip()
        if not text:
            # Even a turn that dies here had ears: report what they cost.
            return UtteranceResult(
                transcript="", reply="", error="no_speech",
                turn=TurnResult(transcript="", reply="", session_id=session_id),
                stages={"transcribe": transcribe_seconds},
            )

        # If a question is open, this is the answer to it. The turn that asked is
        # still suspended waiting for these words; answering it here is what lets
        # that turn finish, and it is why speaking the answer works at all.
        if self.interactions.pending() and self.interactions.resolve_by_voice(text) is not None:
            return UtteranceResult(
                transcript=text, reply="", error="answered_question",
                stages={"transcribe": transcribe_seconds},
            )

        result = await self.turn_text(
            text,
            session_id=session_id,
            allow_confirmation=allow_confirmation,
            speak=speak,
            on_token=on_token,
            on_speech=on_speech,
            on_round=on_round,
        )
        if transcribe_seconds > 0:
            result.stages["transcribe"] = transcribe_seconds
        return result

    async def _speak_question(self, question: str) -> None:
        """Say a question out loud as soon as it is asked.

        Published as an ``audio`` frame rather than played inline, so it reaches
        whichever clients are attached -- including the orb, which owns playback.
        """
        wav = await self.speak(question)
        if not wav or self.bus is None:
            return
        import base64

        self.bus.publish(
            {
                "type": "audio",
                "b64": base64.b64encode(wav).decode("ascii"),
                "text": question,
                "kind": "question",
            }
        )

    def resolve_confirmation(self, confirmation_id: str, approved: bool, source: str = "ui") -> bool:
        return self.broker.resolve(confirmation_id, approved, source=source)

    def answer_question(self, question_id: str, text: str, source: str = "ui") -> bool:
        """Answer a held question, letting the waiting turn continue."""
        return self.interactions.resolve(question_id, text, source=source)

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
        active = (
            brain.get("active")
            or (brain.get("underlying", {}).get("active") if isinstance(brain.get("underlying"), dict) else None)
            or brain.get("name")
            or "router"
        )
        mode = "local" if self.settings.offline_mode or brain.get("local_only") else "cloud brain"

        # Hardware telemetry
        hw: dict[str, Any] = {
            "cpu_percent": 0.0,
            "ram_percent": 0.0,
            "ram_gb": "0/0",
            "ram_used_gb": 0.0,
            "ram_total_gb": 0.0,
            "gpu": "CPU",
            "gpu_name": "CPU",
            "vram_mb": "0/0",
            "gpu_memory_used_mb": 0,
            "gpu_memory_total_mb": 0,
            "cuda": False,
        }
        try:
            import psutil
            vm = psutil.virtual_memory()
            used_gb = round(vm.used / (1024**3), 1)
            total_gb = round(vm.total / (1024**3), 1)
            hw["cpu_percent"] = round(psutil.cpu_percent(), 1)
            hw["ram_percent"] = round(vm.percent, 1)
            hw["ram_used_gb"] = used_gb
            hw["ram_total_gb"] = total_gb
            hw["ram_gb"] = f"{used_gb}/{total_gb} GB"
        except Exception:
            pass

        try:
            import torch
            if torch.cuda.is_available():
                gpu_name = torch.cuda.get_device_name(0)
                tot_mb = torch.cuda.get_device_properties(0).total_memory // (1024 * 1024)
                alloc_mb = torch.cuda.memory_allocated(0) // (1024 * 1024)
                hw["gpu"] = gpu_name
                hw["gpu_name"] = gpu_name
                hw["gpu_memory_used_mb"] = alloc_mb
                hw["gpu_memory_total_mb"] = tot_mb
                hw["vram_mb"] = f"{alloc_mb}/{tot_mb} MB"
                hw["cuda"] = True
            else:
                hw["cuda"] = False
        except Exception:
            pass

        # Subagents stats
        sub_stats = {"total": 0, "active": 0, "completed": 0, "failed": 0}
        try:
            from .tools.subagents import SubagentSwarmManager
            mgr = SubagentSwarmManager.get_instance()
            sub_stats["total"] = len(mgr.records)
            sub_stats["active"] = sum(1 for r in mgr.records.values() if r.status == "running")
            sub_stats["completed"] = sum(1 for r in mgr.records.values() if r.status == "completed")
            sub_stats["failed"] = sum(1 for r in mgr.records.values() if r.status in ("failed", "timed_out"))
        except Exception:
            pass

        # Obsidian Brain stats
        vault_stats = {"total_notes": 0, "vault_path": ""}
        try:
            from .tools.obsidian_brain import VAULT_PATH
            if VAULT_PATH.exists():
                vault_stats["total_notes"] = len(list(VAULT_PATH.rglob("*.md")))
                vault_stats["vault_path"] = str(VAULT_PATH)
        except Exception:
            pass

        # Self-Heal Telemetry
        self_heal_telemetry = {}
        try:
            if hasattr(self, "self_healer") and self.self_healer:
                self_heal_telemetry = self.self_healer.get_telemetry()
        except Exception:
            pass

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
            "hardware": hw,
            "cpu_percent": hw["cpu_percent"],
            "ram_percent": hw["ram_percent"],
            "ram_used_gb": hw["ram_used_gb"],
            "ram_total_gb": hw["ram_total_gb"],
            "gpu_name": hw["gpu_name"],
            "gpu_memory_used_mb": hw["gpu_memory_used_mb"],
            "gpu_memory_total_mb": hw["gpu_memory_total_mb"],
            "cuda_available": hw["cuda"],
            "subagents": sub_stats,
            "obsidian_brain": vault_stats,
            "self_heal": self_heal_telemetry,
            "proactive": self.guardian.get_telemetry() if hasattr(self, "guardian") and self.guardian else {},
            "evolution": {
                "rules_count": len(self.evolution.list_rules()),
                "rules": self.evolution.list_rules()[:10],
            } if hasattr(self, "evolution") and self.evolution else {},
            # Taken from the policy itself rather than rebuilt here: the hand-built
            # copy silently drifted (it had lost sensitive_paths), and /health is
            # exactly where a human goes to check what the gate really enforces.
            "policy": self.policy.describe(),
            "mcp": self.mcp.describe() if self.mcp is not None else {"enabled": False},
            "memory": self.memory.stats(),
            "confirmations_pending": self.broker.snapshot(),
            "questions_pending": self.interactions.snapshot(),
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
