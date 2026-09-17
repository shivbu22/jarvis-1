"""The in-process fallback brain: discovery, gating, and protocol handling.

The point of this brain is that it does not depend on Ollama being *healthy*. Ollama
is a single point of failure that lives outside this project -- here, an interrupted
upgrade left its inference binary missing, so a running Ollama with models installed
still refused every request.

What is tested here is the part this project owns: finding a weights file to borrow,
deciding whether the brain is usable at all, injecting the tool protocol, and parsing
what comes back. The C++ engine itself is not tested, and the tests do not need it --
they run with no model loaded and no GGUF present.

One limitation is recorded deliberately rather than hidden: ``llama-cpp-python`` has no
chat handler for qwen3, so that model's hidden reasoning arrives as ordinary content.
Tool calling through this path is therefore best-effort, and the parser rejects
anything that is not *entirely* one JSON object, because executing half-parsed intent
is the one failure this assistant must not have.
"""

from __future__ import annotations

import json

import pytest
from apps.gateway.brain.llama_cpp import (
    LlamaCppBrain,
    build_fallback_brain,
    discover_model_file,
    gguf_from_manifest,
    ollama_manifest_path,
    parse_tool_protocol,
    tool_catalog,
)


@pytest.fixture
def fake_ollama_store(tmp_path, monkeypatch):
    """A synthetic Ollama store: a manifest plus the blob it points at."""
    base = tmp_path / ".ollama" / "models"
    manifests = base / "manifests" / "registry.ollama.ai" / "library"
    blobs = base / "blobs"
    manifests.mkdir(parents=True)
    blobs.mkdir(parents=True)

    model_dir = manifests / "qwen3"
    model_dir.mkdir()
    digest = "sha256:" + "a" * 64
    blob = blobs / f"sha256-{'a' * 64}"
    blob.write_bytes(b"GGUF-not-really")

    (model_dir / "4b").write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "layers": [
                    {"mediaType": "application/vnd.ollama.image.system", "digest": "sha256:" + "b" * 64},
                    {"mediaType": "application/vnd.ollama.image.model", "digest": digest, "size": 15},
                    {"mediaType": "application/vnd.ollama.image.template", "digest": "sha256:" + "c" * 64},
                ],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr("apps.gateway.brain.llama_cpp.MANIFEST_ROOT", manifests)
    monkeypatch.setattr("apps.gateway.brain.llama_cpp.BLOB_ROOT", blobs)
    return {"base": tmp_path, "manifest": model_dir / "4b", "blob": blob, "digest": digest}


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------


def test_manifest_path_follows_ollamas_layout(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "apps.gateway.brain.llama_cpp.MANIFEST_ROOT", tmp_path / "manifests"
    )
    path = ollama_manifest_path("qwen3:4b")
    assert path == tmp_path / "manifests" / "qwen3" / "4b"
    # A name with no tag is 'latest', exactly as Ollama resolves it.
    assert ollama_manifest_path("qwen3").name == "latest"


def test_the_model_layer_is_the_one_borrowed(fake_ollama_store) -> None:
    """Only the weights layer is a GGUF; system and template layers must be ignored."""
    found = gguf_from_manifest("qwen3:4b")
    assert found == fake_ollama_store["blob"]
    assert found is not None and found.is_file()


def test_a_missing_manifest_is_not_an_error() -> None:
    """The fallback is optional, so absence is a normal answer, not an exception."""
    assert gguf_from_manifest("definitely-not-installed:99b") is None


def test_a_manifest_pointing_at_a_missing_blob_yields_nothing(fake_ollama_store) -> None:
    fake_ollama_store["blob"].unlink()
    assert gguf_from_manifest("qwen3:4b") is None


def test_an_explicit_path_wins_over_the_ollama_store(fake_ollama_store, tmp_path) -> None:
    chosen = tmp_path / "my-model.gguf"
    chosen.write_bytes(b"GGUF")
    assert discover_model_file(preferred="qwen3:4b", explicit=str(chosen)) == chosen


def test_an_explicit_path_that_does_not_exist_is_refused(tmp_path) -> None:
    """A typo in JARVIS_GGUF_PATH must not silently fall back to a different model."""
    assert discover_model_file(explicit=str(tmp_path / "nope.gguf")) is None


def test_the_configured_model_is_preferred(fake_ollama_store) -> None:
    assert discover_model_file(preferred="qwen3:4b") == fake_ollama_store["blob"]


# ---------------------------------------------------------------------------
# availability: a brain that cannot answer must not be in the chain
# ---------------------------------------------------------------------------


def test_unavailable_without_a_model_file() -> None:
    brain = LlamaCppBrain(None)
    assert brain.available() is False
    assert brain.describe()["available"] is False


def test_not_built_when_no_model_can_be_borrowed(settings) -> None:
    assert build_fallback_brain(settings, model="definitely-not-installed:99b") is None


def test_not_built_when_explicit_path_is_bogus(settings, tmp_path) -> None:
    settings.gguf_path = str(tmp_path / "missing.gguf")
    assert build_fallback_brain(settings, model="qwen3:4b") is None


def test_unavailable_when_the_library_is_missing(fake_ollama_store, monkeypatch) -> None:
    """Without llama-cpp-python the brain is skipped, not left to fail at a turn."""
    monkeypatch.setattr(LlamaCppBrain, "library_installed", staticmethod(lambda: False))
    brain = LlamaCppBrain(fake_ollama_store["blob"])
    assert brain.available() is False


def test_describe_is_honest_about_its_state(fake_ollama_store, monkeypatch) -> None:
    monkeypatch.setattr(LlamaCppBrain, "library_installed", staticmethod(lambda: True))
    brain = LlamaCppBrain(fake_ollama_store["blob"], model="borrowed")
    info = brain.describe()
    assert info["available"] is True
    assert info["loaded"] is False
    assert info["local"] is True
    assert str(fake_ollama_store["blob"]) in info["model_path"]


# ---------------------------------------------------------------------------
# the tool protocol this brain has to use
# ---------------------------------------------------------------------------


def test_catalog_is_readable_not_json_schema() -> None:
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Current weather for a city.\nSecond line.",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}, "units": {"type": "string"}},
                    "required": ["city"],
                },
            },
        }
    ]
    catalog = tool_catalog(tools)
    assert "- get_weather(city: string, units: string?):" in catalog
    assert "Second line." in catalog
    # Optional arguments are marked, required ones are not.
    assert "city: string," in catalog


def test_catalog_skips_entryless_tools() -> None:
    assert tool_catalog([{"type": "function", "function": {}}]) == ""


def test_a_clean_json_reply_becomes_a_tool_call() -> None:
    text, call = parse_tool_protocol('{"tool": "get_weather", "args": {"city": "Mumbai"}}')
    assert text == ""
    assert call is not None
    assert call.name == "get_weather"
    assert call.arguments == {"city": "Mumbai"}


def test_a_fenced_json_reply_is_accepted() -> None:
    _, call = parse_tool_protocol('```json\n{"tool": "get_weather", "args": {"city": "Mumbai"}}\n```')
    assert call is not None and call.name == "get_weather"


def test_prose_is_spoken_as_prose() -> None:
    text, call = parse_tool_protocol("It is overcast in Mumbai.")
    assert call is None
    assert text == "It is overcast in Mumbai."


def test_a_call_buried_in_prose_is_refused() -> None:
    """Half-parsed intent is worse than no call: the model must ask properly."""
    text, call = parse_tool_protocol('Sure! {"tool": "get_weather", "args": {"city": "Mumbai"}}')
    assert call is None
    assert "Sure!" in text


@pytest.mark.parametrize(
    "raw",
    [
        '{"tool": "get_weather", "args": {"city":',  # truncated mid-JSON
        '{"tool": "", "args": {}}',  # no tool named
        '{"args": {"city": "Mumbai"}}',  # no tool field
        '["get_weather", "Mumbai"]',  # not an object
        "",
    ],
)
def test_malformed_replies_never_become_calls(raw: str) -> None:
    _, call = parse_tool_protocol(raw)
    assert call is None


def test_alternate_field_names_are_accepted() -> None:
    """Models vary between ``tool``/``name`` and ``args``/``arguments``."""
    _, call = parse_tool_protocol('{"name": "memory_remember", "arguments": {"text": "tea"}}')
    assert call is not None
    assert call.name == "memory_remember"
    assert call.arguments == {"text": "tea"}


# ---------------------------------------------------------------------------
# request shape
# ---------------------------------------------------------------------------


class _FakeLlama:
    """Captures what the brain asked for, so the request can be asserted."""

    def __init__(self, reply: str = "Very good, sir.") -> None:
        self.calls: list[dict] = []
        self._reply = reply
        self.closed = False

    def create_chat_completion(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "choices": [{"message": {"role": "assistant", "content": self._reply}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 3},
        }

    def close(self) -> None:
        self.closed = True


def _brain_with_fake_engine(
    tmp_path, reply: str = "Very good, sir."
) -> tuple[LlamaCppBrain, _FakeLlama]:
    """A brain wired to a fake engine.

    The engine is returned separately because ``aclose()`` releases it -- holding a
    reference here keeps assertions readable whether they run before or after close.
    """
    model = tmp_path / "fake.gguf"
    model.write_bytes(b"GGUF")
    brain = LlamaCppBrain(model, model="fake")
    engine = _FakeLlama(reply)
    brain._llama = engine
    return brain, engine


async def test_native_tools_are_not_passed_to_the_engine(tmp_path) -> None:
    """The engine has no qwen3 tool handler, so tools travel as prompt text instead."""
    brain, engine = _brain_with_fake_engine(tmp_path)
    tools = [{"type": "function", "function": {"name": "get_weather", "description": "d",
              "parameters": {"type": "object", "properties": {"city": {"type": "string"}},
                             "required": ["city"]}}}]
    try:
        reply = await brain.generate([{"role": "user", "content": "hi"}], tools)
    finally:
        await brain.aclose()

    payload = engine.calls[0]
    assert "tools" not in payload
    # The protocol must reach the model in a system message.
    system = payload["messages"][0]
    assert system["role"] == "system"
    assert "get_weather(city: string)" in system["content"]
    assert '"tool"' in system["content"]
    assert reply.text == "Very good, sir."


async def test_the_protocol_is_added_to_an_existing_system_message(tmp_path) -> None:
    brain, engine = _brain_with_fake_engine(tmp_path)
    tools = [{"type": "function", "function": {"name": "t", "description": "d",
              "parameters": {"type": "object", "properties": {}}}}]
    try:
        await brain.generate(
            [{"role": "system", "content": "You are Jarvis."},
             {"role": "user", "content": "hi"}],
            tools,
        )
    finally:
        await brain.aclose()

    messages = engine.calls[0]["messages"]
    assert messages[0]["role"] == "system"
    assert messages[0]["content"].startswith("You are Jarvis.")
    assert len(messages) == 2, "no new system message should be inserted"


async def test_no_protocol_when_there_are_no_tools(tmp_path) -> None:
    brain, engine = _brain_with_fake_engine(tmp_path)
    try:
        await brain.generate([{"role": "user", "content": "hi"}], [])
    finally:
        await brain.aclose()

    payload = engine.calls[0]
    assert "system" not in [m["role"] for m in payload["messages"]]
    assert payload["max_tokens"] == brain.max_tokens


async def test_a_protocol_reply_is_turned_into_a_call(tmp_path) -> None:
    brain, _ = _brain_with_fake_engine(
        tmp_path, '{"tool": "get_weather", "args": {"city": "Mumbai"}}'
    )
    tools = [{"type": "function", "function": {"name": "get_weather", "description": "d",
              "parameters": {"type": "object", "properties": {"city": {"type": "string"}},
                             "required": ["city"]}}}]
    try:
        reply = await brain.generate([{"role": "user", "content": "weather?"}], tools)
    finally:
        await brain.aclose()

    assert [c.name for c in reply.tool_calls] == ["get_weather"]
    assert reply.tool_calls[0].arguments == {"city": "Mumbai"}
    assert reply.text == ""
    assert reply.eval_count == 3
    assert "in-process" in reply.model_note


async def test_token_usage_is_reported(tmp_path) -> None:
    brain, _ = _brain_with_fake_engine(tmp_path)
    try:
        reply = await brain.generate([{"role": "user", "content": "hi"}], [])
    finally:
        await brain.aclose()

    assert reply.prompt_eval_count == 11
    assert reply.eval_count == 3


async def test_an_engine_failure_becomes_a_spoken_error(tmp_path) -> None:
    """A model that cannot load must produce a usable sentence, not a traceback."""
    from apps.gateway.brain.base import BrainError

    brain, engine = _brain_with_fake_engine(tmp_path)

    def explode(**_kwargs):
        raise RuntimeError("no CUDA for you")

    engine.create_chat_completion = explode  # type: ignore[method-assign]
    try:
        with pytest.raises(BrainError) as caught:
            await brain.generate([{"role": "user", "content": "hi"}], [])
        assert "local model" in str(caught.value).lower()
    finally:
        await brain.aclose()


async def test_close_is_idempotent(tmp_path) -> None:
    brain, engine = _brain_with_fake_engine(tmp_path)
    await brain.aclose()
    await brain.aclose()
    assert brain._llama is None
    assert engine.closed is True
