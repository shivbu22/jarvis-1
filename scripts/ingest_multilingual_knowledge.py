"""Ingest personalized master preferences and multilingual command mappings into Jarvis Memory.

Ensures that J.A.R.V.I.S. recognizes multilingual directives in English, Hindi, and Hinglish,
understands the Master's persona and working style, and recalls them dynamically on every turn.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add project root to sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from apps.gateway.memory import MemoryStore

CORE_PERSONALIZATIONS = [
    (
        "Master Profile & Directives: The master is a senior Technical Founder and Quant Architect. "
        "Address him as 'sir' with dignified British wit, intellectual partnership, and extreme decisiveness. "
        "Deliver first-principles technical depth without fluff.",
        "master,persona,founder",
    ),
    (
        "Multilingual Command Comprehension: The master speaks fluent English, colloquial Hinglish, and Hindi. "
        "Understand phrases like 'desktop dikhao' (show desktop), 'gaana bajao' (play music), 'notepad kholo' (open notepad), "
        "'awaz badhao' (increase volume), 'mausam kaisa hai' (check weather), and 'taaza khabar' (read news) as direct tool directives.",
        "language,hinglish,commands,multilingual",
    ),
    (
        "Audio & Media Preferences: The master frequently listens to Lofi hip hop, chill beats, and acoustic Indian music on YouTube and YouTube Music. "
        "When asked to play music or 'gaana bajao', default to lofi beats or YouTube playback immediately without hesitation.",
        "preferences,music,media,youtube",
    ),
    (
        "Workspace & OS Preferences: Default browser is Google Chrome. Primary code editor is VS Code. "
        "When the master says 'desktop dikhao' or 'parde gira do', minimize all open windows and show the clean desktop immediately.",
        "preferences,desktop,apps,chrome",
    ),
    (
        "Obsidian Brain Vault: The master maintains an Obsidian knowledge vault at 'jarvis brain/'. "
        "Store notes, venture roadmaps, and cognitive schemas inside the vault using brain_vault_note_write and brain_vault_search.",
        "preferences,obsidian,brain,notes",
    ),
    (
        "Quant & Ventures: The master actively oversees Swachta AI, Medimin, Quick Queue, and monitors Indian financial indices (Nifty 50, Sensex). "
        "Provide real-time market data, technical indicator analysis, and automated execution when requested.",
        "master,ventures,quant,markets,nifty",
    ),
]


def ingest() -> None:
    db_path = ROOT / "data" / "jarvis.db"
    store = MemoryStore(db_path)
    print(f"Connecting to MemoryStore at {db_path}...")

    added = 0
    updated = 0
    for text, tags in CORE_PERSONALIZATIONS:
        res = store.add(text, tags=tags, source="knowledge_ingest")
        if res > 0:
            added += 1
            print(f"  [+] Ingested new memory: {text[:60]}...")
        else:
            updated += 1
            print(f"  [*] Refreshed existing memory: {text[:60]}...")

    print(f"\nIngestion complete: {added} new memories added, {updated} refreshed.")
    recent = store.recent(limit=10)
    print(f"Total active memories in database: {len(recent)}")


if __name__ == "__main__":
    ingest()
