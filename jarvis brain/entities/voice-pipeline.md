---
title: Neural Voice Pipeline
created: 2026-09-18
updated: 2026-09-18
type: entity
tags: [voice-stt, voice-tts, architecture]
confidence: high
sources: [apps/gateway/stt.py, apps/gateway/tts.py, apps/orb/src/lib/audio.ts]
---

# Neural Voice Pipeline

## Speech-To-Text (STT) & Listening Precision
- **Model:** Faster-Whisper (`Systran/faster-whisper-base.en` / `small.en`) running with Silero VAD.
- **Client VAD Sensitivity:** Lowered RMS speech gate (`0.007`) and trailing silence (`1200ms`) in Orb WebAudio client to prevent cutting off soft speech or whispers.
- **Utterance Buffer:** Minimum audio payload threshold (`200 bytes`) ensuring short confirmations ("Yes", "Proceed") are accurately transcribed.

## Text-To-Speech (TTS) & Synthesis
- **Primary Fast Engine:** Windows SAPI (`David` / `Zira`) for zero-latency, sub-50ms conversational response times.
- **Cloned Neural Voice:** Chatterbox voice cloning pipeline trained on 5-second acoustic embeddings (`voices/jarvis-5s.wav`) for cinematic British persona timbre.
- **Audio Output:** Streamed as standard WAV/MP3 chunks directly to client audio context.
