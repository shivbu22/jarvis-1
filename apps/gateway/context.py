"""Context window manager for J.A.R.V.I.S.

Prevents the LLM context from overflowing by:

1. **Token counting** — estimates token usage before sending.
2. **Sliding window** — keeps the most recent N turns, summarising older ones.
3. **Auto-summarisation** — condenses old context into a compact summary note
   that carries forward the important facts without the raw transcript.

This sits between the memory store and the brain, shaping what enters the
prompt without losing long-term continuity.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("jarvis.context")

# Rough token estimate: 1 token ≈ 4 characters for English text.
# This is intentionally conservative (real tokenizers average 3.5-4.5).
CHARS_PER_TOKEN = 4

# Default context budget — leaves room for system prompt + tool definitions.
DEFAULT_MAX_TOKENS = 6000
DEFAULT_KEEP_RECENT = 6  # keep this many raw turns before summarising
DEFAULT_SUMMARY_TARGET = 400  # target summary length in tokens


def estimate_tokens(text: str) -> int:
    """Fast character-based token estimate."""
    return max(1, len(text) // CHARS_PER_TOKEN)


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    """Estimate total tokens across a message list."""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    total += estimate_tokens(str(part.get("text", "")))
        total += 4  # role/framing overhead per message
    return total


class ContextWindowManager:
    """Manages conversation context to stay within LLM token limits."""

    def __init__(
        self,
        max_context_tokens: int = DEFAULT_MAX_TOKENS,
        keep_recent: int = DEFAULT_KEEP_RECENT,
        summary_target_tokens: int = DEFAULT_SUMMARY_TARGET,
    ) -> None:
        self.max_context_tokens = max_context_tokens
        self.keep_recent = keep_recent
        self.summary_target_tokens = summary_target_tokens
        self._running_summary: str = ""
        self._total_turns_processed: int = 0
        self._total_tokens_saved: int = 0

    def prepare_messages(
        self,
        system_prompt: str,
        conversation_history: list[dict[str, Any]],
        tool_definitions_tokens: int = 800,
    ) -> list[dict[str, Any]]:
        """Build a context-safe message list that fits within the token budget.

        Returns a list of messages ready for the brain, with old history
        compressed into a summary when needed.
        """
        system_tokens = estimate_tokens(system_prompt)
        budget = self.max_context_tokens - system_tokens - tool_definitions_tokens

        if budget < 500:
            logger.warning("Context budget very tight: %d tokens after system prompt", budget)
            budget = 500

        messages: list[dict[str, Any]] = []

        # Always include the running summary if we have one
        if self._running_summary:
            summary_msg = {
                "role": "system",
                "content": f"[Conversation context summary]\n{self._running_summary}",
            }
            messages.append(summary_msg)
            budget -= estimate_tokens(self._running_summary) + 20

        # Fit as many recent turns as possible within budget
        recent = conversation_history[-self.keep_recent * 2:]  # user+assistant pairs
        fitted: list[dict[str, Any]] = []
        tokens_used = 0

        for msg in reversed(recent):
            msg_tokens = estimate_tokens(msg.get("content", ""))
            if tokens_used + msg_tokens > budget:
                break
            fitted.insert(0, msg)
            tokens_used += msg_tokens

        # If we had to drop messages, update the running summary
        dropped_count = len(conversation_history) - len(fitted)
        if dropped_count > 0 and len(conversation_history) > self.keep_recent * 2:
            dropped = conversation_history[:dropped_count]
            self._update_summary(dropped)
            self._total_tokens_saved += sum(
                estimate_tokens(m.get("content", "")) for m in dropped
            )

        messages.extend(fitted)
        self._total_turns_processed = len(conversation_history)

        return messages

    def _update_summary(self, dropped_messages: list[dict[str, Any]]) -> None:
        """Condense dropped messages into the running summary.

        Uses extractive summarisation (key facts, entities, decisions) rather
        than generative — no LLM call, just fast text processing.
        """
        key_facts: list[str] = []

        for msg in dropped_messages:
            content = msg.get("content", "")
            if not content or not isinstance(content, str):
                continue
            role = msg.get("role", "")

            # Extract sentences with high information density
            sentences = re.split(r'[.!?]\s+', content)
            for sent in sentences:
                sent = sent.strip()
                if len(sent) < 10:
                    continue
                # Prioritise sentences with: names, numbers, decisions, instructions
                info_score = 0
                if re.search(r'\b[A-Z][a-z]+\b', sent):  # proper nouns
                    info_score += 1
                if re.search(r'\d+', sent):  # numbers
                    info_score += 1
                if re.search(r'\b(remember|note|important|always|never|preference|want|need)\b', sent, re.I):
                    info_score += 2
                if re.search(r'\b(decided|agreed|confirmed|set|changed|updated)\b', sent, re.I):
                    info_score += 2
                if role == "user":
                    info_score += 1  # user messages are more important

                if info_score >= 2:
                    prefix = "User" if role == "user" else "Jarvis"
                    key_facts.append(f"- {prefix}: {sent[:200]}")

        if key_facts:
            # Merge with existing summary, keeping it bounded
            new_facts = "\n".join(key_facts[-15:])  # keep last 15 key facts
            if self._running_summary:
                # Keep the most recent summary lines + new facts
                existing_lines = self._running_summary.strip().split("\n")
                combined = existing_lines[-10:] + key_facts[-15:]
                self._running_summary = "\n".join(combined[-20:])
            else:
                self._running_summary = new_facts

            # Hard-cap the summary
            while estimate_tokens(self._running_summary) > self.summary_target_tokens:
                lines = self._running_summary.split("\n")
                if len(lines) <= 2:
                    break
                self._running_summary = "\n".join(lines[1:])  # drop oldest line

    def get_summary(self) -> str:
        """Return the current running context summary."""
        return self._running_summary

    def clear(self) -> None:
        """Reset the context manager state."""
        self._running_summary = ""
        self._total_turns_processed = 0
        self._total_tokens_saved = 0

    def get_telemetry(self) -> dict[str, Any]:
        return {
            "running_summary_tokens": estimate_tokens(self._running_summary),
            "total_turns_processed": self._total_turns_processed,
            "total_tokens_saved": self._total_tokens_saved,
            "max_context_tokens": self.max_context_tokens,
            "keep_recent": self.keep_recent,
            "has_summary": bool(self._running_summary),
        }

    @property
    def stats(self) -> dict[str, Any]:
        """Telemetry stats alias."""
        return self.get_telemetry()
