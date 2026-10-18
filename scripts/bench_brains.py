"""Measure candidate brains on the real prompt, the real tools, and real turns.

Swapping the brain is the one change that alters every reply, so it is worth
measuring rather than asserting. This runs the same handful of requests through
each candidate with the *actual* system prompt and the *actual* tool schemas, and
reports what a spoken assistant is judged on:

* **time to first word** -- the pause before Jarvis starts answering,
* **total time** -- the length of the turn,
* **whether the right tool was called**, natively,
* **whether a tool call leaked out as prose** -- the failure that made the local
  brain look broken: ``{"name": "ask_user", "parameters": "I am working, sir."}``
  printed to the orb and spoken aloud.

Usage::

    python scripts/bench_brains.py                      # .env relay, default model
    python scripts/bench_brains.py --models claude-haiku-4.5 claude-sonnet-4.5
    python scripts/bench_brains.py --local              # include the local brain
    python scripts/bench_brains.py --repeat 2

Results are printed as a table and appended to ``data/brain_bench.jsonl``, so a
later swap can be compared against this one instead of remembered.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml
from apps.gateway.brain import RelayBrain
from apps.gateway.brain.base import Brain, looks_like_text_tool_call
from apps.gateway.brain.ollama import OllamaBrain
from apps.gateway.config import load_settings
from apps.gateway.policy import Policy
from apps.gateway.prompts import build_system_prompt
from apps.gateway.tools import build_registry

# Real requests, including the two that actually broke on this machine: a plain
# greeting (which the local brain answered with tool JSON) and a multi-step ask.
PROBES: list[dict[str, str]] = [
    {
        "text": "Hey Jarvis, are you working?",
        "expects": "",
        "why": "a greeting must never become a tool call",
    },
    {
        "text": "What is the weather in Paris right now?",
        "expects": "weather",
        "why": "one tool, a fact Jarvis cannot know",
    },
    {
        "text": "Remember that my workshop starts at nine in the morning.",
        "expects": "memor",
        "why": "memory write",
    },
    {
        "text": "What time is it right now?",
        "expects": "time",
        "why": "the cheapest possible tool call",
    },
    {
        "text": "Search the web for the price of gold and tell me in one sentence.",
        "expects": "search",
        "why": "tool plus a synthesised spoken answer",
    },
    {
        "text": "Summarise what you remember about my workshop.",
        "expects": "memor",
        "why": "recall, which must not invent an answer",
    },
]


def build_prompt_and_tools() -> tuple[str, list[dict]]:
    settings = load_settings()
    policy = Policy(
        yaml.safe_load(settings.resolved_policy_path().read_text(encoding="utf-8")),
        source=str(settings.resolved_policy_path()),
    )
    registry = build_registry(policy)
    tiers = registry.names_by_tier()
    prompt = build_system_prompt(
        tool_names=registry.names(),
        auto_tools=tiers.get("auto", []),
        confirm_tools=tiers.get("confirm", []),
        denied_tools=tiers.get("deny", []),
    )
    return prompt, registry.specs_for_brain()


async def run_probe(brain: Brain, prompt: str, tools: list[dict], text: str) -> dict:
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": text},
    ]
    first: float | None = None
    started = time.monotonic()
    pieces: list[str] = []

    async def on_token(piece: str) -> None:
        nonlocal first
        if first is None:
            first = time.monotonic() - started
        pieces.append(piece)

    try:
        reply = await brain.stream_tokens(messages, tools, on_token)
    except Exception as exc:  # a candidate that fails IS a result
        return {
            "seconds": round(time.monotonic() - started, 2),
            "first_word": round(first, 2) if first else None,
            "tools": [],
            "error": str(exc)[:200],
        }

    total = time.monotonic() - started
    visible = "".join(pieces) or reply.text
    return {
        "seconds": round(total, 2),
        "first_word": round(first, 2) if first is not None else None,
        "tools": [call.name for call in reply.tool_calls],
        "reply": reply.text[:160],
        "prose_tool_call": looks_like_text_tool_call(visible) or '{"name"' in visible,
    }


async def bench_brain(label: str, brain: Brain, prompt: str, tools: list[dict], repeat: int) -> dict:
    print(f"\n=== {label} ===")
    rows: list[dict] = []
    for probe in PROBES:
        for _ in range(repeat):
            result = await run_probe(brain, prompt, tools, probe["text"])
            if "error" in result:
                print(f"  {probe['text'][:44]:46} ERROR {result['error'][:80]}")
                rows.append({**probe, **result})
                continue
            hit = bool(probe["expects"]) and any(
                probe["expects"] in name for name in result["tools"]
            )
            verdict = "ok" if (hit or not probe["expects"]) else "MISSED"
            if result["prose_tool_call"]:
                verdict = "JSON-LEAK"
            print(
                f"  {probe['text'][:44]:46} {result['seconds']:6.2f}s "
                f"first {result['first_word']!s:>5}s  "
                f"{','.join(result['tools']) or '-':24} {verdict}"
            )
            rows.append({**probe, **result, "verdict": verdict})
    ok = sum(1 for row in rows if row.get("verdict") == "ok")
    times = [row["seconds"] for row in rows if "seconds" in row]
    summary = {
        "brain": label,
        "model": getattr(brain, "model", ""),
        "probes": len(rows),
        "correct": ok,
        "accurate_pct": round(100 * ok / len(rows)) if rows else 0,
        "mean_seconds": round(sum(times) / len(times), 2) if times else None,
        "max_seconds": max(times) if times else None,
        "rows": rows,
    }
    print(
        f"  -> correct {summary['correct']}/{summary['probes']} "
        f"({summary['accurate_pct']}%), mean {summary['mean_seconds']}s, "
        f"slowest {summary['max_seconds']}s"
    )
    return summary


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="*", default=None, help="relay models to test")
    parser.add_argument("--local", action="store_true", help="also test the local Ollama brain")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--out", default=str(ROOT / "data" / "brain_bench.jsonl"))
    args = parser.parse_args()

    settings = load_settings()
    prompt, tools = build_prompt_and_tools()
    print(f"{len(tools)} tools, system prompt {len(prompt)} chars")

    candidates: list[tuple[str, Brain]] = []
    if settings.relay_url:
        models = args.models or [settings.relay_model]
        for model in models:
            candidates.append(
                (
                    f"relay:{model}",
                    RelayBrain(
                        url=settings.relay_url,
                        model=model,
                        api_key=settings.relay_key,
                        timeout=settings.relay_timeout_seconds,
                        max_tokens=settings.relay_max_tokens,
                    ),
                )
            )
    else:
        print("no JARVIS_RELAY_URL configured: nothing to compare against the local brain")
    if args.local:
        candidates.append(
            ("local", OllamaBrain(host=settings.ollama_host, model=settings.ollama_model))
        )

    if not candidates:
        return 1

    summaries = []
    for label, brain in candidates:
        summaries.append(await bench_brain(label, brain, prompt, tools, args.repeat))
        close = getattr(brain, "aclose", None)
        if close:
            await close()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as handle:
        for summary in summaries:
            handle.write(json.dumps({**summary, "at": time.strftime("%Y-%m-%d %H:%M:%S")}) + "\n")
    print(f"\nappended to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
