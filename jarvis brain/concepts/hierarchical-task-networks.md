---
title: Hierarchical Task Networks & Decomposition
created: 2026-09-18
updated: 2026-09-18
type: concept
tags: [agentic, task-planning, htn, parallel-execution]
confidence: high
sources: [concepts/subagent-swarm-orchestration.md]
---

# Hierarchical Task Networks & Decomposition

## Overview
Hierarchical Task Networks (HTN) allow [[entities/jarvis-core|JARVIS Core]] to break down high-level user directives into Directed Acyclic Graphs (DAGs) of executable sub-tasks that can be scheduled across parallel sub-agents.

```
                  High-Level Objective
                  "Perform Full Security & Performance Audit"
                                │
               ┌────────────────┴────────────────┐
               ▼                                 ▼
       Static Code Scan                 Live Runtime Profiling
   (Sub-Agent: AST & Files)             (Sub-Agent: Process & Eval)
               │                                 │
               └────────────────┬────────────────┘
                                ▼
                       Consolidated Report
                   (Sub-Agent: Spec Synthesis)
```

## Scheduling Strategies
1. **Parallel Execution Pools:** Independent tasks with 0 inter-dependencies execute concurrently in the same time frame.
2. **Topological Dependency Chains:** Dependent tasks wait on completion signals and output payloads of upstream sub-agents.
3. **Dynamic Re-planning:** If a sub-task discovers unexpected roadblocks, the network spawns dedicated diagnosis sub-agents to resolve blockers.

## Related Concepts
- [[concepts/subagent-swarm-orchestration|Sub-Agent Swarm Orchestration]]
- [[concepts/agentic-self-healing-and-reflection|Agentic Self-Healing and Reflection]]
- [[concepts/tool-orchestration|Tool Orchestration & Policy]]
