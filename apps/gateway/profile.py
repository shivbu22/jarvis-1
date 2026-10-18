"""Persistent user profile and preferences for J.A.R.V.I.S.

Stores structured preferences — timezone, units, communication style,
frequently used apps, etc. — so the system prompt can be tailored and
tools can make smarter defaults without asking every time.

Profile is stored as a JSON file alongside the memory database, and is
exposed to the LLM as part of the system prompt context.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("jarvis.profile")

DEFAULT_PROFILE: dict[str, Any] = {
    "name": "",
    "preferred_name": "",
    "timezone": "Asia/Kolkata",
    "language": "en",
    "temperature_unit": "celsius",
    "date_format": "DD/MM/YYYY",
    "currency": "INR",
    "communication_style": "professional",  # "casual" | "professional" | "formal"
    "verbosity": "balanced",  # "brief" | "balanced" | "detailed"
    "wake_word": "Hey Jarvis",
    "favorite_apps": [],
    "work_hours": {"start": "09:00", "end": "18:00"},
    "interests": [],
    "custom_preferences": {},
    "created_at": 0.0,
    "updated_at": 0.0,
}


class UserProfile:
    """JSON-backed user profile store."""

    def __init__(self, profile_path: Path | str | None = None) -> None:
        if profile_path is None:
            profile_path = Path(__file__).resolve().parents[2] / "data" / "user_profile.json"
        self._path = Path(profile_path)
        self._profile: dict[str, Any] = dict(DEFAULT_PROFILE)
        self._load()

    def _load(self) -> None:
        """Load profile from disk, merging with defaults."""
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                for key, default_val in DEFAULT_PROFILE.items():
                    if key in data:
                        self._profile[key] = data[key]
                logger.info("User profile loaded from %s", self._path)
            except Exception as exc:
                logger.warning("Could not load profile: %s", exc)
        else:
            self._profile["created_at"] = time.time()
            self._save()
            logger.info("Default user profile created at %s", self._path)

    def _save(self) -> None:
        """Persist profile to disk."""
        self._profile["updated_at"] = time.time()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._profile, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def get(self, key: str, default: Any = None) -> Any:
        """Get a profile value."""
        return self._profile.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a profile value and persist."""
        self._profile[key] = value
        self._save()
        logger.info("Profile updated: %s = %s", key, value)

    def update(self, updates: dict[str, Any]) -> None:
        """Batch update multiple profile values."""
        for key, value in updates.items():
            self._profile[key] = value
        self._save()
        logger.info("Profile batch updated: %s keys", len(updates))

    def set_preference(self, key: str, value: Any) -> None:
        """Set a custom preference (stored under custom_preferences)."""
        prefs = self._profile.get("custom_preferences", {})
        prefs[key] = value
        self._profile["custom_preferences"] = prefs
        self._save()

    def get_preference(self, key: str, default: Any = None) -> Any:
        """Get a custom preference."""
        return self._profile.get("custom_preferences", {}).get(key, default)

    def to_dict(self) -> dict[str, Any]:
        """Return the full profile as a dict."""
        return dict(self._profile)

    def all(self) -> dict[str, Any]:
        """Alias for to_dict()."""
        return self.to_dict()

    def build_prompt_section(self) -> str:
        """Alias for to_context_string()."""
        return self.to_context_string()

    def to_context_string(self) -> str:
        """Format profile as a compact string for the LLM system prompt."""
        p = self._profile
        parts = []
        if p.get("preferred_name"):
            parts.append(f"User's name: {p['preferred_name']}")
        if p.get("timezone"):
            parts.append(f"Timezone: {p['timezone']}")
        if p.get("language") and p["language"] != "en":
            parts.append(f"Preferred language: {p['language']}")
        if p.get("temperature_unit"):
            parts.append(f"Temperature: {p['temperature_unit']}")
        if p.get("currency") and p["currency"] != "USD":
            parts.append(f"Currency: {p['currency']}")
        if p.get("communication_style"):
            parts.append(f"Communication style: {p['communication_style']}")
        if p.get("verbosity"):
            parts.append(f"Verbosity: {p['verbosity']}")
        if p.get("interests"):
            parts.append(f"Interests: {', '.join(p['interests'][:5])}")
        if p.get("favorite_apps"):
            parts.append(f"Frequently used apps: {', '.join(p['favorite_apps'][:5])}")
        if p.get("custom_preferences"):
            for k, v in list(p["custom_preferences"].items())[:10]:
                parts.append(f"{k}: {v}")
        return "; ".join(parts) if parts else ""

    def infer_from_interaction(self, user_text: str, tool_used: str = "") -> None:
        """Learn preferences from user interactions (passive inference).

        Called after each turn to update the profile based on usage patterns.
        This is intentionally conservative — only updates on strong signals.
        """
        text_lower = user_text.lower()

        # Track app usage
        if tool_used == "app_launch":
            # Extract app name from the interaction
            import re
            app_match = re.search(r'(?:open|launch|start)\s+(\w+)', text_lower)
            if app_match:
                app = app_match.group(1).title()
                favorites = self._profile.get("favorite_apps", [])
                if app not in favorites:
                    favorites.append(app)
                    self._profile["favorite_apps"] = favorites[-10:]  # keep last 10
                    self._save()

        # Detect temperature preference
        if "fahrenheit" in text_lower:
            if self._profile.get("temperature_unit") != "fahrenheit":
                self.set("temperature_unit", "fahrenheit")
        elif "celsius" in text_lower:
            if self._profile.get("temperature_unit") != "celsius":
                self.set("temperature_unit", "celsius")

        # Detect name preference
        if "call me " in text_lower:
            import re
            name_match = re.search(r'call me (\w+)', text_lower)
            if name_match:
                self.set("preferred_name", name_match.group(1).title())
