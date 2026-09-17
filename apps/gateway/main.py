"""HTTP and websocket transport for the assistant.

Endpoints follow the contract of the reference project this was built from
(`/health`, `/stt`, `/tts`, `/v1/turn`, `/v1/turn/text`, `WS /ws/turn`) so the orb
UI and any existing client keep working, with three deliberate additions:

* ``/v1/confirmations`` and ``/v1/confirm`` make the permission gate answerable
  from outside the audio path -- a click is a legitimate answer to "shall I?".
* ``WS /ws/wake`` streams microphone frames for local wake-word detection.
* ``GET /`` renders a plain status page, so pointing a browser at the gateway is
  informative rather than a 404.

Run it with::

    python -m apps.gateway.main          # or: uvicorn apps.gateway.main:app
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from contextlib import asynccontextmanager, suppress
from typing import Any

import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

from . import __version__
from .config import load_settings
from .service import JarvisService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
logger = logging.getLogger("jarvis.gateway")

# The orb UI is served from a different port, so its origin must be allowed.
DEFAULT_ORIGINS = [
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:8000",
    "http://localhost:8000",
]


class TextRequest(BaseModel):
    text: str
    session_id: str = "default"


class TurnRequest(BaseModel):
    text: str
    session_id: str = "default"
    # False makes a confirm-tier tool unexecutable in this request; it is returned
    # as awaiting approval instead. See Agent.run_turn.
    allow_confirmation: bool = True
    speak: bool = True


class ConfirmRequest(BaseModel):
    id: str
    approved: bool
    source: str = "api"


class WakeFrame(BaseModel):
    """Base64 PCM frames, for clients that cannot send binary websocket frames."""

    audio_b64: str
    reset: bool = False


def create_app(settings: Any | None = None) -> FastAPI:
    resolved = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        service = JarvisService(resolved)
        app.state.jarvis = service
        await service.start()
        logger.info(
            "listening on http://%s:%s | orb expected on 5173",
            resolved.host,
            resolved.port,
        )
        try:
            yield
        finally:
            await service.stop()

    app = FastAPI(
        title="Jarvis",
        version=__version__,
        description="A local-first voice assistant with a policy gate in front of every action.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=DEFAULT_ORIGINS,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def current_service() -> JarvisService:
        """The running service, created by the lifespan handler above.

        Read from app.state at call time rather than captured at import time, so
        the endpoints never hold a stale reference across a restart.
        """
        service: JarvisService | None = getattr(app.state, "jarvis", None)
        if service is None:
            raise HTTPException(status_code=503, detail="Jarvis is still starting up")
        return service

    # -- status ----------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        service = app.state.jarvis
        health = service.health()
        tools = health["tool_count"]
        stt = "ready" if health["stt"]["available"] else "not installed"
        tts = health["tts"].get("engine", "none")
        return _STATUS_TEMPLATE.format(
            version=health["version"],
            brain=health["active_brain"] or "starting",
            mode=health["mode"],
            tools=tools,
            stt=stt,
            tts=tts,
            policy=health["policy"]["source"],
            orb="http://127.0.0.1:5173",
            notes="".join(f"<li>{note}</li>" for note in health["notes"]) or "<li>none</li>",
        )

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return current_service().health()

    @app.get("/tools")
    async def tools() -> dict[str, Any]:
        service = current_service()
        return {"tools": service.registry.describe(), "count": len(service.registry.names())}

    @app.get("/audit")
    async def audit(limit: int = 50) -> dict[str, Any]:
        entries = current_service().audit.tail(limit)
        return {"entries": entries, "count": len(entries)}

    @app.get("/v1/confirmations")
    async def confirmations() -> dict[str, Any]:
        return {"pending": current_service().broker.snapshot()}

    @app.post("/v1/confirm")
    async def confirm(request: ConfirmRequest) -> dict[str, Any]:
        service = current_service()
        resolved = service.resolve_confirmation(request.id, request.approved, source=request.source)
        if not resolved:
            raise HTTPException(status_code=404, detail="no such pending confirmation")
        return {"resolved": True, "id": request.id, "approved": request.approved}

    # -- speech ----------------------------------------------------------

    @app.post("/stt")
    async def stt(file: UploadFile = File(...)) -> dict[str, Any]:
        service = current_service()
        audio = await file.read()
        if not audio:
            raise HTTPException(status_code=400, detail="empty audio upload")
        try:
            transcript = await service.transcribe(audio)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return transcript.describe()

    @app.post("/tts")
    async def tts(request: TextRequest) -> Response:
        service = current_service()
        audio = await service.speak(request.text)
        if not audio:
            raise HTTPException(
                status_code=503,
                detail=f"no speech engine could produce audio ({service.tts.describe()})",
            )
        return Response(content=audio, media_type="audio/wav")

    # -- turns -----------------------------------------------------------

    @app.post("/v1/turn")
    async def turn(file: UploadFile = File(...), session_id: str = "default") -> dict[str, Any]:
        service = current_service()
        audio = await file.read()
        if not audio:
            raise HTTPException(status_code=400, detail="empty audio upload")
        result = await service.turn_audio(audio, session_id=session_id)
        payload = result.describe()
        if result.audio:
            payload["audio_b64"] = base64.b64encode(result.audio).decode("ascii")
        return payload

    @app.post("/v1/turn/text")
    async def turn_text(request: TurnRequest) -> dict[str, Any]:
        service = current_service()
        result = await service.turn_text(
            request.text,
            session_id=request.session_id,
            allow_confirmation=request.allow_confirmation,
            speak=request.speak,
        )
        payload = result.describe()
        if result.audio:
            payload["audio_b64"] = base64.b64encode(result.audio).decode("ascii")
        return payload

    # -- websockets ------------------------------------------------------

    @app.websocket("/ws/turn")
    async def ws_turn(websocket: WebSocket) -> None:
        """Conversation socket.

        Each utterance runs as its own task so the socket keeps reading while a
        turn is suspended waiting for permission. That is what lets a spoken "yes"
        or a button press reach a held action at all.
        """
        await websocket.accept()
        service = websocket.app.state.jarvis
        queue = await service.bus.subscribe()
        session_id = "orb"
        turn_tasks: set[asyncio.Task[Any]] = set()
        stop = asyncio.Event()
        send_lock = asyncio.Lock()

        async def send(payload: dict[str, Any]) -> None:
            async with send_lock:
                try:
                    await websocket.send_json(payload)
                except (WebSocketDisconnect, RuntimeError):
                    stop.set()

        async def pump() -> None:
            while not stop.is_set():
                event = await queue.get()
                await send(event)

        async def run_turn_text(text: str) -> None:
            # ``reply_delta`` frames reach the orb through the bus pump while the
            # sentence is still being generated -- the point of streaming. The
            # complete turn_result still follows, so a client that ignores deltas
            # loses nothing.
            async def on_token(piece: str) -> None:
                await send({"type": "reply_delta", "text": piece})

            try:
                result = await service.turn_text(text, session_id=session_id, on_token=on_token)
            except Exception as exc:
                logger.exception("turn failed")
                await send({"type": "error", "message": str(exc)})
                return
            payload: dict[str, Any] = {"type": "turn_result", **result.describe()}
            payload.pop("turn", None)
            if result.audio:
                payload["audio_b64"] = base64.b64encode(result.audio).decode("ascii")
            await send(payload)

        async def run_turn_audio(audio: bytes) -> None:
            await send({"type": "thinking"})

            async def on_token(piece: str) -> None:
                await send({"type": "reply_delta", "text": piece})

            try:
                result = await service.turn_audio(audio, session_id=session_id, on_token=on_token)
            except Exception as exc:
                logger.exception("audio turn failed")
                await send({"type": "error", "message": str(exc)})
                return
            if result.error == "stt_unavailable":
                await send({"type": "error", "message": result.reply})
                return
            payload: dict[str, Any] = {"type": "turn_result", **result.describe()}
            payload.pop("turn", None)
            if result.audio:
                payload["audio_b64"] = base64.b64encode(result.audio).decode("ascii")
            await send(payload)

        def spawn(coro: Any) -> None:
            task = asyncio.create_task(coro)
            turn_tasks.add(task)
            task.add_done_callback(turn_tasks.discard)

        pump_task = asyncio.create_task(pump())
        await send({"type": "ready", "session_id": session_id})
        try:
            while True:
                message = await websocket.receive()

                if message.get("type") == "websocket.disconnect":
                    break

                if (payload := message.get("bytes")) is not None:
                    if payload:
                        spawn(run_turn_audio(payload))
                    continue

                raw = message.get("text")
                if not raw:
                    continue
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    await send({"type": "error", "message": "malformed JSON frame"})
                    continue

                kind = event.get("type")
                if kind == "ping":
                    await send({"type": "pong"})
                elif kind == "session":
                    session_id = str(event.get("session_id") or session_id)
                    await send({"type": "ready", "session_id": session_id})
                elif kind == "confirm":
                    resolved = service.resolve_confirmation(
                        str(event.get("id", "")), bool(event.get("approved")), source="ui"
                    )
                    await send(
                        {"type": "confirm_ack", "id": event.get("id"), "resolved": resolved}
                    )
                elif kind == "text":
                    text = str(event.get("text", "")).strip()
                    if not text:
                        continue
                    # A held action takes priority over starting a new turn.
                    if service.broker.oldest() is not None:
                        answered = service.answer_pending_by_voice(text)
                        if answered is not None:
                            await send({"type": "confirmation_voice", **answered})
                            continue
                    spawn(run_turn_text(text))
                else:
                    await send({"type": "error", "message": f"unknown frame type {kind!r}"})
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("websocket loop failed")
        finally:
            stop.set()
            pump_task.cancel()
            for task in list(turn_tasks):
                task.cancel()
            await service.bus.unsubscribe(queue)
            with suppress(RuntimeError):
                await websocket.close()

    @app.websocket("/ws/wake")
    async def ws_wake(websocket: WebSocket) -> None:
        """Microphone frames in, wake-word detections out. 16 kHz mono int16."""
        await websocket.accept()
        service = websocket.app.state.jarvis
        detector = service.wake
        if not detector.available():
            await websocket.send_json(
                {
                    "type": "wake_unavailable",
                    "message": (
                        "Wake word needs openwakeword. Install the voice extra with "
                        "'uv pip install -r requirements-voice.txt'."
                    ),
                }
            )
            await websocket.close()
            return

        await websocket.send_json(
            {"type": "ready", "model": resolved.wake_model, "threshold": resolved.wake_threshold}
        )
        try:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                payload = message.get("bytes")
                if payload:
                    detection = await asyncio.to_thread(detector.feed, payload)
                    if detection is not None:
                        await websocket.send_json({"type": "wake_detected", **detection.describe()})
                    continue
                raw = message.get("text")
                if not raw:
                    continue
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "reset":
                    detector.reset()
                    await websocket.send_json({"type": "reset"})
        except WebSocketDisconnect:
            pass
        finally:
            with suppress(RuntimeError):
                await websocket.close()

    return app


app = create_app()


_STATUS_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Jarvis gateway</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin: 0; padding: 3rem 1.5rem; background: #0a0d14; color: #e8ecf4;
         font: 16px/1.6 ui-sans-serif, system-ui, sans-serif; }}
  main {{ max-width: 46rem; margin: 0 auto; }}
  h1 {{ font-size: 1.9rem; margin: 0 0 .25rem; letter-spacing: -.02em; }}
  p.lede {{ color: #8d99ae; margin: 0 0 2rem; }}
  dl {{ display: grid; grid-template-columns: 11rem 1fr; gap: .5rem 1rem; margin: 0 0 2rem; }}
  dt {{ color: #8d99ae; }}
  dd {{ margin: 0; font-variant-numeric: tabular-nums; }}
  .badge {{ display: inline-block; padding: .15rem .6rem; border-radius: 999px;
            background: #16202f; border: 1px solid #26374d; font-size: .85rem; }}
  a {{ color: #7cc4ff; }}
  ul {{ color: #8d99ae; padding-left: 1.2rem; }}
  code {{ background: #121a26; padding: .1rem .35rem; border-radius: .3rem; }}
</style>
</head>
<body>
<main>
  <h1>Jarvis <span class="badge">v{version}</span></h1>
  <p class="lede">The gateway is running. This page is diagnostics, not the interface.</p>
  <dl>
    <dt>Brain</dt><dd>{brain}</dd>
    <dt>Mode</dt><dd>{mode}</dd>
    <dt>Tools</dt><dd>{tools} registered</dd>
    <dt>Speech in</dt><dd>{stt}</dd>
    <dt>Speech out</dt><dd>{tts}</dd>
    <dt>Policy</dt><dd><code>{policy}</code></dd>
  </dl>
  <p>Open the voice interface at <a href="{orb}">{orb}</a>.</p>
  <p>Other useful endpoints:</p>
  <ul>
    <li><a href="/health">/health</a> &mdash; full status, tool inventory and policy</li>
    <li><a href="/tools">/tools</a> &mdash; every tool with its tier</li>
    <li><a href="/audit">/audit</a> &mdash; what Jarvis has actually done</li>
    <li><a href="/v1/confirmations">/v1/confirmations</a> &mdash; actions awaiting permission</li>
    <li><a href="/docs">/docs</a> &mdash; the OpenAPI schema</li>
  </ul>
  <p>Notes:</p>
  <ul>{notes}</ul>
</main>
</body>
</html>
"""


def main() -> None:
    settings = load_settings()
    uvicorn.run(
        "apps.gateway.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
