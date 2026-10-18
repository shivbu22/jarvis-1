"""End-to-end checks against a RUNNING gateway.

This is the test that would have caught every wiring mistake in this project that
unit tests cannot see: a mis-registered route, a broken CORS header, a policy file
that failed to load, an engine that reports itself available and then fails. It
speaks only HTTP, exactly like the orb does.

Policy *enforcement* is deliberately not re-tested here -- that is what the pytest
suite covers, with a scripted brain. What this checks is that a live server is
answering, that its advertised configuration is the real one, and that a turn
actually completes.

Usage:
    python scripts/smoke.py                          # against 127.0.0.1:8000
    python scripts/smoke.py --url http://127.0.0.1:8010
    python scripts/smoke.py --text "what's the weather in Mumbai"
"""

from __future__ import annotations

import argparse
import base64
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class Report:
    checks: list[tuple[str, bool, str]] = field(default_factory=list)

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append((name, ok, detail))
        marker = "PASS" if ok else "FAIL"
        line = f"  {marker}  {name}"
        if detail:
            line += f"  ({detail})"
        print(line, flush=True)
        return ok

    def skip(self, name: str, reason: str) -> None:
        print(f"  SKIP  {name}  ({reason})", flush=True)

    @property
    def failures(self) -> list[str]:
        return [name for name, ok, _ in self.checks if not ok]

    @property
    def passes(self) -> int:
        return sum(1 for _, ok, _ in self.checks if ok)


def _brain_reachable(brain: str | None, health: dict[str, Any]) -> tuple[bool, str]:
    """Is the configured brain actually able to answer? Returns (ready, note).

    "Running" and "working" are different states, and conflating them produces a
    misleading result either way. Ollama in particular can be up, hold a model,
    and still fail every request because its inference binary is missing -- which
    is exactly the state this machine was in when this was written. So the probe
    escalates: listening, then the model is installed, then it can generate.
    """
    if not brain:
        return False, "no brain configured"
    if brain != "Ollama":
        return True, ""  # a cloud brain is only known to be broken once it fails

    model = "qwen3:4b"
    for entry in (health.get("brain") or {}).get("chain") or []:
        if entry.get("name") == "Ollama":
            model = str(entry.get("model") or model)

    try:
        tags = httpx.get("http://127.0.0.1:11434/api/tags", timeout=3.0)
        tags.raise_for_status()
    except httpx.HTTPError:
        return False, (
            "Ollama is not running, so there is no brain yet. Start it with "
            "'ollama serve', then 'ollama pull qwen3:4b'."
        )

    installed = [str(entry.get("name", "")) for entry in tags.json().get("models", [])]
    if not any(name.split(":")[0] == model.split(":")[0] for name in installed):
        return False, f"Ollama has no '{model}' installed. Pull it with 'ollama pull {model}'."

    try:
        probe = httpx.post(
            "http://127.0.0.1:11434/api/chat",
            json={
                "model": model,
                "messages": [{"role": "user", "content": "hi"}],
                "stream": False,
                "options": {"num_predict": 1},
            },
            timeout=30.0,
        )
        if probe.status_code != 200:
            detail = probe.text[:200].replace("\n", " ")
            return False, (
                f"Ollama is running and '{model}' is installed, but it cannot run "
                f"models: {detail}"
            )
    except httpx.HTTPError as exc:
        return False, f"Ollama could not answer a minimal request: {exc}"

    return True, ""


def _check_wake_word(health: dict[str, Any], client: httpx.Client, report: Report, base: str) -> None:
    """Prove the wake word works, using nothing but the running gateway.

    A handshake alone proves nothing: the bug this exists for was a detector
    that answered ``ready`` and then died with ``NoSuchFile`` on its first
    frame, because the ``openwakeword`` wheel ships without its model weights.
    So the gateway speaks "Hey Jarvis" through its own /tts and those samples
    are streamed straight back into /ws/wake, where a detection is required.
    """
    import asyncio
    import io
    import json
    from pathlib import Path

    wake = health.get("wake", {})
    if not wake.get("enabled"):
        report.skip("wake word", "disabled by configuration (WAKE_ENABLED=0)")
        return
    if not wake.get("available"):
        report.check(
            "the wake-word package is installed",
            False,
            str(wake.get("error") or "openwakeword is missing"),
        )
        return
    if not wake.get("ready"):
        # Not a fault: the weights are fetched once, on first use.
        report.skip("wake word", str(wake.get("error") or "weights download on first use"))
        return

    try:
        import numpy as np
        import soundfile as sf
        import websockets
    except ImportError as exc:  # the voice extra is optional
        report.skip("wake word detection", f"needs numpy, soundfile and websockets ({exc})")
        return

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from apps.gateway.wake import FRAME_BYTES, RATE

    spoken = client.post("/tts", json={"text": "Hey Jarvis, are you there?"})
    if spoken.status_code != 200:
        report.check("the wake phrase can be synthesised", False, f"POST /tts {spoken.status_code}")
        return

    samples, rate = sf.read(io.BytesIO(spoken.content), dtype="float32")
    if samples.ndim > 1:
        samples = samples.mean(axis=1)
    if rate != RATE:
        # Linear interpolation is plenty: this only has to stay intelligible to
        # the wake model, and /tts cannot be asked for 16 kHz directly.
        samples = np.interp(
            np.linspace(0, len(samples) - 1, int(len(samples) * RATE / rate)),
            np.arange(len(samples)),
            samples,
        ).astype("float32")
    peak = float(np.max(np.abs(samples))) or 1.0
    pcm = (samples / peak * 0.7 * 32767).astype("<i2").tobytes()

    async def listen() -> list[dict[str, Any]]:
        hits: list[dict[str, Any]] = []
        url = base.replace("https://", "wss://").replace("http://", "ws://") + "/ws/wake"
        async with websockets.connect(url, max_size=None) as socket:
            await asyncio.wait_for(socket.recv(), timeout=15)  # the "ready" frame

            async def drain() -> None:
                async for raw in socket:
                    try:
                        event = json.loads(raw)
                    except ValueError:
                        continue
                    if event.get("type") == "wake_detected":
                        hits.append(event)

            reader = asyncio.create_task(drain())
            for offset in range(0, len(pcm) - FRAME_BYTES + 1, FRAME_BYTES):
                await socket.send(pcm[offset : offset + FRAME_BYTES])
                await asyncio.sleep(0.02)  # faster than real time, still not a flood
            await asyncio.sleep(2)
            reader.cancel()
        return hits

    try:
        hits = asyncio.run(listen())
    except Exception as exc:
        report.check("the wake word listens on /ws/wake", False, f"{type(exc).__name__}: {exc}")
        return

    best = max((float(hit.get("score") or 0) for hit in hits), default=0.0)
    report.check(
        'the wake word hears "Hey Jarvis"',
        bool(hits),
        f"score {best:.2f} against threshold {wake.get('threshold')}",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="Gateway base URL.")
    parser.add_argument("--text", default="", help="Run one turn with this text and print it.")
    parser.add_argument(
        "--timeout",
        type=float,
        default=240.0,
        help=(
            "Per-request timeout. Generous on purpose: a local brain generates at "
            "roughly a dozen tokens a second here, so a real turn takes 20-90 seconds "
            "and a 90-second default made this check fail on healthy code."
        ),
    )
    args = parser.parse_args()

    base = args.url.rstrip("/")
    report = Report()
    client = httpx.Client(base_url=base, timeout=args.timeout)

    print(f"Jarvis smoke test against {base}", flush=True)
    print()

    # --- reachability ------------------------------------------------------
    try:
        health_response = client.get("/health")
    except httpx.HTTPError as exc:
        print(f"  FAIL  the gateway is not answering at {base}: {exc}")
        return 2

    if not report.check("GET /health returns 200", health_response.status_code == 200):
        return 2

    health: dict[str, Any] = health_response.json()
    report.check("status is ok", health.get("status") == "ok", str(health.get("status")))

    # --- self-reported truthfulness ---------------------------------------
    policy = health.get("policy", {})
    report.check(
        "the policy file actually loaded",
        bool(policy.get("loaded_from_file")),
        str(policy.get("source")),
    )
    report.check(
        "unclassified tools would still need confirmation",
        policy.get("default_tier") == "confirm",
        str(policy.get("default_tier")),
    )
    report.check(
        "destructive patterns are configured",
        len(policy.get("dangerous_patterns") or []) >= 10,
        f"{len(policy.get('dangerous_patterns') or [])} patterns",
    )
    report.check(
        "credential paths are marked sensitive",
        bool(policy.get("sensitive_paths")),
        ", ".join(policy.get("sensitive_paths") or []) or "none",
    )
    report.check(
        "the shell jail has roots",
        bool((policy.get("shell") or {}).get("workdir_roots")),
    )
    report.check("shell chaining is disabled", (policy.get("shell") or {}).get("allow_chained_commands") is False)

    # --- tool inventory ----------------------------------------------------
    tools_response = client.get("/tools")
    report.check("GET /tools returns 200", tools_response.status_code == 200)
    tiers = {
        tool["name"]: tool["tier"] for tool in tools_response.json().get("tools", [])
    }
    report.check("a useful number of tools is registered", len(tiers) >= 20, f"{len(tiers)} tools")
    report.check("web_search is automatic", tiers.get("web_search") == "auto", str(tiers.get("web_search")))
    report.check("shell_run needs confirmation", tiers.get("shell_run") == "confirm")
    report.check("shell_run_admin is denied", tiers.get("shell_run_admin") == "deny")

    # --- speech engines ----------------------------------------------------
    stt = health.get("stt", {})
    if stt.get("available"):
        report.check(
            "speech recognition is installed",
            len(str(stt.get("error") or "")) == 0,
            f"device {stt.get('device')} (configured {stt.get('configured_device')})",
        )
        if stt.get("device") == "not loaded":
            report.skip(
                "speech device",
                "no audio has been transcribed yet, so the working device is not known",
            )
    else:
        report.skip("speech recognition", "not installed (text input still works)")

    tts = health.get("tts", {})
    if tts.get("available"):
        speech = client.post("/tts", json={"text": "Systems nominal, sir."})
        ok = speech.status_code == 200 and speech.content[:4] == b"RIFF"
        report.check("POST /tts returns a WAV", ok, f"{len(speech.content)} bytes, {tts.get('engine')}")
    else:
        report.skip("text to speech", "no engine available")

    # --- wake word ---------------------------------------------------------
    _check_wake_word(health, client, report, base)

    # --- a real turn -------------------------------------------------------
    # A turn is only meaningful if the brain behind it is reachable. A reply that
    # is really an error message must not count as a pass, and a missing brain is
    # a configuration state worth reporting rather than a failure of this code.
    brain = health.get("active_brain")
    brain_ready, brain_note = _brain_reachable(brain, health)
    if not brain:
        report.skip("a live turn", "no brain configured")
    elif not brain_ready:
        report.skip("a live turn", brain_note)
    else:
        # Warm the brain first, and do not judge it on that first call. A local
        # model has to be loaded into memory before it can answer anything, and on
        # this machine that cold turn measured ~180 seconds -- long enough that a
        # timeout here said nothing about whether the wiring works. The real check is
        # the second turn, with the model already resident.
        warm_start = time.perf_counter()
        try:
            client.post(
                "/v1/turn/text",
                json={"text": "Ready?", "speak": False},
            )
            print(
                f"  note  the first (cold) turn took {time.perf_counter() - warm_start:.0f}s",
                flush=True,
            )
        except httpx.ReadTimeout:
            print(
                f"  note  the cold turn exceeded {args.timeout:.0f}s; still checking the warm one",
                flush=True,
            )

        turn = client.post(
            "/v1/turn/text",
            json={"text": "In one short sentence, confirm you are working.", "speak": False},
        )
        body = turn.json() if turn.status_code == 200 else {}
        replied = bool(str(body.get("reply", "")).strip())
        report.check(
            f"a text turn completes on {brain}",
            turn.status_code == 200 and replied and not body.get("error"),
            (str(body.get("reply", ""))[:90] or str(body.get("error", ""))[:90]),
        )
        if body.get("audio_b64"):
            report.check("the reply carries audio", base64.b64decode(body["audio_b64"])[:4] == b"RIFF")

    # --- audit ------------------------------------------------------------
    audit = client.get("/audit?limit=20")
    report.check("GET /audit returns 200", audit.status_code == 200)
    entries = audit.json().get("entries", []) if audit.status_code == 200 else []
    report.check("the audit trail is being written", len(entries) > 0, f"{len(entries)} entries")
    report.check(
        "turn decisions are recorded",
        any(entry.get("event") in {"turn", "startup"} for entry in entries),
    )

    confirmations = client.get("/v1/confirmations")
    report.check("GET /v1/confirmations returns 200", confirmations.status_code == 200)

    # --- optional single turn ---------------------------------------------
    if args.text:
        print()
        print(f"Turn: {args.text}", flush=True)
        response = client.post(
            "/v1/turn/text", json={"text": args.text, "speak": False, "allow_confirmation": False}
        )
        if response.status_code != 200:
            report.check("the requested turn succeeded", False, f"HTTP {response.status_code}")
        else:
            body = response.json()
            print(f"  heard: {body.get('transcript')}")
            print(f"  reply: {body.get('reply')}")
            for attempt in body.get("turn", {}).get("tools", []):
                print(f"  tool:  {attempt['name']} [{attempt['decision']}] {attempt.get('summary', '')[:110]}")
            report.check("the requested turn produced a reply", bool(str(body.get("reply", "")).strip()))

    client.close()

    print()
    if report.failures:
        print(f"{len(report.failures)} check(s) failed: {', '.join(report.failures)}")
        return 1
    print(f"All {report.passes} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
