"""Speaker Identification & Voiceprint Biometrics for J.A.R.V.I.S.

Extracts acoustic feature embeddings (MFCCs, spectral contrast, pitch,
spectral centroid/bandwidth/rolloff statistics) and matches incoming audio against
enrolled speaker centroids via Cosine Similarity distance metrics.
"""

from __future__ import annotations

import io
import json
import logging
import threading
import time
import wave
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

TARGET_SR = 16000
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
SPEAKERS_DIR = ROOT_DIR / "data" / "speakers"


@dataclass
class SpeakerProfile:
    name: str
    created_at: float
    updated_at: float
    sample_count: int = 1
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SpeakerRecognizer:
    """Thread-safe voiceprint extractor and speaker identification engine."""

    _instance: SpeakerRecognizer | None = None
    _lock = threading.RLock()

    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or SPEAKERS_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.meta_file = self.data_dir / "speakers_meta.json"
        self._profiles: dict[str, SpeakerProfile] = {}
        self._embeddings: dict[str, np.ndarray] = {}
        self._load_profiles()

    @classmethod
    def get_instance(cls, data_dir: Path | None = None) -> SpeakerRecognizer:
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(data_dir=data_dir)
            return cls._instance

    def _load_profiles(self) -> None:
        with self._lock:
            if self.meta_file.exists():
                try:
                    data = json.loads(self.meta_file.read_text(encoding="utf-8"))
                    for name, p_data in data.items():
                        prof = SpeakerProfile(
                            name=p_data["name"],
                            created_at=p_data.get("created_at", time.time()),
                            updated_at=p_data.get("updated_at", time.time()),
                            sample_count=p_data.get("sample_count", 1),
                            description=p_data.get("description", ""),
                            metadata=p_data.get("metadata", {}),
                        )
                        emb_file = self.data_dir / f"{name}.npy"
                        if emb_file.exists():
                            self._embeddings[name] = np.load(str(emb_file))
                            self._profiles[name] = prof
                except Exception as exc:
                    logger.warning("Error loading speaker profiles: %s", exc)

    def _save_meta(self) -> None:
        with self._lock:
            data = {name: prof.to_dict() for name, prof in self._profiles.items()}
            self.meta_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def extract_embedding(self, audio_bytes: bytes, sample_rate: int = TARGET_SR) -> np.ndarray:
        """Extract a normalized 64-dimensional acoustic voiceprint feature vector."""
        samples = self._decode_audio_to_pcm(audio_bytes, sample_rate)
        if len(samples) < 400:  # Less than 25ms
            return np.zeros(64, dtype=np.float32)

        # Normalize waveform amplitude
        samples = samples - np.mean(samples)
        max_val = np.max(np.abs(samples))
        if max_val > 1e-5:
            samples = samples / max_val

        # Compute STFT frames
        n_fft = 512
        hop_length = 256
        window = np.hanning(n_fft)

        # Frame segmentation
        num_frames = max(1, (len(samples) - n_fft) // hop_length + 1)
        frames = np.lib.stride_tricks.sliding_window_view(samples[: (num_frames - 1) * hop_length + n_fft], n_fft)[::hop_length]
        frames = frames * window

        # Magnitude Spectrum
        spectrogram = np.abs(np.fft.rfft(frames, n=n_fft))  # shape: (num_frames, 257)
        spectrogram = np.maximum(spectrogram, 1e-10)
        power_spec = spectrogram**2

        # 1. Mel Filterbank Energies (26 filters)
        n_mels = 26
        mel_filters = self._create_mel_filterbank(sample_rate, n_fft, n_mels)
        mel_energies = np.dot(power_spec, mel_filters.T)  # (num_frames, 26)
        log_mel_energies = np.log(np.maximum(mel_energies, 1e-10))

        # 2. MFCCs (13 coefficients) via DCT
        mfccs = self._dct(log_mel_energies)[:, :13]

        # 3. Spectral Centroid & Spread
        freqs = np.fft.rfftfreq(n_fft, d=1.0 / sample_rate)
        spec_sum = np.sum(spectrogram, axis=1, keepdims=True) + 1e-10
        centroids = np.sum(spectrogram * freqs, axis=1, keepdims=True) / spec_sum
        bandwidths = np.sqrt(np.sum(spectrogram * ((freqs - centroids) ** 2), axis=1, keepdims=True) / spec_sum)

        # 4. Zero Crossing Rate
        zcr = np.mean(np.abs(np.diff(np.sign(frames))), axis=1, keepdims=True) / 2.0

        # 5. Energy statistics
        frame_energies = np.sum(power_spec, axis=1, keepdims=True)

        # Combine frame features: 13 MFCCs + 1 centroid + 1 bandwidth + 1 ZCR + 1 energy = 17 features per frame
        features = np.hstack([mfccs, centroids / 4000.0, bandwidths / 4000.0, zcr, np.log1p(frame_energies)])

        # Delta features across time
        deltas = np.gradient(features, axis=0) if len(features) > 2 else np.zeros_like(features)

        # Aggregate temporal statistics: mean (17) + std (17) + max (17) + delta_mean (13) = 64 dimensions
        f_mean = np.mean(features, axis=0)
        f_std = np.std(features, axis=0) + 1e-6
        f_max = np.max(features, axis=0)
        d_mean = np.mean(deltas[:, :13], axis=0)

        embedding = np.concatenate([f_mean, f_std, f_max, d_mean])  # 17*3 + 13 = 64
        norm = np.linalg.norm(embedding)
        if norm > 1e-6:
            embedding = embedding / norm

        return embedding.astype(np.float32)

    def _create_mel_filterbank(self, sample_rate: int, n_fft: int, n_mels: int) -> np.ndarray:
        """Construct triangular Mel filterbank matrix."""
        low_freq = 100.0
        high_freq = sample_rate / 2.0

        low_mel = 2595.0 * np.log10(1.0 + low_freq / 700.0)
        high_mel = 2595.0 * np.log10(1.0 + high_freq / 700.0)
        mel_points = np.linspace(low_mel, high_mel, n_mels + 2)
        hz_points = 700.0 * (10.0 ** (mel_points / 2595.0) - 1.0)

        bin_points = np.floor((n_fft + 1) * hz_points / sample_rate).astype(int)
        filters = np.zeros((n_mels, n_fft // 2 + 1))

        for m in range(1, n_mels + 1):
            f_m_minus = bin_points[m - 1]
            f_m = bin_points[m]
            f_m_plus = bin_points[m + 1]

            for k in range(f_m_minus, f_m):
                if f_m != f_m_minus:
                    filters[m - 1, k] = (k - f_m_minus) / (f_m - f_m_minus)
            for k in range(f_m, f_m_plus):
                if f_m_plus != f_m:
                    filters[m - 1, k] = (f_m_plus - k) / (f_m_plus - f_m)

        return filters

    def _dct(self, x: np.ndarray) -> np.ndarray:
        """Type-II Discrete Cosine Transform."""
        n = x.shape[1]
        k = np.arange(n)
        matrix = 2.0 * np.cos(np.pi * (np.arange(n) + 0.5)[:, None] * k[None, :] / n)
        return np.dot(x, matrix)

    def _decode_audio_to_pcm(self, audio_bytes: bytes, target_sr: int) -> np.ndarray:
        """Decode raw audio bytes (WAV, WebM, OGG) to 16kHz mono float array."""
        if not audio_bytes:
            return np.zeros(0, dtype=np.float32)

        # Try standard WAV header parse first
        if audio_bytes[:4] == b"RIFF" and audio_bytes[8:12] == b"WAVE":
            try:
                with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
                    sr = wf.getframerate()
                    n_channels = wf.getnchannels()
                    sampwidth = wf.getsampwidth()
                    frames = wf.readframes(wf.getnframes())

                    if sampwidth == 2:
                        pcm = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
                    elif sampwidth == 4:
                        pcm = np.frombuffer(frames, dtype=np.int32).astype(np.float32) / 2147483648.0
                    elif sampwidth == 1:
                        pcm = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
                    else:
                        pcm = np.frombuffer(frames, dtype=np.float32)

                    if n_channels > 1:
                        pcm = pcm.reshape(-1, n_channels).mean(axis=1)

                    if sr != target_sr and len(pcm) > 0:
                        # Resample
                        indices = np.round(np.arange(0, len(pcm), sr / target_sr)).astype(int)
                        indices = indices[indices < len(pcm)]
                        pcm = pcm[indices]

                    return pcm.astype(np.float32)
            except Exception:
                pass

        # Fallback to soundfile / PyAV or raw PCM16
        try:
            import soundfile as sf
            data, sr = sf.read(io.BytesIO(audio_bytes))
            if data.ndim > 1:
                data = data.mean(axis=1)
            if sr != target_sr:
                indices = np.round(np.arange(0, len(data), sr / target_sr)).astype(int)
                indices = indices[indices < len(data)]
                data = data[indices]
            return data.astype(np.float32)
        except Exception:
            pass

        # Raw int16 fallback
        try:
            return (np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0)
        except Exception:
            return np.zeros(0, dtype=np.float32)

    def enroll(
        self,
        name: str,
        audio_bytes: bytes,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        """Enroll or update a speaker's voiceprint profile."""
        clean_name = name.strip()
        if not clean_name:
            return False, "Speaker name cannot be empty."

        embedding = self.extract_embedding(audio_bytes)
        if np.all(embedding == 0):
            return False, "Could not extract valid audio voiceprint from sample."

        with self._lock:
            emb_file = self.data_dir / f"{clean_name}.npy"
            now = time.time()

            if clean_name in self._profiles and clean_name in self._embeddings:
                # Update existing profile using moving average centroid
                old_emb = self._embeddings[clean_name]
                count = self._profiles[clean_name].sample_count
                new_emb = (old_emb * count + embedding) / (count + 1)
                new_emb = new_emb / np.linalg.norm(new_emb)
                self._embeddings[clean_name] = new_emb
                self._profiles[clean_name].sample_count += 1
                self._profiles[clean_name].updated_at = now
                if description:
                    self._profiles[clean_name].description = description
                if metadata:
                    self._profiles[clean_name].metadata.update(metadata)
            else:
                # New profile
                self._embeddings[clean_name] = embedding
                self._profiles[clean_name] = SpeakerProfile(
                    name=clean_name,
                    created_at=now,
                    updated_at=now,
                    sample_count=1,
                    description=description or f"Profile for {clean_name}",
                    metadata=metadata or {},
                )

            # Persist to disk
            np.save(str(emb_file), self._embeddings[clean_name])
            self._save_meta()

            return True, f"Successfully enrolled voiceprint for '{clean_name}' (Total samples: {self._profiles[clean_name].sample_count})."

    def identify(
        self,
        audio_bytes: bytes,
        threshold: float = 0.70,
    ) -> tuple[str, float, dict[str, float]]:
        """Identify the speaker of the incoming audio.

        Returns (speaker_name, confidence, all_scores).
        If highest confidence is below threshold, returns ('Unknown', highest_score, all_scores).
        """
        with self._lock:
            if not self._embeddings:
                return "Unknown (No profiles enrolled)", 0.0, {}

            embedding = self.extract_embedding(audio_bytes)
            if np.all(embedding == 0):
                return "Unknown (Audio too short or silent)", 0.0, {}

            scores: dict[str, float] = {}
            for name, prof_emb in self._embeddings.items():
                sim = float(np.dot(embedding, prof_emb))
                # Cosine similarity range [-1, 1] normalized to [0, 1]
                norm_sim = max(0.0, min(1.0, (sim + 1.0) / 2.0))
                scores[name] = round(norm_sim, 3)

            best_speaker = max(scores, key=lambda k: scores[k])
            best_score = scores[best_speaker]

            if best_score >= threshold:
                return best_speaker, best_score, scores
            else:
                return "Unknown Guest", best_score, scores

    def list_profiles(self) -> list[dict[str, Any]]:
        with self._lock:
            return [prof.to_dict() for prof in self._profiles.values()]

    def delete_profile(self, name: str) -> bool:
        clean_name = name.strip()
        with self._lock:
            if clean_name in self._profiles:
                del self._profiles[clean_name]
                if clean_name in self._embeddings:
                    del self._embeddings[clean_name]
                emb_file = self.data_dir / f"{clean_name}.npy"
                if emb_file.exists():
                    emb_file.unlink()
                self._save_meta()
                return True
            return False
