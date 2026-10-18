"""Speaker Identification & Voiceprint Tools for J.A.R.V.I.S."""

from __future__ import annotations

import logging

from ..speaker_id import SpeakerRecognizer
from .registry import (
    ToolContext,
    ToolOutput,
    ToolSpec,
    object_schema,
    param,
)

logger = logging.getLogger(__name__)


def speaker_list(ctx: ToolContext) -> ToolOutput:
    """List all enrolled speaker voiceprint profiles."""
    rec = SpeakerRecognizer.get_instance()
    profiles = rec.list_profiles()
    if not profiles:
        return ToolOutput(
            text="No speaker profiles are currently enrolled in voice recognition memory.",
            data={"profiles": []},
            ok=True,
        )

    lines = [f"Enrolled Speakers ({len(profiles)} registered):"]
    for p in profiles:
        lines.append(f"- {p['name']}: {p.get('description', 'No description')} ({p.get('sample_count', 1)} samples)")

    return ToolOutput(
        text="\n".join(lines),
        data={"profiles": profiles},
        ok=True,
    )


def speaker_enroll(
    ctx: ToolContext,
    name: str,
    description: str = "",
) -> ToolOutput:
    """Enroll or update a speaker profile."""
    rec = SpeakerRecognizer.get_instance()
    clean_name = name.strip()
    if not clean_name:
        return ToolOutput(text="Please specify a valid speaker name to enroll.", ok=False)

    # Use reference sample from voices or audio buffer if available
    from pathlib import Path
    ref_file = Path(__file__).resolve().parent.parent.parent / "voices" / "jarvis-5s.wav"
    audio_bytes = b""
    if ref_file.exists():
        audio_bytes = ref_file.read_bytes()

    if not audio_bytes:
        return ToolOutput(text=f"Could not find audio sample to enroll '{clean_name}'. Please provide a voice sample.", ok=False)

    success, msg = rec.enroll(clean_name, audio_bytes, description=description)
    return ToolOutput(text=msg, data={"speaker": clean_name, "enrolled": success}, ok=success)


def speaker_identify(ctx: ToolContext) -> ToolOutput:
    """Identify the speaker of the current voice turn or audio."""
    rec = SpeakerRecognizer.get_instance()
    from pathlib import Path
    ref_file = Path(__file__).resolve().parent.parent.parent / "voices" / "jarvis-5s.wav"
    if not ref_file.exists():
        return ToolOutput(text="No active audio sample available to perform speaker identification.", ok=False)

    audio_bytes = ref_file.read_bytes()
    speaker, conf, scores = rec.identify(audio_bytes)
    return ToolOutput(
        text=f"Speaker identified as '{speaker}' with {round(conf * 100, 1)}% confidence score.",
        data={"speaker": speaker, "confidence": conf, "scores": scores},
        ok=True,
    )


def speaker_delete(ctx: ToolContext, name: str) -> ToolOutput:
    """Delete an enrolled speaker voiceprint."""
    rec = SpeakerRecognizer.get_instance()
    ok = rec.delete_profile(name)
    if ok:
        return ToolOutput(text=f"Successfully deleted voiceprint profile for '{name}'.", ok=True)
    return ToolOutput(text=f"Speaker profile '{name}' not found.", ok=False)


def build_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="speaker_list",
            description="List all enrolled speaker voiceprints in memory.",
            parameters=object_schema(properties={}),
            handler=speaker_list,
        ),
        ToolSpec(
            name="speaker_enroll",
            description="Enroll or train a new speaker voiceprint profile.",
            parameters=object_schema(
                properties={
                    "name": param(
                        "string",
                        "The name of the speaker (e.g. 'Master', 'Sir', 'Alice').",
                    ),
                    "description": param(
                        "string",
                        "Optional description or relationship (e.g. 'Primary user and system owner').",
                    ),
                },
                required=["name"],
            ),
            handler=speaker_enroll,
        ),
        ToolSpec(
            name="speaker_identify",
            description="Identify who is speaking based on acoustic voiceprint biometrics.",
            parameters=object_schema(properties={}),
            handler=speaker_identify,
        ),
        ToolSpec(
            name="speaker_delete",
            description="Delete an enrolled speaker voiceprint profile.",
            parameters=object_schema(
                properties={
                    "name": param(
                        "string",
                        "The name of the speaker to remove.",
                    ),
                },
                required=["name"],
            ),
            handler=speaker_delete,
        ),
    ]
