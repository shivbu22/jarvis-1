"""Memory store tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from apps.gateway.memory import MemoryStore


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    memory = MemoryStore(tmp_path / "test.db")
    yield memory
    memory.close()


def test_remember_then_recall(store: MemoryStore) -> None:
    store.add("My workshop is on Friday")
    results = store.search("workshop")
    assert len(results) == 1
    assert "Friday" in results[0]["text"]


def test_recall_survives_reopening(tmp_path: Path) -> None:
    """The whole point: a note must outlive the process."""
    path = tmp_path / "durable.db"
    first = MemoryStore(path)
    first.add("The spare key is with Mrs Patel", tags="house")
    first.close()

    second = MemoryStore(path)
    results = second.search("key")
    assert len(results) == 1
    assert "Mrs Patel" in results[0]["text"]
    second.close()


def test_search_is_ranked_and_limited(store: MemoryStore) -> None:
    for index in range(10):
        store.add(f"note number {index} about invoices")
    assert len(store.search("invoices", limit=3)) == 3


def test_search_matches_tags(store: MemoryStore) -> None:
    store.add("Bring the samples", tags="travel mumbai")
    assert store.search("mumbai")


def test_search_with_punctuation_does_not_raise(store: MemoryStore) -> None:
    """Spoken questions arrive with apostrophes, quotes and question marks."""
    store.add("Dad's birthday is 3rd March")
    for query in ["what about the 2026 plan?", "Dad's birthday", 'quote "unbalanced', "a & b"]:
        assert isinstance(store.search(query), list)


def test_empty_query_returns_recent(store: MemoryStore) -> None:
    store.add("first")
    store.add("second")
    assert store.search("")[0]["text"] == "second"


def test_recent_orders_newest_first(store: MemoryStore) -> None:
    store.add("older")
    store.add("newer")
    assert [row["text"] for row in store.recent(2)] == ["newer", "older"]


def test_forget_removes_a_note(store: MemoryStore) -> None:
    memory_id = store.add("temporary")
    assert store.count() == 1
    assert store.forget(memory_id) is True
    assert store.count() == 0
    assert store.forget(memory_id) is False


def test_empty_note_is_rejected(store: MemoryStore) -> None:
    with pytest.raises(ValueError):
        store.add("   ")


def test_conversation_history_round_trip(store: MemoryStore) -> None:
    store.add_turn("main", "user", "what's the weather")
    store.add_turn("main", "assistant", "It is raining.")
    history = store.history("main", limit=10)
    assert [message["role"] for message in history] == ["user", "assistant"]
    assert history[0]["content"] == "what's the weather"


def test_history_is_isolated_per_session(store: MemoryStore) -> None:
    store.add_turn("a", "user", "from a")
    store.add_turn("b", "user", "from b")
    assert store.history("a") == [{"role": "user", "content": "from a"}]


def test_history_respects_the_limit_and_stays_chronological(store: MemoryStore) -> None:
    for index in range(10):
        store.add_turn("main", "user", f"message {index}")
    history = store.history("main", limit=3)
    assert [message["content"] for message in history] == ["message 7", "message 8", "message 9"]


def test_clearing_a_session_leaves_notes_alone(store: MemoryStore) -> None:
    store.add("keep me")
    store.add_turn("main", "user", "hello")
    assert store.clear_session("main") == 1
    assert store.history("main") == []
    assert store.count() == 1


def test_stats_report_what_is_stored(store: MemoryStore) -> None:
    store.add("a note")
    store.add_turn("main", "user", "hi")
    stats = store.stats()
    assert stats["memories"] == 1
    assert stats["turns"] == 1
    assert isinstance(stats["fts"], bool)


def test_a_paraphrase_of_an_existing_note_is_refused(store: MemoryStore) -> None:
    """The same fact in different words must not become a second note.

    Observed live: the model re-saved the workshop time in slightly different
    words on separate turns, and recall then returned both copies.
    """
    first = store.add("my workshop is on Friday at 3pm")
    duplicate = store.add("the workshop is on Friday at 3pm")
    assert first > 0
    assert duplicate < 0, "a paraphrase was stored as a new note"
    assert store.count() == 1


def test_a_number_change_is_a_new_fact_not_a_duplicate(store: MemoryStore) -> None:
    """\"2 litres\" and \"3 litres\" are different orders, not paraphrases."""
    first = store.add("buy 2 litres of milk")
    second = store.add("buy 3 litres of milk")
    assert first > 0 and second > 0
    assert store.count() == 2


def test_a_repeated_duplicate_bumps_the_seen_count(store: MemoryStore) -> None:
    store.add("the garage door code is 1234")
    store.add("garage door code 1234")
    store.add("it's 1234 for the garage door")
    rows = store.search("garage door")
    assert len(rows) == 1
    assert rows[0]["seen_count"] == 2
