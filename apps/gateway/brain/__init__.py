"""Brain selection and failover.

Selection policy, in one place so it is auditable:

* ``offline`` mode -- only local brains. Cloud providers are not constructed at
  all, so "nothing leaves the machine" is structural rather than a promise.
* ``gemini`` -- Gemini first, Ollama behind it.
* ``ollama`` -- Ollama only, then Groq if a key happens to be set.
* ``auto`` (default) -- Gemini when a key exists, otherwise Ollama.

The router tries each brain in order and records every failover, so a mid-
conversation provider outage becomes "the cloud brain failed, I answered
locally" rather than a dead turn.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

from .base import Brain, BrainError, BrainReply, ToolCall, function_spec
from .gemini import GeminiBrain
from .groq import GroqBrain
from .llama_cpp import LlamaCppBrain, build_fallback_brain
from .ollama import OllamaBrain
from .openai_compat import OpenAICompatBrain

logger = logging.getLogger(__name__)

__all__ = [
    "Brain",
    "BrainError",
    "BrainReply",
    "BrainRouter",
    "GeminiBrain",
    "GroqBrain",
    "LlamaCppBrain",
    "OllamaBrain",
    "OpenAICompatBrain",
    "ToolCall",
    "build_brain_chain",
    "build_fallback_brain",
    "function_spec",
]


class BrainRouter(Brain):
    """Tries each brain in order, remembering which one last answered."""

    name = "router"

    def __init__(self, brains: list[Brain], *, audit: Any | None = None) -> None:
        self.brains = brains
        self.audit = audit
        self.last_used: str | None = None

    @property
    def model(self) -> str:  # type: ignore[override]
        if not self.brains:
            return ""
        return self.brains[0].model

    @property
    def is_local(self) -> bool:  # type: ignore[override]
        return all(getattr(brain, "is_local", False) for brain in self.brains)

    def _record(self, event: str, **fields: Any) -> None:
        """Record without ever letting bookkeeping break the answer.

        A failover is the moment the system is already degraded, so it must not also
        be the moment a logging or disk problem turns a recoverable outage into a
        failed turn.
        """
        if self.audit is None:
            return
        try:
            self.audit.record(event, **fields)
        except Exception as exc:
            logger.warning("could not record %s: %s", event, exc)

    async def generate(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> BrainReply:
        if not self.brains:
            return BrainReply(error="No brain is configured.")

        errors: list[str] = []
        for index, brain in enumerate(self.brains):
            try:
                reply = await brain.generate(messages, tools)
            except BrainError as exc:
                errors.append(f"{brain.name}: {exc}")
                # No ``reply`` exists on this path -- it is the one that raised. Record
                # only what the brain can tell us about itself.
                self._record(
                    "brain_failover",
                    brain=brain.name,
                    error=str(exc),
                    tried=index + 1,
                    model=str(getattr(brain, "model", "") or ""),
                    resolved_model=getattr(brain, "model_note", "") or "",
                )
                logger.warning("brain %s failed: %s", brain.name, exc)
                continue
            self.last_used = brain.name
            if errors and index > 0:
                # Answered by a fallback: worth recording, and worth a suffix the
                # gateway can surface in the UI rather than silently hiding it.
                self._record(
                    "brain_fallback",
                    used=brain.name,
                    skipped=errors,
                    model=str(getattr(reply, "model", "") or ""),
                    resolved_model=getattr(brain, "model_note", "") or "",
                    duration_ns=getattr(reply, "duration_ns", 0),
                    prompt_eval_count=getattr(reply, "prompt_eval_count", 0),
                    eval_count=getattr(reply, "eval_count", 0),
                )
                reply.error = None
                reply.raw = {"fallback_from": errors, "raw": None}
            return reply

        return BrainReply(
            error=" ".join(errors) or "No brain could answer.",
            duration_ns=0,
            prompt_eval_count=0,
            eval_count=0,
            model_note="",
        )

    def describe(self) -> dict[str, Any]:
        return {
            "mode": "router",
            "active": self.last_used or (self.brains[0].name if self.brains else None),
            "chain": [brain.describe() for brain in self.brains],
            "local_only": self.is_local,
        }

    async def aclose(self) -> None:
        for brain in self.brains:
            with contextlib.suppress(Exception):  # shutdown must not raise
                await brain.aclose()


def build_brain_chain(settings: Any, *, audit: Any | None = None) -> BrainRouter:
    """Construct the ordered brain chain for these settings."""
    mode = (settings.brain_mode or "auto").lower()
    offline = bool(settings.offline_mode)

    gemini: Brain | None = None
    if settings.gemini_api_key and not offline and mode in {"auto", "gemini"}:
        try:
            gemini = GeminiBrain(
                api_key=settings.gemini_api_key,
                model=settings.gemini_model,
                timeout=settings.brain_timeout_seconds,
            )
        except BrainError as exc:  # pragma: no cover - only on a broken install
            logger.warning("Gemini unavailable: %s", exc)
            gemini = None

    ollama: Brain | None = None
    if mode in {"auto", "ollama", "gemini"} or offline:
        ollama = OllamaBrain(
            host=settings.ollama_host,
            model=settings.ollama_model,
            timeout=settings.brain_timeout_seconds,
        )

    groq: Brain | None = None
    if settings.groq_api_key and not offline and mode in {"auto", "groq"}:
        groq = GroqBrain(api_key=settings.groq_api_key, model=settings.groq_model)

    ordered: list[Brain] = []
    if mode == "gemini":
        ordered = [gemini, ollama, groq]
    elif mode == "ollama":
        ordered = [ollama, groq]
    elif mode == "groq":
        ordered = [groq, ollama]
    else:  # auto
        ordered = [gemini, ollama] if gemini else [ollama, gemini]
        if groq:
            ordered.append(groq)

    chain = [brain for brain in ordered if brain is not None]

    # The in-process GGUF fallback goes last, and only when it could actually work:
    # llama-cpp-python installed *and* a set of weights to borrow. It exists because
    # Ollama is a single point of failure that lives outside this project -- an
    # interrupted upgrade left its server binary missing here, and a brain that
    # cannot be started is not a brain.
    append_fallback = bool(getattr(settings, "local_fallback", True))
    if append_fallback and mode in {"auto", "ollama", "gemini", "groq"}:
        fallback = build_fallback_brain(settings, model=settings.ollama_model)
        if fallback is not None:
            chain.append(fallback)

    return BrainRouter(chain, audit=audit)
