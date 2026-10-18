# Self-Healing and Autonomous Diagnostic Engine

Autonomous resilience and auto-remediation architecture for J.A.R.V.I.S. runtime, monitoring 10 core subsystem tiers with continuous non-blocking background watchdog routines.

## Overview
- **Type**: Core System Engine
- **Module**: `apps/gateway/self_heal.py`
- **Tool Definitions**: `apps/gateway/tools/self_heal.py`
- **Watchdog Cadence**: 60.0s autonomous background loop
- **Related Concepts**: [[self-healing-and-autonomous-repair]], [[recursive-self-building-and-meta-learning]], [[four-layer-brain-architecture]]

---

## 10-Tier Monitored Subsystems

| Subsystem | Scope & Diagnostic Target | Autonomous Remediation Action |
| :--- | :--- | :--- |
| **`database`** | SQLite schema integrity, table counts (`turns`, `memories`), journal mode | Re-enables WAL mode, runs `VACUUM` & `PRAGMA optimize` |
| **`obsidian_vault`** | Vault folder hierarchy, note count, index/log file existence | Auto-creates missing layer directories, heals index files |
| **`brain_router`** | Local Ollama daemon (`11434`), Relay status, fallback chains | Auto-resets connection pools, adjusts routing priorities |
| **`subagents`** | Active vs stalled background swarms (>300s timeout detection) | Marks zombie subagents as `timed_out`, persists state |
| **`stt`** | FasterWhisper on CUDA, Silero VAD threshold (0.20), whisper priming | Re-initializes CUDA context, applies whisper priming |
| **`tts`** | Microsoft Edge Neural British voice (`en-GB-RyanNeural`), latency | Auto-switches fallback between Chatterbox and SAPI |
| **`wake_word`** | openWakeWord model runtime, streaming session buffers | Clears orphaned stream sessions, resets detector state |
| **`filesystem`** | Storage write access, free disk space (>1GB safety threshold) | Cleans temporary audio cache and orphaned temp files |
| **`policy`** | 3-tier security policy (`auto`, `confirm`, `deny`) | Re-verifies syntax and confirms tier assignment |
| **`audit_log`** | `data/audit.jsonl` appendability and stream integrity | Restores log file handle and ensures append permission |

---

## Exposed Tool Interface

1. `self_heal_diagnose`: Deep multi-tier diagnostics returning health status, latency, and remediation flags.
2. `self_heal_repair`: Executes targeted or comprehensive autonomous repair procedures across any degraded subsystem.
3. `self_heal_status`: Returns runtime telemetry, watchdog status, last scan timestamp, and historical self-healing events.

---

## System Integration & Auto-Recovery
The engine operates both reactively (invoked directly during tool failures or user queries) and proactively (running in the background as `SelfHealWatchdog` every 60 seconds). Every remediation action is logged to `data/audit.jsonl` and mirrored to the knowledge graph.
