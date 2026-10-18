---
title: J.A.R.V.I.S. Core
created: 2026-09-18
updated: 2026-09-18
type: entity
tags: [ai-brain, agentic, architecture, superintelligence]
confidence: high
sources: [config/policy.yaml, apps/gateway/main.py]
---

# J.A.R.V.I.S. Core

**J.A.R.V.I.S.** (Just A Rather Very Intelligent System) is an autonomous, local-first agentic cognitive AI companion built for high-speed systems orchestration, deep conversational debate, and autonomous engineering.

## Key Subsystems
- **Cognitive Brain:** Powered by [[entities/ollama-engine|Ollama Local LLM Engine]] running optimized `jarvis-brain:3b` and `jarvis-brain:4b` neural models.
- **Voice I/O Interface:** Bi-directional neural audio via [[entities/voice-pipeline|Voice Pipeline]], featuring Faster-Whisper VAD and dual-engine TTS (Windows SAPI & Chatterbox clone).
- **Tool Automation Suite:** 38+ specialized tools spanning Python evaluation, desktop control, web research, file patching, and process management, governed by [[concepts/tool-orchestration|Tool Orchestration Policies]].
- **Cognitive Loop:** Autonomous reasoning driven by the [[concepts/autonomous-agentic-loop|Autonomous Agentic Loop]] and [[concepts/cognitive-superintelligence|Superintelligent Debate Architecture]].

## Operational Principles
1. **Local Autonomy:** Zero mandatory cloud dependencies; inference, storage, and speech execution remain on the [[entities/system-node|Host System Node]].
2. **Deterministic Safety:** Dangerous tools require user verification while safe read-only queries execute instantly via the 3-tier policy model.
3. **Compound Learning:** Long-term memory and knowledge reside in the [[concepts/four-layer-brain-architecture|4-Layer Brain Architecture]] stored within this Obsidian Vault.
