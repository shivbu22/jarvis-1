"""Gemini brain -- the sharper option, when a key is configured.

Two details here are load-bearing.

**Automatic function calling is disabled.** The SDK would happily execute our
tool functions itself, which would route around the policy gate entirely. Every
tool call must come back to the agent loop as an *intent* so the gate sees it, so
``AutomaticFunctionCallingConfig(disable=True)`` is not an optimisation flag, it
is the safety boundary.

**Function responses go back as role ``user`` parts.** That is what the SDK's own
chat implementation does (``google/genai/chats.py``, ``func_response_content``),
so tool results round-trip in the shape the API expects.
"""

from __future__ import annotations

from typing import Any

from .base import Brain, BrainError, BrainReply, ToolCall, clean_text, parse_arguments


class GeminiBrain(Brain):
    name = "Gemini"
    is_local = False

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash", timeout: float = 120.0) -> None:
        if not api_key:
            raise BrainError("No Gemini API key was provided.")
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:  # pragma: no cover - dependency is in requirements.txt
            raise BrainError(
                "The google-genai package is not installed, so I cannot use Gemini."
            ) from exc

        self._types = types
        self._client = genai.Client(api_key=api_key)
        self.model = model
        self._timeout = timeout

    # -- request ----------------------------------------------------------

    async def generate(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> BrainReply:
        types = self._types
        system_instruction, contents = self._split(messages)

        config = types.GenerateContentConfig(
            system_instruction=system_instruction or None,
            temperature=0.2,
            tools=self._tool_declarations(tools),
            # Never let the SDK execute a tool for us: see module docstring.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

        try:
            response = await self._client.aio.models.generate_content(
                model=self.model, contents=contents, config=config
            )
        except Exception as exc:  # provider SDK surfaces many exception types
            raise BrainError(self._explain(exc)) from exc

        return self._parse(response)

    def _explain(self, exc: Exception) -> str:
        text = str(exc)
        lowered = text.lower()
        if "api key" in lowered or "api_key" in lowered or "unauthenticated" in lowered:
            return "Gemini rejected the API key. Check GEMINI_API_KEY in .env."
        if "quota" in lowered or "rate" in lowered or "resource_exhausted" in lowered:
            return "Gemini is rate limiting this key, or the quota is used up."
        if "not found" in lowered and "model" in lowered:
            return f"Gemini does not offer the model '{self.model}'. Set GEMINI_MODEL in .env."
        if "timeout" in lowered or "deadline" in lowered:
            return "Gemini took too long to answer."
        return f"Gemini could not answer: {text[:300]}"

    # -- conversion -------------------------------------------------------

    def _split(self, messages: list[dict[str, Any]]) -> tuple[str, list[Any]]:
        """Canonical messages -> (system instruction, Gemini contents)."""
        types = self._types
        system_parts: list[str] = []
        contents: list[Any] = []

        for message in messages:
            role = message.get("role")
            if role == "system":
                if message.get("content"):
                    system_parts.append(str(message["content"]))
                continue

            if role == "user":
                contents.append(
                    types.Content(
                        role="user", parts=[types.Part(text=str(message.get("content", "")))]
                    )
                )
                continue

            if role == "assistant":
                parts: list[Any] = []
                if message.get("content"):
                    parts.append(types.Part(text=str(message["content"])))
                for call in message.get("tool_calls") or []:
                    function = call.get("function") or {}
                    parts.append(
                        types.Part(
                            function_call=types.FunctionCall(
                                name=function.get("name", ""),
                                args=parse_arguments(function.get("arguments")),
                            )
                        )
                    )
                if parts:
                    contents.append(types.Content(role="model", parts=parts))
                continue

            if role == "tool":
                # Role "user" for function responses: matches the SDK's own chat loop.
                response_part: dict[str, Any] = {"response": {"result": message.get("content", "")}}
                if message.get("name"):
                    response_part["name"] = str(message["name"])
                if message.get("tool_call_id"):
                    response_part["id"] = str(message["tool_call_id"])
                contents.append(
                    types.Content(
                        role="user",
                        parts=[types.Part(function_response=types.FunctionResponse(**response_part))],
                    )
                )
                continue

        return "\n\n".join(system_parts), contents

    def _tool_declarations(self, tools: list[dict[str, Any]]) -> list[Any] | None:
        if not tools:
            return None
        types = self._types
        declarations = []
        for spec in tools:
            function = spec.get("function") or {}
            name = function.get("name")
            if not name:
                continue
            declarations.append(
                types.FunctionDeclaration(
                    name=name,
                    description=function.get("description", ""),
                    # Passing JSON Schema straight through avoids hand-building
                    # a types.Schema tree per tool, and keeps the single source of
                    # truth for a tool's shape in the registry.
                    parameters_json_schema=function.get("parameters") or {"type": "object"},
                )
            )
        if not declarations:
            return None
        return [types.Tool(function_declarations=declarations)]

    # -- parsing ----------------------------------------------------------

    def _parse(self, response: Any) -> BrainReply:
        text = ""
        try:
            text = clean_text(getattr(response, "text", "") or "")
        except Exception:
            text = ""

        calls: list[ToolCall] = []
        for index, call in enumerate(getattr(response, "function_calls", None) or []):
            name = getattr(call, "name", "") or ""
            if not name:
                continue
            args = getattr(call, "args", None)
            calls.append(
                ToolCall(
                    id=str(getattr(call, "id", "") or f"gemini_{index}"),
                    name=name,
                    arguments=parse_arguments(args if args is not None else {}),
                    raw=call,
                )
            )

        if not text and not calls:
            # A blocked or empty candidate: say something useful rather than nothing.
            block = ""
            try:
                candidate = (response.candidates or [None])[0]
                block = str(getattr(candidate, "finish_reason", "") or "")
            except Exception:
                block = ""
            if block:
                return BrainReply(model=self.model, error=f"Gemini stopped early ({block}).")

        return BrainReply(text=text, tool_calls=calls, model=self.model, raw=response, duration_ns=0, prompt_eval_count=0, eval_count=0, model_note="")

    async def aclose(self) -> None:
        client = getattr(self, "_client", None)
        try:
            if client is not None:
                await client.aio.aclose()
        except Exception:
            pass
