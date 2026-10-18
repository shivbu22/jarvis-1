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
import json
import logging
import re
import sqlite3
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger("jarvis.memory")


def get_embedding(text: str) -> list[float] | None:
    """Fetch dense 768-d vector embedding from Ollama nomic-embed-text."""
    clean = (text or "").strip()
    if not clean:
        return None
    try:
        data = json.dumps({"model": "nomic-embed-text", "prompt": clean}).encode("utf-8")
        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/embeddings",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            emb = body.get("embedding")
            if isinstance(emb, list) and emb:
                return emb
    except Exception:
        return None
    return None


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Calculate cosine similarity between two float vectors."""
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for a, b in zip(vec_a, vec_b):
        dot += a * b
        norm_a += a * a
        norm_b += b * b
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return dot / ((norm_a ** 0.5) * (norm_b ** 0.5))


#: Words too common to identify anything. Searching for spoken English without
#: dropping these retrieves every note ever saved, because the index ORs its
#: terms: "what's the weather like" would match a note about a birthday party on
#: the strength of "the" alone.
STOPWORDS = frozenset(
    ["a", "about", "after", "all", "also", "am", "an", "and", "any", "are", "as", "at", "be", "because", "been", "before", "being", "but", "by", "can", "could", "did", "do", "does", "doing", "done", "for", "from", "further", "get", "got", "had", "has", "have", "having", "he", "her", "here", "hers", "him", "his", "how", "i", "if", "in", "into", "is", "it", "its", "just", "me", "more", "most", "my", "no", "nor", "not", "now", "of", "off", "on", "once", "only", "or", "other", "our", "out", "over", "own", "same", "she", "should", "so", "some", "such", "tell", "than", "that", "the", "their", "them", "then", "there", "these", "they", "this", "those", "through", "to", "too", "under", "until", "up", "us", "very", "was", "we", "were", "what", "when", "where", "which", "while", "who", "whom", "why", "will", "with", "would", "you", "your", "yours"]
)


def content_terms(text: str) -> list[str]:
    """The words in ``text`` worth matching on, longest first.

    Longest first so that a specific term ("workshop") is tried before a generic
    one ("plan") when a limit truncates the results.
    """
    words = re.findall(r"[a-z0-9']+", (text or "").lower())
    keep = [word for word in words if (len(word) > 1 and word not in STOPWORDS) or word.isdigit()]
    return sorted(dict.fromkeys(keep), key=len, reverse=True)

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

CREATE TABLE IF NOT EXISTS execution_logs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp      TEXT NOT NULL,
    goal           TEXT NOT NULL,
    tools_used     TEXT NOT NULL,
    outcome        TEXT NOT NULL,
    result_quality INTEGER NOT NULL,
    lessons        TEXT NOT NULL,
    adjustments    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_execution_logs_time ON execution_logs(id DESC);

CREATE TABLE IF NOT EXISTS portfolio_positions (
    symbol         TEXT PRIMARY KEY,
    quantity       REAL NOT NULL,
    avg_price      REAL NOT NULL,
    updated_at     REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS portfolio_trades (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol         TEXT NOT NULL,
    side           TEXT NOT NULL,
    quantity       REAL NOT NULL,
    price          REAL NOT NULL,
    trade_type     TEXT NOT NULL,
    timestamp      REAL NOT NULL,
    pnl            REAL DEFAULT 0.0,
    advisor_notes  TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS portfolio_meta (
    key            TEXT PRIMARY KEY,
    value          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lead_pipeline (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    company_name   TEXT NOT NULL,
    website        TEXT NOT NULL DEFAULT '',
    contact_name   TEXT NOT NULL DEFAULT '',
    contact_email  TEXT NOT NULL DEFAULT '',
    vertical       TEXT NOT NULL DEFAULT '',
    fit_score      INTEGER NOT NULL DEFAULT 0,
    status         TEXT NOT NULL DEFAULT 'discovered',
    outreach_copy  TEXT NOT NULL DEFAULT '',
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_leads_status ON lead_pipeline(status, id DESC);

CREATE TABLE IF NOT EXISTS expenses (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp      TEXT NOT NULL,
    category       TEXT NOT NULL,
    amount         REAL NOT NULL,
    currency       TEXT NOT NULL DEFAULT 'USD',
    vendor         TEXT NOT NULL DEFAULT '',
    description    TEXT NOT NULL DEFAULT '',
    receipt_ref    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_expenses_category ON expenses(category, id DESC);

CREATE TABLE IF NOT EXISTS invoices (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_num    TEXT UNIQUE NOT NULL,
    client_name    TEXT NOT NULL,
    client_email   TEXT NOT NULL DEFAULT '',
    total_amount   REAL NOT NULL,
    currency       TEXT NOT NULL DEFAULT 'USD',
    status         TEXT NOT NULL DEFAULT 'draft',
    items_json     TEXT NOT NULL DEFAULT '[]',
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scheduled_crons (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT UNIQUE NOT NULL,
    interval_m     INTEGER NOT NULL,
    tool_name      TEXT NOT NULL,
    args_json      TEXT NOT NULL DEFAULT '{}',
    status         TEXT NOT NULL DEFAULT 'active',
    last_run       REAL DEFAULT 0.0,
    created_at     REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS custom_commands (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger_phrase TEXT UNIQUE NOT NULL,
    action_payload TEXT NOT NULL,
    action_type    TEXT NOT NULL DEFAULT 'directive',
    language       TEXT NOT NULL DEFAULT 'any',
    description    TEXT NOT NULL DEFAULT '',
    created_at     REAL NOT NULL,
    use_count      INTEGER NOT NULL DEFAULT 0,
    last_used_at   REAL DEFAULT NULL
);
CREATE INDEX IF NOT EXISTS idx_custom_commands_trigger ON custom_commands(trigger_phrase);
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

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self.has_fts = False
        with self._lock:
            self._conn.executescript(SCHEMA)
            # Migration, not schema: applying it inside SCHEMA would raise
            # "duplicate column" on every database created before this change.
            columns = {row[1] for row in self._conn.execute("PRAGMA table_info(memories)")}
            if "seen_count" not in columns:
                self._conn.execute(
                    "ALTER TABLE memories ADD COLUMN seen_count INTEGER NOT NULL DEFAULT 0"
                )
            if "embedding" not in columns:
                self._conn.execute(
                    "ALTER TABLE memories ADD COLUMN embedding TEXT DEFAULT ''"
                )
            try:
                self._conn.executescript(FTS_SCHEMA)
                self.has_fts = True
            except sqlite3.OperationalError:
                # No FTS5 in this SQLite build: LIKE search still works.
                self.has_fts = False
            self._conn.commit()

    # -- notes ------------------------------------------------------------

    # Two notes sharing this proportion of content words are the same fact in
    # different words. High enough that a birthday and a deadline never merge;
    # low enough that "workshop is Friday at 3" and "my workshop is on Friday
    # at 3pm" do. Word-overlap rather than embeddings: exact, fast, and honest
    # about what it can and cannot catch.
    DUPLICATE_OVERLAP = 0.6

    def add(self, text: str, tags: str = "", source: str = "voice") -> int:
        text = (text or "").strip()
        if not text:
            raise ValueError("cannot remember an empty note")
        with self._lock:
            duplicate = self._find_duplicate(text)
            if duplicate is not None:
                self._conn.execute(
                    "UPDATE memories SET seen_count = seen_count + 1 WHERE id = ?",
                    (duplicate["id"],),
                )
                self._conn.commit()
                return -duplicate["id"]
            emb = get_embedding(text)
            emb_json = json.dumps(emb) if emb else ""
            cursor = self._conn.execute(
                "INSERT INTO memories (created_at, text, tags, source, embedding) VALUES (?, ?, ?, ?, ?)",
                (time.time(), text, tags.strip(), source, emb_json),
            )
            self._conn.commit()
            return int(cursor.lastrowid or 0)

    def _find_duplicate(self, text: str) -> dict[str, Any] | None:
        """The stored note this duplicates, if any.

        Overlap of the *content* words both ways: a paraphrase shares nearly all
        of the original's terms, while a genuinely new fact about the same topic
        ("workshop is Friday" vs "the workshop budget was approved") does not.
        """
        incoming = set(content_terms(text))
        if not incoming:
            return None
        for row in self._conn.execute(
            "SELECT id, text FROM memories ORDER BY created_at DESC, id DESC LIMIT 200"
        ):
            existing = set(content_terms(row["text"]))
            if not existing:
                continue
            # A differing number means a different fact: "2 litres" and
            # "3 litres", "at 5" and "at 6" must both survive. Differing filler
            # words are what paraphrasing looks like, so they are allowed.
            difference = incoming ^ existing
            if any(term.isdigit() for term in difference):
                continue
            overlap = len(incoming & existing) / min(len(incoming), len(existing))
            if overlap >= self.DUPLICATE_OVERLAP:
                return dict(row)
        return None

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
                SELECT m.id, m.created_at, m.text, m.tags, m.seen_count,
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
            SELECT id, created_at, text, tags, seen_count, 0.0 AS rank
            FROM memories
            WHERE text LIKE ? OR tags LIKE ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (pattern, pattern, limit),
        )
        return [dict(row) for row in cursor.fetchall()]

    def recall(self, query: str, limit: int = 4) -> list[dict[str, Any]]:
        """Notes relevant to ``query`` using hybrid dense vector + lexical scoring."""
        query_text = (query or "").strip()
        if not query_text:
            return []

        terms = content_terms(query_text)
        if not terms:
            return []

        limit_val = max(1, min(int(limit), 25))
        query_emb = get_embedding(query_text)

        with self._lock:
            cursor = self._conn.execute(
                "SELECT id, created_at, text, tags, seen_count, embedding FROM memories ORDER BY created_at DESC, id DESC LIMIT 200"
            )
            rows = [dict(r) for r in cursor.fetchall()]

        if query_emb and rows:
            scored = []
            terms = content_terms(query_text)
            for row in rows:
                raw_emb = row.get("embedding")
                sim = 0.0
                if raw_emb:
                    try:
                        emb = json.loads(raw_emb)
                        sim = cosine_similarity(query_emb, emb)
                    except Exception:
                        sim = 0.0

                row_text_lower = str(row.get("text", "")).lower()
                lexical_bonus = 0.15 if any(t in row_text_lower for t in terms) else 0.0
                total_score = sim + lexical_bonus
                if total_score >= 0.45 or (lexical_bonus > 0 and sim >= 0.35):
                    scored.append((total_score, row))

            if scored:
                scored.sort(key=lambda x: x[0], reverse=True)
                return [item[1] for item in scored[:limit_val]]

        # Fallback to lexical content-words search if embeddings unavailable
        terms = content_terms(query_text)
        if not terms:
            return []
        rows = self.search(" ".join(terms), limit=limit_val)
        return [
            row
            for row in rows
            if any(term in str(row.get("text", "")).lower() for term in terms)
        ][:limit_val]

    def backfill_embeddings(self, limit: int = 100) -> int:
        """Compute embeddings for existing memories that lack them."""
        with self._lock:
            cursor = self._conn.execute(
                "SELECT id, text FROM memories WHERE embedding IS NULL OR embedding = '' LIMIT ?",
                (limit,),
            )
            rows = cursor.fetchall()
            updated = 0
            for r in rows:
                emb = get_embedding(r["text"])
                if emb:
                    self._conn.execute(
                        "UPDATE memories SET embedding = ? WHERE id = ?",
                        (json.dumps(emb), r["id"]),
                    )
                    updated += 1
            if updated > 0:
                self._conn.commit()
            return updated

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
                SELECT id, created_at, text, tags, seen_count, 0.0 AS rank
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

    # -- autonomous execution logs ----------------------------------------

    def log_execution(
        self,
        goal: str,
        tools_used: list[str] | str,
        outcome: str,
        result_quality: int,
        lessons: str,
        adjustments: str,
        timestamp: str | None = None,
    ) -> int:
        """Store a structured self-learning record for autonomous execution."""
        ts = timestamp or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        tools_str = ", ".join(tools_used) if isinstance(tools_used, (list, tuple)) else str(tools_used)
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO execution_logs (timestamp, goal, tools_used, outcome, result_quality, lessons, adjustments)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (ts, goal.strip(), tools_str.strip(), outcome.strip(), max(0, min(10, int(result_quality))), lessons.strip(), adjustments.strip()),
            )
            self._conn.commit()
            return cursor.lastrowid or 0

    def list_execution_logs(self, query: str = "", limit: int = 20) -> list[dict[str, Any]]:
        """Retrieve historical execution logs, optionally filtered by keyword."""
        limit = max(1, min(int(limit), 100))
        with self._lock:
            if query.strip():
                cursor = self._conn.execute(
                    """
                    SELECT id, timestamp, goal, tools_used, outcome, result_quality, lessons, adjustments
                    FROM execution_logs
                    WHERE goal LIKE ? OR lessons LIKE ? OR adjustments LIKE ?
                    ORDER BY id DESC LIMIT ?
                    """,
                    (f"%{query.strip()}%", f"%{query.strip()}%", f"%{query.strip()}%", limit),
                )
            else:
                cursor = self._conn.execute(
                    """
                    SELECT id, timestamp, goal, tools_used, outcome, result_quality, lessons, adjustments
                    FROM execution_logs
                    ORDER BY id DESC LIMIT ?
                    """,
                    (limit,),
                )
            return [dict(row) for row in cursor.fetchall()]

    def get_learning_analytics(self) -> dict[str, Any]:
        """Aggregate performance metrics, success rates, and tool effectiveness from execution logs."""
        with self._lock:
            cursor = self._conn.execute(
                "SELECT outcome, result_quality, tools_used FROM execution_logs"
            )
            rows = cursor.fetchall()

        total = len(rows)
        if total == 0:
            return {
                "total_executions": 0,
                "success_rate_percent": 100.0,
                "avg_quality_score": 10.0,
                "success_count": 0,
                "partial_count": 0,
                "fail_count": 0,
                "top_tools": [],
            }

        successes = sum(1 for r in rows if r["outcome"].lower() == "success")
        partials = sum(1 for r in rows if r["outcome"].lower() == "partial")
        fails = sum(1 for r in rows if r["outcome"].lower() == "fail")
        avg_quality = sum(r["result_quality"] for r in rows) / total

        tool_counts: dict[str, int] = {}
        for r in rows:
            raw_tools = r["tools_used"] or ""
            for t in raw_tools.split(","):
                t_clean = t.strip()
                if t_clean:
                    tool_counts[t_clean] = tool_counts.get(t_clean, 0) + 1

        top_tools = sorted(tool_counts.items(), key=lambda x: x[1], reverse=True)[:5]

        return {
            "total_executions": total,
            "success_rate_percent": round((successes / total) * 100, 1),
            "avg_quality_score": round(avg_quality, 1),
            "success_count": successes,
            "partial_count": partials,
            "fail_count": fails,
            "top_tools": [{"tool": t[0], "count": t[1]} for t in top_tools],
        }

    # -- paper trading portfolio ------------------------------------------

    def get_cash_balance(self) -> float:
        with self._lock:
            row = self._conn.execute("SELECT value FROM portfolio_meta WHERE key = 'cash'").fetchone()
            if row:
                try:
                    return float(row["value"])
                except ValueError:
                    pass
            # Default starting paper trading capital: $100,000 USD / INR equivalent
            default_cash = 100000.0
            self._conn.execute("INSERT OR REPLACE INTO portfolio_meta (key, value) VALUES ('cash', ?)", (str(default_cash),))
            self._conn.commit()
            return default_cash

    def set_cash_balance(self, amount: float) -> float:
        with self._lock:
            self._conn.execute("INSERT OR REPLACE INTO portfolio_meta (key, value) VALUES ('cash', ?)", (str(amount),))
            self._conn.commit()
            return amount

    def record_trade(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        trade_type: str = "market",
        advisor_notes: str = "",
    ) -> dict[str, Any]:
        """Execute and log a paper trade, updating cash and positions atomically."""
        sym = symbol.strip().upper()
        side_clean = side.strip().lower()  # buy | sell
        qty = float(quantity)
        px = float(price)
        trade_cost = qty * px

        with self._lock:
            cash = self.get_cash_balance()
            pos_row = self._conn.execute(
                "SELECT quantity, avg_price FROM portfolio_positions WHERE symbol = ?", (sym,)
            ).fetchone()
            curr_qty = float(pos_row["quantity"]) if pos_row else 0.0
            curr_avg = float(pos_row["avg_price"]) if pos_row else 0.0

            pnl = 0.0
            if side_clean == "buy":
                if cash < trade_cost:
                    return {"ok": False, "error": f"Insufficient paper cash: balance ${cash:,.2f}, required ${trade_cost:,.2f}"}
                new_cash = cash - trade_cost
                new_qty = curr_qty + qty
                new_avg = ((curr_qty * curr_avg) + trade_cost) / new_qty if new_qty > 0 else 0.0
                self.set_cash_balance(new_cash)
                self._conn.execute(
                    "INSERT OR REPLACE INTO portfolio_positions (symbol, quantity, avg_price, updated_at) VALUES (?, ?, ?, ?)",
                    (sym, new_qty, new_avg, time.time()),
                )
            elif side_clean == "sell":
                if curr_qty < qty:
                    return {"ok": False, "error": f"Insufficient position to sell: owned {curr_qty} {sym}, attempted to sell {qty}"}
                new_cash = cash + trade_cost
                pnl = (px - curr_avg) * qty
                new_qty = curr_qty - qty
                self.set_cash_balance(new_cash)
                if new_qty > 0.00001:
                    self._conn.execute(
                        "UPDATE portfolio_positions SET quantity = ?, updated_at = ? WHERE symbol = ?",
                        (new_qty, time.time(), sym),
                    )
                else:
                    self._conn.execute("DELETE FROM portfolio_positions WHERE symbol = ?", (sym,))
            else:
                return {"ok": False, "error": f"Invalid trade side '{side}', must be 'buy' or 'sell'"}

            cursor = self._conn.execute(
                """
                INSERT INTO portfolio_trades (symbol, side, quantity, price, trade_type, timestamp, pnl, advisor_notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (sym, side_clean, qty, px, trade_type, time.time(), pnl, advisor_notes.strip()),
            )
            self._conn.commit()
            return {
                "ok": True,
                "trade_id": cursor.lastrowid,
                "symbol": sym,
                "side": side_clean,
                "quantity": qty,
                "price": px,
                "pnl": pnl,
                "cash_remaining": self.get_cash_balance(),
                "position_qty": new_qty,
            }

    def get_portfolio(self) -> dict[str, Any]:
        """Fetch all open paper holdings and recent trade history."""
        with self._lock:
            cash = self.get_cash_balance()
            positions_cur = self._conn.execute("SELECT symbol, quantity, avg_price, updated_at FROM portfolio_positions WHERE quantity > 0")
            positions = [dict(row) for row in positions_cur.fetchall()]

            trades_cur = self._conn.execute(
                "SELECT id, symbol, side, quantity, price, trade_type, timestamp, pnl, advisor_notes FROM portfolio_trades ORDER BY id DESC LIMIT 10"
            )
            trades = [dict(row) for row in trades_cur.fetchall()]

        total_cost_basis = sum(p["quantity"] * p["avg_price"] for p in positions)
        return {
            "cash_balance": cash,
            "cost_basis": total_cost_basis,
            "total_portfolio_value": cash + total_cost_basis,
            "positions_count": len(positions),
            "positions": positions,
            "recent_trades": trades,
        }

    # -- lead generation pipeline -----------------------------------------

    def add_or_update_lead(
        self,
        company_name: str,
        website: str = "",
        contact_name: str = "",
        contact_email: str = "",
        vertical: str = "",
        fit_score: int = 0,
        status: str = "discovered",
        outreach_copy: str = "",
    ) -> dict[str, Any]:
        """Register or update a B2B sales lead."""
        now = time.time()
        cname = company_name.strip()
        with self._lock:
            existing = self._conn.execute("SELECT id FROM lead_pipeline WHERE company_name = ?", (cname,)).fetchone()
            if existing:
                lead_id = int(existing["id"])
                self._conn.execute(
                    """
                    UPDATE lead_pipeline SET
                        website = COALESCE(NULLIF(?, ''), website),
                        contact_name = COALESCE(NULLIF(?, ''), contact_name),
                        contact_email = COALESCE(NULLIF(?, ''), contact_email),
                        vertical = COALESCE(NULLIF(?, ''), vertical),
                        fit_score = CASE WHEN ? > 0 THEN ? ELSE fit_score END,
                        status = COALESCE(NULLIF(?, ''), status),
                        outreach_copy = COALESCE(NULLIF(?, ''), outreach_copy),
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (website.strip(), contact_name.strip(), contact_email.strip(), vertical.strip(), fit_score, fit_score, status.strip(), outreach_copy.strip(), now, lead_id),
                )
            else:
                cursor = self._conn.execute(
                    """
                    INSERT INTO lead_pipeline (company_name, website, contact_name, contact_email, vertical, fit_score, status, outreach_copy, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (cname, website.strip(), contact_name.strip(), contact_email.strip(), vertical.strip(), max(0, min(100, int(fit_score))), status.strip(), outreach_copy.strip(), now, now),
                )
                lead_id = cursor.lastrowid or 0
            self._conn.commit()
            return {"id": lead_id, "company_name": cname, "status": status}

    def list_leads(self, status: str = "", vertical: str = "", limit: int = 50) -> list[dict[str, Any]]:
        """List active pipeline leads by stage or vertical."""
        limit = max(1, min(int(limit), 200))
        query = "SELECT id, company_name, website, contact_name, contact_email, vertical, fit_score, status, outreach_copy, created_at, updated_at FROM lead_pipeline WHERE 1=1"
        params: list[Any] = []
        if status.strip():
            query += " AND status = ?"
            params.append(status.strip().lower())
        if vertical.strip():
            query += " AND vertical LIKE ?"
            params.append(f"%{vertical.strip()}%")
        query += " ORDER BY fit_score DESC, id DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            rows = self._conn.execute(query, tuple(params)).fetchall()
            return [dict(row) for row in rows]

    def update_lead_status(self, lead_id: int, status: str, outreach_copy: str = "") -> bool:
        with self._lock:
            if outreach_copy.strip():
                cursor = self._conn.execute(
                    "UPDATE lead_pipeline SET status = ?, outreach_copy = ?, updated_at = ? WHERE id = ?",
                    (status.strip().lower(), outreach_copy.strip(), time.time(), int(lead_id)),
                )
            else:
                cursor = self._conn.execute(
                    "UPDATE lead_pipeline SET status = ?, updated_at = ? WHERE id = ?",
                    (status.strip().lower(), time.time(), int(lead_id)),
                )
            self._conn.commit()
            return cursor.rowcount > 0

    # -- expenses ledger --------------------------------------------------

    def add_expense(
        self,
        category: str,
        amount: float,
        vendor: str = "",
        description: str = "",
        currency: str = "USD",
        receipt_ref: str = "",
        timestamp: str | None = None,
    ) -> int:
        ts = timestamp or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO expenses (timestamp, category, amount, currency, vendor, description, receipt_ref)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (ts, category.strip().lower(), float(amount), currency.strip().upper(), vendor.strip(), description.strip(), receipt_ref.strip()),
            )
            self._conn.commit()
            return cursor.lastrowid or 0

    def list_expenses(self, category: str = "", limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        with self._lock:
            if category.strip():
                cursor = self._conn.execute(
                    "SELECT id, timestamp, category, amount, currency, vendor, description, receipt_ref FROM expenses WHERE category = ? ORDER BY id DESC LIMIT ?",
                    (category.strip().lower(), limit),
                )
            else:
                cursor = self._conn.execute(
                    "SELECT id, timestamp, category, amount, currency, vendor, description, receipt_ref FROM expenses ORDER BY id DESC LIMIT ?",
                    (limit,),
                )
            return [dict(row) for row in cursor.fetchall()]

    def get_expense_summary(self) -> dict[str, Any]:
        with self._lock:
            rows = self._conn.execute("SELECT category, SUM(amount) AS total, COUNT(*) AS count FROM expenses GROUP BY category").fetchall()
            overall = self._conn.execute("SELECT SUM(amount) AS total, COUNT(*) AS count FROM expenses").fetchone()

        categories = {row["category"]: {"total": float(row["total"] or 0.0), "count": int(row["count"] or 0)} for row in rows}
        return {
            "grand_total": float(overall["total"] or 0.0) if overall else 0.0,
            "total_expenses": int(overall["count"] or 0) if overall else 0,
            "by_category": categories,
        }

    # -- invoice management -----------------------------------------------

    def create_invoice(
        self,
        invoice_num: str,
        client_name: str,
        total_amount: float,
        client_email: str = "",
        currency: str = "USD",
        items: list[dict[str, Any]] | None = None,
        status: str = "draft",
    ) -> dict[str, Any]:
        now = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        items_raw = json.dumps(items or [])
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT OR REPLACE INTO invoices (invoice_num, client_name, client_email, total_amount, currency, status, items_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (invoice_num.strip(), client_name.strip(), client_email.strip(), float(total_amount), currency.strip().upper(), status.strip().lower(), items_raw, now),
            )
            self._conn.commit()
            return {
                "id": cursor.lastrowid or 0,
                "invoice_num": invoice_num.strip(),
                "client_name": client_name.strip(),
                "total_amount": float(total_amount),
                "currency": currency.strip().upper(),
                "status": status.strip().lower(),
                "created_at": now,
            }

    def list_invoices(self, status: str = "", limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 100))
        with self._lock:
            if status.strip():
                cursor = self._conn.execute(
                    "SELECT id, invoice_num, client_name, client_email, total_amount, currency, status, items_json, created_at FROM invoices WHERE status = ? ORDER BY id DESC LIMIT ?",
                    (status.strip().lower(), limit),
                )
            else:
                cursor = self._conn.execute(
                    "SELECT id, invoice_num, client_name, client_email, total_amount, currency, status, items_json, created_at FROM invoices ORDER BY id DESC LIMIT ?",
                    (limit,),
                )
            rows = [dict(row) for row in cursor.fetchall()]
            for r in rows:
                with contextlib.suppress(Exception):
                    r["items"] = json.loads(r["items_json"])
            return rows

    # -- cron schedule tasks ----------------------------------------------

    def add_scheduled_cron(
        self,
        name: str,
        interval_m: int,
        tool_name: str,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = time.time()
        args_raw = json.dumps(args or {})
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT OR REPLACE INTO scheduled_crons (name, interval_m, tool_name, args_json, status, created_at)
                VALUES (?, ?, ?, ?, 'active', ?)
                """,
                (name.strip(), int(interval_m), tool_name.strip(), args_raw, now),
            )
            self._conn.commit()
            return {
                "id": cursor.lastrowid or 0,
                "name": name.strip(),
                "interval_m": int(interval_m),
                "tool_name": tool_name.strip(),
                "status": "active",
            }

    def list_scheduled_crons(self, status: str = "") -> list[dict[str, Any]]:
        with self._lock:
            if status.strip():
                cursor = self._conn.execute("SELECT id, name, interval_m, tool_name, args_json, status, last_run, created_at FROM scheduled_crons WHERE status = ?", (status.strip().lower(),))
            else:
                cursor = self._conn.execute("SELECT id, name, interval_m, tool_name, args_json, status, last_run, created_at FROM scheduled_crons")
            rows = [dict(row) for row in cursor.fetchall()]
            for r in rows:
                with contextlib.suppress(Exception):
                    r["args"] = json.loads(r["args_json"])
            return rows

    def update_cron_status(self, name: str, status: str) -> bool:
        with self._lock:
            cursor = self._conn.execute("UPDATE scheduled_crons SET status = ? WHERE name = ?", (status.strip().lower(), name.strip()))
            self._conn.commit()
            return cursor.rowcount > 0

    # -- custom learned commands ------------------------------------------

    def add_custom_command(
        self,
        trigger_phrase: str,
        action_payload: str,
        action_type: str = "directive",
        language: str = "any",
        description: str = "",
    ) -> dict[str, Any]:
        trigger = (trigger_phrase or "").strip()
        action = (action_payload or "").strip()
        if not trigger or not action:
            raise ValueError("Trigger phrase and action payload cannot be empty")
        now = time.time()
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT OR REPLACE INTO custom_commands
                (trigger_phrase, action_payload, action_type, language, description, created_at, use_count, last_used_at)
                VALUES (?, ?, ?, ?, ?, ?, COALESCE((SELECT use_count FROM custom_commands WHERE trigger_phrase = ?), 0), ?)
                """,
                (trigger.lower(), action, action_type, language, description, now, trigger.lower(), now),
            )
            self._conn.commit()
            return {
                "id": cursor.lastrowid or 0,
                "trigger_phrase": trigger.lower(),
                "action_payload": action,
                "action_type": action_type,
                "language": language,
                "description": description,
                "created_at": now,
            }

    def list_custom_commands(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT id, trigger_phrase, action_payload, action_type, language, description, created_at, use_count, last_used_at FROM custom_commands ORDER BY use_count DESC, id DESC LIMIT ?",
                (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_custom_command(self, trigger_phrase: str) -> dict[str, Any] | None:
        trigger = (trigger_phrase or "").strip().lower()
        if not trigger:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT id, trigger_phrase, action_payload, action_type, language, description, created_at, use_count, last_used_at FROM custom_commands WHERE trigger_phrase = ?",
                (trigger,),
            ).fetchone()
            return dict(row) if row else None

    def delete_custom_command(self, trigger_phrase: str) -> bool:
        trigger = (trigger_phrase or "").strip().lower()
        if not trigger:
            return False
        with self._lock:
            cursor = self._conn.execute("DELETE FROM custom_commands WHERE trigger_phrase = ?", (trigger,))
            self._conn.commit()
            return cursor.rowcount > 0

    def match_custom_command(self, text: str) -> dict[str, Any] | None:
        raw = (text or "").strip().lower()
        if not raw:
            return None
        cleaned = re.sub(r"^(?:hey\s+)?jarvis[,:\s]*", "", raw, flags=re.IGNORECASE).strip()
        with self._lock:
            row = self._conn.execute(
                "SELECT id, trigger_phrase, action_payload, action_type, language, description, created_at, use_count, last_used_at FROM custom_commands WHERE trigger_phrase = ? OR trigger_phrase = ?",
                (raw, cleaned),
            ).fetchone()
            if row:
                self._record_command_use(row["id"])
                res = dict(row)
                res["use_count"] = res.get("use_count", 0) + 1
                return res

            cursor = self._conn.execute(
                "SELECT id, trigger_phrase, action_payload, action_type, language, description, created_at, use_count, last_used_at FROM custom_commands"
            )
            candidates = [dict(r) for r in cursor.fetchall()]

        for cmd in candidates:
            trig = cmd["trigger_phrase"]
            pattern = rf"\b{re.escape(trig)}\b"
            if re.search(pattern, cleaned, re.IGNORECASE):
                self._record_command_use(cmd["id"])
                cmd["use_count"] = cmd.get("use_count", 0) + 1
                return cmd

        return None

    def _record_command_use(self, command_id: int) -> None:
        now = time.time()
        with self._lock:
            self._conn.execute(
                "UPDATE custom_commands SET use_count = use_count + 1, last_used_at = ? WHERE id = ?",
                (now, command_id),
            )
            self._conn.commit()

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
