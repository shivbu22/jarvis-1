"""OpenAI-compatible chat-completions brain.

Ollama and Groq both expose this shape, so both are one subclass over the same
HTTP path. Writing it once also means the error messages -- which are spoken
aloud, and therefore have to be actionable rather than diagnostic -- are written
once: "I cannot reach Ollama" is useless, "Ollama is not running, start it with
ollama serve" is a thing the master can act on.

Token streaming exists here for one reason: a local model generates at a dozen
tokens a second, so a reply that is fully formed in 4 seconds is still 4 seconds of
silence. ``stream_tokens`` hands each piece to a callback the moment it arrives, so
the UI can show the sentence while it is being written. It is an optional side
channel -- ``generate`` stays the one method the agent loop depends on, and nothing
downstream changes when no callback is given.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable
from typing import Any

import httpx

from .base import Brain, BrainError, BrainReply, ToolCall, clean_text, parse_arguments


class OpenAICompatBrain(Brain):
    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout: float = 120.0,
        temperature: float = 0.2,
        extra_headers: dict[str, str] | None = None,
        is_local: bool = False,
        start_hint: str = "",
        max_tokens: int = 0,
    ) -> None:
        self.name = name
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.is_local = is_local
        self.temperature = temperature
        self.start_hint = start_hint
        # 0 leaves the provider's own default in place (cloud brains); a local
        # brain sets this so an unbounded generation cannot eat the turn budget.
        self.max_tokens = max_tokens
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if extra_headers:
            headers.update(extra_headers)
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=httpx.Timeout(timeout, connect=10.0),
        )

    # -- request ----------------------------------------------------------

    async def generate(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> BrainReply:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [self._sanitise(message) for message in messages],
            "temperature": self.temperature,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if self.max_tokens > 0:
            # A spoken reply is short, so an unbounded generation is pure latency.
            # Capping it also means a confused local model cannot burn the whole
            # timeout on one turn.
            payload["max_tokens"] = self.max_tokens

        try:
            response = await self._client.post("/chat/completions", json=payload)
        except httpx.ConnectError as exc:
            raise BrainError(self._connection_message()) from exc
        except httpx.TimeoutException as exc:
            raise BrainError(
                f"The {self.name} model took too long to answer. Try a shorter question, "
                "or a smaller model."
            ) from exc

        if response.status_code != 200:
            raise BrainError(self._status_message(response))

        try:
            body = response.json()
        except json.JSONDecodeError as exc:
            raise BrainError(f"{self.name} returned a response I could not read.") from exc

        return self._parse(body, response)

    async def stream_tokens(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        on_token: Callable[[str], Any],
    ) -> BrainReply:
        """Generate while handing visible text pieces to ``on_token`` as they arrive.

        Falls back to a plain ``generate`` -- one silent reply, no streaming -- when
        the provider does not cooperate, so a caller can use this unconditionally.
        Tool-call deltas are accumulated but never announced: a call spoken halfway
        through would promise an action the turn has not taken yet.
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [self._sanitise(message) for message in messages],
            "temperature": self.temperature,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if self.max_tokens > 0:
            payload["max_tokens"] = self.max_tokens

        try:
            async with self._client.stream("POST", "/chat/completions", json=payload) as response:
                if response.status_code != 200:
                    # Not a streaming-grade failure: just do the plain request, which
                    # produces the provider's own spoken error message.
                    return await self.generate(messages, tools)

                pieces: list[str] = []
                calls: dict[int, dict[str, Any]] = {}
                model_name = ""
                finish_reason = ""

                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    model_name = str(chunk.get("model") or model_name)
                    choice = (chunk.get("choices") or [{}])[0]
                    delta = choice.get("delta") or {}
                    piece = delta.get("content")
                    if piece:
                        pieces.append(str(piece))
                        with contextlib.suppress(Exception):
                            await on_token(str(piece))
                    for raw_call in delta.get("tool_calls") or []:
                        index = int(raw_call.get("index") or 0)
                        slot = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                        if raw_call.get("id"):
                            slot["id"] = str(raw_call["id"])
                        function = raw_call.get("function") or {}
                        if function.get("name"):
                            slot["name"] += str(function["name"])
                        if function.get("arguments"):
                            slot["arguments"] += str(function["arguments"])
                    if choice.get("finish_reason"):
                        finish_reason = str(choice["finish_reason"])

        except httpx.TimeoutException as exc:
            raise BrainError(
                f"The {self.name} model took too long to answer. Try a shorter question, "
                "or a smaller model."
            ) from exc
        except httpx.HTTPError as exc:
            raise BrainError(self._connection_message()) from exc

        text = clean_text("".join(pieces))
        parsed_calls = [
            ToolCall(
                id=slot["id"] or f"call_{index}",
                name=slot["name"],
                arguments=parse_arguments(slot["arguments"]),
                raw=slot,
            )
            for index, slot in sorted(calls.items())
            if slot["name"]
        ]
        reply = BrainReply(text=text, tool_calls=parsed_calls, model=model_name or self.model)
        reply.finish_reason = finish_reason
        return reply

    def _connection_message(self) -> str:
        hint = f" {self.start_hint}" if self.start_hint else ""
        return f"I cannot reach {self.name} at {self.base_url}.{hint}"

    def _status_message(self, response: httpx.Response) -> str:
        detail = response.text[:400].replace("\n", " ").strip()
        try:
            parsed = json.loads(response.text)
            detail = str(parsed.get("error") or parsed.get("detail") or detail)[:400]
        except (json.JSONDecodeError, AttributeError):
            pass

        lowered = detail.lower()
        if response.status_code == 404 and "model" in lowered:
            return (
                f"{self.name} does not have the model '{self.model}' installed. "
                f"Pull it first, or set a different model in .env."
            )
        if "context length" in lowered or "too large" in lowered:
            return "That conversation is longer than the model's context window. Try again."
        return f"{self.name} refused the request ({response.status_code}): {detail}"

    # -- parsing ----------------------------------------------------------

    def _sanitise(self, message: dict[str, Any]) -> dict[str, Any]:
        """Drop fields a stricter provider would reject."""
        clean = {k: v for k, v in message.items() if v is not None}
        if clean.get("role") == "tool" and "tool_call_id" not in clean:
            clean["tool_call_id"] = clean.get("name", "")
        return clean

    def _parse(self, body: dict[str, Any], response: httpx.Response | None = None) -> BrainReply:
        choices = body.get("choices") or []
        if not choices:
            error = body.get("error")
            if error:
                raise BrainError(f"{self.name} reported: {str(error)[:300]}")
            raise BrainError(f"{self.name} returned no answer.")

        message = choices[0].get("message") or {}
        text = clean_text(message.get("content"))
        calls: list[ToolCall] = []
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

        timing: dict[str, Any] = {"duration_ns": 0, "prompt_eval_count": 0, "eval_count": 0}
        if response is not None and response.status_code == 200:
            try:
                timing_body = response.json()
                if isinstance(timing_body, dict):
                    timing["duration_ns"] = int(timing_body.get("total_duration") or 0)
                    timing["prompt_eval_count"] = int(timing_body.get("prompt_eval_count") or 0)
                    timing["eval_count"] = int(timing_body.get("eval_count") or 0)
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

        return BrainReply(
            text=text,
            tool_calls=calls,
            model=str(body.get("model") or self.model or ""),
            raw=body,
            duration_ns=timing.get("duration_ns", 0),
            prompt_eval_count=timing.get("prompt_eval_count", 0),
            eval_count=timing.get("eval_count", 0),
            model_note="",
        )

    async def aclose(self) -> None:
        await self._client.aclose()
