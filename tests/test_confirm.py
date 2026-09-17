"""Confirmation tests.

The failure this file exists to prevent: a "no" that gets read as a "yes", or a
silence that gets read as consent. Both are checked explicitly.
"""

from __future__ import annotations

import asyncio

import pytest
from apps.gateway.confirm import classify_reply, describe_call

# --------------------------------------------------------------------------
# spoken answers
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "phrase",
    ["yes", "Yes please", "yeah", "sure", "go ahead", "do it", "ok", "confirm", "make it so", "YES!"],
)
def test_affirmatives(phrase: str) -> None:
    assert classify_reply(phrase) == "yes", phrase


@pytest.mark.parametrize(
    "phrase",
    ["no", "No thanks", "nope", "don't", "cancel", "never mind", "stop", "leave it", "not now", "deny"],
)
def test_negatives(phrase: str) -> None:
    assert classify_reply(phrase) == "no", phrase


@pytest.mark.parametrize("phrase", ["don't do it", "no, do not send it", "dont send that"])
def test_negation_wins_over_embedded_affirmatives(phrase: str) -> None:
    """"don't do it" contains "do it" -- negatives must be checked first."""
    assert classify_reply(phrase) == "no", phrase


@pytest.mark.parametrize(
    "phrase", ["maybe later", "what exactly would you delete?", "hmm", "", "   ", "run a search instead"]
)
def test_unclear_answers_are_not_consent(phrase: str) -> None:
    assert classify_reply(phrase) == "unclear", phrase


async def test_unclear_never_resolves_a_pending_request(harness) -> None:
    broker = harness.broker
    task = asyncio.create_task(broker.request("fs_write", {"path": "x"}, "write x"))
    await asyncio.sleep(0.01)

    assert broker.resolve_by_voice("what do you mean?") is None
    assert broker.oldest() is not None  # still waiting, not silently approved

    broker.cancel_all()
    await task


# --------------------------------------------------------------------------
# broker
# --------------------------------------------------------------------------


async def test_approval_resolves_true(harness) -> None:
    broker = harness.broker
    task = asyncio.create_task(broker.request("fs_write", {"path": "a.txt"}, "write a.txt"))
    await asyncio.sleep(0.01)

    pending = broker.oldest()
    assert pending is not None
    assert broker.resolve(pending.id, True) is True
    assert await task is True


async def test_refusal_resolves_false(harness) -> None:
    broker = harness.broker
    task = asyncio.create_task(broker.request("shell_run", {"command": "git status"}, "run git status"))
    await asyncio.sleep(0.01)

    pending = broker.oldest()
    broker.resolve(pending.id, False)
    assert await task is False


async def test_silence_is_a_refusal(harness) -> None:
    """No answer within the timeout must mean no, never yes."""
    broker = harness.broker
    assert broker.timeout_seconds <= 1, "fixture should use a short timeout for this test"
    approved = await broker.request("mail_send", {"to": "a@b.c"}, "send mail")
    assert approved is False
    assert broker.oldest() is None


async def test_voice_answer_resolves_the_held_call(harness) -> None:
    broker = harness.broker
    task = asyncio.create_task(broker.request("app_launch", {"app": "notepad"}, "open notepad"))
    await asyncio.sleep(0.01)

    answered = broker.resolve_by_voice("yes, go ahead")
    assert answered is not None
    assert await task is True


async def test_resolving_an_unknown_id_is_rejected(harness) -> None:
    assert harness.broker.resolve("does-not-exist", True) is False


async def test_resolving_twice_is_rejected(harness) -> None:
    broker = harness.broker
    task = asyncio.create_task(broker.request("app_launch", {"app": "notepad"}, "open notepad"))
    await asyncio.sleep(0.01)
    pending = broker.oldest()
    assert broker.resolve(pending.id, True) is True
    assert broker.resolve(pending.id, True) is False
    assert await task is True


async def test_shutdown_refuses_everything_held(harness) -> None:
    broker = harness.broker
    tasks = [
        asyncio.create_task(broker.request("fs_delete", {"path": f"f{i}.txt"}, "delete file"))
        for i in range(3)
    ]
    await asyncio.sleep(0.01)
    assert broker.cancel_all() == 3
    assert await asyncio.gather(*tasks) == [False, False, False]


async def test_every_decision_reaches_the_audit_log(harness) -> None:
    broker = harness.broker
    task = asyncio.create_task(broker.request("fs_write", {"path": "a.txt"}, "write a.txt"))
    await asyncio.sleep(0.01)
    broker.resolve(broker.oldest().id, True, source="ui")
    await task

    entries = [e for e in harness.audit.tail(50) if e.get("event") == "confirmation"]
    assert entries and entries[-1]["approved"] is True and entries[-1]["source"] == "ui"


async def test_pending_requests_are_visible_to_clients(harness) -> None:
    broker = harness.broker
    task = asyncio.create_task(broker.request("fs_delete", {"path": "gone.txt"}, "delete gone.txt"))
    await asyncio.sleep(0.01)
    snapshot = broker.snapshot()
    assert len(snapshot) == 1
    assert snapshot[0]["tool"] == "fs_delete"
    assert snapshot[0]["summary"] == "delete gone.txt"
    assert snapshot[0]["seconds_left"] > 0
    broker.cancel_all()
    await task


# --------------------------------------------------------------------------
# spoken summaries
# --------------------------------------------------------------------------


def test_summaries_are_written_for_the_ear() -> None:
    assert describe_call("shell_run", {"command": "git status"}) == "run the command git status"
    assert describe_call("fs_delete", {"path": "C:\\notes\\old.txt"}).startswith("delete")
    assert describe_call("app_launch", {"app": "notepad"}) == "open notepad"
    assert (
        describe_call("mail_send", {"to": "x@y.z", "subject": "hi"})
        == "send an email to x@y.z, subject hi"
    )


def test_long_values_are_truncated() -> None:
    summary = describe_call("fs_write", {"path": "C:\\n\\" + "x" * 500 + ".txt", "content": "y"})
    assert len(summary) < 200
    assert summary.endswith("...")


def test_shell_commands_are_never_silently_truncated() -> None:
    """The danger of a command can live in its tail; approval requires hearing it all."""
    command = "git commit -m " + "x" * 300
    summary = describe_call("shell_run", {"command": command})
    assert command in summary, "the command tail must survive the summary"


def test_mail_summaries_disclose_subject_and_body() -> None:
    """Approve this email means nothing without hearing WHAT would be sent."""
    summary = describe_call(
        "mail_send",
        {"to": "boss@x.com", "subject": "raise", "body": "I resign effective today"},
    )
    assert "boss@x.com" in summary
    assert "raise" in summary
    assert "I resign effective today" in summary


def test_app_launch_summaries_disclose_the_argument() -> None:
    """Open powershell alone hides the payload; the argument must be spoken."""
    summary = describe_call(
        "app_launch", {"app": "powershell", "argument": "-NoProfile -Command Get-Date"}
    )
    assert "powershell" in summary
    assert "-NoProfile -Command Get-Date" in summary
