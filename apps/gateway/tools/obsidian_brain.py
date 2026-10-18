"""Obsidian Brain Vault tools for J.A.R.V.I.S.

Provides native capabilities to query, search, read, write, and lint the
Obsidian-compatible 4-Layer Cognitive Brain Vault located at
`C:\\Users\\ahiva\\OneDrive\\Desktop\\jarvis\\jarvis brain`.
"""

from __future__ import annotations

import datetime
import os
import re
from pathlib import Path
from typing import Any

from .registry import ToolContext, ToolOutput, ToolSpec, object_schema, param

VAULT_PATH = Path(r"C:\Users\ahiva\OneDrive\Desktop\jarvis\jarvis brain")


def _resolve_vault_path(rel_path: str) -> Path:
    p = rel_path.strip().replace("\\", "/").lstrip("/")
    if not p.endswith(".md") and "." not in os.path.basename(p):
        p += ".md"
    target = (VAULT_PATH / p).resolve()
    try:
        target.relative_to(VAULT_PATH.resolve())
    except ValueError:
        raise ValueError(f"Target path {rel_path} escapes vault root") from None
    return target


def brain_vault_search(ctx: ToolContext, query: str, limit: int = 5) -> ToolOutput:
    """Search for notes in the Obsidian Brain vault by title, tags, or content."""
    if not VAULT_PATH.exists():
        return ToolOutput(text=f"Brain vault directory not found at {VAULT_PATH}", data={"results": []})

    limit = max(1, min(int(limit or 5), 20))
    query_lower = query.lower()
    matches: list[dict[str, Any]] = []

    for file_path in VAULT_PATH.rglob("*.md"):
        rel = file_path.relative_to(VAULT_PATH).as_posix()
        if rel in ("SCHEMA.md", "index.md", "log.md") and query_lower not in rel.lower():
            continue
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        score = 0
        snippet = ""
        # Match title / filename
        if query_lower in rel.lower():
            score += 10
        # Match content
        if query_lower in content.lower():
            score += 5
            # Find snippet
            idx = content.lower().find(query_lower)
            start = max(0, idx - 60)
            end = min(len(content), idx + 100)
            snippet = content[start:end].replace("\n", " ").strip()

        if score > 0:
            # Extract frontmatter title
            title_m = re.search(r"^title:\s*(.+)$", content, re.MULTILINE)
            title = title_m.group(1).strip() if title_m else rel
            matches.append({
                "path": rel,
                "title": title,
                "score": score,
                "snippet": snippet or content[:120].replace("\n", " "),
            })

    matches.sort(key=lambda x: x["score"], reverse=True)
    results = matches[:limit]

    if not results:
        return ToolOutput(
            text=f"No brain notes found matching '{query}' in {VAULT_PATH}.",
            data={"query": query, "results": []},
        )

    lines = [f"Found {len(results)} brain note(s) matching '{query}':"]
    for r in results:
        lines.append(f"- [[{r['path']}]] ({r['title']}): \"{r['snippet']}...\"")

    return ToolOutput(text="\n".join(lines), data={"query": query, "results": results})


def brain_vault_read(ctx: ToolContext, note_path: str) -> ToolOutput:
    """Read the full content of a note from the Obsidian Brain vault."""
    try:
        target = _resolve_vault_path(note_path)
    except Exception as exc:
        return ToolOutput(text=f"Invalid path: {exc}", data={"error": str(exc)})

    if not target.exists():
        # Try searching by basename
        stem = Path(note_path).stem.lower()
        found = list(VAULT_PATH.rglob(f"*{stem}*.md"))
        if found:
            target = found[0]
        else:
            return ToolOutput(text=f"Note '{note_path}' does not exist in the brain vault.", data={"found": False})

    content = target.read_text(encoding="utf-8", errors="replace")
    rel_path = target.relative_to(VAULT_PATH).as_posix()
    return ToolOutput(
        text=f"Content of [[{rel_path}]]:\n\n{content}",
        data={"path": rel_path, "content": content},
    )


def brain_vault_note_write(
    ctx: ToolContext,
    note_path: str,
    title: str,
    content: str,
    note_type: str = "concept",
    tags: str = "",
) -> ToolOutput:
    """Create or update a knowledge note in the Obsidian Brain vault."""
    try:
        target = _resolve_vault_path(note_path)
    except Exception as exc:
        return ToolOutput(text=f"Invalid path: {exc}", data={"error": str(exc)})

    target.parent.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()
    tag_list = [t.strip() for t in re.split(r"[,\s]+", tags) if t.strip()]

    # Format frontmatter
    fm = [
        "---",
        f"title: {title}",
        f"created: {today}",
        f"updated: {today}",
        f"type: {note_type}",
        f"tags: [{', '.join(tag_list)}]",
        "confidence: high",
        "---",
        "",
    ]
    full_text = "\n".join(fm) + content.strip() + "\n"
    target.write_text(full_text, encoding="utf-8")

    rel_path = target.relative_to(VAULT_PATH).as_posix()

    # Append to log.md
    log_file = VAULT_PATH / "log.md"
    if log_file.exists():
        log_entry = f"\n## [{today}] note_write | [[{rel_path}]] ({title})\n- Updated note with type '{note_type}' and tags {tag_list}\n"
        with log_file.open("a", encoding="utf-8") as f:
            f.write(log_entry)

    return ToolOutput(
        text=f"Successfully written brain note [[{rel_path}]] with title '{title}'.",
        data={"path": rel_path, "title": title, "type": note_type, "tags": tag_list},
    )


def brain_vault_lint(ctx: ToolContext) -> ToolOutput:
    """Audit the Obsidian Brain vault for broken links, orphan notes, and schema integrity."""
    if not VAULT_PATH.exists():
        return ToolOutput(text="Brain vault path does not exist.", data={"status": "missing"})

    all_notes = {p.stem.lower(): p for p in VAULT_PATH.rglob("*.md")}
    inbound_links: dict[str, int] = {k: 0 for k in all_notes}
    broken_links: list[dict[str, str]] = []

    for file_path in VAULT_PATH.rglob("*.md"):
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        links = re.findall(r"\[\[([^\|\]]+)(?:\|[^\]]+)?\]\]", text)
        for link in links:
            target_stem = Path(link.strip()).stem.lower()
            if target_stem in inbound_links:
                inbound_links[target_stem] += 1
            else:
                broken_links.append({
                    "source": file_path.relative_to(VAULT_PATH).as_posix(),
                    "target": link,
                })

    orphans = [
        path.relative_to(VAULT_PATH).as_posix()
        for stem, path in all_notes.items()
        if inbound_links.get(stem, 0) == 0 and stem not in ("schema", "index", "log", "welcome")
    ]

    report = [
        f"Brain Vault Audit ({len(all_notes)} total notes):",
        f"- Broken Links: {len(broken_links)}",
        f"- Orphan Notes: {len(orphans)}",
    ]
    if broken_links:
        report.append("\nBroken Links:")
        for bl in broken_links[:5]:
            report.append(f"  * From [[{bl['source']}]] to missing [[{bl['target']}]]")
    if orphans:
        report.append("\nOrphan Notes (no inbound links):")
        for op in orphans[:5]:
            report.append(f"  * [[{op}]]")

    if not broken_links and not orphans:
        report.append("\nVault is 100% healthy with zero broken links or orphan notes.")

    return ToolOutput(
        text="\n".join(report),
        data={"total_notes": len(all_notes), "broken_links": broken_links, "orphans": orphans},
    )


def build_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="brain_vault_search",
            description="Search the Obsidian Brain vault for concepts, entities, syntheses, or trading notes.",
            parameters=object_schema(
                {
                    "query": param("query", "string", "Keywords, concept, entity, or topic to search for in the brain."),
                    "limit": param("limit", "integer", "Max results to return (default 5).", default=5, minimum=1, maximum=20),
                },
                ["query"],
            ),
            handler=brain_vault_search,
            executing=False,
            category="brain",
        ),
        ToolSpec(
            name="brain_vault_read",
            description="Read the complete markdown content of a note from the Obsidian Brain vault.",
            parameters=object_schema(
                {
                    "note_path": param("note_path", "string", "Path or filename of the note (e.g. 'entities/jarvis-core' or 'concepts/autonomous-agentic-loop')."),
                },
                ["note_path"],
            ),
            handler=brain_vault_read,
            executing=False,
            category="brain",
        ),
        ToolSpec(
            name="brain_vault_note_write",
            description="Create or update a structured markdown note in the Obsidian Brain vault with frontmatter.",
            parameters=object_schema(
                {
                    "note_path": param("note_path", "string", "Relative path (e.g. 'concepts/my-new-concept.md' or 'entities/my-entity')."),
                    "title": param("title", "string", "Readable note title."),
                    "content": param("content", "string", "Markdown body of the note."),
                    "note_type": param("note_type", "string", "Note type: entity | concept | comparison | query | memory | procedure.", default="concept"),
                    "tags": param("tags", "string", "Space or comma-separated taxonomy tags.", default=""),
                },
                ["note_path", "title", "content"],
            ),
            handler=brain_vault_note_write,
            executing=False,
            category="brain",
        ),
        ToolSpec(
            name="brain_vault_lint",
            description="Audit the Obsidian Brain vault for broken wikilinks, orphan notes, and knowledge graph health.",
            parameters=object_schema({}),
            handler=brain_vault_lint,
            executing=False,
            category="brain",
        ),
    ]
