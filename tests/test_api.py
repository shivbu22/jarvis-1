"""HTTP contract tests, run against the real app with a scripted brain.

The brain is swapped after startup so these tests never depend on a model being
reachable, while everything around it -- policy, registry, gate, audit, broker --
is the real thing.

Timing fields from the brain layer are recorded on ``/v1/turn/text`` and exposed
on the audit endpoint so the audit trail can show provider-reported latency and the
model that was actually used (e.g. ``jarvis-brain:4b`` when Ollama resolves the
tuned local brain)."""

from __future__ import annotations

import base64
import platform

import pytest
from apps.gateway.brain.base import BrainReply
from apps.gateway.main import create_app
from fastapi.testclient import TestClient
from tests.conftest import ScriptedBrain, tool_reply


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        app.state.jarvis.agent.brain = ScriptedBrain([BrainReply(text="Very good, sir.")])
        yield test_client


def set_script(app, replies) -> None:
    app.state.jarvis.agent.brain = ScriptedBrain(replies)


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------


def test_health_reports_a_working_assistant(client) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["tool_count"] > 20
    assert body["policy"]["source"].endswith("policy.yaml")
    assert body["policy"]["default_tier"] == "confirm"
    assert body["policy"]["sensitive_paths"], "key material must be listed"
    assert "platform" in body


def test_health_surfaces_the_policy_actually_loaded(client) -> None:
    """A fail-closed fallback must be visible, not silently equal to a loaded file."""
    policy = client.get("/health").json()["policy"]
    assert policy["source"].endswith("policy.yaml")
    assert policy["tiers"]["auto"] and policy["tiers"]["deny"]


def test_tools_are_listed_with_tiers(client) -> None:
    body = client.get("/tools").json()
    tiers = {tool["name"]: tool["tier"] for tool in body["tools"]}
    assert tiers["web_search"] == "auto"
    assert tiers["shell_run"] == "confirm"
    assert tiers["shell_run_admin"] == "deny"


def test_index_page_is_human_readable(client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Jarvis" in response.text
    assert "/health" in response.text


def test_openapi_schema_is_served(client) -> None:
    assert client.get("/openapi.json").status_code == 200


# --------------------------------------------------------------------------
# turns
# --------------------------------------------------------------------------


def test_text_turn_returns_a_reply(client) -> None:
    response = client.post("/v1/turn/text", json={"text": "good morning", "speak": False})
    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == "Very good, sir."
    assert body["transcript"] == "good morning"


def test_text_turn_with_a_tool_records_it(client, app) -> None:
    set_script(
        app,
        [tool_reply("memory_remember", {"text": "buy saffron"}), BrainReply(text="Saved.")],
    )
    body = client.post(
        "/v1/turn/text", json={"text": "remember to buy saffron", "speak": False}
    ).json()
    assert body["reply"] == "Saved."
    assert body["turn"]["tools"][0]["name"] == "memory_remember"
    assert body["turn"]["tools"][0]["decision"] == "auto"


def test_confirm_tier_tool_awaits_when_confirmation_is_disallowed(client, app) -> None:
    set_script(
        app,
        [
            tool_reply(
                "fs_write",
                {
                    "path": "C:\\Users\\ahiva\\OneDrive\\Desktop\\jarvis\\data\\x.txt",
                    "content": "no",
                },
            ),
            BrainReply(text="I need permission."),
        ],
    )
    body = client.post(
        "/v1/turn/text",
        json={"text": "write a file", "speak": False, "allow_confirmation": False},
    ).json()
    assert body["turn"]["tools"][0]["decision"] == "awaiting"
    assert client.get("/v1/confirmations").json()["pending"] == []


def test_unanswered_confirmation_expires_to_a_refusal(client, app) -> None:
    """Over HTTP nothing resolves the prompt, so it must time out into 'declined'."""
    set_script(
        app,
        [
            tool_reply("shell_run", {"command": "git status"}),
            BrainReply(text="I did not run it."),
        ],
    )
    body = client.post(
        "/v1/turn/text", json={"text": "run git status", "speak": False}
    ).json()
    attempt = body["turn"]["tools"][0]
    assert attempt["decision"] == "declined"
    assert attempt["ok"] is False


def test_destructive_tool_is_refused_over_http(client, app) -> None:
    set_script(
        app, [tool_reply("shell_run", {"command": "rm -rf /"}), BrainReply(text="No.")]
    )
    body = client.post(
        "/v1/turn/text", json={"text": "wipe everything", "speak": False}
    ).json()
    assert body["turn"]["tools"][0]["decision"] == "denied"


def test_empty_text_is_rejected_gracefully(client) -> None:
    body = client.post("/v1/turn/text", json={"text": "  ", "speak": False}).json()
    assert body["error"] == "empty_transcript"


# --------------------------------------------------------------------------
# confirmations and audit
# --------------------------------------------------------------------------


def test_confirming_an_unknown_id_is_a_404(client) -> None:
    response = client.post("/v1/confirm", json={"id": "nope", "approved": True})
    assert response.status_code == 404


def test_audit_endpoint_exposes_recent_events(client) -> None:
    client.post("/v1/turn/text", json={"text": "hello", "speak": False})
    body = client.get("/audit?limit=20").json()
    assert body["count"] >= 1
    assert any(entry["event"] == "turn" for entry in body["entries"])


# --------------------------------------------------------------------------
# speech endpoints
# --------------------------------------------------------------------------


def test_stt_reports_unavailable_with_a_reason(client) -> None:
    service = client.app.state.jarvis
    if service.stt.available():
        pytest.skip("the voice extra is installed, so STT is expected to work")

    response = client.post(
        "/stt", files={"file": ("clip.webm", b"not-really-audio", "audio/webm")}
    )
    assert response.status_code == 503
    assert "voice" in response.json()["detail"].lower()


def test_stt_rejects_an_empty_upload(client) -> None:
    response = client.post("/stt", files={"file": ("clip.webm", b"", "audio/webm")})
    assert response.status_code == 400


def test_turn_rejects_an_empty_upload(client) -> None:
    response = client.post("/v1/turn", files={"file": ("clip.webm", b"", "audio/webm")})
    assert response.status_code == 400


@pytest.mark.skipif(platform.system() != "Windows", reason="the fallback voice is Windows-only")
def test_tts_returns_a_real_wav(client) -> None:
    service = client.app.state.jarvis
    if not service.tts.available():
        pytest.skip("no speech engine available on this machine")

    response = client.post("/tts", json={"text": "Yes, sir."})
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.content[:4] == b"RIFF"
    assert len(response.content) > 1000


def test_turn_includes_audio_when_it_can_speak(client) -> None:
    service = client.app.state.jarvis
    if not service.tts.available():
        pytest.skip("no speech engine available")

    body = client.post("/v1/turn/text", json={"text": "hello there"}).json()
    assert body["reply"]
    assert body.get("audio_b64")
    assert base64.b64decode(body["audio_b64"])[:4] == b"RIFF"
