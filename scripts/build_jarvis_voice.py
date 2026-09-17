"""Prepare the cloned Jarvis voice.

Chatterbox clones a voice from a short reference clip, so this script does the
three things that reference needs:

1. **Validate and normalise** it -- mono, 24 kHz PCM, loudness-evened, at least
   five seconds of clear speech. A clip that is too short or silent produces an
   unstable voice, and finding that out here is far better than mid-sentence.
2. **Optionally prefetch the model weights** (~1.5 GB). The gateway will NOT
   download them during a conversation: a spoken reply must never turn into a
   five-minute unexplained wait. Fetching them is this explicit step.
3. **Write a manifest** recording which clip is in use, so "why does it sound
   different today?" has an answer six months from now.

    python scripts/build_jarvis_voice.py                  # normalise voices/jarvis-5s.wav
    python scripts/build_jarvis_voice.py --prefetch        # also fetch the model
    python scripts/build_jarvis_voice.py --make-reference  # synthesise a placeholder clip

Put your own recording at voices/jarvis-5s.wav. A clean, dry, close-mic clip of
one continuous speaker reads best -- the model copies the recording conditions as
well as the voice.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.gateway.config import load_settings
from apps.gateway.tts import (
    TTSError,
    build_and_save_conds,
)


def make_reference(settings, seconds: float = 8.0) -> Path:
    """Synthesise a placeholder reference from the Windows voice.

    This exists so the whole cloning path can be exercised on a machine with no
    recording yet. It is honest about what it is: a placeholder that makes Jarvis
    sound like a narrator, not a butler. A real clip is strictly better.
    """
    from apps.gateway.tts import SapiEngine

    speaker = SapiEngine(settings)
    if not speaker.available():
        raise TTSError(
            "cannot synthesise a placeholder reference: the Windows voice is unavailable. "
            "Record about ten seconds of speech and save it as voices/jarvis-5s.wav."
        )

    lines = [
        "Good evening sir. All systems are nominal and standing by.",
        "I have reviewed the day's correspondence and nothing requires your attention.",
        "The weather this evening is clear, with a light breeze from the east.",
        "Should you need me, I am listening.",
    ]
    text = " ".join(lines)
    # Repeat until comfortably past the minimum length.
    while len(text) < seconds * 14:
        text += " " + " ".join(lines)

    settings.voices_dir.mkdir(parents=True, exist_ok=True)
    target = settings.voice_ref
    audio = speaker.synthesize(text).data
    target.write_bytes(audio)
    print(f"Wrote a placeholder reference to {target}")
    return target


def prefetch_model(settings) -> bool:
    """Download the Chatterbox weights deliberately, with visible progress."""
    from apps.gateway.tts import _chatterbox_repo

    repo = _chatterbox_repo(settings.tts_backend)
    print(f"Fetching the {repo} model (about 1.5 GB, once)...")
    try:
        from huggingface_hub import snapshot_download

        path = snapshot_download(repo_id=repo)
        print(f"Model ready at {path}")
        return True
    except ImportError:
        print("huggingface_hub is missing; install the voice extra first.", file=sys.stderr)
        return False
    except Exception as exc:
        print(f"Could not fetch the model: {exc}", file=sys.stderr)
        print(
            "Jarvis will keep using the Windows voice until this succeeds. "
            "Re-run when the network is available.",
            file=sys.stderr,
        )
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", type=Path, help="Reference clip (default: voices/jarvis-5s.wav).")
    parser.add_argument("--out", type=Path, help="Normalised output (default: voices/jarvis-ref-24k.wav).")
    parser.add_argument("--make-reference", action="store_true", help="Synthesise a placeholder reference first.")
    parser.add_argument("--prefetch", action="store_true", help="Download the Chatterbox model weights.")
    parser.add_argument("--device", default="cpu", help="cpu | cuda | mps (recorded in the manifest).")
    parser.add_argument("--backend", default="turbo", help="turbo | full")
    parser.add_argument("--exaggeration", type=float, default=0.5, help="Delivery intensity (0.25-1.0).")
    args = parser.parse_args()

    settings = load_settings()
    settings.voices_dir.mkdir(parents=True, exist_ok=True)

    exit_code = 0

    reference = args.ref or settings.voice_ref
    if args.make_reference and not reference.exists():
        try:
            reference = make_reference(settings)
        except TTSError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    if not reference.exists():
        print(
            f"No reference clip at {reference}.\n\n"
            "Either record about ten seconds of the voice you want and save it there,\n"
            "or let Windows synthesise a placeholder:\n\n"
            "    python scripts/build_jarvis_voice.py --make-reference\n",
            file=sys.stderr,
        )
        exit_code = 1
    else:
        target = args.out or (settings.voices_dir / "jarvis-ref-24k.wav")
        try:
            manifest = build_and_save_conds(
                ref_wav=reference,
                out_path=target,
                device=args.device,
                backend=args.backend,
                exaggeration=args.exaggeration,
            )
        except TTSError as exc:
            print(f"Reference rejected: {exc}", file=sys.stderr)
            exit_code = 1
        else:
            print(
                "Reference voice ready:\n"
                f"  clip        {manifest['source']}\n"
                f"  normalised  {manifest['normalised']} ({manifest['seconds']}s @ {manifest['sample_rate']} Hz)\n"
                f"  backend     {manifest['backend']} on {manifest['device']}\n"
                "  manifest    voices/jarvis-conds.json"
            )
            print(
                "\nSet TTS_DEVICE accordingly in .env. The gateway now uses this clip "
                "whenever the cloned voice is the active engine."
            )

    if args.prefetch:
        print()
        if not prefetch_model(settings):
            exit_code = 1
        else:
            print("Restart the gateway; /health will report tts.engine = chatterbox.")

    if exit_code == 0 and not args.prefetch:
        print(
            "\nNext, if you want the cloned voice rather than the Windows one:\n"
            "    python scripts/build_jarvis_voice.py --prefetch\n"
            "(or set TTS_ALLOW_DOWNLOAD=1 in .env to fetch it on first use)"
        )

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
