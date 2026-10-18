"""J.A.R.V.I.S. Knowledge & Memory Ingestion Engine.

Ingests files, documents, codebases, and notes into:
1. Obsidian Brain Vault (jarvis brain/concepts/ or jarvis brain/entities/)
2. Persistent SQLite Memory (data/jarvis.db) for semantic full-text retrieval
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VAULT_DIR = ROOT / "jarvis brain"
DB_FILE = ROOT / "data" / "jarvis.db"

SUPPORTED_EXTENSIONS = {".md", ".txt", ".py", ".ts", ".tsx", ".js", ".json", ".yaml", ".yml", ".html", ".csv"}


def sanitize_filename(name: str) -> str:
    """Make filename safe for filesystem and Obsidian wikilinks."""
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    return name.strip().replace(" ", "-").lower()


sys.path.insert(0, str(ROOT))
from apps.gateway.memory import MemoryStore


def ingest_to_sqlite(text: str, category: str = "general", source: str = "") -> bool:
    """Insert text chunk into SQLite memories table with FTS5 indexing using MemoryStore."""
    if not DB_FILE.exists():
        return False

    try:
        store = MemoryStore(DB_FILE)
        store.add(text.strip(), tags=f"knowledge,{category}", source=source or "ingest")
        return True
    except Exception as exc:
        print(f"[Knowledge] SQLite insert warning: {exc}", file=sys.stderr)
        return False


def ingest_file(file_path: Path, category: str = "concept") -> dict[str, str] | None:
    """Ingest a single file into both the Obsidian Vault and SQLite Memory."""
    if not file_path.exists() or file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        return None

    try:
        content = file_path.read_text(encoding="utf-8", errors="replace").strip()
    except Exception as exc:
        print(f"[Knowledge] Read error for {file_path.name}: {exc}", file=sys.stderr)
        return None

    if not content:
        return None

    title = file_path.stem.replace("-", " ").replace("_", " ").title()
    slug = sanitize_filename(file_path.stem)

    # 1. Write to Obsidian Vault
    target_folder = VAULT_DIR / ("concepts" if category == "concept" else "entities")
    target_folder.mkdir(parents=True, exist_ok=True)
    target_file = target_folder / f"{slug}.md"

    vault_doc = (
        f"---\n"
        f"title: \"{title}\"\n"
        f"category: \"{category}\"\n"
        f"source: \"{file_path.name}\"\n"
        f"date: \"{time.strftime('%Y-%m-%d %H:%M:%S')}\"\n"
        f"tags: [jarvis, knowledge, {category}]\n"
        f"---\n\n"
        f"# {title}\n\n"
        f"> Ingested from `{file_path.name}` on {time.strftime('%Y-%m-%d')}.\n\n"
        f"{content}\n"
    )
    target_file.write_text(vault_doc, encoding="utf-8")

    # 2. Index in SQLite memory (chunking content + extracting table rows if any)
    indexed_chunks = 0
    table_rows = re.findall(r"^\|(.+?)\|$", content, re.MULTILINE)
    for row in table_rows:
        cols = [c.strip() for c in row.split("|")]
        if any(c.startswith("---") or c.startswith(":---") for c in cols):
            continue
        if len(cols) >= 4 and not cols[0].lower().startswith("action"):
            chunk_text = f"[{title} Command] Intent: {cols[0]} | English: {cols[1]} | Hinglish: {cols[2]}"
            if len(cols) >= 5:
                chunk_text += f" | Hindi: {cols[3]} | Target: {cols[4]}"
            elif len(cols) == 4:
                chunk_text += f" | Target: {cols[3]}"
            if ingest_to_sqlite(chunk_text, category="command", source=file_path.name):
                indexed_chunks += 1

    paragraphs = [p.strip() for p in content.split("\n\n") if len(p.strip()) > 30 and not p.strip().startswith("|")]
    for p in paragraphs[:12]:
        if ingest_to_sqlite(f"[{title}] {p}", category=category, source=file_path.name):
            indexed_chunks += 1

    return {
        "file": file_path.name,
        "vault_path": str(target_file),
        "chunks_indexed": str(indexed_chunks),
    }


def ingest_directory(dir_path: Path, category: str = "concept") -> list[dict[str, str]]:
    """Recursively ingest supported files from a directory."""
    results = []
    for root, _, files in os.walk(dir_path):
        for f in files:
            p = Path(root) / f
            res = ingest_file(p, category=category)
            if res:
                results.append(res)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest documents/code into JARVIS Knowledge Vault and SQLite")
    parser.add_argument("--path", type=Path, help="File or directory path to ingest")
    parser.add_argument("--text", type=str, help="Direct text note to remember")
    parser.add_argument("--category", type=str, default="concept", choices=["concept", "entity", "code", "guide"])
    args = parser.parse_args()

    print(f"\n========================================================")
    print(f"  J.A.R.V.I.S. KNOWLEDGE & MEMORY INGESTION ENGINE")
    print(f"========================================================")

    if args.text:
        title = args.text[:30] + "..."
        slug = sanitize_filename(args.text[:20])
        target_file = VAULT_DIR / "concepts" / f"{slug}.md"
        vault_doc = (
            f"---\n"
            f"title: \"Memory Note\"\n"
            f"date: \"{time.strftime('%Y-%m-%d %H:%M:%S')}\"\n"
            f"---\n\n"
            f"{args.text}\n"
        )
        target_file.write_text(vault_doc, encoding="utf-8")
        ingest_to_sqlite(args.text, category=args.category, source="cli_input")
        print(f"[Knowledge] Saved direct memory and wrote vault note -> {target_file}")
        return

    if not args.path:
        # Default: verify existing vault notes
        notes = list(VAULT_DIR.rglob("*.md"))
        print(f"[Knowledge] Vault Status: {len(notes)} knowledge notes currently active in '{VAULT_DIR}'.")
        print("[Tip] To ingest files: python scripts/ingest_knowledge.py --path C:/path/to/docs")
        return

    target = args.path
    if not target.exists():
        print(f"[Error] Path '{target}' does not exist!", file=sys.stderr)
        sys.exit(1)

    if target.is_file():
        res = ingest_file(target, category=args.category)
        if res:
            print(f"[Knowledge] Successfully ingested {res['file']} -> {res['vault_path']}")
        else:
            print(f"[Knowledge] Could not ingest {target.name} (unsupported format or empty).")
    else:
        results = ingest_directory(target, category=args.category)
        print(f"[Knowledge] Ingested {len(results)} files from '{target}' into Vault and SQLite.")


if __name__ == "__main__":
    main()
