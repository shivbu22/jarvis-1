"""J.A.R.V.I.S. Speaker Voiceprint Training & Enrollment CLI.

Enrolls the master user's voice into the biometric speaker identification system
(data/speakers/) using acoustic feature vectors (MFCCs, spectral contrast, pitch).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.gateway.speaker_id import SpeakerRecognizer, SPEAKERS_DIR


def list_speakers() -> None:
    rec = SpeakerRecognizer.get_instance()
    profiles = rec.list_profiles()
    print("\n========================================================")
    print("  J.A.R.V.I.S. ENROLLED SPEAKER BIOMETRIC PROFILES")
    print("========================================================")
    if not profiles:
        print("No speaker profiles are currently enrolled in memory.")
        print("To enroll yourself:")
        print("  python scripts/enroll_speaker_cli.py --name Ahiv --description Master")
        return

    for p in profiles:
        name = p.get("name")
        desc = p.get("description", "Master")
        samples = p.get("sample_count", 1)
        print(f"• Name: {name:<12} | Role: {desc:<15} | Samples: {samples}")
    print("--------------------------------------------------------")


def enroll_from_file(name: str, audio_path: Path, description: str = "Master") -> bool:
    if not audio_path.exists():
        print(f"[Error] Audio file not found: {audio_path}", file=sys.stderr)
        return False

    try:
        audio_bytes = audio_path.read_bytes()
    except Exception as exc:
        print(f"[Error] Reading audio file: {exc}", file=sys.stderr)
        return False

    rec = SpeakerRecognizer.get_instance()
    success, msg = rec.enroll(name=name, audio_bytes=audio_bytes, description=description)
    if success:
        print(f"[Speaker ID] Successfully enrolled '{name}' ({description}) from {audio_path.name}!")
        print(f"[Speaker ID] Voiceprint embedding stored in: {SPEAKERS_DIR / f'{name}.npy'}")
    else:
        print(f"[Speaker ID] Enrollment failed: {msg}", file=sys.stderr)
    return success


def record_and_enroll(name: str, seconds: float = 5.0, description: str = "Master") -> bool:
    """Record directly from user's microphone and enroll."""
    print(f"[Speaker ID] Preparing to record {seconds} seconds from default microphone for '{name}'...")
    try:
        import sounddevice as sd
        import numpy as np

        sr = 16000
        print("\n>>> SPEAK NOW: Introduce yourself ('I am the master, Jarvis verify my voice')...")
        recording = sd.rec(int(seconds * sr), samplerate=sr, channels=1, dtype="int16")
        sd.wait()
        print(">>> RECORDING CAPTURED! Processing acoustic embedding...")

        import io
        import wave
        wav_buf = io.BytesIO()
        with wave.open(wav_buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(recording.tobytes())

        audio_bytes = wav_buf.getvalue()
        rec = SpeakerRecognizer.get_instance()
        success, msg = rec.enroll(name=name, audio_bytes=audio_bytes, description=description)
        if success:
            print(f"[Speaker ID] Successfully enrolled '{name}' ({description})!")
        else:
            print(f"[Speaker ID] Enrollment failed: {msg}")
        return success
    except ImportError:
        print("[Notice] 'sounddevice' is not installed for direct terminal mic recording.")
        print("You can provide any existing .wav recording:")
        print(f"  python scripts/enroll_speaker_cli.py --name {name} --audio path/to/your_voice.wav")
        # Check if voices/jarvis-5s.wav exists as a fallback sample
        ref_file = ROOT / "voices" / "jarvis-5s.wav"
        if ref_file.exists():
            print(f"[Speaker ID] Using reference audio sample {ref_file} for initial voiceprint profile...")
            return enroll_from_file(name, ref_file, description=description)
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="J.A.R.V.I.S. Speaker Voiceprint Enrollment CLI")
    parser.add_argument("--list", action="store_true", help="List enrolled speaker profiles")
    parser.add_argument("--name", type=str, default="Ahiv", help="Speaker's name to enroll")
    parser.add_argument("--description", type=str, default="Master", help="Speaker role / description")
    parser.add_argument("--audio", type=Path, help="Path to a .wav audio sample")
    parser.add_argument("--record", action="store_true", help="Record 5s from microphone")
    args = parser.parse_args()

    if args.list:
        list_speakers()
        return

    if args.audio:
        enroll_from_file(args.name, args.audio, args.description)
        return

    # Default to recording or enrolling
    if args.record:
        record_and_enroll(args.name, description=args.description)
    else:
        # Check if audio exists or enroll default
        ref_wav = ROOT / "voices" / "jarvis-5s.wav"
        if ref_wav.exists():
            enroll_from_file(args.name, ref_wav, args.description)
        else:
            record_and_enroll(args.name, description=args.description)


if __name__ == "__main__":
    main()
