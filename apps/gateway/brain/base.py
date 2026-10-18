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
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


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

#: Some local models continue the transcript instead of answering, emitting the
#: next speaker's name first ("assistant\n\nI am well, sir."). The prefix is not
#: something to say out loud, and this output path is a speaker.
#:
#: Two accepted forms, and nothing else: a label followed by punctuation, or a
#: label on its own line. Requiring one of those is what keeps "systemd is
#: running" and "assistant is running on this machine" intact.
_ROLE_PREFIX = re.compile(
    r"^\s*(?:assistant|jarvis|ai|system)\s*(?:[:,]\s*|[-\u2013]\s*|\n+)\s*", re.IGNORECASE
)

#: A reply that is *nothing but* a speaker label. Observed from a small local
#: model under pressure: the entire answer was the token "assistant". It is not a
#: sentence, and saying it aloud is worse than admitting the failure -- emptying it
#: here sends the agent down its "ask again" path instead.
_BARE_ROLE = re.compile(r"^(?:assistant|jarvis|ai|system)\s*[:,]?\s*$", re.IGNORECASE)


def clean_text(text: str | None) -> str:
    """Strip reasoning blocks and role labels that local models emit inline."""
    if not text:
        return ""
    cleaned = _THINK_BLOCK.sub("", text).strip()
    previous = None
    while previous != cleaned:
        previous = cleaned
        cleaned = _ROLE_PREFIX.sub("", cleaned).strip()
    if _BARE_ROLE.match(cleaned):
        return ""
    return cleaned


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
        parsed = json.loads(_clean_json_relaxed(text))
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return {}
        try:
            parsed = json.loads(_clean_json_relaxed(match.group(0)))
        except json.JSONDecodeError:
            return {}
    return parsed if isinstance(parsed, dict) else {}


#: Keys a model might use when it writes a tool call out as plain text. Small
#: local models do this instead of using the tool-calling API: they print the JSON
#: they were shown in the prompt. The reply then looks like an answer, and its
#: content is spoken aloud -- which is how a greeting once came out of the speaker
#: as ``{"name":"ask_user","parameters":"How are you?"}``.
_TOOL_NAME_KEYS = ("name", "tool", "tool_name", "function")
_TOOL_ARG_KEYS = ("parameters", "arguments", "args", "input")


def _unfence(text: str) -> str:
    """Strip a markdown code fence, which models add even when told not to."""
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```[a-zA-Z]*\s*", "", candidate)
        candidate = re.sub(r"\s*```$", "", candidate)
    return candidate.strip()


#: A tool-object key, used to decide whether a brace in prose is worth parsing.
#: Requiring this shape is what keeps "remember the {thing} I said" from being
#: read as a tool call.
_TOOL_SHAPE = re.compile(r'\{\s*"(?:name|tool|tool_name|function)"\s*:', re.IGNORECASE)


def _clean_json_relaxed(text: str) -> str:
    """Strip trailing commas that small models emit before closing braces/brackets."""
    return re.sub(r",\s*([\]\}])", r"\1", text)


def _first_json_object(text: str) -> dict[str, Any] | None:
    candidate = _unfence(text)
    # A model may preface it ("Sure! {...}"): only look past a non-brace opening
    # when the text really does contain tool-call syntax.
    if not candidate.startswith("{") and not _TOOL_SHAPE.search(candidate):
        return None
    cleaned = _clean_json_relaxed(candidate)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = _TOOL_SHAPE.search(candidate)
        if not match:
            return None
        # Walk outwards to the matching closing brace of that object.
        depth = 0
        end = -1
        for position in range(match.start(), len(candidate)):
            if candidate[position] == "{":
                depth += 1
            elif candidate[position] == "}":
                depth -= 1
                if depth == 0:
                    end = position + 1
                    break
        if end == -1:
            return None
        try:
            sub = _clean_json_relaxed(candidate[match.start() : end])
            parsed = json.loads(sub)
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


def parse_args_string(arg_str: str) -> dict[str, Any] | str:
    """Extract key-value arguments or bare string from text tool invocation."""
    arg_str = arg_str.strip()
    if not arg_str:
        return {}
    if arg_str.startswith("{") and arg_str.endswith("}"):
        try:
            return json.loads(_clean_json_relaxed(arg_str))
        except Exception:
            pass

    kv_pattern = re.compile(r'([a-zA-Z0-9_]+)\s*[:=]\s*(?:"([^"]*)"|\'([^\']*)\'|([^,;*)]+))')
    matches = kv_pattern.findall(arg_str)
    if matches:
        args: dict[str, Any] = {}
        for m in matches:
            key = m[0]
            val = m[1] or m[2] or m[3].strip()
            val = val.strip("'\"")
            if val.lower() == "true":
                args[key] = True
            elif val.lower() == "false":
                args[key] = False
            elif val.isdigit():
                args[key] = int(val)
            else:
                args[key] = val
        return args

    m_bare = re.match(r'^["\'](.*)["\']$', arg_str)
    if m_bare:
        return m_bare.group(1)

    return arg_str


def recover_text_tool_call(text: str, known: Collection[str] | None = None) -> tuple[str, Any] | None:
    """Recover a tool call that a model wrote as text.

    Returns ``(tool_name, raw_arguments)``, or None when this is not a tool call.
    ``known``, when given, restricts recovery to tools that actually exist -- the
    difference between rescuing a real intent and inventing a tool from prose.
    """
    if not text:
        return None

    known_map = {k.lower(): k for k in known} if known is not None else None

    def _match_name(cand: str) -> str | None:
        if not cand:
            return None
        cand_clean = cand.strip().replace(" ", "_")
        if known_map is None:
            return cand_clean
        return known_map.get(cand_clean.lower())

    # 1. Colon-separated pattern: Volume control: volume_up or volume_control: up
    m_colon = re.search(r'(?:^|[\r\n])\s*([a-zA-Z0-9_ ]+?)\s*:\s*([a-zA-Z0-9_]+)\s*$', text)
    if m_colon:
        canonical = _match_name(m_colon.group(1))
        if canonical:
            arg = m_colon.group(2).strip()
            logger.info("recovered a colon-separated tool call the model wrote as text: %s (%s)", canonical, arg)
            return canonical, arg

    # 2. JSON tool object
    parsed = _first_json_object(text)
    if parsed is not None:
        name_raw = next((parsed[key] for key in _TOOL_NAME_KEYS if isinstance(parsed.get(key), str)), None)
        canonical = _match_name(name_raw)
        if canonical:
            arguments = next((parsed[key] for key in _TOOL_ARG_KEYS if key in parsed), None)
            logger.info("recovered a JSON tool call the model wrote as text: %s", canonical)
            return canonical, arguments

    # 2. Asterisk pattern: *desktop_hotkey*keys:win+d* or *desktop_hotkey*keys="win+d"*
    m_star = re.search(r'\*+([a-zA-Z0-9_]+)\*+([^*]+)\*+', text)
    if m_star:
        canonical = _match_name(m_star.group(1))
        if canonical:
            args = parse_args_string(m_star.group(2))
            logger.info("recovered an asterisk tool call the model wrote as text: %s", canonical)
            return canonical, args

    # 3. Call pattern: [TOOL: name(args)] or name(args)
    m_call = re.search(r'(?:\[(?:TOOL|CALL):?\s*)?(?:\*+)?([a-zA-Z0-9_]+)(?:\*+)?\s*\(([^)]*)\)', text)
    if m_call:
        canonical = _match_name(m_call.group(1))
        if canonical:
            args = parse_args_string(m_call.group(2))
            logger.info("recovered a functional tool call the model wrote as text: %s", canonical)
            return canonical, args

    # 4. Comma pattern: music_play, query="music", service="youtube"
    m_comma = re.search(r'(?:^|[\r\n])\s*([a-zA-Z0-9_]+)\s*,\s*([a-zA-Z0-9_]+\s*[:=].*)', text)
    if m_comma:
        canonical = _match_name(m_comma.group(1))
        if canonical:
            args = parse_args_string(m_comma.group(2))
            logger.info("recovered a comma-separated tool call the model wrote as text: %s", canonical)
            return canonical, args

    # 5. Name followed by JSON object: app_launch {"app": "notepad"} or tool_name, {"action": "minimize"}
    m_name_json = re.search(r'(?:^|[\r\n])\s*([a-zA-Z0-9_]+)\s*[:,]?\s*(\{.*\})', text, re.DOTALL)
    if m_name_json:
        canonical = _match_name(m_name_json.group(1))
        if canonical:
            try:
                args = json.loads(_clean_json_relaxed(m_name_json.group(2)))
                logger.info("recovered a name+json tool call the model wrote as text: %s", canonical)
                return canonical, args
            except Exception:
                pass

    # 6. Name followed by simple string/quoted argument: app_launch "notepad" or app_launch notepad
    m_name_bare = re.search(r'(?:^|[\r\n])\s*([a-zA-Z0-9_]+)\s+["\']?([a-zA-Z0-9_.:/\\]+)["\']?\s*$', text)
    if m_name_bare:
        canonical = _match_name(m_name_bare.group(1))
        if canonical:
            arg = m_name_bare.group(2).strip()
            logger.info("recovered a bare-arg tool call the model wrote as text: %s (%s)", canonical, arg)
            return canonical, arg

    return None


def looks_like_text_tool_call(text: str) -> bool:
    """Whether ``text`` is a tool call in prose clothing, whatever it names.

    Used to decide that a reply is not something to say out loud even when the
    named tool could not be recovered.
    """
    if not text:
        return False
    parsed = _first_json_object(text)
    if parsed is not None:
        has_name = any(isinstance(parsed.get(key), str) for key in _TOOL_NAME_KEYS)
        has_args = any(key in parsed for key in _TOOL_ARG_KEYS)
        if has_name and (has_args or len(parsed) <= 2):
            return True

    if re.search(r'\*+[a-zA-Z0-9_]+\*+[^*]+\*+', text):
        return True
    if re.search(r'(?:^|[\r\n])\s*[a-zA-Z0-9_]+\s*,\s*[a-zA-Z0-9_]+\s*[:=]', text):
        return True
    if re.search(r'[a-zA-Z0-9_]+\s*\([a-zA-Z0-9_]+\s*[:=]', text):
        return True
    if re.search(r'(?:^|[\r\n])\s*[a-zA-Z0-9_]+\s*[:,]?\s*\{.*\}', text, re.DOTALL):
        return True
    return False


def normalize_recovered_arguments(raw: Any, required: list[str] | None = None) -> dict[str, Any]:
    """Make recovered arguments usable by the registry's validation.

    A model writing a call as text sometimes puts a bare sentence where an object
    belongs (``"parameters": "Which Bob did you mean?"``). When the tool takes
    exactly one required argument, that sentence is plainly its value, and binding
    it is better than failing with a missing-argument error the model cannot see
    the cause of.
    """
    if isinstance(raw, str) and len(required or []) == 1:
        return {required[0]: raw.strip()}
    return parse_arguments(raw)


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
