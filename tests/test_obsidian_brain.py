"""Unit tests for Obsidian Brain Vault tools."""

from unittest.mock import MagicMock

from apps.gateway.tools.obsidian_brain import (
    VAULT_PATH,
    brain_vault_lint,
    brain_vault_note_write,
    brain_vault_read,
    brain_vault_search,
    build_specs,
)
from apps.gateway.tools.registry import ToolContext


def test_obsidian_brain_specs() -> None:
    specs = build_specs()
    names = {s.name for s in specs}
    assert "brain_vault_search" in names
    assert "brain_vault_read" in names
    assert "brain_vault_note_write" in names
    assert "brain_vault_lint" in names


def test_obsidian_brain_search_and_read() -> None:
    ctx = MagicMock(spec=ToolContext)
    res_search = brain_vault_search(ctx, "quantum")
    assert "quantum" in res_search.text.lower()
    assert len(res_search.data["results"]) > 0

    res_read = brain_vault_read(ctx, "entities/jarvis-core")
    assert "J.A.R.V.I.S." in res_read.text
    assert res_read.data["path"].endswith("jarvis-core.md")


def test_obsidian_brain_lint() -> None:
    ctx = MagicMock(spec=ToolContext)
    res_lint = brain_vault_lint(ctx)
    assert res_lint.data["total_notes"] >= 5
    assert "Brain Vault Audit" in res_lint.text


def test_obsidian_brain_write_note() -> None:
    ctx = MagicMock(spec=ToolContext)
    log_file = VAULT_PATH / "log.md"
    orig_log = log_file.read_text(encoding="utf-8") if log_file.exists() else None
    try:
        res_write = brain_vault_note_write(
            ctx,
            note_path="concepts/test-temp-concept.md",
            title="Test Dynamic Concept",
            content="This is a test note for verification of dynamic knowledge indexing.",
            note_type="concept",
            tags="test, verification",
        )
        assert "Successfully written" in res_write.text
        temp_file = VAULT_PATH / "concepts" / "test-temp-concept.md"
        assert temp_file.exists()
        if temp_file.exists():
            temp_file.unlink()
    finally:
        if orig_log is not None:
            log_file.write_text(orig_log, encoding="utf-8")
