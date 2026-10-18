"""The local model relay -- a large brain that costs no VRAM.

This machine's own brain is a 3B model sharing a 4 GB GPU with Whisper. It is fast
for what it is, but it is small enough that it sometimes writes a tool call as
prose (``{"name": "ask_user", "parameters": "..."}``) instead of calling the tool,
and it loses the thread on anything multi-step.

A *relay* is an OpenAI-compatible server on loopback that fronts real models. It
fixes both problems without spending a byte of VRAM, and it does so natively:
measured on this machine, a greeting that took the local brain 16 seconds answered
in 1.5 seconds, with provider-side tool calls rather than prose that has to be
recovered after the fact.

The trade is privacy, not speed. The request leaves this machine even though the
socket it leaves through is on loopback, so ``is_local`` is False, offline mode
never builds this brain, and the status endpoint reports the chain honestly.

One thing about the URL deserves code rather than a comment. A relay hands out
access by embedding the key in the path
(``http://127.0.0.1:20128/api/v1/vscode/sk-.../``), and the gateway *speaks* brain
errors aloud. A connection failure that echoed ``base_url`` would read the
credential out loud and print it in the orb. Every message below is therefore built
from the host alone, and any credential-shaped run of text that comes back from a
provider is redacted before it is spoken.
"""

from __future__ import annotations

import contextlib
import re
import time
from urllib.parse import urlsplit

import httpx

from .base import BrainError, BrainReply
from .openai_compat import OpenAICompatBrain

#: Anything that looks like a handed-out key, so a provider echoing our URL back at
#: us cannot put it in the transcript. Matches the shapes these relays use
#: (``sk-...``, ``key=...``, a long dotless token after a slash).
_KEY = re.compile(r"(sk-[A-Za-z0-9_\-]{6,}|[A-Za-z0-9_\-]{24,})")


def redact(text: str) -> str:
    """Replace credential-shaped words with a placeholder, keeping the rest readable."""
    return _KEY.sub("sk-***", text)


#: How long to stand down after the relay refuses us, when it does not say.
#: A minute is a compromise: long enough that a dead relay costs one cheap check
#: per minute instead of one per turn, short enough that restored credits (or a
#: restarted app) are picked up automatically without the master doing anything.
DEFAULT_COOLDOWN = 60.0


class RelayBrain(OpenAICompatBrain):
    """A relay on loopback, reached as if it were any OpenAI-compatible provider."""

    def __init__(
        self,
        *,
        url: str,
        model: str,
        api_key: str = "",
        timeout: float = 60.0,
        max_tokens: int = 0,
    ) -> None:
        self.url = url.rstrip("/")
        # Only the host is ever spoken or logged; the path may be the credential.
        self.host = urlsplit(self.url).netloc or self.url
        self._blocked_until = 0.0
        self._blocked_reason = ""
        super().__init__(
            name="relay",
            base_url=self.url,
            model=model,
            api_key=api_key,
            timeout=timeout,
            temperature=0.2,
            # The relay's models run somewhere else, so their token bill is not GPU
            # time here. Capping output is still right for a spoken answer, but the
            # default is 0 -- no cap -- because capping a *reasoning* model truncates
            # it mid-thought and the visible reply comes back empty, which the agent
            # then reports as a bare "Done." (the local brain has the same trap; see
            # OllamaBrain's max_tokens note).
            max_tokens=max_tokens,
            is_local=False,
        )

    # -- standing down ----------------------------------------------------

    def blocked_for(self) -> float:
        """Seconds this relay is standing down for; 0.0 when it may be tried."""
        return max(0.0, self._blocked_until - time.monotonic())

    def _stand_down(self, seconds: float, reason: str) -> None:
        self._blocked_until = time.monotonic() + max(seconds, DEFAULT_COOLDOWN)
        self._blocked_reason = reason

    def _refuse_if_blocked(self) -> None:
        """Fail instantly, without a request, while standing down.

        This is what keeps a spent account from taxing every turn: the router still
        tries the relay first, gets an answer in microseconds, and hands the turn to
        the local brain. Without it, each turn would pay a full round trip to a
        server that is going to say no anyway.
        """
        wait = self.blocked_for()
        if wait > 0:
            raise BrainError(
                f"I am standing down from the model relay for another {wait:.0f} "
                f"seconds ({self._blocked_reason}). The local brain is answering."
            )

    async def generate(
        self, messages: list[dict], tools: list[dict]
    ) -> BrainReply:
        self._refuse_if_blocked()
        return await super().generate(messages, tools)

    async def stream_tokens(
        self, messages: list[dict], tools: list[dict], on_token
    ) -> BrainReply:
        self._refuse_if_blocked()
        return await super().stream_tokens(messages, tools, on_token)

    # -- spoken error messages --------------------------------------------

    def _connection_message(self) -> str:
        # Reached only when nothing answered on the socket: the app serving the
        # relay is not running (closing the desktop app takes the brain with it).
        self._stand_down(DEFAULT_COOLDOWN, "nothing answering on its socket")
        return (
            f"I cannot reach the model relay at {self.host}, so my large brain is "
            "offline. Start the app that serves it, or set JARVIS_BRAIN=ollama to "
            "run on the local model instead."
        )

    def _status_message(self, response: httpx.Response) -> str:
        detail = redact(response.text[:400].replace("\n", " ").strip())
        with contextlib.suppress(ValueError, AttributeError):
            parsed = response.json()
            detail = redact(str(parsed.get("error") or parsed.get("detail") or detail)[:400])
        lowered = detail.lower()

        # Rate limits are checked FIRST, and that order is load-bearing: a cooldown
        # body reads "All credentials for model X are cooling down", so testing for
        # the word "credentials" first reported a temporary throttle as a broken key
        # and sent the master off to edit a URL that was working.
        if response.status_code == 429 or "rate_limit" in lowered or "cooling down" in lowered:
            reset = 0
            with contextlib.suppress(ValueError, AttributeError, TypeError):
                reset = int(response.json().get("error", {}).get("reset_seconds") or 0)
            self._stand_down(reset, "the model is rate limited")
            wait = f" It frees up in about {reset} seconds." if reset else ""
            return f"The model relay is rate limiting this model.{wait} The local brain can take this turn."
        if "credits" in lowered or "exhausted" in lowered:
            # Not a broken key and not worth re-trying this second: the account
            # behind the relay is out of credit until somebody tops it up. Stand
            # down and let the local brain carry the conversation in the meantime.
            self._stand_down(DEFAULT_COOLDOWN, "the relay's account is out of credits")
            return (
                "The model relay has no credits left for this model, so I am using "
                "the local brain. Reconnect the relay's account to get the large "
                "brain back."
            )
        if response.status_code in {401, 403}:
            self._stand_down(DEFAULT_COOLDOWN, "it rejected the key")
            return (
                "The model relay refused my credentials. Its URL in .env carries the "
                "key, so JARVIS_RELAY_URL needs updating (or set JARVIS_RELAY_KEY)."
            )
        if response.status_code >= 500:
            self._stand_down(15.0, "it is failing upstream")
            return (
                f"The model relay is failing upstream ({response.status_code}). "
                "The local brain can take these turns meanwhile."
            )
        if "determine provider" in lowered or "not a valid model" in lowered or (
            response.status_code == 404 and "model" in lowered
        ):
            return (
                f"The model relay does not have a model called '{self.model}'. "
                "Set JARVIS_RELAY_MODEL in .env to one it serves."
            )
        if "context length" in lowered or "too large" in lowered:
            return "That conversation is longer than the model's context window. Try again."
        return f"The model relay refused the request ({response.status_code}): {detail}"

    def describe(self) -> dict:
        described = super().describe()
        described["host"] = self.host
        described["url"] = redact(self.url)
        wait = self.blocked_for()
        if wait > 0:
            described["standing_down_seconds"] = round(wait)
            described["standing_down_reason"] = self._blocked_reason
        return described
