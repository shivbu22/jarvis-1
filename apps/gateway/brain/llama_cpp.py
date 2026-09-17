"""An in-process brain that loads a GGUF directly, with no Ollama in the path.

Why this exists. Ollama is the keyless default, but it is also a single point of
failure that lives *outside* this project: on this machine an interrupted upgrade
left its inference binary missing, so a perfectly healthy-looking Ollama (running,
models listed) refused every request. A voice assistant whose only brain cannot be
started is not an assistant.

This brain is the answer to that: it loads the very same weights Ollama already
downloaded -- read-only, straight out of Ollama's blob store -- and runs them inside
the gateway process. No server to be running, no daemon to be healthy.

It is deliberately the *last* resort, not the default. Ollama is better at this job:
it manages loading, keeps the model warm between turns, and offloads to the GPU more
capably. This is insurance, and it is only reached when everything ahead of it has
already failed.

The dependency is optional. ``llama-cpp-python`` is not required to run Jarvis; when
it is absent, ``available()`` is False, the brain is left out of the chain, and
nothing else changes. Install it deliberately:

    uv pip install --python <venv>\\Scripts\\python.exe llama-cpp-python

Notes that matter in practice:

* Ollama's blobs are plain GGUF files, so they can be loaded directly. Only the
  manifest is read (never written), and the file is opened read-only.
* Inference is blocking C++ code, so it runs in a worker thread and is serialised
  with a lock: two turns must not drive one ``Llama`` object at the same time.
* A cold load of a 2.5 GB model takes tens of seconds. That cost is paid once, on
  the first turn that needs it, and never on a turn Ollama already handled.

**A measured limitation, stated plainly.** ``llama-cpp-python`` 0.3.x ships no chat
handler for qwen3's tool-call format, so passing ``tools`` through to the engine
does not work: measured on this machine, the model replied "Let me check the tools
provided..." and ``tool_calls`` came back null. That is why this brain injects its own
JSON protocol (see ``_TOOL_PROTOCOL``) and parses the reply itself. It also means
qwen3's hidden reasoning arrives as ordinary *content* here, so this fallback speaks
better than it acts: it will answer a question from what it knows, and it is not a
reliable substitute for Ollama when a turn needs a tool. It exists so a stopped or
broken Ollama leaves Jarvis with a voice rather than silence -- not so that it can
quietly replace the brain.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from pathlib import Path
from typing import Any

from .base import Brain, BrainError, BrainReply, ToolCall, clean_text, parse_arguments

logger = logging.getLogger(__name__)

# Ollama's manifest layout. Kept in one place because these paths are an external
# contract we only *read*.
OLLAMA_HOME = Path.home() / ".ollama" / "models"
MANIFEST_ROOT = OLLAMA_HOME / "manifests" / "registry.ollama.ai" / "library"
BLOB_ROOT = OLLAMA_HOME / "blobs"

MODEL_LAYER_SUFFIX = "image.model"

# llama-cpp-python 0.3.x ships no chat handler for qwen3's tool-call format, so asking
# it to call tools natively returns narration and no call at all -- measured: the model
# wrote "Let me check the tools provided..." and ``tool_calls`` came back null. A voice
# assistant that can silently *not* call a tool and instead invent an answer is worse
# than one that cannot answer, so the fallback gets an explicit protocol of its own:
# ask for one line of JSON, then parse it here where the behaviour is ours.
_TOOL_PROTOCOL = """\
You can use the tools listed at the end of this message.

When the request needs a tool, reply with ONLY one line of JSON, and nothing else:

{{"tool": "<tool name>", "args": {{ ... }}}}

When no tool is needed, reply normally in one or two sentences. Never invent a tool
name, never describe the JSON you are about to write, and never guess at a value a
tool would have told you.

Tools:
{catalog}"""

# A tool call is only accepted when the whole reply is one JSON object. Anything else
# is spoken as prose: a half-parsed call would execute the wrong thing.
_JSON_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def ollama_manifest_path(model: str) -> Path:
    """Where Ollama keeps the manifest for ``name:tag``.

    ``qwen3:4b`` lives at ``.../library/qwen3/4b``. A name without a tag means
    ``latest``, which is how Ollama itself resolves it.
    """
    name, _, tag = model.partition(":")
    return MANIFEST_ROOT / name / (tag or "latest")


def gguf_from_manifest(model: str) -> Path | None:
    """Resolve the weights file Ollama downloaded for ``model``, if present.

    Returns None rather than raising when anything is missing: this is a fallback
    path, and "I could not find a model to borrow" is a normal answer, not an error.
    """
    manifest = ollama_manifest_path(model)
    if not manifest.is_file():
        return None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    layers = data.get("layers")
    if not isinstance(layers, list):
        return None
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        if not str(layer.get("mediaType", "")).endswith(MODEL_LAYER_SUFFIX):
            continue
        digest = str(layer.get("digest") or "")
        if not digest.startswith("sha256:"):
            continue
        # Ollama stores a blob as "sha256-<hex>", not "sha256:<hex>".
        blob = BLOB_ROOT / f"sha256-{digest.split(':', 1)[1]}"
        if blob.is_file():
            return blob
    return None


def tool_catalog(tools: list[dict[str, Any]]) -> str:
    """A compact, readable list of tools for the prompt.

    Deliberately not the raw JSON Schema: a 4B model follows one line per tool far
    better than it follows nested ``properties`` objects, and it keeps the prompt
    small enough to matter at ~12 tokens a second.
    """
    lines: list[str] = []
    for spec in tools:
        function = spec.get("function") or {}
        name = str(function.get("name") or "").strip()
        if not name:
            continue
        schema = function.get("parameters") or {}
        properties = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        params: list[str] = []
        for key, meta in properties.items():
            kind = ""
            if isinstance(meta, dict):
                kind = str(meta.get("type") or "")
            marker = "" if key in required else "?"
            params.append(f"{key}: {kind}{marker}".strip())
        signature = ", ".join(params)
        description = " ".join(str(function.get("description") or "").split())[:160]
        lines.append(f"- {name}({signature}): {description}")
    return "\n".join(lines)


def parse_tool_protocol(text: str) -> tuple[str, ToolCall | None]:
    """Split a reply into (spoken text, tool call).

    Only a reply that is *entirely* one JSON object is treated as a call. A call
    buried in prose is rejected on purpose: executing half-parsed intent is exactly
    the failure this assistant must not have.
    """
    cleaned = _JSON_FENCE.sub("", (text or "").strip()).strip()
    if not cleaned.startswith("{"):
        return cleaned, None
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        return cleaned, None
    if not isinstance(payload, dict):
        return cleaned, None

    name = payload.get("tool") or payload.get("name")
    if not isinstance(name, str) or not name.strip():
        return cleaned, None
    raw_args = payload.get("args")
    if raw_args is None:
        raw_args = payload.get("arguments")
    return "", ToolCall(
        id="call_0", name=name.strip(), arguments=parse_arguments(raw_args), raw=payload
    )


def discover_model_file(preferred: str = "", explicit: str = "") -> Path | None:
    """Find a GGUF to load: an explicit path first, then Ollama's store.

    ``explicit`` is what the master set in .env, and it wins because a person who
    names a file means it. ``preferred`` is the model name Jarvis is configured to
    use, so the fallback borrows the same weights rather than picking arbitrarily.
    """
    if explicit:
        candidate = Path(explicit).expanduser()
        return candidate if candidate.is_file() else None
    if preferred:
        found = gguf_from_manifest(preferred)
        if found is not None:
            return found
    return None


class LlamaCppBrain(Brain):
    """Runs a GGUF in-process. Last resort when no other brain can answer."""

    name = "llama.cpp"
    is_local = True

    def __init__(
        self,
        model_path: Path | None,
        *,
        model: str = "",
        n_ctx: int = 8192,
        n_gpu_layers: int = -1,
        timeout: float = 240.0,
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> None:
        self.model_path = model_path
        self.model = model or (model_path.name if model_path else "")
        self.n_ctx = n_ctx
        self.n_gpu_layers = n_gpu_layers
        self.timeout = timeout
        self.temperature = temperature
        # Same reasoning as the Ollama brain: a local model's reply length *is* its
        # latency, but the cap must still leave room for hidden reasoning tokens.
        self.max_tokens = max_tokens
        self._llama: Any = None
        self._lock = threading.Lock()
        self.load_error: str | None = None

    # -- availability -----------------------------------------------------

    @staticmethod
    def library_installed() -> bool:
        """Is ``llama-cpp-python`` importable? Import is deferred on purpose."""
        try:
            import llama_cpp  # noqa: F401
        except Exception:
            return False
        return True

    def available(self) -> bool:
        """True only when this brain could actually answer right now."""
        if self.model_path is None or not Path(self.model_path).is_file():
            return False
        return self.library_installed()

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "model": self.model,
            "local": True,
            "available": self.available(),
            "library_installed": self.library_installed(),
            "model_path": str(self.model_path) if self.model_path else "",
            "loaded": self._llama is not None,
            "n_ctx": self.n_ctx,
            "error": self.load_error,
        }

    # -- loading ----------------------------------------------------------

    def _load(self) -> Any:
        """Load the model once. Never call this from the event loop: it blocks."""
        if self._llama is not None:
            return self._llama
        with self._lock:
            if self._llama is not None:  # another thread won the race
                return self._llama
            if not self.available():
                raise BrainError(
                    "I have no local model to fall back on. Install llama-cpp-python "
                    "and keep an Ollama model installed, or set a cloud key."
                )
            try:
                from llama_cpp import Llama
            except Exception as exc:
                raise BrainError(f"I could not load my local model engine: {exc}") from exc

            logger.info("loading GGUF in-process: %s", self.model_path)
            try:
                self._llama = Llama(
                    model_path=str(self.model_path),
                    n_ctx=self.n_ctx,
                    n_gpu_layers=self.n_gpu_layers,
                    verbose=False,
                    # Chat templates come from the GGUF metadata, so tool calling works
                    # for models whose template supports it (qwen3 does).
                    # Let the GGUF's own template drive the chat format.
                    chat_format=None,
                )
            except Exception as exc:
                self.load_error = f"{type(exc).__name__}: {exc}"
                raise BrainError(f"I could not load the local model: {exc}") from exc
        return self._llama

    # -- generation -------------------------------------------------------

    async def generate(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> BrainReply:
        # Blocking C++ inference, off the event loop so a turn cannot stall the
        # gateway's websockets.
        return await asyncio.to_thread(self._generate_blocking, messages, tools)

    def _generate_blocking(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> BrainReply:
        llm = self._load()
        prepared = [self._sanitise(message) for message in messages]
        calling = bool(tools) and bool(tool_catalog(tools))
        if calling:
            prepared = self._teach_tool_protocol(prepared, tools)

        payload: dict[str, Any] = {
            "messages": prepared,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

        try:
            with self._lock:  # one Llama object, one turn at a time
                body = llm.create_chat_completion(**payload)
        except Exception as exc:
            raise BrainError(f"My local model failed: {exc}") from exc

        return self._parse(body, tools=calling)

    def _teach_tool_protocol(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Add the JSON tool protocol, replacing any earlier copy.

        The instruction is appended to the system message (creating one if the caller
        sent none) because a system turn is the only place a small model reliably
        reads before deciding what to do.
        """
        block = _TOOL_PROTOCOL.format(catalog=tool_catalog(tools))
        prepared = [dict(message) for message in messages]
        for message in prepared:
            if message.get("role") == "system":
                content = str(message.get("content") or "")
                if "\n\nTools:\n" in content:
                    content = content.split("\n\nTools:\n", 1)[0]
                message["content"] = f"{content}\n\n{block}" if content else block
                return prepared
        prepared.insert(0, {"role": "system", "content": block})
        return prepared

    def _sanitise(self, message: dict[str, Any]) -> dict[str, Any]:
        """Keep the fields this engine understands and drop the rest."""
        clean: dict[str, Any] = {
            "role": message.get("role", "user"),
            "content": message.get("content") or "",
        }
        if message.get("tool_calls"):
            clean["tool_calls"] = message["tool_calls"]
        if message.get("tool_call_id"):
            clean["tool_call_id"] = message["tool_call_id"]
        if message.get("name"):
            clean["name"] = message["name"]
        return clean

    def _parse(self, body: dict[str, Any], *, tools: bool = False) -> BrainReply:
        choices = body.get("choices") or []
        if not choices:
            error = body.get("error")
            if error:
                raise BrainError(f"My local model reported: {str(error)[:300]}")
            raise BrainError("My local model returned no answer.")

        message = choices[0].get("message") or {}
        text = clean_text(message.get("content"))
        calls: list[ToolCall] = []

        # Native calls first, in case a future llama-cpp-python does parse them.
        for index, raw_call in enumerate(message.get("tool_calls") or []):
            function = raw_call.get("function") or {}
            name = function.get("name") or raw_call.get("name") or ""
            if not name:
                continue
            calls.append(
                ToolCall(
                    id=str(raw_call.get("id") or f"call_{index}"),
                    name=name,
                    arguments=parse_arguments(function.get("arguments")),
                    raw=raw_call,
                )
            )

        # Otherwise fall back to the JSON protocol this brain asked for.
        if not calls and tools:
            spoken, call = parse_tool_protocol(text)
            if call is not None:
                return self._reply(spoken, [call], body)

        return self._reply(text, calls, body)

    def _reply(self, text: str, calls: list[ToolCall], body: dict[str, Any]) -> BrainReply:
        usage = body.get("usage") or {}
        return BrainReply(
            text=text,
            tool_calls=calls,
            model=self.model,
            raw=body,
            prompt_eval_count=int(usage.get("prompt_tokens") or 0),
            eval_count=int(usage.get("completion_tokens") or 0),
            model_note=f"in-process GGUF: {self.model_path}",
        )

    async def aclose(self) -> None:
        """Release the model. Safe to call twice."""
        with self._lock:
            llama = self._llama
            self._llama = None
        closer = getattr(llama, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:
                logger.debug("ignoring error while closing the local model", exc_info=True)


def build_fallback_brain(settings: Any, *, model: str = "") -> LlamaCppBrain | None:
    """Construct the in-process fallback, or None when it would be useless.

    Returning None keeps the chain honest: a brain that cannot answer must not be
    listed as one that might, which is what would happen if we appended it blindly
    and let it fail at the first turn.
    """
    explicit = getattr(settings, "gguf_path", "") or ""
    path = discover_model_file(preferred=model, explicit=explicit)
    brain = LlamaCppBrain(
        path,
        model=explicit or model or (path.stem if path else ""),
        n_ctx=int(getattr(settings, "local_fallback_ctx", 8192) or 8192),
        n_gpu_layers=int(getattr(settings, "local_fallback_gpu_layers", -1)),
        timeout=float(getattr(settings, "brain_timeout_seconds", 240.0) or 240.0),
    )
    if not brain.available():
        return None
    return brain
