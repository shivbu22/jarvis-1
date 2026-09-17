"""Groq brain -- an optional cloud fallback that is fast and cheap.

Only reachable when GROQ_API_KEY is set, and never in offline mode. It exists so
that a Gemini outage or a rate limit does not end the conversation.
"""

from __future__ import annotations

from .openai_compat import OpenAICompatBrain

GROQ_BASE_URL = "https://api.groq.com/openai/v1"


class GroqBrain(OpenAICompatBrain):
    def __init__(self, api_key: str, model: str, timeout: float = 60.0) -> None:
        super().__init__(
            name="Groq",
            base_url=GROQ_BASE_URL,
            model=model,
            api_key=api_key,
            timeout=timeout,
            temperature=0.2,
            is_local=False,
        )

    def _status_message(self, response) -> str:  # type: ignore[override]
        if response.status_code == 401:
            return "Groq rejected the API key. Check GROQ_API_KEY in .env."
        if response.status_code == 429:
            return "Groq is rate limiting this key right now. Try again shortly."
        return super()._status_message(response)
