"""Relay-brain tests: selection, secrets, and standing down.

The relay is a large hosted brain reached over loopback. Three things about it are
easy to get wrong and expensive when wrong, so each has a test here:

1. **What gets *spoken*.** The relay's URL is the credential -- the key lives in the
   path -- and brain errors are read aloud and printed in the orb. A connection
   failure that echoed the URL would read the key out to the room.
2. **Selection.** Configured means used, in `auto` mode, ahead of the small local
   model; but offline mode must never touch it, because the socket is on loopback
   and the brain behind it is not.
3. **A refusal that costs every turn.** The relay's pooled account runs out of
   credit (observed: HTTP 401 "credits exhausted", and 429 "model is cooling down"
   with a reset_seconds hint). Retrying a dead relay on every turn would tax each
   one with a pointless round trip, so it stands down and answers surface as a
   failover to the local brain instead.
"""

from __future__ import annotations

import dataclasses
import time

import pytest
from apps.gateway.brain import BrainRouter, build_brain_chain
from apps.gateway.brain.base import Brain, BrainError, BrainReply
from apps.gateway.brain.relay import RelayBrain, redact

KEYED_URL = "http://127.0.0.1:20128/api/v1/vscode/sk-b586def6cbd23fbe-5c4e7d"


class _Response:
    def __init__(self, status_code: int, body: dict) -> None:
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self) -> dict:
        return self._body


class _Client:
    """Stands in for httpx.AsyncClient: scripted status, counted calls."""

    def __init__(self, *responses: _Response) -> None:
        self.responses = list(responses)
        self.calls = 0

    async def post(self, path, json=None):
        self.calls += 1
        if self.responses:
            return self.responses.pop(0)
        raise AssertionError("the relay was contacted again while standing down")

    async def aclose(self) -> None:
        return None


def _relay(*responses: _Response, model: str = "claude-haiku-4.5") -> tuple[RelayBrain, _Client]:
    brain = RelayBrain(url=KEYED_URL, model=model)
    client = _Client(*responses)
    brain._client = client  # type: ignore[assignment]
    return brain, client


def _ok() -> _Response:
    return _Response(200, {"choices": [{"message": {"content": "Very good, sir."}}]})


CREDITS_GONE = _Response(
    401,
    {
        "error": {
            "message": "[openrouter] All 1 connection(s) credits exhausted",
            "type": "authentication_error",
            "code": "invalid_api_key",
        }
    },
)

COOLING_DOWN = _Response(
    429,
    {
        "error": {
            "message": "All credentials for model anthropic/claude-haiku-4.5 are cooling down",
            "type": "rate_limit_error",
            "code": "model_cooldown",
            "reset_seconds": 84,
        }
    },
)

UNKNOWN_MODEL = _Response(
    400,
    {
        "error": {
            "message": "Unable to determine provider for model 'grok-4-fast'.",
            "type": "invalid_request_error",
        }
    },
)


# ---------------------------------------------------------------------------
# 1. what gets spoken
# ---------------------------------------------------------------------------


def test_the_key_in_the_url_is_never_spoken_or_described() -> None:
    """A brain error is read aloud, so it must not carry the credential."""
    brain = RelayBrain(url=KEYED_URL, model="m")
    message = brain._connection_message()
    assert "127.0.0.1:20128" in message
    assert "sk-b586def6cbd23fbe" not in message
    assert "sk-b586def6cbd23fbe" not in brain.describe()["url"]


def test_provider_echo_of_our_url_is_redacted_before_it_is_spoken() -> None:
    brain, _ = _relay(_Response(400, {"error": {"message": f"cannot proxy to {KEYED_URL}"}}))
    message = brain._status_message(_Response(400, {"error": {"message": f"bad request for {KEYED_URL}"}}))
    assert "sk-b586def6cbd23fbe" not in message
    assert redact(KEYED_URL) == "http://127.0.0.1:20128/api/v1/vscode/sk-***"


# ---------------------------------------------------------------------------
# 2. selection
# ---------------------------------------------------------------------------


@pytest.fixture
def relay_settings(settings):
    """The suite's settings, online, with a relay configured."""
    return dataclasses.replace(settings, offline_mode=False, relay_url=KEYED_URL)


def test_a_configured_relay_leads_the_chain(relay_settings) -> None:
    chain = build_brain_chain(relay_settings)
    assert chain.brains[0].name == "relay"
    # The local brain stays directly behind it, so the next turn still answers when
    # the relay stands down mid-conversation.
    assert any(brain.name == "Ollama" for brain in chain.brains[1:])


def test_offline_mode_never_builds_the_relay(relay_settings) -> None:
    """`JARVIS_OFFLINE=1` means nothing leaves the machine, relay included."""
    chain = build_brain_chain(dataclasses.replace(relay_settings, offline_mode=True))
    assert all(brain.name != "relay" for brain in chain.brains)


def test_relay_mode_leads_even_without_a_gemini_key(settings) -> None:
    chain = build_brain_chain(
        dataclasses.replace(settings, brain_mode="relay", offline_mode=False, relay_url=KEYED_URL)
    )
    assert chain.brains[0].name == "relay"


def test_no_relay_configured_leaves_the_chain_local(settings) -> None:
    chain = build_brain_chain(
        dataclasses.replace(settings, offline_mode=False, relay_url="", gemini_api_key="")
    )
    assert chain.brains[0].name == "Ollama"


def test_a_relay_is_not_treated_as_a_local_brain() -> None:
    """Its socket is on loopback; its brain is not. Status must not claim otherwise."""
    brain = RelayBrain(url=KEYED_URL, model="m")
    assert brain.is_local is False


# ---------------------------------------------------------------------------
# 3. refusing cheaply
# ---------------------------------------------------------------------------


async def test_a_spent_account_stands_down_and_the_local_brain_answers() -> None:
    relay, client = _relay(CREDITS_GONE)

    class Local(Brain):
        name = "Ollama"
        model = "local"

        async def generate(self, messages, tools):  # type: ignore[override]
            return BrainReply(text="The local brain answered.", model=self.model)

    router = BrainRouter([relay, Local()])
    reply = await router.generate([{"role": "user", "content": "are you there?"}], [])

    assert reply.text == "The local brain answered."
    assert client.calls == 1
    # Standing down means the *next* turn costs nothing: no second request.
    started = time.monotonic()
    with pytest.raises(BrainError) as caught:
        await relay.generate([{"role": "user", "content": "again"}], [])
    assert "standing down" in str(caught.value)
    assert time.monotonic() - started < 0.2
    assert client.calls == 1
    assert relay.blocked_for() > 0


async def test_the_stand_down_expires_so_recovered_credits_are_picked_up() -> None:
    relay, client = _relay(CREDITS_GONE)
    with pytest.raises(BrainError):
        await relay.generate([{"role": "user", "content": "hi"}], [])
    assert relay.blocked_for() > 0

    relay._blocked_until = time.monotonic() - 1  # as if a minute had passed
    client.responses.append(_ok())
    reply = await relay.generate([{"role": "user", "content": "hi"}], [])
    assert reply.text == "Very good, sir."


async def test_a_rate_limit_stands_down_for_the_time_it_asked_for() -> None:
    """The relay says when it frees up; believing it beats guessing."""
    relay, _ = _relay(COOLING_DOWN)
    with pytest.raises(BrainError) as caught:
        await relay.generate([{"role": "user", "content": "hi"}], [])
    assert "rate limiting" in str(caught.value)
    assert "84 seconds" in str(caught.value)
    assert relay.blocked_for() >= 80


async def test_a_status_that_is_not_a_temporary_refusal_does_not_stand_down() -> None:
    """A bad model name is the master's setting to fix, not a reason to sulk."""
    relay, _ = _relay(UNKNOWN_MODEL)
    with pytest.raises(BrainError) as caught:
        await relay.generate([{"role": "user", "content": "hi"}], [])
    assert "JARVIS_RELAY_MODEL" in str(caught.value)
    assert relay.blocked_for() == 0


async def test_a_standing_down_relay_is_not_advertised_as_the_active_brain() -> None:
    """/health must name who would answer now, not merely who is first in line."""
    relay, _ = _relay(CREDITS_GONE)

    class Local(Brain):
        name = "Ollama"
        model = "local"

        async def generate(self, messages, tools):  # type: ignore[override]
            return BrainReply(text="local", model=self.model)

    router = BrainRouter([relay, Local()])
    assert router.describe()["active"] == "relay"
    await router.generate([{"role": "user", "content": "hi"}], [])
    assert router.describe()["active"] == "Ollama"


async def test_standing_down_is_visible_in_status() -> None:
    relay, _ = _relay(CREDITS_GONE)
    with pytest.raises(BrainError):
        await relay.generate([{"role": "user", "content": "hi"}], [])
    described = relay.describe()
    assert described["standing_down_seconds"] > 0
    assert "credits" in described["standing_down_reason"]
