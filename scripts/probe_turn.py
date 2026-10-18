"""One text turn over the real websocket, with timing and every frame printed.

Usage: python scripts/probe_turn.py "your text goes here"
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
import time

import websockets

URI = "ws://127.0.0.1:8000/ws/turn"


async def main(text: str) -> None:
    started = time.monotonic()
    first_delta: float | None = None
    audio_chunks = 0
    final: dict | None = None

    async with websockets.connect(URI, max_size=20 * 2**20) as ws:
        await ws.send(json.dumps({"type": "text", "text": text}))
        while True:
            try:
                frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=150))
            except TimeoutError:
                print("!! timed out waiting for frames")
                break
            kind = frame.get("type")
            if kind == "ready":
                continue
            if kind == "transcript":
                print(f"[{time.monotonic() - started:6.2f}s] transcript: {frame.get('text')!r}")
            elif kind == "reply_delta":
                if first_delta is None:
                    first_delta = time.monotonic() - started
                print(f"[{time.monotonic() - started:6.2f}s] delta: {frame.get('text')!r}")
            elif kind == "reply_retract":
                print(f"[{time.monotonic() - started:6.2f}s] RETRACT (junk round taken back)")
            elif kind == "audio":
                audio_chunks += 1
                wav = base64.b64decode(frame.get("b64", ""))
                print(f"[{time.monotonic() - started:6.2f}s] audio #{audio_chunks}: {len(wav)} bytes {frame.get('text','')[:60]!r}")
            elif kind == "turn_result":
                final = frame
                print(f"[{time.monotonic() - started:6.2f}s] turn_result:")
                print("   reply      :", frame.get("reply"))
                print("   streamed   :", frame.get("speech_streamed"), "incomplete:", frame.get("speech_incomplete"))
                break
            elif kind == "error":
                print(f"[{time.monotonic() - started:6.2f}s] error: {frame.get('message')}")
                break

    if final:
        print(f"total {time.monotonic() - started:.2f}s | first delta {first_delta:.2f}s" if first_delta else f"total {time.monotonic() - started:.2f}s | no deltas")
    if '{"name"' in (final or {}).get("reply", "") or (final or {}).get("speech_incomplete"):
        print("!! LEAK: tool JSON reached the reply or the speaker")


if __name__ == "__main__":
    asyncio.run(main(" ".join(sys.argv[1:]) or "hello jarvis, how are you"))
