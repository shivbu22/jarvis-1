# J.A.R.V.I.S. Neural Brain Knowledge Schema
> Obsidian-compatible 4-Layer Cognitive Brain & LLM-Wiki
> Vault Root: `C:\Users\ahiva\OneDrive\Desktop\jarvis\jarvis brain`

## Domain
Autonomous superintelligent AI agent, neural cognitive memory, systems automation, algorithmic trading, software engineering, and strategic synthesis.

## Conventions
- **Format:** Pure Markdown with YAML frontmatter on all knowledge notes.
- **Filenames:** Lowercase, hyphens, alphanumeric (e.g. `autonomous-agentic-loop.md`).
- **Linking:** Mandatory Wikilinks (e.g. `[[concepts/autonomous-agentic-loop]]`) linking between related concepts (min 2 outbound links per note).
- **Updates:** Always update the `updated: YYYY-MM-DD` frontmatter and log changes in `log.md`.
- **Navigation:** Every page is cataloged in `index.md` under its respective category.
- **Provenance:** Raw input resides immutably in `raw/`; synthetic claims reference their source notes.

## Frontmatter Standard
```yaml
---
title: Note Title
created: YYYY-MM-DD
updated: YYYY-MM-DD
type: entity | concept | comparison | query | memory | procedure
tags: [taxonomy-tag-1, taxonomy-tag-2]
confidence: high | medium | low
sources: [raw/source-file.md]
---
```

## Tag Taxonomy
- **Cognition & Core:** `ai-brain`, `cognition`, `agentic`, `persona`, `superintelligence`, `reasoning`
- **System & Tools:** `architecture`, `desktop-automation`, `python-eval`, `voice-stt`, `voice-tts`, `ollama`
- **User & Domain:** `user-profile`, `trading`, `quant`, `indian-markets`, `nse`, `bse`
- **Memory & Synthesis:** `episodic-memory`, `semantic-memory`, `procedural-memory`, `synthesis`, `comparison`

## Cognitive Layers
1. **Layer 1 (Raw Ingest):** Immutable inputs in `raw/` (transcripts, papers, articles, telemetry).
2. **Layer 2 (The Wiki / Vault):** Actively curated notes in `entities/`, `concepts/`, `comparisons/`, `queries/`.
3. **Layer 3 (Schema & Index):** Structural integrity via `SCHEMA.md` and `index.md`.
4. **Layer 4 (Procedural & Meta-Cognition):** Real-time tool execution, reflective validation, and autonomous self-tuning.
