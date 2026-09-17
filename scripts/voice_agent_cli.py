"""Run one turn without starting a server.

Useful for a quick check, for scripting Jarvis from another program, and for
debugging the pipeline without HTTP or a browser in the way.

    python scripts/voice_agent_cli.py --text "what's the weather in Mumbai" --print-tools
    python scripts/voice_agent_cli.py --audio-in sample.wav --audio-out reply.wav
    python scripts/voice_agent_cli.py --text "remember the workshop is Friday"

Exit codes: 0 success, 1 the turn failed, 2 bad arguments.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.gateway.service import JarvisService


async def run(args: argparse.Namespace) -> int:
    service = JarvisService()
    await service.start()

    from apps.gateway.stt import STTUnavailable

    try:
        if args.audio_in:
            audio = Path(args.audio_in).read_bytes()
            try:
                result = await service.turn_audio(
                    audio, session_id=args.session, speak=not args.no_speak
                )
            except STTUnavailable as exc:
                print(f"speech recognition unavailable: {exc}", file=sys.stderr)
                return 1
        else:
            result = await service.turn_text(
                args.text, session_id=args.session, speak=not args.no_speak
            )
    finally:
        # The turn may leave a held confirmation; refuse it so nothing hangs.
        cancelled = service.broker.cancel_all("cli_exit")
        if cancelled:
            print(f"({cancelled} unconfirmed action(s) refused)", file=sys.stderr)

    if result.transcript:
        print(f"you:    {result.transcript}")
    if result.error and result.error != "empty_transcript":
        print(f"status: {result.error}", file=sys.stderr)
    print(f"jarvis: {result.reply}")

    if args.print_tools and result.turn is not None:
        if not result.turn.tools:
            print("tools:  (none)")
        for attempt in result.turn.tools:
            print(
                f"tools:  {attempt.name} [{attempt.decision}] "
                f"{attempt.duration_ms}ms  {attempt.summary[:120]}"
            )

    if args.print_audit:
        print("audit:  last 5 tool decisions")
        for entry in service.audit.tail(200):
            if entry.get("event") == "tool_call":
                print(f"        {entry.get('tool')} [{entry.get('decision')}] {entry.get('reason', '')[:80]}")

    if args.audio_out:
        if result.audio:
            Path(args.audio_out).write_bytes(result.audio)
            print(f"audio:  wrote {len(result.audio)} bytes to {args.audio_out}")
        else:
            print("audio:  none produced (no speech engine available)", file=sys.stderr)
            return 1

    if args.audio_out_b64:
        if result.audio:
            print(base64.b64encode(result.audio).decode("ascii"))
        else:
            return 1

    await service.stop()

    if args.fail_on_error and result.error:
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--text", help="A single command to send.")
    source.add_argument("--audio-in", help="An audio file to transcribe and act on.")
    parser.add_argument("--audio-out", help="Write the spoken reply here as a WAV.")
    parser.add_argument("--audio-out-b64", action="store_true", help="Print the reply audio as base64.")
    parser.add_argument("--session", default="cli", help="Session id for conversation memory.")
    parser.add_argument("--print-tools", action="store_true", help="Show the tool calls that were made.")
    parser.add_argument("--print-audit", action="store_true", help="Show recent tool decisions.")
    parser.add_argument("--no-speak", action="store_true", help="Skip speech synthesis entirely.")
    parser.add_argument("--fail-on-error", action="store_true", help="Exit 1 when the turn reports an error.")
    args = parser.parse_args()

    if args.audio_out and args.no_speak:
        parser.error("--audio-out needs speech synthesis; drop --no-speak")

    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
