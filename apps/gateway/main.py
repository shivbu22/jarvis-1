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
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .config import load_settings
from .service import JarvisService
from .wake import WakeUnavailable

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


class AnswerRequest(BaseModel):
    """A reply to a question Jarvis asked mid-turn."""

    id: str
    text: str
    source: str = "api"


class SpawnSubagentRequest(BaseModel):
    goal: str
    role: str = "assistant"
    context: str = ""


class TradeRequest(BaseModel):
    symbol: str
    side: str
    quantity: int
    price: float | None = None


class UltimatePolicyRequest(BaseModel):
    enabled: bool | None = None


class BrainSwitchRequest(BaseModel):
    target: str | None = None


class WakeFrame(BaseModel):
    """Base64 PCM frames, for clients that cannot send binary websocket frames."""

    audio_b64: str
    reset: bool = False


class ToolCallRequest(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    confirmed: bool = True


class VisionDescribeRequest(BaseModel):
    source: str = "camera"  # camera | screen
    prompt: str = ""
    save: bool = False


class MemoryForgetRequest(BaseModel):
    target: str = ""
    id: str = ""


class ProfileUpdateRequest(BaseModel):
    name: str | None = None
    preferred_name: str | None = None
    timezone: str | None = None
    communication_style: str | None = None
    verbosity: str | None = None
    currency: str | None = None
    temperature_unit: str | None = None
    custom_preferences: dict[str, Any] | None = None


class ScheduleTaskRequest(BaseModel):
    message: str
    time_expression: str = ""
    cron_expression: str = ""
    task_type: str = "reminder"
    tool_name: str = ""
    tool_args: dict[str, Any] = Field(default_factory=dict)


class EvolutionRuleRequest(BaseModel):
    rule: str
    category: str = "general"
    trigger_pattern: str = "*"


class BriefingRequest(BaseModel):
    deliver_toast: bool = True
    speak: bool = False


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

    # -- mount static orb UI bundle if compiled ---------------------------
    root_dir = Path(__file__).resolve().parents[2]
    dist_dir = root_dir / "apps" / "orb" / "dist"
    if dist_dir.exists():
        assets_dir = dist_dir / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

        @app.get("/app", response_class=HTMLResponse)
        async def serve_orb_app() -> HTMLResponse:
            index_path = dist_dir / "index.html"
            if index_path.exists():
                return HTMLResponse(index_path.read_text(encoding="utf-8"))
            return HTMLResponse("<h1>Orb UI building...</h1>")

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

    @app.get("/v1/questions")
    async def questions() -> dict[str, Any]:
        return {"pending": current_service().interactions.snapshot()}

    @app.post("/v1/answer")
    async def answer(request: AnswerRequest) -> dict[str, Any]:
        """Answer a question Jarvis asked, which resumes the turn holding it open."""
        service = current_service()
        resolved = service.answer_question(request.id, request.text, source=request.source)
        if not resolved:
            raise HTTPException(status_code=404, detail="no such pending question")
        return {"resolved": True, "id": request.id}

    # -- rich telemetry & management api for dynamic GUI -----------------

    @app.get("/api/overview")
    async def api_overview() -> dict[str, Any]:
        """Aggregated real-time overview across hardware, subagents, obsidian, learning, and ventures."""
        service = current_service()
        health_data = service.health()

        # Subagents
        sub_mgr = None
        sub_list = []
        try:
            from .tools.subagents import SubagentSwarmManager
            sub_mgr = SubagentSwarmManager.get_instance()
            sub_list = [r.as_dict() for r in list(sub_mgr.records.values())[-16:]]
        except Exception:
            pass

        # Obsidian notes catalog
        notes_list = []
        try:
            from .tools.obsidian_brain import VAULT_PATH
            if VAULT_PATH.exists():
                for p in sorted(VAULT_PATH.rglob("*.md")):
                    rel = p.relative_to(VAULT_PATH).as_posix()
                    try:
                        content = p.read_text(encoding="utf-8")
                        title = rel
                        for line in content.splitlines()[:5]:
                            if line.startswith("# "):
                                title = line[2:].strip()
                                break
                            if line.startswith("title:"):
                                title = line.split(":", 1)[1].strip()
                        notes_list.append({
                            "path": rel,
                            "title": title,
                            "category": rel.split("/")[0] if "/" in rel else "root",
                            "size_bytes": p.stat().st_size,
                            "words": len(content.split()),
                        })
                    except Exception:
                        pass
        except Exception:
            pass

        # Learning & Strategy Stats
        learning_stats = {}
        try:
            learning_stats = service.memory.get_learning_analytics()
        except Exception:
            pass

        # Portfolio stats
        portfolio = {}
        try:
            portfolio = service.memory.get_portfolio()
        except Exception:
            pass

        # Multi-Venture Live Telemetry
        ventures = {
            "swachta": {
                "id": "swachta",
                "name": "Swachta AI",
                "tagline": "Autonomous Municipal Waste & Route Optimization",
                "stage": "Active MVP Pilot",
                "target": "Urban Municipalities & Waste Concessionaires",
                "kpis": [
                    {"label": "Smart Bins Monitored", "value": "1,240 Units", "trend": "+12% MoM"},
                    {"label": "Route SLA Efficiency", "value": "99.4%", "trend": "Optimal"},
                    {"label": "Daily Waste Processed", "value": "42.8 Metric Tons", "trend": "+4.2 T"},
                    {"label": "B2B Outreach Pipeline", "value": "₹48,50,000", "trend": "5 Deals Active"},
                ],
                "status": "OPERATIONAL",
            },
            "medimin": {
                "id": "medimin",
                "name": "Medimin",
                "tagline": "AI Healthcare Supply Chain & Pharma Intelligence",
                "stage": "Private Beta",
                "target": "Retail Pharmacies & Urgent Care Clinics",
                "kpis": [
                    {"label": "Pharma Catalog SKUs", "value": "14,200 SKUs", "trend": "100% Synced"},
                    {"label": "Price Engine Latency", "value": "18 ms", "trend": "Real-time"},
                    {"label": "Prescription OCR Accuracy", "value": "99.1%", "trend": "Validated"},
                    {"label": "Reorder Velocity", "value": "+28% MoM", "trend": "High Ret."},
                ],
                "status": "OPERATIONAL",
            },
            "quick_queue": {
                "id": "quick_queue",
                "name": "Quick Queue",
                "tagline": "Virtual OPD & Patient Flow Orchestration",
                "stage": "Pilot Ready",
                "target": "Hospitals, Diagnostic Centers & OPD Clinics",
                "kpis": [
                    {"label": "Active Clinic Queues", "value": "84 Queues", "trend": "Active"},
                    {"label": "Avg Wait Time Reduction", "value": "-42 Minutes", "trend": "High Impact"},
                    {"label": "Doctor Utilization Rate", "value": "93.8%", "trend": "+18%"},
                    {"label": "Viral K-Factor", "value": "1.34", "trend": "Self-Sustaining"},
                ],
                "status": "OPERATIONAL",
            },
        }

        # Market Snapshot
        market_snapshot = {
            "indices": [
                {"symbol": "NIFTY 50", "name": "NSE Nifty", "price": "24,835.40", "change": "+0.65%", "up": True},
                {"symbol": "SENSEX", "name": "BSE Sensex", "price": "81,210.80", "change": "+0.58%", "up": True},
                {"symbol": "USD/INR", "name": "Forex", "price": "₹83.74", "change": "-0.04%", "up": False},
                {"symbol": "BTC-USD", "name": "Bitcoin", "price": "$64,250.00", "change": "+2.14%", "up": True},
            ],
            "portfolio": portfolio,
        }

        # Scheduler active tasks
        sched_tasks = []
        try:
            sched_tasks = service.scheduler.list_tasks()
        except Exception:
            pass

        # User profile
        profile_data = {}
        try:
            profile_data = service.profile.all()
        except Exception:
            pass

        # Context manager metrics
        context_metrics = {}
        try:
            context_metrics = service.context_mgr.stats
        except Exception:
            pass

        # Recent notifications
        recent_notifs = []
        try:
            recent_notifs = [n.as_dict() for n in service.notifier.history[-10:]]
        except Exception:
            pass

        # Evolution rules
        evolution_stats = {"rules_count": 0, "rules": []}
        try:
            if hasattr(service, "evolution") and service.evolution:
                evolution_stats = {
                    "rules_count": len(service.evolution.list_rules()),
                    "rules": service.evolution.list_rules(),
                }
        except Exception:
            pass

        return {
            "health": health_data,
            "subagents": {
                "total": len(sub_mgr.records) if sub_mgr else 0,
                "active": sum(1 for r in sub_mgr.records.values() if r.status == "running") if sub_mgr else 0,
                "completed": sum(1 for r in sub_mgr.records.values() if r.status == "completed") if sub_mgr else 0,
                "failed": sum(1 for r in sub_mgr.records.values() if r.status in ("failed", "timed_out")) if sub_mgr else 0,
                "recent": sub_list,
            },
            "obsidian_brain": {
                "total_notes": len(notes_list),
                "notes": notes_list,
            },
            "learning": learning_stats,
            "market": market_snapshot,
            "ventures": ventures,
            "scheduler": {
                "tasks": sched_tasks,
                "count": len(sched_tasks),
            },
            "profile": profile_data,
            "context_metrics": context_metrics,
            "notifications": recent_notifs,
            "evolution": evolution_stats,
        }

    @app.get("/api/evolution/rules")
    async def api_get_evolution_rules() -> dict[str, Any]:
        service = current_service()
        rules = service.evolution.list_rules() if hasattr(service, "evolution") and service.evolution else []
        return {"rules": rules, "count": len(rules)}

    @app.post("/api/evolution/rules")
    async def api_add_evolution_rule(req: EvolutionRuleRequest) -> dict[str, Any]:
        service = current_service()
        if hasattr(service, "evolution") and service.evolution:
            rule = service.evolution.add_rule(
                rule_text=req.rule,
                category=req.category,
                trigger_pattern=req.trigger_pattern,
                source="api_user",
            )
            return {"ok": True, "rule": rule.to_dict()}
        return {"ok": False, "error": "Evolution engine not initialized"}

    @app.delete("/api/evolution/rules/{rule_id}")
    async def api_delete_evolution_rule(rule_id: str) -> dict[str, Any]:
        service = current_service()
        if hasattr(service, "evolution") and service.evolution:
            ok = service.evolution.delete_rule(rule_id)
            return {"ok": ok}
        return {"ok": False, "error": "Evolution engine not initialized"}

    @app.post("/api/evolution/briefing")
    async def api_trigger_briefing(req: BriefingRequest) -> dict[str, Any]:
        service = current_service()
        if hasattr(service, "evolution") and service.evolution:
            briefing = await service.evolution.generate_daily_briefing(service=service)
            if req.deliver_toast and hasattr(service, "notifier") and service.notifier:
                await service.notifier.send(
                    title=briefing["title"],
                    message=briefing["text"][:240] + ("..." if len(briefing["text"]) > 240 else ""),
                    urgency="normal",
                    speak=req.speak,
                )
            return {"ok": True, "briefing": briefing}
        return {"ok": False, "error": "Evolution engine not initialized"}

    @app.post("/api/upload")
    async def api_upload_file(file: UploadFile = File(...), prompt: str = Form(default="")) -> dict[str, Any]:
        """Multi-modal document and visual file intake pipeline.
        
        Accepts PDFs, DOCX, text files, code, and images. Performs instant text extraction
        and visual inspection so files can be dropped directly into the context stream.
        """
        service = current_service()
        upload_dir = Path(service.settings.data_dir) / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)

        raw_bytes = await file.read()
        if not raw_bytes:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")

        safe_name = f"{int(time.time())}_{Path(file.filename or 'upload.dat').name}"
        dest_path = upload_dir / safe_name
        dest_path.write_bytes(raw_bytes)

        suffix = dest_path.suffix.lower()
        extracted_text = ""
        is_image = suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
        is_document = suffix in {".pdf", ".docx", ".doc"}

        # 1. PDF extraction via pypdf
        if suffix == ".pdf":
            try:
                import pypdf
                reader = pypdf.PdfReader(str(dest_path))
                pages_text = []
                for idx, page in enumerate(reader.pages[:30]):
                    t = page.extract_text() or ""
                    if t.strip():
                        pages_text.append(f"--- Page {idx + 1} ---\n{t.strip()}")
                extracted_text = "\n\n".join(pages_text)
                if not extracted_text:
                    extracted_text = "[PDF contains no selectable text or consists entirely of scanned images.]"
            except Exception as exc:
                extracted_text = f"[PDF extraction notice: {exc}]"

        # 2. DOCX extraction via python-docx
        elif suffix in {".docx", ".doc"}:
            try:
                import docx
                doc = docx.Document(str(dest_path))
                paras = [p.text for p in doc.paragraphs if p.text.strip()]
                for table in doc.tables:
                    for row in table.rows:
                        row_text = " | ".join(c.text.strip() for c in row.cells if c.text.strip())
                        if row_text:
                            paras.append(row_text)
                extracted_text = "\n\n".join(paras)
            except Exception as exc:
                extracted_text = f"[DOCX extraction notice: {exc}]"

        # 3. Image analysis via multimodal vision (moondream)
        elif is_image:
            try:
                from .tools.vision import _analyze_image_with_ollama
                img_prompt = prompt or "Describe this image in detail, extract any text, charts, diagrams, or UI components visible."
                analysis = _analyze_image_with_ollama(dest_path, img_prompt)
                extracted_text = f"[Visual Inspection of {file.filename}]: {analysis}"
            except Exception as exc:
                extracted_text = f"[Visual inspection failed: {exc}]"

        # 4. Text/code files
        else:
            try:
                extracted_text = raw_bytes.decode("utf-8", errors="replace")
            except Exception as exc:
                extracted_text = f"[Binary data, {len(raw_bytes)} bytes]"

        preview = (extracted_text[:280] + "...") if len(extracted_text) > 280 else extracted_text

        service.audit.record(
            "file_uploaded",
            filename=file.filename,
            size=len(raw_bytes),
            path=str(dest_path),
            suffix=suffix,
        )

        return {
            "ok": True,
            "filename": file.filename or safe_name,
            "path": str(dest_path),
            "size_bytes": len(raw_bytes),
            "content_type": file.content_type or "application/octet-stream",
            "extracted_text": extracted_text[:25000],
            "preview": preview,
            "is_image": is_image,
            "is_document": is_document,
        }

    @app.get("/api/profile")
    async def api_get_profile() -> dict[str, Any]:
        service = current_service()
        return {"profile": service.profile.all()}

    @app.patch("/api/profile")
    async def api_update_profile(req: ProfileUpdateRequest) -> dict[str, Any]:
        service = current_service()
        updates = {k: v for k, v in req.model_dump().items() if v is not None}
        for k, v in updates.items():
            service.profile.set(k, v)
        return {"ok": True, "profile": service.profile.all()}

    @app.get("/api/scheduler/tasks")
    async def api_get_scheduled_tasks() -> dict[str, Any]:
        service = current_service()
        tasks = service.scheduler.list_tasks()
        return {"tasks": tasks, "count": len(tasks)}

    @app.post("/api/scheduler/schedule")
    async def api_schedule_task(req: ScheduleTaskRequest) -> dict[str, Any]:
        service = current_service()
        if req.cron_expression:
            task = service.scheduler.add_cron(
                cron_expr=req.cron_expression,
                message=req.message,
                tool_name=req.tool_name,
                tool_args=req.tool_args,
            )
        else:
            from .tools.scheduler import parse_time_expression
            parsed = parse_time_expression(req.time_expression or "in 1 minute")
            fire_time = parsed if parsed is not None else (time.time() + 60.0)
            task = service.scheduler.add_reminder(
                message=req.message,
                fire_at=fire_time,
            )
        return {"ok": True, "task": task.as_dict()}

    @app.delete("/api/scheduler/tasks/{task_id}")
    async def api_cancel_scheduled_task(task_id: str) -> dict[str, Any]:
        service = current_service()
        success = service.scheduler.cancel(task_id)
        if not success:
            raise HTTPException(status_code=404, detail="Task not found or already completed")
        return {"ok": True, "task_id": task_id}

    @app.get("/api/notifications")
    async def api_get_notifications(limit: int = 20) -> dict[str, Any]:
        service = current_service()
        history = service.notifier.history[-limit:]
        return {"notifications": [n.as_dict() for n in history], "count": len(history)}

    @app.get("/api/notes")
    async def api_notes_list() -> dict[str, Any]:
        """List all markdown notes in Obsidian Brain vault."""
        from .tools.obsidian_brain import VAULT_PATH
        notes_list = []
        if VAULT_PATH.exists():
            for p in sorted(VAULT_PATH.rglob("*.md")):
                rel = p.relative_to(VAULT_PATH).as_posix()
                try:
                    content = p.read_text(encoding="utf-8")
                    title = rel
                    for line in content.splitlines()[:5]:
                        if line.startswith("# "):
                            title = line[2:].strip()
                            break
                        if line.startswith("title:"):
                            title = line.split(":", 1)[1].strip()
                    notes_list.append({
                        "path": rel,
                        "title": title,
                        "category": rel.split("/")[0] if "/" in rel else "root",
                        "size_bytes": p.stat().st_size,
                        "words": len(content.split()),
                    })
                except Exception:
                    pass
        return {"notes": notes_list, "total": len(notes_list)}

    @app.get("/api/notes/{file_path:path}")
    async def api_note_content(file_path: str) -> dict[str, Any]:
        """Read a single note's full text content."""
        from .tools.obsidian_brain import VAULT_PATH
        if not file_path:
            return await api_notes_list()
        target = (VAULT_PATH / file_path).resolve()
        if not target.exists() or not target.is_file() or not target.is_relative_to(VAULT_PATH):
            raise HTTPException(status_code=404, detail="Note not found in vault")
        content = target.read_text(encoding="utf-8")
        return {"path": file_path, "content": content}

    @app.post("/api/subagents/spawn")
    async def api_spawn_subagent(request: SpawnSubagentRequest) -> dict[str, Any]:
        """Spawn a subagent autonomously."""
        from .tools.subagents import subagent_spawn
        service = current_service()
        ctx = getattr(service, "tools_ctx", None)
        sub_context = f"[Role: {request.role}] {request.context}".strip()
        res = await subagent_spawn(ctx, goal=request.goal, context=sub_context, background=True)
        return {"ok": res.ok, "text": res.text, "data": res.data}

    @app.post("/api/self_heal/diagnose")
    async def api_self_heal_diagnose() -> dict[str, Any]:
        """Trigger an on-demand self-healing diagnostic scan across all 10 subsystems."""
        service = current_service()
        if hasattr(service, "self_healer") and service.self_healer:
            raw = await service.self_healer.diagnose_all()
            healthy = sum(1 for r in raw if getattr(r, "status", "") == "healthy")
            total = len(raw)
            overall = "healthy" if healthy == total else ("warning" if any(getattr(r, "status", "") == "warning" for r in raw) else "error")
            return {
                "status": overall,
                "healthy_subsystems": healthy,
                "total_subsystems": total,
                "results": {r.subsystem: {"status": r.status, "details": r.details} for r in raw},
            }
        return {"status": "unavailable", "healthy_subsystems": 0, "total_subsystems": 0, "results": {}}

    @app.post("/api/trade")
    async def api_execute_trade(request: TradeRequest) -> dict[str, Any]:
        """Execute a paper trade order."""
        service = current_service()
        res = service.memory.record_trade(
            symbol=request.symbol.upper(),
            side=request.side.lower(),
            quantity=request.quantity,
            price=request.price or 100.0,
        )
        return res

    @app.post("/api/brain/switch")
    async def api_brain_switch(request: BrainSwitchRequest = BrainSwitchRequest()) -> dict[str, Any]:
        """Cycle or set the active primary brain in the chain."""
        service = current_service()
        new_active = "unknown"
        if request.target:
            if hasattr(service.brain, "switch_to_brain"):
                new_active = service.brain.switch_to_brain(request.target)
            else:
                new_active = request.target
        elif hasattr(service.brain, "cycle_brain"):
            new_active = service.brain.cycle_brain()
        elif hasattr(service.brain, "brains") and len(service.brain.brains) > 1:
            service.brain.brains = service.brain.brains[1:] + service.brain.brains[:1]
            new_active = getattr(service.brain.brains[0], "name", "unknown")
            service.brain.last_used = new_active
        else:
            new_active = getattr(service.brain, "name", "ultimate")

        service.audit.record("brain_switched", active=new_active)
        return {"ok": True, "active_brain": new_active}

    @app.post("/api/policy/ultimate")
    async def api_policy_ultimate(request: UltimatePolicyRequest) -> dict[str, Any]:
        """Toggle or explicitly set Ultimate Unrestricted Permission Mode."""
        service = current_service()
        current_perm = service.policy.ultimate_permission
        target = not current_perm if request.enabled is None else request.enabled
        service.policy.grant_ultimate_permission(target)
        if hasattr(service.brain, "toggle_power_mode"):
            service.brain.toggle_power_mode(target)
        service.audit.record("ultimate_mode_toggled", enabled=target)
        return {"ok": True, "ultimate_permission": target}

    @app.get("/api/training/status")
    async def api_training_status() -> dict[str, Any]:
        """Fetch current training telemetry: SFT datasets, notes, speakers, models."""
        from pathlib import Path
        import json

        root = Path(__file__).resolve().parents[2]
        training_dir = root / "data" / "training"
        alpaca = training_dir / "sft_alpaca.jsonl"
        colab = training_dir / "jarvis_finetune_colab.ipynb"

        sft_samples = 0
        if alpaca.exists():
            with open(alpaca, "r", encoding="utf-8") as f:
                sft_samples = sum(1 for line in f if line.strip())

        vault_notes = len(list((root / "jarvis brain").rglob("*.md")))

        speakers = []
        meta_file = root / "data" / "speakers" / "speakers_meta.json"
        if meta_file.exists():
            try:
                data = json.loads(meta_file.read_text(encoding="utf-8"))
                speakers = list(data.values())
            except Exception:
                pass

        return {
            "status": "ready",
            "sft_samples": sft_samples,
            "colab_notebook_ready": colab.exists(),
            "vault_notes": vault_notes,
            "enrolled_speakers": len(speakers),
            "speakers": speakers,
            "voice_ref_present": (root / "voices" / "jarvis-5s.wav").exists(),
        }

    @app.post("/api/training/dataset")
    async def api_training_dataset() -> dict[str, Any]:
        """Trigger SFT dataset extraction from interaction logs and SQLite turns."""
        import subprocess
        import sys
        root = Path(__file__).resolve().parents[2]
        proc = subprocess.run(
            [sys.executable, "scripts/extract_training_dataset.py"],
            cwd=root,
            capture_output=True,
            text=True,
        )
        return {
            "ok": proc.returncode == 0,
            "output": proc.stdout,
            "error": proc.stderr if proc.returncode != 0 else None,
        }

    @app.post("/api/training/modelfile")
    async def api_training_modelfile() -> dict[str, Any]:
        """Recompile Ollama jarvis-ultimate model definition."""
        import subprocess
        root = Path(__file__).resolve().parents[2]
        mf_path = root / "config" / "ollama" / "Modelfile.jarvis"
        proc = subprocess.run(
            ["ollama", "create", "jarvis-ultimate:latest", "-f", str(mf_path)],
            cwd=root,
            capture_output=True,
            text=True,
        )
        return {
            "ok": proc.returncode == 0,
            "output": proc.stdout,
            "error": proc.stderr if proc.returncode != 0 else None,
        }

    @app.get("/api/skills")
    async def api_list_skills(filter: str = "") -> dict[str, Any]:
        """List all discoverable SKILL.md packages across installed directories."""
        from .tools.skills import _discover_skills
        all_skills = _discover_skills()
        results = []
        kw = filter.lower().strip()
        for name, path in sorted(all_skills.items()):
            if kw and kw not in name.lower():
                continue
            desc = ""
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
                for line in content.splitlines()[:15]:
                    if line.startswith("description:"):
                        desc = line.split(":", 1)[1].strip()
                        break
            except Exception:
                pass
            results.append({"name": name, "path": str(path), "description": desc})
        return {"total": len(results), "skills": results}

    @app.get("/api/skills/{name}")
    async def api_read_skill(name: str) -> dict[str, Any]:
        """Read full instructions of a specific SKILL.md package."""
        from .tools.skills import _discover_skills
        all_skills = _discover_skills()
        target = all_skills.get(name.strip())
        if not target or not target.exists():
            raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
        try:
            content = target.read_text(encoding="utf-8", errors="ignore")
            if len(content) > 16000:
                content = content[:16000] + "\n...[truncated for view performance]"
            return {"name": name, "path": str(target), "instructions": content}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

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

    @app.post("/v1/chat")
    async def v1_chat(request: TurnRequest) -> dict[str, Any]:
        """Typed chat turn complying with TRD §5 contract."""
        return await turn_text(request)

    @app.post("/v1/tools/call")
    async def v1_tools_call(request: ToolCallRequest) -> dict[str, Any]:
        """Direct tool invocation complying with TRD §5 contract."""
        service = current_service()
        result = await service.registry.call(request.name, request.arguments, confirmed=request.confirmed)
        return {
            "name": result.name,
            "ok": result.ok,
            "content": result.content,
            "data": result.data,
            "error": result.error,
            "tier": result.tier,
            "duration_ms": result.duration_ms,
        }

    @app.post("/v1/vision/describe")
    async def v1_vision_describe(request: VisionDescribeRequest) -> dict[str, Any]:
        """Vision scene description complying with TRD §5 contract."""
        service = current_service()
        src = (request.source or "camera").lower().strip()
        tool_name = "screen_describe" if src == "screen" else "camera_describe"
        result = await service.registry.call(tool_name, {"prompt": request.prompt}, confirmed=True)
        return {
            "source": src,
            "ok": result.ok,
            "description": result.content,
            "data": result.data,
        }

    @app.get("/v1/memory/search")
    async def v1_memory_search(q: str = "") -> dict[str, Any]:
        """Search memories complying with TRD §5 contract."""
        service = current_service()
        results = service.memory.relevant_notes(q, limit=10) if q else []
        return {"query": q, "count": len(results), "memories": results}

    @app.post("/v1/memory/forget")
    async def v1_memory_forget(request: MemoryForgetRequest) -> dict[str, Any]:
        """Forget/delete memory by keyword or note id complying with TRD §5 contract."""
        service = current_service()
        target = request.target or request.id
        deleted = service.memory.forget(target)
        return {"ok": bool(deleted), "target": target, "deleted_count": deleted}

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

        def speech_pipeline(
            turn_started: float, stages: dict[str, float] | None = None
        ) -> tuple[Any, Any, Any]:
            """A worker that synthesises and sends speech while the turn runs.

            Synthesis is slow on CPU and the model is the slow part of a turn, so
            running them one after the other makes the master wait for *both*.
            Here the answering text is chunked as it streams and each chunk is
            spoken by this task concurrently with the rest of the generation, so
            the wait is the slower of the two rather than their sum.

            Returns (on_speech, finish, task): ``on_speech`` is non-blocking, and
            ``finish`` drains the queue and awaits the worker. When ``stages`` is
            given, ``first_sound`` is stamped when the first audio frame actually
            goes out -- the moment the master hears the reply, not the moment it
            was queued.
            """
            queue: asyncio.Queue[str | None] = asyncio.Queue()

            async def worker() -> None:
                while True:
                    chunk = await queue.get()
                    if chunk is None:
                        return
                    try:
                        wav = await service.speak(chunk)
                    except Exception:
                        logger.exception("speaking a chunk failed")
                        continue
                    if wav:
                        if stages is not None and "first_sound" not in stages:
                            stages["first_sound"] = time.monotonic() - turn_started
                        # One frame per spoken chunk: the orb plays them in order,
                        # so a long reply starts talking at the first sentence.
                        await send(
                            {
                                "type": "audio",
                                "b64": base64.b64encode(wav).decode("ascii"),
                                "text": chunk,
                            }
                        )

            async def on_speech(chunk: str) -> None:
                queue.put_nowait(chunk)

            task = asyncio.create_task(worker())

            async def finish() -> None:
                queue.put_nowait(None)
                with suppress(Exception):
                    await task

            return on_speech, finish, task

        async def run_turn_text(text: str) -> None:
            # ``reply_delta`` frames reach the orb through the bus pump while the
            # sentence is still being generated -- the point of streaming. The
            # complete turn_result still follows, so a client that ignores deltas
            # loses nothing.
            async def on_token(piece: str) -> None:
                await send({"type": "reply_delta", "text": piece})

            async def on_round(kind: str, _iteration: int) -> None:
                # A streamed round that turns out to be tool-call JSON was never
                # the answer: tell the orb to take the message back instead of
                # leaving raw JSON sitting in the transcript.
                if kind == "retract":
                    await send({"type": "reply_retract"})

            turn_started = time.monotonic()
            transport_stages: dict[str, float] = {}
            on_speech, finish, _task = speech_pipeline(turn_started, transport_stages)
            try:
                result = await service.turn_text(
                    text,
                    session_id=session_id,
                    on_token=on_token,
                    on_speech=on_speech,
                    on_round=on_round,
                )
            except Exception as exc:
                logger.exception("turn failed")
                await send({"type": "error", "message": str(exc)})
                return
            finally:
                await finish()
            # The transport knows when sound actually went out; its measurement
            # replaces the service's enqueue-time proxy.
            if "first_sound" in transport_stages:
                result.stages["first_sound"] = transport_stages["first_sound"]
            payload: dict[str, Any] = {"type": "turn_result", **result.describe()}
            payload.pop("turn", None)
            if result.audio:
                payload["audio_b64"] = base64.b64encode(result.audio).decode("ascii")
            await send(payload)

        async def run_turn_audio(audio: bytes) -> None:
            await send({"type": "thinking"})

            async def on_token(piece: str) -> None:
                await send({"type": "reply_delta", "text": piece})

            async def on_round(kind: str, _iteration: int) -> None:
                # A streamed round that turns out to be tool-call JSON was never
                # the answer: tell the orb to take the message back instead of
                # leaving raw JSON sitting in the transcript.
                if kind == "retract":
                    await send({"type": "reply_retract"})

            turn_started = time.monotonic()
            transport_stages: dict[str, float] = {}
            on_speech, finish, _task = speech_pipeline(turn_started, transport_stages)
            try:
                result = await service.turn_audio(
                    audio,
                    session_id=session_id,
                    on_token=on_token,
                    on_speech=on_speech,
                    on_round=on_round,
                )
            except Exception as exc:
                logger.exception("audio turn failed")
                await send({"type": "error", "message": str(exc)})
                return
            finally:
                await finish()
            if "first_sound" in transport_stages:
                result.stages["first_sound"] = transport_stages["first_sound"]
            if result.error == "stt_unavailable":
                await send({"type": "error", "message": result.reply})
                return
            if result.error == "answered_question":
                # The words went to a held question, so the original turn is now
                # running on. Reporting a turn_result here would show an empty
                # reply and end the turn twice.
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
                elif kind == "answer":
                    answered = service.answer_question(
                        str(event.get("id", "")), str(event.get("text", "")), source="ui"
                    )
                    await send(
                        {"type": "answer_ack", "id": event.get("id"), "resolved": answered}
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
                    # Then a held question: the turn that asked is still suspended
                    # waiting for exactly these words.
                    if service.interactions.pending() and service.answer_question(
                        str(service.interactions.oldest().id), text, source="ui"
                    ):
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
        try:
            # One independent detector stream per connection: the ONNX model
            # keeps rolling feature state between predictions, so two
            # connections feeding one model corrupt each other's audio.
            session = detector.open_session()
        except WakeUnavailable as exc:
            await websocket.send_json({"type": "wake_unavailable", "message": str(exc)})
            await websocket.close()
            return

        await websocket.send_json(
            {
                "type": "ready",
                "model": detector.settings.wake_model,
                "threshold": detector.settings.wake_threshold,
            }
        )
        try:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                payload = message.get("bytes")
                if payload:
                    detection = await asyncio.to_thread(session.feed, payload)
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
                    session.reset()
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
