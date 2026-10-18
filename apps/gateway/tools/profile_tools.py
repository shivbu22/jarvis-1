"""User profile tool handlers for J.A.R.V.I.S.

Exposes the UserProfile system as callable tools so the LLM can read and
update preferences on behalf of the user.
"""

from __future__ import annotations

import logging
from typing import Any

from .registry import ToolContext, ToolOutput, ToolSpec, object_schema, param

logger = logging.getLogger("jarvis.tools.profile")


def _get_profile(ctx: ToolContext) -> Any:
    """Retrieve the user profile from the service context."""
    service = getattr(ctx, "service", None) if ctx else None
    if service and hasattr(service, "profile"):
        return service.profile
    return None


def profile_get(ctx: ToolContext, key: str = "") -> ToolOutput:
    """Get the user's profile or a specific preference.

    Args:
        key: Specific preference key to retrieve (empty = full profile).
    """
    profile = _get_profile(ctx)
    if not profile:
        return ToolOutput(text="User profile not available.", ok=False)

    if key:
        value = profile.get(key)
        if value is None:
            value = profile.get_preference(key)
        return ToolOutput.from_ok({"key": key, "value": value})

    return ToolOutput.from_ok(profile.to_dict())


def profile_set(ctx: ToolContext, key: str, value: str) -> ToolOutput:
    """Set a user preference or profile field.

    Args:
        key: The preference key to set (e.g., 'timezone', 'preferred_name', 'temperature_unit').
        value: The value to set.
    """
    profile = _get_profile(ctx)
    if not profile:
        return ToolOutput(text="User profile not available.", ok=False)

    # Known top-level fields
    top_level_keys = {
        "name", "preferred_name", "timezone", "language", "temperature_unit",
        "date_format", "currency", "communication_style", "verbosity",
        "wake_word", "favorite_apps", "interests",
    }

    if key in top_level_keys:
        # Parse lists from comma-separated strings
        if key in ("favorite_apps", "interests"):
            import json
            try:
                parsed = json.loads(value)
            except Exception:
                parsed = [v.strip() for v in value.split(",")]
            profile.set(key, parsed)
        else:
            profile.set(key, value)
    else:
        profile.set_preference(key, value)

    return ToolOutput.from_ok({
        "updated": key,
        "value": value,
        "status": "saved",
    })


def build_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="profile_get",
            description="Get the user's profile information or a specific preference value.",
            parameters=object_schema(
                properties={
                    "key": param("string", "Specific preference key to retrieve. Empty for full profile.", default=""),
                },
            ),
            handler=profile_get,
            category="profile",
        ),
        ToolSpec(
            name="profile_set",
            description="Set a user preference: timezone, name, temperature_unit, currency, communication_style, verbosity, or any custom preference.",
            parameters=object_schema(
                properties={
                    "key": param("string", "The preference key to set."),
                    "value": param("string", "The value to set."),
                },
                required=["key", "value"],
            ),
            handler=profile_set,
            category="profile",
        ),
    ]
