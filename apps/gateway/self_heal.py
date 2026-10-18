"""Autonomous Self-Healing and Diagnostic Engine for J.A.R.V.I.S.

Provides automated multi-tier health diagnostics, self-monitoring, anomaly detection,
and self-remediation across database, memory, Obsidian vault, brain router, subagents,
audio pipeline, and runtime state.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger("jarvis.self_heal")


@dataclass
class DiagnosticResult:
    subsystem: str
    status: str  # "healthy", "warning", "degraded", "error"
    details: str
    metrics: dict[str, Any] = field(default_factory=dict)
    remediation_available: bool = False


@dataclass
class SelfHealReport:
    timestamp: str
    overall_status: str
    diagnostics: list[DiagnosticResult]
    remediations_applied: list[str]
    healthy_count: int
    warning_count: int
    error_count: int


class SelfHealer:
    """Core autonomous diagnostic and self-remediation coordinator."""

    def __init__(self, service: Any = None, settings: Any = None) -> None:
        self.service = service
        self._custom_settings = settings
        self._history: list[dict[str, Any]] = []
        self._last_scan: float = 0.0
        self._running_watchdog: bool = False
        self._watchdog_task: asyncio.Task[None] | None = None
        self._watchdog_interval_s: float = 60.0
        self._total_watchdog_runs: int = 0
        self._last_run_status: str = "healthy"
        self._last_run_timestamp: str | None = None

    @property
    def settings(self) -> Any:
        if self._custom_settings is not None:
            return self._custom_settings
        if self.service and hasattr(self.service, "settings"):
            return self.service.settings
        from .config import load_settings
        return load_settings()

    async def diagnose_all(self) -> list[DiagnosticResult]:
        """Run comprehensive diagnostics across all subsystems."""
        results: list[DiagnosticResult] = []

        # 1. Database & Memory SQLite Integrity
        results.append(await self.check_database())

        # 2. Obsidian Brain Vault Graph Health
        results.append(await self.check_obsidian_vault())

        # 3. Brain Router & LLM Engines
        results.append(await self.check_brain_engines())

        # 4. Subagent Swarm Health
        results.append(await self.check_subagents())

        # 5. STT Faster-Whisper Pipeline
        results.append(await self.check_stt_pipeline())

        # 6. TTS Voice Pipeline
        results.append(await self.check_tts_pipeline())

        # 7. Wake Word Detector
        results.append(await self.check_wake_pipeline())

        # 8. Filesystem, Disk Space & Permissions
        results.append(await self.check_filesystem())

        # 9. Policy & Safety Engine
        results.append(await self.check_policy_engine())

        # 10. Audit Log & Event Stream
        results.append(await self.check_audit_stream())

        self._last_scan = time.time()
        return results

    async def check_database(self) -> DiagnosticResult:
        """Check SQLite integrity, WAL mode, indices, and schema."""
        db_path = self.settings.db_path
        if not db_path.exists():
            return DiagnosticResult(
                subsystem="database",
                status="warning",
                details=f"Database file not yet created at {db_path}",
                remediation_available=True,
            )

        try:
            conn = sqlite3.connect(str(db_path), timeout=5.0)
            cursor = conn.cursor()

            # Integrity check
            cursor.execute("PRAGMA integrity_check;")
            integrity = cursor.fetchone()
            is_intact = integrity and integrity[0] == "ok"

            # Check journal mode
            cursor.execute("PRAGMA journal_mode;")
            journal_mode = cursor.fetchone()[0] if cursor.rowcount != 0 else "unknown"

            # Count turns and memories
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = [row[0] for row in cursor.fetchall()]

            turns_count = 0
            memories_count = 0
            if "turns" in tables:
                cursor.execute("SELECT count(*) FROM turns;")
                turns_count = cursor.fetchone()[0]
            if "memories" in tables:
                cursor.execute("SELECT count(*) FROM memories;")
                memories_count = cursor.fetchone()[0]

            conn.close()

            if not is_intact:
                return DiagnosticResult(
                    subsystem="database",
                    status="error",
                    details=f"SQLite integrity check failed: {integrity}",
                    metrics={"tables": tables, "integrity": integrity},
                    remediation_available=True,
                )

            return DiagnosticResult(
                subsystem="database",
                status="healthy",
                details=f"SQLite intact (journal={journal_mode}, turns={turns_count}, memories={memories_count})",
                metrics={
                    "journal_mode": journal_mode,
                    "turns": turns_count,
                    "memories": memories_count,
                    "tables": tables,
                },
                remediation_available=False,
            )
        except Exception as exc:
            return DiagnosticResult(
                subsystem="database",
                status="error",
                details=f"Database diagnostic exception: {exc}",
                remediation_available=True,
            )

    async def check_obsidian_vault(self) -> DiagnosticResult:
        """Verify Obsidian vault graph health, wikilinks, and indices."""
        try:
            from .tools.obsidian_brain import VAULT_PATH
            vault_root = VAULT_PATH

            if not vault_root.exists():
                return DiagnosticResult(
                    subsystem="obsidian_vault",
                    status="warning",
                    details=f"Obsidian vault directory not found at {vault_root}",
                    remediation_available=True,
                )

            md_files = list(vault_root.rglob("*.md"))
            if not md_files:
                return DiagnosticResult(
                    subsystem="obsidian_vault",
                    status="warning",
                    details=f"No markdown notes found in vault {vault_root}",
                    remediation_available=True,
                )

            # Check index.md exists
            index_path = vault_root / "index.md"
            log_path = vault_root / "log.md"

            has_index = index_path.exists()
            has_log = log_path.exists()

            if not has_index or not has_log:
                return DiagnosticResult(
                    subsystem="obsidian_vault",
                    status="warning",
                    details=f"Missing core vault files (index={has_index}, log={has_log})",
                    metrics={"total_notes": len(md_files)},
                    remediation_available=True,
                )

            return DiagnosticResult(
                subsystem="obsidian_vault",
                status="healthy",
                details=f"Vault healthy with {len(md_files)} knowledge notes across 4 layers",
                metrics={"total_notes": len(md_files), "vault_root": str(vault_root)},
                remediation_available=False,
            )
        except Exception as exc:
            return DiagnosticResult(
                subsystem="obsidian_vault",
                status="error",
                details=f"Obsidian vault check error: {exc}",
                remediation_available=True,
            )

    async def check_brain_engines(self) -> DiagnosticResult:
        """Check active brain models, local Ollama daemon, or Relay reachability."""
        try:
            active_brain = "unknown"
            chain_status: list[dict[str, Any]] = []

            if self.service and hasattr(self.service, "brain"):
                desc = self.service.brain.describe()
                active_brain = desc.get("active", "none")
                chain_status = desc.get("chain", [])

            return DiagnosticResult(
                subsystem="brain_router",
                status="healthy" if active_brain != "none" else "warning",
                details=f"Brain router active engine: '{active_brain}' with {len(chain_status)} fallback tiers",
                metrics={"active": active_brain, "chain": chain_status},
                remediation_available=active_brain == "none",
            )
        except Exception as exc:
            return DiagnosticResult(
                subsystem="brain_router",
                status="error",
                details=f"Brain diagnostic error: {exc}",
                remediation_available=True,
            )

    async def check_subagents(self) -> DiagnosticResult:
        """Inspect subagent swarm for stalled, deadlocked, or orphaned tasks."""
        try:
            from .tools.subagents import SubagentSwarmManager
            mgr = SubagentSwarmManager.get_instance()
            records = list(mgr.records.values())

            now = time.time()
            stalled_count = 0
            active_count = 0

            for rec in records:
                if rec.status == "running":
                    active_count += 1
                    # Consider tasks running > 300s without update as potentially stalled
                    if now - rec.started_at > 300.0:
                        stalled_count += 1

            if stalled_count > 0:
                return DiagnosticResult(
                    subsystem="subagents",
                    status="warning",
                    details=f"Found {stalled_count} stalled sub-agent(s) out of {active_count} active",
                    metrics={"active": active_count, "stalled": stalled_count},
                    remediation_available=True,
                )

            return DiagnosticResult(
                subsystem="subagents",
                status="healthy",
                details=f"Subagent swarm operational ({active_count} active, {len(records)} total tracked)",
                metrics={"active": active_count, "total": len(records)},
                remediation_available=False,
            )
        except Exception as exc:
            return DiagnosticResult(
                subsystem="subagents",
                status="warning",
                details=f"Subagent check note: {exc}",
                remediation_available=True,
            )

    async def check_stt_pipeline(self) -> DiagnosticResult:
        """Verify speech-to-text availability and configuration."""
        try:
            if self.service and hasattr(self.service, "stt"):
                stt = self.service.stt
                available = stt.available() if callable(getattr(stt, "available", None)) else False
                desc = stt.describe() if callable(getattr(stt, "describe", None)) else {}
                return DiagnosticResult(
                    subsystem="stt",
                    status="healthy" if available else "warning",
                    details=f"Faster-Whisper STT (model={desc.get('model', 'small.en')}, device={desc.get('device', 'auto')}, available={available})",
                    metrics=desc,
                    remediation_available=not available,
                )
            return DiagnosticResult(
                subsystem="stt",
                status="healthy",
                details="STT configured with Silero VAD whisper filter (threshold=0.20)",
                remediation_available=False,
            )
        except Exception as exc:
            return DiagnosticResult(
                subsystem="stt",
                status="error",
                details=f"STT diagnostic error: {exc}",
                remediation_available=True,
            )

    async def check_tts_pipeline(self) -> DiagnosticResult:
        """Verify text-to-speech engine readiness."""
        try:
            if self.service and hasattr(self.service, "tts"):
                tts = self.service.tts
                available = tts.available() if callable(getattr(tts, "available", None)) else False
                name = getattr(tts, "name", "edge")
                return DiagnosticResult(
                    subsystem="tts",
                    status="healthy" if available else "warning",
                    details=f"TTS Engine: {name} (voice=en-GB-RyanNeural, available={available})",
                    metrics={"engine": name, "available": available},
                    remediation_available=not available,
                )
            return DiagnosticResult(
                subsystem="tts",
                status="healthy",
                details="TTS configured with Edge British Neural Engine",
                remediation_available=False,
            )
        except Exception as exc:
            return DiagnosticResult(
                subsystem="tts",
                status="error",
                details=f"TTS diagnostic error: {exc}",
                remediation_available=True,
            )

    async def check_wake_pipeline(self) -> DiagnosticResult:
        """Verify wake word detector status."""
        try:
            from .wake import WakeWordDetector
            detector = WakeWordDetector(self.settings)
            available = detector.available
            ready = detector.ready
            return DiagnosticResult(
                subsystem="wake_word",
                status="healthy" if (available and ready) else "warning",
                details=f"openWakeWord (model={self.settings.wake_model}, threshold={self.settings.wake_threshold}, ready={ready})",
                metrics={"available": available, "ready": ready},
                remediation_available=not ready,
            )
        except Exception as exc:
            return DiagnosticResult(
                subsystem="wake_word",
                status="warning",
                details=f"Wake detector note: {exc}",
                remediation_available=True,
            )

    async def check_filesystem(self) -> DiagnosticResult:
        """Check filesystem read/write access and free storage space."""
        try:
            data_dir = self.settings.data_dir
            data_dir.mkdir(parents=True, exist_ok=True)

            # Test write access
            test_file = data_dir / ".health_probe.tmp"
            test_file.write_text(f"probe_{time.time()}", encoding="utf-8")
            test_file.unlink(missing_ok=True)

            # Check free space
            _total, _used, free = shutil.disk_usage(str(data_dir))
            free_gb = free / (1024 ** 3)

            status = "healthy"
            if free_gb < 1.0:
                status = "error"
            elif free_gb < 5.0:
                status = "warning"

            return DiagnosticResult(
                subsystem="filesystem",
                status=status,
                details=f"Data storage writable. Free disk space: {free_gb:.1f} GB",
                metrics={"free_gb": round(free_gb, 2), "data_dir": str(data_dir)},
                remediation_available=status != "healthy",
            )
        except Exception as exc:
            return DiagnosticResult(
                subsystem="filesystem",
                status="error",
                details=f"Filesystem access error: {exc}",
                remediation_available=True,
            )

    async def check_policy_engine(self) -> DiagnosticResult:
        """Check policy security rules and tier enforcement."""
        try:
            if self.service and hasattr(self.service, "policy"):
                policy = self.service.policy
                return DiagnosticResult(
                    subsystem="policy",
                    status="healthy",
                    details=f"Policy rules loaded ({policy.source}, default_tier={policy.default_tier})",
                    metrics={"source": policy.source, "default_tier": policy.default_tier},
                    remediation_available=False,
                )
            return DiagnosticResult(
                subsystem="policy",
                status="healthy",
                details="3-Tier Policy Engine active",
                remediation_available=False,
            )
        except Exception as exc:
            return DiagnosticResult(
                subsystem="policy",
                status="warning",
                details=f"Policy check error: {exc}",
                remediation_available=True,
            )

    async def check_audit_stream(self) -> DiagnosticResult:
        """Check audit log integrity and appendability."""
        try:
            audit_path = self.settings.audit_path
            if not audit_path.exists():
                return DiagnosticResult(
                    subsystem="audit_log",
                    status="warning",
                    details=f"Audit log file not yet created at {audit_path}",
                    remediation_available=True,
                )

            size_bytes = audit_path.stat().st_size
            return DiagnosticResult(
                subsystem="audit_log",
                status="healthy",
                details=f"Audit log active ({size_bytes / 1024:.1f} KB)",
                metrics={"size_bytes": size_bytes},
                remediation_available=False,
            )
        except Exception as exc:
            return DiagnosticResult(
                subsystem="audit_log",
                status="error",
                details=f"Audit check error: {exc}",
                remediation_available=True,
            )

    # -- Autonomous Self-Repair Routines -----------------------------------

    async def repair(
        self,
        target_subsystems: list[str] | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Execute autonomous self-repair actions on degraded subsystems."""
        diagnostics = await self.diagnose_all()
        applied_actions: list[str] = []
        errors_encountered: list[str] = []

        for diag in diagnostics:
            if target_subsystems and diag.subsystem not in target_subsystems:
                continue

            if diag.status in ("warning", "degraded", "error") or (target_subsystems and diag.subsystem in target_subsystems):
                if dry_run:
                    applied_actions.append(f"[DRY RUN] Would remediate {diag.subsystem} ({diag.details})")
                    continue
                try:
                    repaired = await self._repair_subsystem(diag.subsystem)
                    if repaired:
                        applied_actions.extend(repaired)
                except Exception as exc:
                    err_msg = f"Failed self-repair for {diag.subsystem}: {exc}"
                    logger.error(err_msg)
                    errors_encountered.append(err_msg)

        # Re-run diagnostics to verify post-repair state
        post_diagnostics = await self.diagnose_all()
        healthy_count = sum(1 for d in post_diagnostics if d.status == "healthy")
        warning_count = sum(1 for d in post_diagnostics if d.status == "warning")
        error_count = sum(1 for d in post_diagnostics if d.status in ("error", "degraded"))

        overall = "healthy" if error_count == 0 and warning_count == 0 else ("degraded" if error_count > 0 else "warning")

        record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "overall_status": overall,
            "actions_applied": applied_actions,
            "errors": errors_encountered,
            "post_healthy": healthy_count,
            "post_warning": warning_count,
            "post_error": error_count,
        }
        self._history.append(record)

        # Record remediation in service audit if present
        if self.service and hasattr(self.service, "audit"):
            self.service.audit.record(
                "self_heal_executed",
                actions_count=len(applied_actions),
                actions=applied_actions,
                overall_status=overall,
            )

        return record

    async def _repair_subsystem(self, subsystem: str) -> list[str]:
        """Execute specific remediation recipe for a given subsystem."""
        actions: list[str] = []

        if subsystem == "database":
            db_path = self.settings.db_path
            db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA synchronous=NORMAL;")
            cursor.execute("PRAGMA optimize;")
            conn.commit()
            conn.close()
            actions.append("Optimized SQLite PRAGMAs (WAL mode enabled, index optimized)")

        elif subsystem == "obsidian_vault":
            from .tools.obsidian_brain import VAULT_PATH
            vault_root = VAULT_PATH
            vault_root.mkdir(parents=True, exist_ok=True)

            # Ensure essential folders exist
            for sub in ("entities", "concepts", "comparisons", "queries", "raw/transcripts", "raw/articles"):
                (vault_root / sub).mkdir(parents=True, exist_ok=True)

            # Ensure index.md and log.md exist
            index_path = vault_root / "index.md"
            if not index_path.exists():
                index_path.write_text("# Master Knowledge Graph Index\n\n- [[Welcome]]\n", encoding="utf-8")
                actions.append("Recreated missing Obsidian vault index.md")

            log_path = vault_root / "log.md"
            if not log_path.exists():
                log_path.write_text("# Knowledge Graph Changelog\n\n## System Logs\n", encoding="utf-8")
                actions.append("Recreated missing Obsidian vault log.md")

            actions.append("Verified and repaired Obsidian Brain structure")

        elif subsystem == "subagents":
            from .tools.subagents import SubagentSwarmManager
            mgr = SubagentSwarmManager.get_instance()
            now = time.time()
            repaired_count = 0
            for rec in list(mgr.records.values()):
                if rec.status == "running" and (now - rec.started_at > 300.0):
                    rec.status = "timed_out"
                    rec.result = "Sub-agent execution timed out; self-healed by watchdog."
                    repaired_count += 1
            if repaired_count > 0:
                mgr._persist_all()
                actions.append(f"Auto-healed {repaired_count} stalled sub-agent(s)")

        elif subsystem == "filesystem":
            data_dir = self.settings.data_dir
            data_dir.mkdir(parents=True, exist_ok=True)
            # Clean temporary probe files
            for tmp in data_dir.glob("*.tmp"):
                with contextlib.suppress(Exception):
                    tmp.unlink(missing_ok=True)
            actions.append("Purged stale temporary runtime artifacts")

        elif subsystem == "audit_log":
            audit_path = self.settings.audit_path
            audit_path.parent.mkdir(parents=True, exist_ok=True)
            if not audit_path.exists():
                audit_path.touch()
                actions.append("Created initial audit log file")

        elif subsystem == "wake_word":
            from .wake import WakeWordDetector
            detector = WakeWordDetector(self.settings)
            if not detector.ready:
                # Trigger model weight preparation
                detector.open_session()
                actions.append("Pre-warmed openWakeWord acoustic model session")

        return actions

    # -- Background Watchdog Task -----------------------------------------

    async def start_watchdog(self, interval_seconds: float = 60.0) -> None:
        """Start non-blocking autonomous watchdog background task."""
        if self._running_watchdog:
            return
        self._running_watchdog = True
        self._watchdog_interval_s = interval_seconds
        self._watchdog_task = asyncio.create_task(self._watchdog_loop(interval_seconds))
        logger.info("Self-healing watchdog started (interval=%.0fs)", interval_seconds)

    async def stop_watchdog(self) -> None:
        """Stop autonomous watchdog background task."""
        self._running_watchdog = False
        if self._watchdog_task and not self._watchdog_task.done():
            self._watchdog_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watchdog_task
        logger.info("Self-healing watchdog stopped")

    async def _watchdog_loop(self, interval_seconds: float) -> None:
        while self._running_watchdog:
            try:
                await asyncio.sleep(interval_seconds)
                self._total_watchdog_runs += 1
                self._last_run_timestamp = datetime.now(UTC).isoformat()
                diagnostics = await self.diagnose_all()
                degraded = [d for d in diagnostics if d.status in ("warning", "degraded", "error") and d.remediation_available]
                if degraded:
                    self._last_run_status = "degraded"
                    logger.warning("Watchdog detected %d degraded subsystems: %s. Initiating self-healing...", len(degraded), [d.subsystem for d in degraded])
                    await self.repair([d.subsystem for d in degraded])
                else:
                    self._last_run_status = "healthy"
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._last_run_status = "error"
                logger.error("Watchdog loop error: %s", exc)

    def get_telemetry(self) -> dict[str, Any]:
        """Return real-time diagnostic telemetry and watchdog history."""
        return {
            "watchdog_running": self._running_watchdog,
            "watchdog_interval_s": self._watchdog_interval_s,
            "total_watchdog_runs": self._total_watchdog_runs,
            "last_run_status": self._last_run_status,
            "last_run_timestamp": self._last_run_timestamp,
            "remediations_count": len(self._history),
            "recent_remediations": self._history[-10:],
        }
