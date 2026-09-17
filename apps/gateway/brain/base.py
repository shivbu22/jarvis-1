"""The brain interface.

One canonical conversation format -- OpenAI-style message dicts -- is the hub
every provider is adapted to, so the agent loop never learns which model it is
talking to. Messages are:

    {"role": "system",    "content": str}
    {"role": "user",      "content": str}
    {"role": "assistant", "content": str, "tool_calls": [ {...}, ... ]}
    {"role": "tool",      "tool_call_id": str, "name": str, "content": str}

Tool definitions are always JSON-Schema function specs (the OpenAI shape); each
provider converts to its own format at the edge.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class BrainError(RuntimeError):
    """Raised when a provider cannot answer. Carries a message fit to speak."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    raw: Any = None


@dataclass
class BrainReply:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str = ""
    error: str | None = None
    raw: Any = None

    #Timing info from the provider, when available. Ollama's chat endpoint returns
    # ``total_duration`` in nanoseconds; Jarvis records it on the turn so the audit
    # trail can show real latency rather than just wall-clock.
    duration_ns: int = 0
    prompt_eval_count: int = 0
    eval_count: int = 0
    # The model the brain actually used this turn (final resolved name). For
    # OpenAI-compatible providers this is supplied by the ``model`` field if present;
    # the generic layer leaves it empty when the provider did not report one.
    model_note: str = ""
    # Why the provider stopped, when it says ("stop", "tool_calls", "length").
    # Streaming fills this in; a plain generate usually leaves it empty, and the
    # agent loop treats empty as "no opinion". "length" is how a truncated reply
    # announces itself -- the signature of a token cap set too low.
    finish_reason: str = ""

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)

    def as_message(self) -> dict[str, Any]:
        """Render as the canonical assistant message, for the next round trip."""
        message: dict[str, Any] = {"role": "assistant", "content": self.text or ""}
        if self.tool_calls:
            message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                }
                for call in self.tool_calls
            ]
        return message


class Brain(ABC):
    """A chat model that can call tools."""

    name: str = "brain"
    model: str = ""
    is_local: bool = False

    @abstractmethod
    async def generate(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> BrainReply:
        """Produce the next assistant message, with tool calls if it wants them."""

    async def aclose(self) -> None:  # noqa: B027 - a default no-op is intentional:
        # brains that hold no resources should not be forced to write an empty
        # override just to satisfy the base class.
        """Release network resources. Safe to call twice."""

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "model": self.model, "local": self.is_local}


_THINK_BLOCK = re.compile(r"<think(?:ing)?>.*?</think(?:ing)?>", re.DOTALL | re.IGNORECASE)


def clean_text(text: str | None) -> str:
    """Strip reasoning blocks some local models emit inline."""
    if not text:
        return ""
    return _THINK_BLOCK.sub("", text).strip()


def parse_arguments(raw: Any) -> dict[str, Any]:
    """Best-effort parse of model-supplied tool arguments.

    Models sometimes emit arguments as a JSON string, sometimes as an object, and
    occasionally with trailing prose. A malformed call must never crash a turn --
    it degrades to an empty argument set, and the tool reports the missing field.
    """
    if isinstance(raw, dict):
        return raw
    if raw is None:
        return {}
    if not isinstance(raw, str):
        return {}
    text = raw.strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return {}
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def function_spec(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    """Wrap a JSON Schema into the canonical tool definition."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }
