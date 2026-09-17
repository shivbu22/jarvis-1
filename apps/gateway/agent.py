"""The turn loop.

One turn is: assemble context, let the brain think, and for every tool it asks
for, put that request through the gate before anything happens. The loop ends when
the brain answers in prose rather than asking for another tool.

Two properties are deliberately enforced here rather than hoped for:

**A bounded loop.** The brain gets at most ``max_tool_iterations`` rounds of tools.
On the last round it is called again with tools disabled and asked to summarise
what it has, so a confused model produces a sentence instead of an exhausted
budget or a silently truncated turn.

**No unconfirmed execution path.** ``allow_confirmation=False`` (the HTTP one-shot
default) cannot execute a confirm-tier tool at all -- the call is returned as
awaiting approval, and the caller has to resolve it through the broker. A caller
cannot wait its way past the gate.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from .brain import Brain
from .confirm import ConfirmationBroker, describe_call
from .prompts import build_system_prompt
from .tools import ToolRegistry

logger = logging.getLogger(__name__)


@dataclass
class ToolAttempt:
    name: str
    args: dict[str, Any]
    tier: str
    decision: str  # auto | confirmed | declined | timeout | denied | error | awaiting
    ok: bool
    duration_ms: int = 0
    summary: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "args": self.args,
            "tier": self.tier,
            "decision": self.decision,
            "ok": self.ok,
            "duration_ms": self.duration_ms,
            "summary": self.summary,
        }


@dataclass
class TurnResult:
    transcript: str
    reply: str
    session_id: str = "default"
    tools: list[ToolAttempt] = field(default_factory=list)
    awaiting: list[dict[str, Any]] = field(default_factory=list)
    iterations: int = 0
    brain: str = ""
    error: str | None = None
    duration_ms: int = 0
    # Provider-reported timing, when the brain exposes it (e.g. Ollama's
    # ``total_duration`` nanosecond wall clock). Used by the audit trail.
    duration_ns: int = 0
    prompt_eval_count: int = 0
    eval_count: int = 0
    # The model the brain actually used this turn (final resolved name, e.g.
    # "jarvis-brain:4b" when Ollama resolves the tuned local brain).
    resolved_model: str = ""
    model_note: str = ""

    @property
    def used_tools(self) -> list[str]:
        return [attempt.name for attempt in self.tools]

    def as_dict(self) -> dict[str, Any]:
        return {
            "transcript": self.transcript,
            "reply": self.reply,
            "session_id": self.session_id,
            "tools": [attempt.as_dict() for attempt in self.tools],
            "awaiting": self.awaiting,
            "iterations": self.iterations,
            "brain": self.brain,
            "error": self.error,
            "duration_ms": self.duration_ms,
        }


class Agent:
    def __init__(
        self,
        *,
        settings: Any,
        brain: Brain,
        registry: ToolRegistry,
        broker: ConfirmationBroker,
        audit: Any,
        bus: Any,
        memory: Any,
    ) -> None:
        self.settings = settings
        self.brain = brain
        self.registry = registry
        self.broker = broker
        self.audit = audit
        self.bus = bus
        self.memory = memory

    # -- prompt -----------------------------------------------------------

    def system_prompt(self, extra_context: str = "") -> str:
        tiers = self.registry.names_by_tier()
        return build_system_prompt(
            tool_names=self.registry.names(),
            auto_tools=tiers.get("auto", []),
            confirm_tools=tiers.get("confirm", []),
            denied_tools=tiers.get("deny", []),
            extra_context=extra_context,
        )

    # -- the turn ---------------------------------------------------------

    async def run_turn(
        self,
        text: str,
        *,
        session_id: str = "default",
        allow_confirmation: bool = True,
        on_token: Any | None = None,
    ) -> TurnResult:
        """Run one conversational turn.

        ``on_token``, when given, receives pieces of the *final* spoken sentence as
        the brain forms them, so the UI can show text while it is still being
        written. It is only used on the last brain call -- the ones that decide on a
        tool are working, not speaking, and announcing their internals would be noise
        for the master to read.
        """
        started = time.perf_counter()
        transcript = (text or "").strip()
        result = TurnResult(transcript=transcript, reply="", session_id=session_id)
        if not transcript:
            result.reply = "I did not catch that."
            result.error = "empty_transcript"
            return result

        self.memory.add_turn(session_id, "user", transcript)
        if self.bus is not None:
            self.bus.publish({"type": "transcript", "text": transcript, "session_id": session_id})

        messages: list[dict[str, Any]] = [{"role": "system", "content": self.system_prompt()}]
        messages.extend(self.memory.history(session_id, limit=self.settings.history_turns))
        if not messages or messages[-1].get("content") != transcript:
            messages.append({"role": "user", "content": transcript})

        tools = self.registry.specs_for_brain()
        max_iterations = max(1, int(self.settings.max_tool_iterations))

        for iteration in range(1, max_iterations + 1):
            result.iterations = iteration
            # Streaming policy, in one place. The final spoken answer can come at any
            # iteration -- most turns here decide on a tool at 1 and speak at 2 -- so
            # waiting for the last allowed iteration would stream nothing. Instead the
            # *tool rounds* run silently and every call from here on streams; when the
            # model answers in prose the master has watched the sentence arrive, and
            # when it asks for a tool the round trip is over in seconds anyway. A
            # streamed call that turns out to want tools has cost nothing: deltas are
            # only shown to the master via on_token, and the loop proceeds normally.
            streaming = on_token is not None and iteration > 1
            if streaming:
                reply = await self._generate_streaming(messages, tools, on_token)
            else:
                reply = await self.brain.generate(messages, tools)
            result.brain = getattr(self.brain, "last_used", None) or getattr(self.brain, "name", "")
            # Track the provider's own timing so the audit trail can report real
            # latency rather than just the delta between HTTP request and response.
            result.duration_ns = getattr(reply, "duration_ns", 0)
            result.prompt_eval_count = getattr(reply, "prompt_eval_count", 0)
            result.eval_count = getattr(reply, "eval_count", 0)
            # The brain's own resolved name this turn (e.g. "jarvis-brain:4b" when
            # Ollama picks the tuned local variant). Prefer anything the brain layer
            # recorded over the generic last_used label.
            result.resolved_model = (getattr(reply, "model", "") or "").strip() or getattr(self.brain, "model", "") or ""
            result.model_note = getattr(reply, "model_note", "") or getattr(self.brain, "model_note", "") or ""

            if reply.error:
                result.reply = self._speakable_error(reply.error)
                result.error = reply.error
                self._finish(result, started)
                self._audit_turn(result, ok=False)
                return result

            if not reply.wants_tools:
                text = reply.text.strip()
                if not text:
                    # An empty answer with no tool call is a specific, detectable
                    # failure: a reasoning model spent its whole token budget
                    # thinking and never produced the sentence. It is worth one
                    # narrowed retry -- tools removed, and an explicit instruction
                    # to answer now -- because the alternative is saying "Done."
                    # to a question that was actually understood.
                    text = await self._insist_on_an_answer(messages)
                result.reply = text or "I could not put that into words."
                self.memory.add_turn(session_id, "assistant", result.reply)
                self._finish(result, started)
                self._audit_turn(result, ok=True)
                return result

            messages.append(reply.as_message())
            for call in reply.tool_calls:
                attempt, message = await self._handle_tool_call(call, allow_confirmation=allow_confirmation)
                result.tools.append(attempt)
                messages.append(message)

        # Iteration cap reached: ask for prose with no tools so the turn still ends
        # with something the master can hear.
        final = await self.brain.generate(messages, [])
        text = (final.text or "").strip()
        if not text and not final.error:
            text = await self._insist_on_an_answer(messages)
        result.reply = text or self._summarise_attempts(result)
        if final.error:
            result.error = final.error
        self.memory.add_turn(session_id, "assistant", result.reply)
        self._finish(result, started)
        self._audit_turn(result, ok=True)
        return result

    async def _generate_streaming(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], on_token: Any
    ) -> Any:
        """Stream through the router to whichever brain answers, publishing as it goes.

        Only the OpenAI-compatible brains can stream today; Gemini and the in-process
        fallback still answer through ``generate``, which this handles by simply not
        streaming. The router's failover semantics are preserved: if the streaming
        attempt dies part way, the next brain is tried with a fresh, non-streamed
        call rather than a half-spoken sentence.
        """
        for brain in getattr(self.brain, "brains", [self.brain]):
            stream = getattr(brain, "stream_tokens", None)
            if stream is None:
                continue
            announced = False

            # Deltas reach the UI through exactly one path: the ``on_token`` callback
            # the transport handed in. The bus deliberately does not carry them -- the
            # websocket pump relays bus events to the same socket, so publishing here
            # too would double every word (measured: "TheThe weather weather").
            async def publish(piece: str) -> None:
                nonlocal announced
                if not announced:
                    announced = True
                result = on_token(piece)
                if hasattr(result, "__await__"):
                    await result

            try:
                reply = await stream(messages, tools, publish)
            except Exception as exc:  # fall through to the next brain
                logger.warning("streaming with %s failed: %s", brain.name, exc)
                continue
            self.brain.last_used = brain.name  # keep the router's bookkeeping honest
            return reply

        return await self.brain.generate(messages, tools)

    async def _insist_on_an_answer(self, messages: list[dict[str, Any]]) -> str:
        """Ask again for prose, once, with every tool taken away.

        Local reasoning models can consume their whole output budget on hidden
        reasoning and return no visible text at all. Removing the tools and saying
        so plainly gives the model nothing to deliberate about, which measurably
        turns that failure into a real sentence. Errors here are not fatal: an
        empty result simply falls back to the caller's apology.
        """
        nudge = (
            "Answer the master's last message now, in one short spoken sentence. "
            "Do not reason aloud, do not mention tools, and do not explain yourself."
        )
        retry = [*messages, {"role": "system", "content": nudge}]
        try:
            followup = await self.brain.generate(retry, [])
        except Exception as exc:  # a retry must never end the turn
            logger.warning("the follow-up answer failed: %s", exc)
            return ""
        return (followup.text or "").strip()

    async def _handle_tool_call(
        self, call: Any, *, allow_confirmation: bool
    ) -> tuple[ToolAttempt, dict[str, Any]]:
        spec = self.registry.get(call.name)
        if spec is None:
            attempt = ToolAttempt(
                name=call.name, args=call.arguments, tier="confirm", decision="error", ok=False,
                summary="unknown tool",
            )
            return attempt, {
                "role": "tool",
                "tool_call_id": call.id,
                "name": call.name,
                "content": f"No tool named '{call.name}' exists. Use one of your listed tools.",
            }

        decision = self.registry.policy.screen(
            spec.name, call.arguments, executing=spec.executing
        )
        confirmed = False

        if decision.needs_confirmation and decision.allowed:
            if not allow_confirmation:
                attempt = ToolAttempt(
                    name=spec.name,
                    args=call.arguments,
                    tier=decision.tier,
                    decision="awaiting",
                    ok=False,
                    summary="needs the master's permission",
                )
                return attempt, {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": spec.name,
                    "content": (
                        "This action requires the master's permission, which cannot be "
                        "obtained in this request. Tell them what you need and stop."
                    ),
                }

            summary = describe_call(spec.name, call.arguments)
            if self.bus is not None:
                self.bus.publish(
                    {
                        "type": "tool_pending_confirmation",
                        "tool": spec.name,
                        "args": call.arguments,
                        "summary": summary,
                    }
                )
            approved = await self.broker.request(spec.name, call.arguments, summary)
            confirmed = approved
            if not approved:
                attempt = ToolAttempt(
                    name=spec.name,
                    args=call.arguments,
                    tier=decision.tier,
                    decision="declined",
                    ok=False,
                    summary=summary,
                )
                return attempt, {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": spec.name,
                    "content": (
                        f"The master did not approve this action, so it did not run. "
                        f"({summary})"
                    ),
                }

        if self.bus is not None:
            self.bus.publish(
                {"type": "tool_start", "tool": spec.name, "args": call.arguments}
            )

        result = await self.registry.call(spec.name, call.arguments, confirmed=confirmed)

        if self.bus is not None:
            payload = result.describe()
            payload["type"] = "tool_result"
            self.bus.publish(payload)

        decision_label = (
            "denied" if result.error == "policy_denied"
            else "error" if result.error
            else "confirmed" if confirmed
            else "auto"
        )
        attempt = ToolAttempt(
            name=spec.name,
            args=call.arguments,
            tier=result.tier,
            decision=decision_label,
            ok=result.ok,
            duration_ms=result.duration_ms,
            summary=result.content[:300],
        )
        return attempt, result.as_message(call.id)

    # -- helpers ----------------------------------------------------------

    def _finish(self, result: TurnResult, started: float) -> TurnResult:
        result.duration_ms = int((time.perf_counter() - started) * 1000)
        if self.bus is not None:
            payload = result.as_dict()
            payload["type"] = "turn_complete"
            self.bus.publish(payload)
        return result

    def _audit_turn(self, result: TurnResult, *, ok: bool) -> None:
        self.audit.record(
            "turn",
            session_id=result.session_id,
            transcript=result.transcript,
            reply=result.reply,
            brain=result.brain,
            tools=[attempt.as_dict() for attempt in result.tools],
            iterations=result.iterations,
            error=result.error,
            ok=ok,
            duration_ms=result.duration_ms,
            duration_ns=result.duration_ns,
            prompt_eval_count=result.prompt_eval_count,
            eval_count=result.eval_count,
            resolved_model=result.resolved_model,
            model_note=result.model_note,
        )

    @staticmethod
    def _speakable_error(error: str) -> str:
        """A brain failure, phrased for the ear rather than for a log file."""
        cleaned = error.strip().rstrip(".")
        if not cleaned:
            return "I could not reach my language model just now."
        return f"I could not answer that. {cleaned}."

    @staticmethod
    def _summarise_attempts(result: TurnResult) -> str:
        if not result.tools:
            return "I could not work that out."
        failed = [attempt for attempt in result.tools if not attempt.ok]
        if failed:
            return "I tried, but it did not work out."
        return "That is done."
