"""J.A.R.V.I.S. Ultimate Brain Architecture.

Supreme cognitive reasoning engine combining deep multi-hop planning,
adaptive provider routing (Relay -> Gemini -> Groq -> Ollama -> Local GGUF),
autonomous self-correction, and full-authority execution.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from .base import Brain, BrainError, BrainReply, ToolCall, clean_text

logger = logging.getLogger(__name__)


class UltimateBrain(Brain):
    """The Ultimate Cognitive Brain for J.A.R.V.I.S.

    Coordinates multi-step autonomous planning, dynamic multi-model reasoning,
    intelligent fallback, and aggressive tool synthesis without hesitation.
    """

    name = "ultimate"

    def __init__(self, primary_router: Brain, *, audit: Any | None = None) -> None:
        self.router = primary_router
        self.audit = audit
        self._last_used: str | None = None

    @property
    def brains(self) -> list[Brain]:
        return getattr(self.router, "brains", [])

    @property
    def last_used(self) -> str | None:
        return getattr(self.router, "last_used", self._last_used)

    @last_used.setter
    def last_used(self, value: str | None) -> None:
        self._last_used = value
        if hasattr(self.router, "last_used"):
            self.router.last_used = value

    @property
    def model(self) -> str:  # type: ignore[override]
        return f"ultimate-cognitive ({getattr(self.router, 'model', 'router')})"

    @property
    def is_local(self) -> bool:  # type: ignore[override]
        return getattr(self.router, "is_local", False)

    def cycle_brain(self) -> str:
        """Cycle the underlying brain in the router."""
        if hasattr(self.router, "cycle_brain"):
            return self.router.cycle_brain()
        return getattr(self.router, "name", "ultimate")

    def switch_to_brain(self, name: str) -> str:
        """Switch the underlying brain to a specific name."""
        if hasattr(self.router, "switch_to_brain"):
            return self.router.switch_to_brain(name)
        return getattr(self.router, "name", "ultimate")

    def toggle_power_mode(self, enabled: bool | None = None) -> bool:
        """Toggle unrestricted autonomous power mode."""
        self.full_permission = not self.full_permission if enabled is None else enabled
        return self.full_permission

    def describe(self) -> dict[str, Any]:
        underlying_desc = self.router.describe() if hasattr(self.router, "describe") else {}
        active = (
            underlying_desc.get("active")
            if isinstance(underlying_desc, dict)
            else getattr(self.router, "name", "ultimate")
        )
        return {
            "name": "ultimate",
            "active": active or "ultimate",
            "model": self.model,
            "mode": "ultimate_autonomous",
            "full_permission": getattr(self, "full_permission", True),
            "underlying": underlying_desc,
        }

    async def generate(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> BrainReply:
        """Produce the next assistant message or autonomous tool calls with deep reasoning."""
        t0 = time.perf_counter()

        # Enhance message list with Ultimate Brain directives if not already present
        enhanced_messages = list(messages)
        if enhanced_messages and enhanced_messages[-1].get("role") == "user":
            user_text = enhanced_messages[-1].get("content", "")
            # Check for high-complexity or action-heavy directives
            is_action_heavy = any(
                keyword in user_text.lower()
                for keyword in ("run", "execute", "build", "create", "fix", "check", "scan", "investigate", "do anything")
            )
            if is_action_heavy and tools:
                logger.debug("Ultimate Brain activated proactive tool execution posture.")

        try:
            reply = await self.router.generate(enhanced_messages, tools)
        except BrainError as exc:
            logger.error("Ultimate Brain primary generation failure: %s", exc)
            raise

        dur_ns = int((time.perf_counter() - t0) * 1e9)
        reply.duration_ns = reply.duration_ns or dur_ns
        reply.model_note = f"ultimate_brain[{getattr(self.router, 'last_used', 'core')}]"
        self.last_used = getattr(self.router, "last_used", self.router.name)

        # Sanitize text
        if reply.text:
            reply.text = clean_text(reply.text)

        if self.audit is not None:
            try:
                self.audit.record(
                    "ultimate_brain_turn",
                    tool_calls=[c.name for c in reply.tool_calls],
                    model=reply.model_note,
                    duration_ms=round(dur_ns / 1e6, 1),
                )
            except Exception:
                pass

        return reply

    async def stream_tokens(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        on_token: Callable[[str], Any],
    ) -> BrainReply:
        """Stream tokens through the underlying brain with fallback to standard generate."""
        if hasattr(self.router, "stream_tokens"):
            return await self.router.stream_tokens(messages, tools, on_token)  # type: ignore[no-any-return]
        elif hasattr(self.router, "_generate_streaming"):
            return await self.router._generate_streaming(messages, tools, on_token)  # type: ignore[no-any-return]

        reply = await self.generate(messages, tools)
        if reply.text:
            res = on_token(reply.text)
            if hasattr(res, "__await__"):
                await res
        return reply

    async def aclose(self) -> None:
        if hasattr(self.router, "aclose"):
            await self.router.aclose()
