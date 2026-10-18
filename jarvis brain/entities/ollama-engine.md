---
title: Ollama Inference Engine
created: 2026-09-18
updated: 2026-09-18
type: entity
tags: [ollama, ai-brain, architecture]
confidence: high
sources: [apps/gateway/brain/ollama.py, config/ollama/Modelfile.jarvis]
---

# Ollama Inference Engine

## Overview
Local inference backend hosting optimized quantized neural models for [[entities/jarvis-core|JARVIS Core]].

## Active Models
- **`jarvis-brain:3b`:** Fast quantized Llama 3.2 3B model tuned with custom system templates, stop sequences, and high-precision tool formatting.
- **`jarvis-brain:4b`:** Deep-reasoning 4B parameter model for complex code evaluation and strategic debate.

## Features
- **Deterministic Tool Calling:** Direct JSON-schema tool emission and argument binding.
- **Low Latency:** Instant response time (<300ms time-to-first-token on NVIDIA RTX 3050).
- **Context Window:** 4096-8192 tokens with KV-cache persistence.
- **Connection:** Local REST endpoint at `http://127.0.0.1:11434/api/generate` and `/api/chat`.
