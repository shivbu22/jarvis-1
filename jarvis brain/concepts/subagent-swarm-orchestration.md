---
title: Sub-Agent Swarm Orchestration
created: 2026-09-18
updated: 2026-09-18
type: concept
tags: [agentic, subagents, swarm, parallel-execution, superintelligence]
confidence: high
sources: [apps/gateway/tools/subagents.py, concepts/autonomous-agentic-loop.md]
---

# Sub-Agent Swarm Orchestration

## Overview
Sub-Agent Swarm Orchestration is the architectural pattern where [[entities/jarvis-core|JARVIS Core]] acts as a primary controller/dispatcher that delegates distinct, parallel sub-tasks to isolated child agents (sub-agents). Each sub-agent runs in its own bounded asynchronous context with a dedicated objective, private working memory, and a tailored tool whitelist.

```
                   ┌──────────────────────────────────────┐
                   │           J.A.R.V.I.S. Core          │
                   │      (Controller & Orchestrator)     │
                   └──────────────────┬───────────────────┘
                                      │
           ┌──────────────────────────┼──────────────────────────┐
           │ parallel dispatch        │ parallel dispatch        │ parallel dispatch
           ▼                          ▼                          ▼
┌───────────────────────┐  ┌───────────────────────┐  ┌───────────────────────┐
│     Sub-Agent A       │  │     Sub-Agent B       │  │     Sub-Agent C       │
│  (Market Recon & STT) │  │  (Codebase Deep Scan) │  │ (Vault Knowledge RAG) │
├───────────────────────┤  ├───────────────────────┤  ├───────────────────────┤
│ Context: Isolated     │  │ Context: Isolated     │  │ Context: Isolated     │
│ Tools: Web + Quant    │  │ Tools: AST + Eval     │  │ Tools: Obsidian Brain │
└──────────┬────────────┘  └──────────┬────────────┘  └──────────┬────────────┘
           │                          │                          │
           └──────────────────────────┼──────────────────────────┘
                                      ▼
                   ┌──────────────────────────────────────┐
                   │    Multi-Agent Synthesis Engine      │
                   │   - Conflict Resolution & Review     │
                   │   - Final Unified Solution to User   │
                   └──────────────────────────────────────┘
```

## Key Principles
1. **Context Isolation:** Sub-agents operate in fresh, lightweight context environments to prevent context window degradation and hallucination accumulation.
2. **Parallel Concurrency:** Independent research, code review, file analysis, and verification tasks run simultaneously using asynchronous task pooling (`asyncio.gather`).
3. **Strict Tool Sandboxing:** Each sub-agent is assigned an explicit whitelist of permissible tools, preventing unauthorized side effects or runaway executions.
4. **Hierarchical Two-Stage Review:** As specified in [[concepts/agentic-self-healing-and-reflection|Agentic Self-Healing]], sub-agent outputs undergo spec verification and quality validation before being merged into the global state.

## Related Concepts
- [[concepts/autonomous-agentic-loop|Autonomous Agentic Loop]]
- [[concepts/hierarchical-task-networks|Hierarchical Task Networks]]
- [[concepts/inter-agent-communication-protocol|Inter-Agent Communication Protocol]]
- [[entities/subagent-swarm-runtime|Sub-Agent Swarm Runtime]]
