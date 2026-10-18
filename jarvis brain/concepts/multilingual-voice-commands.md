---
title: Multilingual Voice & Intent Command Taxonomy
created: 2026-09-26
updated: 2026-09-26
type: procedure
tags: [voice-stt, voice-tts, persona, desktop-automation, cognition]
confidence: high
sources: [raw/voice-transcripts.md]
---

# Multilingual Voice & Intent Command Taxonomy

This procedure documents the canonical multilingual command set for [[concepts/autonomous-agentic-loop]] and the [[entities/jarvis-core-architecture]].

## 1. Intent Transformation Architecture
J.A.R.V.I.S. integrates real-time intent normalization via `apps/gateway/hinglish.py`:
- Inbound speech in English, Hinglish, or Devanagari Hindi is parsed by Faster-Whisper.
- Intent transforms map vernacular utterances to canonical tool directives before dispatching to the Ollama local brain.
- Results route back through Edge TTS with automatic voice selection between British (`en-GB-RyanNeural`) and Indian (`en-IN-PrabhatNeural`) accents.

## 2. Core Operational Matrices

### Desktop & Windows Execution
- **Show Desktop**: "desktop dikhao", "minimize all windows", "parde gira do", "डेस्कटॉप दिखाओ" -> `desktop_window_control(action='show_desktop')`
- **Application Launching**: "notepad kholo", "open calculator", "terminal chalao", "नोटपैड खोलो" -> `app_launch`
- **Visual Capture**: "screenshot le lo", "screen capture karo", "camera se photo khincho" -> `desktop_screenshot`, `camera_capture`

### Media & Audio Intelligence
- **Playback**: "lofi music chalao", "Arijit Singh ke gaane sunao", "गाना बजाओ" -> `music_play`
- **Volume**: "awaaz badhao", "volume kam karo", "chup ho jao", "आवाज़ बढ़ाओ" -> `volume_control`
- **Track Navigation**: "agla gaana", "pause karo", "wapas chalao" -> `media_control`

### Cognitive Vault & RAG Directives
- **Search Vault**: "obsidian me search karo", "brain vault me dhoondo" -> `brain_vault_search`
- **Record Knowledge**: "obsidian me note banao", "yeh yaad rakhna" -> `brain_vault_note_write`, `memory_remember`
- **Vitals & Health**: "system ka haal batao", "self heal diagnose karo" -> `system_info`, `self_heal_diagnose`

## Related Notes
- [[concepts/autonomous-agentic-loop]]
- [[index]]
