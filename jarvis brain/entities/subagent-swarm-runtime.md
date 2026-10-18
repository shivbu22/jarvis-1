---
title: Sub-Agent Swarm Runtime
created: 2026-09-18
updated: 2026-09-18
type: entity
tags: [agentic, subagents, swarm, runtime, architecture]
confidence: high
sources: [apps/gateway/tools/subagents.py]
---

# Sub-Agent Swarm Runtime

## Overview
The Sub-Agent Swarm Runtime is the execution engine embedded within [[entities/jarvis-core|JARVIS Core]] that manages asynchronous child agent lifecycles, concurrency pools, and tool dispatching.

```
┌────────────────────────────────────────────────────────┐
│               Sub-Agent Swarm Runtime                  │
│                                                        │
│  - Task Queue & Dispatcher: asyncio task management    │
│  - Tool Whitelist Enforcement: 3-tier security sandbox │
│  - Batch Aggregator: asyncio.gather parallel pooling   │
│  - Life-cycle Audit: state transitions to log.md       │
└────────────────────────────────────────────────────────┘
```

## Runtime Operations
- `subagent_spawn`: Spawns a background or foreground sub-agent for an independent task.
- `subagent_batch_run`: Runs $N$ sub-agents in parallel simultaneously and gathers all reports.
- `subagent_list`: Lists all running and completed sub-agents.
- `subagent_status`: Inspects a specific sub-agent's step log and result.
- `subagent_abort`: Cancels a running sub-agent.

## Related Concepts
- [[concepts/subagent-swarm-orchestration|Sub-Agent Swarm Orchestration]]
- [[concepts/hierarchical-task-networks|Hierarchical Task Networks]]
- [[concepts/inter-agent-communication-protocol|Inter-Agent Communication Protocol]]
