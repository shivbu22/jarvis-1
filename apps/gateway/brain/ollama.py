"""Ollama brain -- the keyless default.

Two things make this more than a thin wrapper. First, it discovers what is
actually installed: a `.env` naming a model that was never pulled is the most
common way a local setup fails, so the brain checks `/api/tags` and falls back to
the best installed alternative instead of erroring. Second, it reports *why* it
could not answer in words that tell the master what to type.
"""

from __future__ import annotations

import httpx

from .base import BrainError
from .openai_compat import OpenAICompatBrain

# Preferred order when the configured model is not installed. Small enough for a
# 4 GB GPU plus CPU offload, and all of these support tool calling.
PREFERRED_MODELS = (
    "llama3.2:3b",
    "qwen3:4b",
    "qwen3:8b",
    "llama3.1:8b",
    "qwen2.5:7b",
    "qwen2.5:3b",
    "mistral:7b",
    "gemma3:4b",
)

# The tuned local variants, built from ``config\ollama\Modelfile.*`` by
# ``scripts\setup.ps1``. Ollama 0.34 loads these models with a 65536-token context
# by default, which on a 4 GB GPU means 10-13 GB resident, ~80% of it on the CPU,
# and generation at a fraction of the speed -- measured, a spoken turn overran its
# timeout entirely. The derived names cap the context at 8192 so the model fits.
#
# Map: the stock name a master might set in .env -> the tuned model to prefer.
# A *different* deliberate choice is untouched; see resolve_model().
TUNED_VARIANTS = {
    "llama3.2:3b": "jarvis-brain:3b",
    "qwen3:4b": "jarvis-brain:4b",
}

# The default brain's tuned name. llama3.2 is the default because it does not
# reason before answering: measured on this machine, 2.2s to choose a tool and 0.9s
# to speak the answer, where qwen3:4b takes 24-28s per call. qwen3 remains the
# choice when a hard, multi-step question is worth the wait.
JARVIS_BRAIN_MODEL = "jarvis-brain:3b"


class OllamaBrain(OpenAICompatBrain):
    def __init__(self, host: str, model: str, timeout: float = 120.0) -> None:
        self.host = host.rstrip("/")
        super().__init__(
            name="Ollama",
            base_url=f"{self.host}/v1",
            model=model,
            timeout=timeout,
            temperature=0.2,
            is_local=True,
            start_hint=(
                "Start it with 'ollama serve' and pull a model, for example "
                "'ollama pull qwen3:4b'."
            ),
            # A local 4B model generates at roughly a dozen tokens a second here, so
            # the reply length *is* the latency -- but this cap must stay generous,
            # and that is not obvious. qwen3 reasons before it answers, and on this
            # machine that hidden reasoning costs 250-700 tokens. Capping too low
            # truncates the turn mid-thought and the visible answer comes back EMPTY,
            # which the agent loop then reports as a bare "Done." 1024 measured as the
            # smallest value that reliably fits reasoning plus a spoken answer.
            max_tokens=1024,
        )
        self.model_note: str | None = None

    async def installed_models(self) -> list[str]:
        """Names of locally available models. Empty list when Ollama is down."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.host}/api/tags")
            if response.status_code != 200:
                return []
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return []
        names: list[str] = []
        for entry in payload.get("models") or []:
            name = entry.get("name") or entry.get("model")
            if name:
                names.append(str(name))
        return names

    async def is_running(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.get(f"{self.host}/api/tags")
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def resolve_model(self) -> str:
        """Pick a usable model, recording a note when it differs from the request.

        Raises:
            BrainError: when Ollama is reachable but has no models at all.
        """
        installed = await self.installed_models()
        if not installed:
            if not await self.is_running():
                raise BrainError(
                    "Ollama is not running, so I have no brain. Start it with "
                    "'ollama serve'."
                )
            raise BrainError(
                "Ollama is running but has no models. Pull one with "
                "'ollama pull qwen3:4b'."
            )

        def present(name: str) -> bool:
            return name in installed

        # 1. Asked for a stock base that has a tuned variant? Prefer the tuned one --
        #    same weights, a context that fits this GPU. This is the common case: the
        #    default config names a stock model and the tuned variant is installed.
        tuned = TUNED_VARIANTS.get(self.model)
        if tuned is None and self.model.endswith(":latest"):
            tuned = TUNED_VARIANTS.get(self.model.split(":")[0])
        if tuned:
            for candidate in (tuned, f"{tuned}:latest"):
                if present(candidate):
                    self.model_note = f"using {candidate} instead of {self.model}"
                    self.model = candidate
                    return candidate

        # 2. An explicitly requested model, when it is installed. Reached directly if
        #    the master named a specific model (including a tuned name), and by the
        #    stock base when no tuned variant exists -- so a deliberate choice in .env
        #    is always respected.
        for candidate in (self.model, f"{self.model}:latest"):
            if present(candidate):
                return self.model

        # 3. Same family (e.g. some other qwen3/llama3/mistral tag already on disk).
        base = self.model.split(":")[0]
        for name in installed:
            if str(name).split(":")[0] == base:
                self.model_note = f"{self.model} is not installed; using {name}"
                self.model = str(name)
                return self.model

        # 4. A preferred size, then anything at all -- better a slow brain than none.
        for preferred in PREFERRED_MODELS:
            if present(preferred):
                self.model_note = f"{self.model} is not installed; using {preferred}"
                self.model = preferred
                return preferred

        fallback = str(installed[0])
        self.model_note = f"{self.model} is not installed; using {fallback}"
        self.model = fallback
        return fallback
