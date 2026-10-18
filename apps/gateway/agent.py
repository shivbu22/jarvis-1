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

import contextlib
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from .brain import Brain
from .brain.base import (
    ToolCall,
    looks_like_text_tool_call,
    normalize_recovered_arguments,
    recover_text_tool_call,
)
from .confirm import ConfirmationBroker, describe_call
from .prompts import build_system_prompt
from .tools import ToolRegistry

logger = logging.getLogger(__name__)

#: Tools that *suspend* the turn. Recovering one of these from text is not
#: helpful even when it parses: the observed model wrote ask_user as prose to make
#: conversation, and honouring it turned a greeting into a stall for the whole
#: question window. A written-out call to these is treated as unusable text, so
#: the model is asked again for a sentence instead.
SUSPENDING_TOOLS = frozenset({"ask_user"})


def _salvage_suspension_text(raw_arguments: Any) -> str:
    """The words the model put inside a call that must not suspend the turn.

    Measured live: asked for a greeting, the model streamed
    ``{"name":"ask_user","parameters":"I am working, sir."}`` -- its actual
    reply is the payload. Saying that (without waiting for anyone) is both
    faithful and un-stallable; refusing the call and then insisting on a fresh
    answer turned the same turn into "I could not work that out.".
    """
    if isinstance(raw_arguments, str):
        return raw_arguments.strip()
    if isinstance(raw_arguments, dict):
        for value in raw_arguments.values():
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


async def _maybe_await(value: Any) -> Any:
    """Await ``value`` if it is awaitable.

    Callbacks here come from two places with different habits -- an HTTP handler
    that is async, and a test that is not -- so both are accepted rather than
    forcing one shape on either.
    """
    if hasattr(value, "__await__"):
        return await value
    return value


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
        tps = round(self.eval_count / (self.duration_ms / 1000.0), 1) if self.duration_ms > 0 and self.eval_count > 0 else 0.0
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
            "prompt_eval_count": self.prompt_eval_count,
            "eval_count": self.eval_count,
            "tokens_per_second": tps,
            "resolved_model": self.resolved_model,
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
        profile: Any | None = None,
        context_mgr: Any | None = None,
        evolution: Any | None = None,
    ) -> None:
        self.settings = settings
        self.brain = brain
        self.registry = registry
        self.broker = broker
        self.audit = audit
        self.bus = bus
        self.memory = memory
        self.profile = profile
        self.context_mgr = context_mgr
        self.evolution = evolution

    # -- prompt -----------------------------------------------------------

    def system_prompt(self, extra_context: str = "") -> str:
        tiers = self.registry.names_by_tier()
        profile_part = ""
        if self.profile is not None and hasattr(self.profile, "build_prompt_section"):
            p_sec = self.profile.build_prompt_section()
            if p_sec:
                profile_part = f"User profile & preferences: {p_sec}"
        evolution_part = ""
        if self.evolution is not None and hasattr(self.evolution, "build_prompt_section"):
            e_sec = self.evolution.build_prompt_section(extra_context)
            if e_sec:
                evolution_part = e_sec
        combined_extra = "\n\n".join(part for part in [profile_part, evolution_part, extra_context] if part)
        return build_system_prompt(
            tool_names=self.registry.names(),
            auto_tools=tiers.get("auto", []),
            confirm_tools=tiers.get("confirm", []),
            denied_tools=tiers.get("deny", []),
            extra_context=combined_extra,
        )

    # -- memory -----------------------------------------------------------

    def recall(self, transcript: str, limit: int = 4) -> str:
        """Notes the master saved that look relevant to what was just said.

        Put into the prompt rather than left to a tool call: a model that has to
        *decide* to consult memory asks the master to repeat things they already
        told it, which is the exact failure that saved notes exist to prevent.
        The store's own ``recall`` keeps this from dragging in unrelated notes.
        """
        if not transcript.strip():
            return ""
        try:
            rows = self.memory.recall(transcript, limit=limit)
        except Exception as exc:  # memory is an enhancement, never a turn-killer
            logger.warning("recall failed: %s", exc)
            return ""
        if not rows:
            return ""
        lines = ["Things the master asked you to remember, which look relevant now:"]
        for row in rows:
            try:
                when = time.strftime("%d %B", time.localtime(float(row["created_at"])))
            except (KeyError, TypeError, ValueError):
                when = "earlier"
            lines.append(f"- {row.get('text', '')} (noted {when})")
        return "\n".join(lines)

    # -- the turn ---------------------------------------------------------

    async def run_turn(
        self,
        text: str,
        *,
        session_id: str = "default",
        allow_confirmation: bool = True,
        on_token: Any | None = None,
        on_round: Any | None = None,
    ) -> TurnResult:
        """Run one conversational turn.

        ``on_token``, when given, receives pieces of the reply as the brain forms
        them, so the UI can show text while it is still being written.

        ``on_round`` reports the *shape* of each round as ``("start" | "prose",
        iteration)``. It exists so speech can be gated on rounds that are actually
        the answer: a round that calls a tool may narrate first ("Let me check"),
        and speaking that aloud is both noise and a lie when the real answer comes
        later. Text still streams immediately either way -- only the voice waits.
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

        # 1. Check for dynamic command learning intent (English, Hinglish, or Hindi)
        from .command_learner import parse_learning_intent, learn_command
        learn_match = parse_learning_intent(transcript)
        if learn_match is not None:
            trigger_phrase, action_payload, desc = learn_match
            learn_res = learn_command(self.memory, trigger=trigger_phrase, action=action_payload, description=desc)
            result.reply = learn_res.get("reply", f"Learned command for '{trigger_phrase}'.")
            self._finish(result, started)
            self._audit_turn(result, ok=True)
            return result

        # 2. Check against active dynamically learned custom commands
        matched_cmd = self.memory.match_custom_command(transcript)
        transcript_for_intent = matched_cmd["action_payload"] if matched_cmd else transcript

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt(extra_context=self.recall(transcript))}
        ]
        from .hinglish import normalize_hinglish_intent

        normalized = normalize_hinglish_intent(transcript_for_intent)

        # 3. Fast-Path Direct Execution (<30ms for common OS, browser, media directives)
        fast_action = await self._try_fast_path(normalized)
        if fast_action is not None:
            tool_name, tool_args, reply_template = fast_action
            if self.bus is not None:
                self.bus.publish({"type": "tool_start", "tool": tool_name, "args": tool_args})
            t_res = await self.registry.call(tool_name, tool_args, confirmed=True)
            if self.bus is not None:
                payload = t_res.describe()
                payload["type"] = "tool_result"
                self.bus.publish(payload)
            if t_res.ok:
                result.reply = reply_template.format(**tool_args) if "{" in reply_template else reply_template
                attempt = ToolAttempt(
                    name=tool_name,
                    args=tool_args,
                    tier="auto",
                    decision="auto",
                    ok=True,
                    duration_ms=t_res.duration_ms,
                    summary=t_res.content[:200] if t_res.content else "",
                )
                result.tools.append(attempt)
                self.memory.add_turn(session_id, "assistant", result.reply)
                self._finish(result, started)
                self._audit_turn(result, ok=True)
                return result
        prompt_content = f"{normalized} (Master spoke: '{transcript}')" if normalized != transcript else transcript

        raw_history = self.memory.history(session_id, limit=20) if hasattr(self.memory, "history") else []
        prior_turns = [
            turn for turn in raw_history[:-1]
            if turn.get("content") != transcript
        ] if len(raw_history) > 1 and raw_history[-1].get("content") == transcript else (raw_history[:-1] if raw_history else [])

        system_text = self.system_prompt(extra_context=self.recall(transcript))
        if self.context_mgr is not None:
            history_for_context = list(prior_turns) + [{"role": "user", "content": prompt_content}]
            messages = self.context_mgr.prepare_messages(
                system_prompt=system_text,
                conversation_history=history_for_context,
            )
            if not messages or messages[0].get("role") != "system":
                messages.insert(0, {"role": "system", "content": system_text})
        else:
            messages = [{"role": "system", "content": system_text}]
            for t in prior_turns[-6:]:
                messages.append(t)
            messages.append({"role": "user", "content": prompt_content})

        combined_query = f"{transcript} {normalized} {transcript_for_intent}" if normalized != transcript else transcript
        tools = self.registry.specs_for_query(combined_query) if hasattr(self.registry, "specs_for_query") else self.registry.specs_for_brain()
        max_iterations = max(1, int(self.settings.max_tool_iterations))

        for iteration in range(1, max_iterations + 1):
            result.iterations = iteration
            if on_round is not None:
                await _maybe_await(on_round("start", iteration))
            # Streaming policy, in one place. Every round streams, including the
            # first: a plain question is answered in one round, and holding the
            # stream back until round two meant the most common kind of turn was
            # the one that could not start speaking until it had finished. A
            # streamed round that turns out to want tools costs nothing -- deltas
            # are shown to the master via on_token and the loop proceeds normally.
            streaming = on_token is not None
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
                recovered = recover_text_tool_call(reply.text, self.registry.names())
                if recovered is not None and recovered[0] in SUSPENDING_TOOLS:
                    logger.warning(
                        "ignoring %s written as text: it would suspend the turn", recovered[0]
                    )
                    # The JSON already streamed to the master's screen; the orb is
                    # told to take the message back so no garbage remains. What
                    # the model meant to *say* is salvaged from the call's payload
                    # and spoken as the reply -- the turn never waits on anyone.
                    if on_round is not None:
                        await _maybe_await(on_round("retract", iteration))
                    reply.text = _salvage_suspension_text(recovered[1])
                    recovered = None
                if recovered is not None:
                    # The model meant to call a tool and wrote it out instead. Doing
                    # what it plainly asked for beats speaking the JSON at the master.
                    name, raw_arguments = recovered
                    spec = self.registry.get(name)
                    required = list((spec.parameters.get("required") if spec else None) or [])
                    original = reply.text
                    reply.tool_calls = [
                        ToolCall(
                            id=f"recovered_{iteration}",
                            name=name,
                            arguments=normalize_recovered_arguments(raw_arguments, required),
                            raw=original,
                        )
                    ]
                    reply.text = ""
                    logger.info("executing %s recovered from the model's text", name)
                    if self.audit is not None:
                        self.audit.record(
                            "tool_call_recovered",
                            tool=name,
                            session_id=session_id,
                            wording=original[:300],
                        )

            if not reply.wants_tools:
                text = reply.text.strip()
                speakable = bool(text)
                if looks_like_text_tool_call(reply.text):
                    # Tool syntax that named nothing real: not a sentence, not a call,
                    # and never something to read aloud.
                    logger.warning("unusable tool syntax in a reply: %r", reply.text[:200])
                    text = ""
                    speakable = False
                    if on_round is not None:
                        await _maybe_await(on_round("retract", iteration))
                # The round is announced as the answer only once it is known to be
                # a sentence. Announcing first would let the transport speak the
                # mangled text before this check could reject it.
                if speakable and on_round is not None:
                    await _maybe_await(on_round("prose", iteration))
                if not text:
                    # An empty answer with no tool call is a specific, detectable
                    # failure: a reasoning model spent its whole token budget
                    # thinking and never produced the sentence. It is worth one
                    # narrowed retry -- tools removed, and an explicit instruction
                    # to answer now -- because the alternative is saying "Done."
                    # to a question that was actually understood.
                    text = await self._insist_on_an_answer(messages)
                # If the model still cannot phrase it, describe what actually
                # happened. "I could not put that into words" is a lie when the
                # tools ran and succeeded -- the master needs to know the action
                # was carried out, which is the thing they will act on.
                result.reply = text or self._summarise_attempts(result)
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
                        f"ACTION NOT PERFORMED -- the master declined, so nothing happened. "
                        f"Do not claim the action was done; say you stood down instead. "
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
        if getattr(self, "profile", None) is not None and hasattr(self.profile, "infer_from_interaction"):
            used_tool = result.tools[0].name if result.tools else ""
            self.profile.infer_from_interaction(result.transcript, tool_used=used_tool)
        if getattr(self, "evolution", None) is not None:
            if hasattr(self.evolution, "inspect_and_learn_from_correction"):
                with contextlib.suppress(Exception):
                    self.evolution.inspect_and_learn_from_correction(result.transcript)
            if hasattr(self.evolution, "record_turn_for_fine_tuning"):
                with contextlib.suppress(Exception):
                    self.evolution.record_turn_for_fine_tuning(
                        user_text=result.transcript,
                        assistant_reply=result.reply,
                        tools_used=[t.as_dict() for t in result.tools],
                        quality_score=1.0 if not result.error else 0.5,
                    )
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

    async def _try_fast_path(self, text: str) -> tuple[str, dict[str, Any], str] | None:
        """Instantaneous local execution path for unambiguous OS, media, and navigation directives."""
        clean = (text or "").strip().lower()

        # 1. App Launch / Open Service
        m_open = re.match(r"^(?:(?:hey\s+)?jarvis[,:\s]*)?(?:please\s*)?(?:open|launch)\s+([a-zA-Z0-9_\- .]+)$", clean)
        if m_open:
            app_target = m_open.group(1).strip()
            if app_target not in {"questions", "source", "file", "tab", "eyes", "door"}:
                web_services = {
                    "youtube", "yt", "youtube.com", "music.youtube.com", "spotify", "netflix",
                    "google", "google.com", "github", "github.com", "reddit", "reddit.com",
                    "twitter", "x", "x.com", "chatgpt", "chatgpt.com", "claude", "claude.ai",
                    "gemini", "gmail", "gmail.com", "mail.google.com"
                }
                if app_target in web_services or app_target.endswith((".com", ".org", ".net", ".io", ".in", ".ai")):
                    return ("web_open", {"target": app_target}, f"Opening {app_target.title()} in your browser, sir.")
                return ("app_launch", {"app": app_target}, f"Opened {app_target.title()}, sir.")

        # 2. YouTube Play
        m_yt = re.match(r"^(?:(?:hey\s+)?jarvis[,:\s]*)?(?:please\s*)?play\s+(.+?)(?:\s+on\s+youtube)?$", clean)
        if m_yt:
            query = m_yt.group(1).strip()
            if query not in {"music", "song", "audio", "video"}:
                return ("youtube_play", {"query": query}, f"Playing '{query}' on YouTube, sir.")

        # 3. Media Controls
        if re.match(r"^(?:(?:hey\s+)?jarvis[,:\s]*)?(?:pause\s*(?:music|playback|audio)?|stop\s+music)$", clean):
            return ("media_control", {"action": "pause"}, "Music paused, sir.")

        if re.match(r"^(?:(?:hey\s+)?jarvis[,:\s]*)?(?:resume\s*(?:music|playback|audio)?|play\s+music)$", clean):
            return ("media_control", {"action": "play"}, "Resuming music, sir.")

        if re.match(r"^(?:(?:hey\s+)?jarvis[,:\s]*)?(?:next\s*(?:song|track)?|skip\s*(?:song|track)?)$", clean):
            return ("media_control", {"action": "next"}, "Skipping to next track, sir.")

        # 4. Volume Control
        if re.match(r"^(?:(?:hey\s+)?jarvis[,:\s]*)?(?:volume\s*up|increase\s+volume|raise\s+volume)$", clean):
            return ("volume_control", {"action": "up"}, "Volume increased, sir.")

        if re.match(r"^(?:(?:hey\s+)?jarvis[,:\s]*)?(?:volume\s*down|decrease\s+volume|lower\s+volume)$", clean):
            return ("volume_control", {"action": "down"}, "Volume decreased, sir.")

        if re.match(r"^(?:(?:hey\s+)?jarvis[,:\s]*)?(?:mute\s*(?:audio|volume)?|unmute\s*(?:audio|volume)?)$", clean):
            return ("media_control", {"action": "mute"}, "Volume toggled, sir.")

        # 5. Desktop and Windows
        if re.match(r"^(?:(?:hey\s+)?jarvis[,:\s]*)?(?:show\s+desktop|minimize\s*(?:all)?\s*windows)$", clean):
            return ("desktop_hotkey", {"keys": ["win", "d"]}, "Minimizing windows, sir.")

        # 6. Lock Screen
        if re.match(r"^(?:(?:hey\s+)?jarvis[,:\s]*)?(?:lock\s*(?:the)?\s*(?:pc|computer|workstation|screen))$", clean):
            return ("system_quick_action", {"action": "lock"}, "Workstation locked, sir.")

        # 7. WhatsApp Messaging
        m_wa = re.match(
            r"^(?:(?:hey\s+)?jarvis[,:\s]*)?(?:please\s*)?(?:send\s+)?(?:a\s+)?whatsapp\s*(?:message)?\s+(?:to\s+)?([a-zA-Z0-9_\-+ ]+?)\s+(?:saying|that|with message|:)?\s*(.+)$",
            clean,
        )
        if m_wa:
            recipient = m_wa.group(1).strip()
            message = m_wa.group(2).strip()
            if recipient not in {"message", "web", "app", "desktop"}:
                return ("whatsapp_send", {"recipient": recipient, "message": message}, f"Sending WhatsApp message to {recipient}, sir.")

        # 8. Active Focused Window Perception ("what window is active", "what app am I using", "which window is focused")
        if re.match(r"^(?:(?:hey\s+)?jarvis[,:\s]*)?(?:what\s+(?:window|app)\s+is\s+(?:currently\s+)?(?:active|focused|open)|which\s+window\s+is\s+focused|current\s+window)$", clean):
            return ("desktop_window_control", {"action": "active"}, "Inspecting the focused window, sir.")

        # 9. Screen Perception ("what is on my screen", "describe my screen")
        if re.match(r"^(?:(?:hey\s+)?jarvis[,:\s]*)?(?:what(?:'s|\s+is)\s+on\s+(?:the\s+|my\s+)?screen|describe\s+(?:the\s+|my\s+)?screen)$", clean):
            return ("screen_describe", {"prompt": "Describe what is visible on the screen, active applications, and text."}, "Analyzing your screen now, sir.")

        # 10. Web Direct Navigation ("go to X", "visit X")
        m_goto = re.match(r"^(?:(?:hey\s+)?jarvis[,:\s]*)?(?:please\s*)?(?:go\s+to|visit)\s+([a-zA-Z0-9_\-\. ]+)$", clean)
        if m_goto:
            target = m_goto.group(1).strip()
            if target not in {"bed", "sleep", "work"}:
                return ("web_open", {"target": target}, f"Navigating to {target.title()}, sir.")

        # 11. Quick Web Search ("search for X", "google X", "look up X")
        m_search = re.match(r"^(?:(?:hey\s+)?jarvis[,:\s]*)?(?:please\s*)?(?:search\s+(?:for\s+|the\s+web\s+for\s+)?|google\s+|look\s+up\s+)(.+)$", clean)
        if m_search:
            s_query = m_search.group(1).strip()
            if s_query:
                return ("web_search", {"query": s_query}, f"Searching the web for '{s_query}', sir.")

        # 12. Quick System Info ("system status", "what time is it", "pc status")
        if re.match(r"^(?:(?:hey\s+)?jarvis[,:\s]*)?(?:system\s+status|pc\s+status|what\s+time\s+is\s+it|check\s+system)$", clean):
            return ("system_info", {}, "Here is the current system status, sir.")

        # 13. Visual Click Element ("click on the chrome button", "click start menu", "double click vscode")
        m_click = re.match(
            r"^(?:(?:hey\s+)?jarvis[,:\s]*)?(?:please\s*)?(?:(double|right)\s+)?click\s+(?:on\s+)?(?:the\s+)?([a-zA-Z0-9_\-\. ]+?)(?:\s+button|\s+icon)?$",
            clean,
        )
        if m_click:
            modifier = (m_click.group(1) or "").strip().lower()
            elem_target = (m_click.group(2) or "").strip()
            if elem_target and elem_target not in {"here", "there", "now", "it"}:
                return (
                    "screen_click_element",
                    {
                        "element": elem_target,
                        "double_click": modifier == "double",
                        "right_click": modifier == "right",
                    },
                    f"Clicking on {elem_target}, sir.",
                )

        # 14. Visual Locate Element ("locate the save button", "find chrome icon")
        m_loc = re.match(
            r"^(?:(?:hey\s+)?jarvis[,:\s]*)?(?:please\s*)?(?:locate|find)\s+(?:the\s+)?([a-zA-Z0-9_\-\. ]+?)(?:\s+on\s+(?:the\s+)?screen)?$",
            clean,
        )
        if m_loc:
            loc_target = (m_loc.group(1) or "").strip()
            if loc_target and loc_target not in {"out", "me", "it", "my"}:
                return ("screen_locate_element", {"element": loc_target}, f"Locating '{loc_target}' on screen, sir.")

        return None

