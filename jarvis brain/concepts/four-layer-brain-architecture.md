---
title: 4-Layer Brain Architecture
created: 2026-09-18
updated: 2026-09-18
type: concept
tags: [ai-brain, cognition, semantic-memory, procedural-memory]
confidence: high
sources: [llm-wiki, obsidian]
---

# 4-Layer Brain Architecture

## Overview
A multi-tier cognitive architecture enabling [[entities/jarvis-core|JARVIS Core]] to move beyond transient conversational context into persistent, compounding intelligence.

```
┌────────────────────────────────────────────────────────┐
│  Layer 4: Meta-Cognition & Procedural Self-Tuning      │
│  - Tool routing, execution policies, dynamic planning  │
├────────────────────────────────────────────────────────┤
│  Layer 3: Schema & Structural Topology                 │
│  - SCHEMA.md, index.md, taxonomy, wikilink validation  │
├────────────────────────────────────────────────────────┤
│  Layer 2: Curated Knowledge Vault (The Brain)          │
│  - Entities, Concepts, Comparisons, Queries            │
├────────────────────────────────────────────────────────┤
│  Layer 1: Raw Ground Truth Ingestion                   │
│  - Transcripts, Market Data, Articles, System Logs     │
└────────────────────────────────────────────────────────┘
```

## Cognitive Sub-Types
1. **Episodic Memory:** Chronological audit trail of interactions, logged in `log.md` and SQLite `jarvis.db`.
2. **Semantic Memory:** Interconnected knowledge graph in `entities/` and `concepts/` linked via bidirectional wikilinks.
3. **Procedural Memory:** Executable tool definitions in `apps/gateway/tools/` for interacting with the host OS.
4. **Meta-Cognitive Memory:** Strategic decision rules and self-reflective debate in `prompts.py`.
