---
title: Host Hardware & System Node
created: 2026-09-18
updated: 2026-09-18
type: entity
tags: [architecture, system-node, ollama, voice-stt]
confidence: high
sources: [apps/gateway/config.py]
---

# Host Hardware & System Node

## Specifications
- **Operating System:** Windows 11 64-bit
- **Host User:** `ahiva` (`C:\Users\ahiva`)
- **Acceleration GPU:** NVIDIA GeForce RTX 3050 Laptop GPU (CUDA 12.6, PyTorch `2.6.0+cu126`)
- **Python Runtime:** Python 3.12.14 dedicated virtual environment (`C:\Users\ahiva\AppData\Local\Jarvis\venv`)
- **Local LLM Engine:** [[entities/ollama-engine|Ollama Service]] running at `http://127.0.0.1:11434`
- **Gateway Server:** Fast-async Gateway server on `http://127.0.0.1:8000`
- **Visual Orb UI:** Vite React UI on `http://127.0.0.1:5173`

## Subsystem Architecture
```
[User Microphone / Voice]
          │
          ▼
[Faster-Whisper VAD STT Engine]
          │
          ▼
[JARVIS FastAPI Gateway Server (:8000)]
   │              │                 │
   ▼              ▼                 ▼
[Ollama LLM]  [Tool Engine]   [Obsidian Vault (:brain)]
   │              │                 │
   └──────────────┬─────────────────┘
                  ▼
   [SAPI / Chatterbox TTS Engine]
                  │
                  ▼
         [User Speakers / Orb UI]
```
