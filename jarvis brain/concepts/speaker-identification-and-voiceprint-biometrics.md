---
type: concept
tags:
  - biometrics
  - voice-recognition
  - speaker-diarization
  - security
title: Speaker Identification and Voiceprint Biometrics
created_at: 2026-09-18
updated_at: 2026-09-18
---

# Speaker Identification and Voiceprint Biometrics

Speaker Identification enables J.A.R.V.I.S. to determine **who is speaking** based on acoustic biometric features rather than solely transcribing words.

## Architecture & Feature Pipeline

1. **Acoustic Waveform Preprocessing:**
   - 16 kHz Mono float normalization.
   - Slided STFT frames (512 FFT, 256 hop length) with Hanning windowing.

2. **64-Dimensional Feature Representation:**
   - **13 MFCCs:** Mel-frequency cepstral coefficients capturing vocal tract resonances.
   - **Spectral Centroid & Bandwidth:** Frequency center of mass and dispersion.
   - **Zero-Crossing Rate (ZCR):** High-frequency / fricative content indicator.
   - **Delta Dynamics:** First-order temporal derivatives of spectral trajectories.
   - **Statistical Aggregation:** Mean, variance, max, and temporal deltas.
   - **L2 Sphere Normalization:** Ensures cosine similarity equivalence.

3. **Centroid Enrollment & Similarity Matching:**
   - Enrolled profiles stored in `data/speakers/`.
   - Incoming audio matched via Cosine Similarity:
     $$\text{Similarity}(u, v) = \frac{u \cdot v}{\|u\|_2 \|v\|_2}$$
   - Dynamic thresholding ($0.70$) distinguishes Master from Guests / Unknown speakers.

## Related Notes
- [[entities/voice-pipeline]]
- [[entities/jarvis-core]]
- [[concepts/autonomous-agentic-loop]]
