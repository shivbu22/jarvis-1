---
title: Autonomous Agentic Loop
created: 2026-09-18
updated: 2026-09-18
type: concept
tags: [agentic, cognition, superintelligence]
confidence: high
sources: [apps/gateway/agent.py, apps/gateway/tools/agentic.py]
---

# Autonomous Agentic Loop

## Definition
The operational cycle through which [[entities/jarvis-core|JARVIS]] autonomously takes high-level user intent, formulates a multi-step execution plan, invokes necessary tools, checks intermediate results, and self-corrects until completion.

```
       [User Intent / Voice Command]
                     │
                     ▼
           [Goal Decomposition]
                     │
                     ▼
             [Tool Selection] ──► [[concepts/tool-orchestration|Tool Security Policy]]
                     │
                     ▼
             [Tool Execution] (Python eval, Code Search, Web Scrape, Desktop)
                     │
                     ▼
         [Intermediate Observation]
                     │
          ┌──────────┴──────────┐
   [Needs Correction?]     [Objective Met?]
          │                     │
      (Refine)             (Synthesize)
          │                     │
          └──────────┬──────────┘
                     ▼
           [Final Verbal Report]
```

## Agentic Capabilities
1. **Task Plan Management:** Storing and tracking structured plans in `data/tasks.json`.
2. **Dynamic Script Execution:** Executing ephemeral Python scripts to process data, verify APIs, and compute analytics.
3. **Reflective Verification:** Confirming changes on disk before reporting task completion to the user.
