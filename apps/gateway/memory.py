"""SQLite memory: durable notes, the conversation log, and a search index.

Two kinds of remembering, deliberately kept apart:

* **Notes** (``memories``) are things the master asked Jarvis to remember. They
  survive forever, they are searchable, and they are the reason "remember that my
  workshop is on Friday" means something a month later.
* **Turns** (``turns``) are raw conversation history for context continuity.

A whole tool loop lives inside one turn and is *not* persisted: cross-turn
context is the user/assistant text, within-turn context is the live message list.
That keeps the log legible and stops a stale tool result from being replayed into
a later decision.

FTS5 is used when the interpreter's SQLite has it (it normally does) with a LIKE
fallback, so search degrades in quality rather than failing.
"""

from __future__ import annotations

import contextlib
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    text       TEXT NOT NULL,
    tags       TEXT NOT NULL DEFAULT '',
    source     TEXT NOT NULL DEFAULT 'voice'
);
CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at DESC);

CREATE TABLE IF NOT EXISTS turns (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id, id DESC);
"""

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    text, tags, content='memories', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, text, tags) VALUES (new.id, new.text, new.tags);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, text, tags)
        VALUES ('delete', old.id, old.text, old.tags);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, text, tags)
        VALUES ('delete', old.id, old.text, old.tags);
    INSERT INTO memories_fts(rowid, text, tags) VALUES (new.id, new.text, new.tags);
END;
"""


class MemoryStore:
    """Thread-safe SQLite wrapper. One connection, guarded by a lock.

    Tools run in worker threads (filesystem, shell, whisper), so a single
    check_same_thread=False connection with an explicit lock is simpler and safer
    than a per-thread pool for this workload.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self.has_fts = False
        with self._lock:
            self._conn.executescript(SCHEMA)
            try:
                self._conn.executescript(FTS_SCHEMA)
                self.has_fts = True
            except sqlite3.OperationalError:
                # No FTS5 in this SQLite build: LIKE search still works.
                self.has_fts = False
            self._conn.commit()

    # -- notes ------------------------------------------------------------

    def add(self, text: str, tags: str = "", source: str = "voice") -> int:
        text = (text or "").strip()
        if not text:
            raise ValueError("cannot remember an empty note")
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO memories (created_at, text, tags, source) VALUES (?, ?, ?, ?)",
                (time.time(), text, tags.strip(), source),
            )
            self._conn.commit()
            return int(cursor.lastrowid or 0)

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        query = (query or "").strip()
        limit = max(1, min(int(limit), 25))
        with self._lock:
            if not query:
                return self.recent(limit)
            if self.has_fts:
                rows = self._search_fts(query, limit)
                if rows:
                    return rows
            return self._search_like(query, limit)

    def _search_fts(self, query: str, limit: int) -> list[dict[str, Any]]:
        # Quote each term so punctuation in a spoken sentence cannot break the
        # FTS query syntax ("what about the 2026 plan?" must not raise).
        terms = [f'"{t}"' for t in query.replace('"', " ").split() if t.strip()]
        if not terms:
            return []
        match = " OR ".join(terms)
        try:
            cursor = self._conn.execute(
                """
                SELECT m.id, m.created_at, m.text, m.tags,
                       bm25(memories_fts) AS rank
                FROM memories_fts
                JOIN memories m ON m.id = memories_fts.rowid
                WHERE memories_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (match, limit),
            )
            return [dict(row) for row in cursor.fetchall()]
        except sqlite3.OperationalError:
            return []

    def _search_like(self, query: str, limit: int) -> list[dict[str, Any]]:
        pattern = f"%{query}%"
        cursor = self._conn.execute(
            """
            SELECT id, created_at, text, tags, 0.0 AS rank
            FROM memories
            WHERE text LIKE ? OR tags LIKE ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (pattern, pattern, limit),
        )
        return [dict(row) for row in cursor.fetchall()]

    def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        """Most recent notes first.

        Ordered by id as well as created_at: Windows' time.time() has roughly
        15 ms granularity, so two notes saved in quick succession can carry the
        same timestamp and would otherwise come back in an arbitrary order.
        """
        limit = max(1, min(int(limit), 100))
        with self._lock:
            cursor = self._conn.execute(
                """
                SELECT id, created_at, text, tags, 0.0 AS rank
                FROM memories ORDER BY created_at DESC, id DESC LIMIT ?
                """,
                (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def forget(self, memory_id: int) -> bool:
        with self._lock:
            cursor = self._conn.execute("DELETE FROM memories WHERE id = ?", (int(memory_id),))
            self._conn.commit()
            return cursor.rowcount > 0

    def count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM memories").fetchone()
            return int(row["n"]) if row else 0

    # -- conversation -----------------------------------------------------

    def add_turn(self, session_id: str, role: str, content: str) -> None:
        content = (content or "").strip()
        if not content:
            return
        with self._lock:
            self._conn.execute(
                "INSERT INTO turns (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (session_id, role, content, time.time()),
            )
            self._conn.commit()

    def history(self, session_id: str, limit: int = 12) -> list[dict[str, str]]:
        """Recent turns as canonical chat messages, oldest first."""
        limit = max(0, min(int(limit), 100))
        if limit == 0:
            return []
        with self._lock:
            cursor = self._conn.execute(
                """
                SELECT role, content FROM turns
                WHERE session_id = ? ORDER BY id DESC LIMIT ?
                """,
                (session_id, limit),
            )
            rows = [dict(row) for row in cursor.fetchall()]
        rows.reverse()
        return [{"role": row["role"], "content": row["content"]} for row in rows]

    def clear_session(self, session_id: str) -> int:
        with self._lock:
            cursor = self._conn.execute("DELETE FROM turns WHERE session_id = ?", (session_id,))
            self._conn.commit()
            return cursor.rowcount

    def sessions(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock:
            cursor = self._conn.execute(
                """
                SELECT session_id, COUNT(*) AS turns, MAX(created_at) AS last_used
                FROM turns GROUP BY session_id ORDER BY last_used DESC LIMIT ?
                """,
                (max(1, min(int(limit), 50)),),
            )
            return [dict(row) for row in cursor.fetchall()]

    # -- lifecycle --------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        with self._lock:
            memories = self.count()
            turns = self._conn.execute("SELECT COUNT(*) AS n FROM turns").fetchone()
        return {
            "path": str(self.path),
            "memories": memories,
            "turns": int(turns["n"]) if turns else 0,
            "fts": self.has_fts,
        }

    def close(self) -> None:
        with self._lock, contextlib.suppress(sqlite3.Error):
            self._conn.close()
