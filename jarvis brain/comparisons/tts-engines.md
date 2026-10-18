---
title: Neural Voice Engine Evaluation
created: 2026-09-18
updated: 2026-09-18
type: comparison
tags: [comparison, voice-tts, architecture]
confidence: high
sources: [entities/voice-pipeline.md]
---

# Neural Voice Engine Evaluation

## Benchmark Dimensions

| Metric | Windows SAPI (`David` / `Zira`) | Cloned Neural Chatterbox |
| :--- | :--- | :--- |
| **Synthesis Latency** | **<50ms** (Instant zero-wait) | 1.2s - 2.5s on GPU / 12s on CPU |
| **Acoustic Realism** | Robotic / Classic Windows tone | Natural human timbre, British accent |
| **Compute Overhead** | Negligible (0% GPU / <1% CPU) | High (PyTorch acoustic auto-regressive model) |
| **Reliability** | Native Windows kernel subsystem | Dependent on PyTorch CUDA device context |

## Recommendation
- **Interactive Conversational Mode:** Use Windows SAPI for snappy conversational responsiveness.
- **Cinematic / Extended Monologue Mode:** Use Chatterbox on GPU for realistic British persona voice delivery.
