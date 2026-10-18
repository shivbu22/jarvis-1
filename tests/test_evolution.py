"""Tests for the Self-Evolution & Continuous Adaptation Engine."""

import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from apps.gateway.evolution import EvolutionEngine, EvolutionRule
from apps.gateway.tools.evolution_tools import (
    delete_evolution_rule,
    list_evolution_rules,
    record_learned_rule,
    skill_create,
    trigger_daily_briefing,
)
from apps.gateway.tools.registry import ToolContext


@pytest.fixture
def temp_evolution_engine(tmp_path: Path) -> EvolutionEngine:
    rules_file = tmp_path / "evolution_rules.json"
    return EvolutionEngine(data_path=rules_file)


def test_evolution_rule_serde():
    rule = EvolutionRule(
        rule_id="rule-test1234",
        category="preference",
        trigger_pattern="browser",
        rule_text="Always use Chrome for web automation.",
        source="user_correction",
        confidence=0.98,
        times_applied=5,
    )
    d = rule.to_dict()
    assert d["rule_id"] == "rule-test1234"
    assert d["category"] == "preference"
    assert d["times_applied"] == 5

    restored = EvolutionRule.from_dict(d)
    assert restored.rule_id == rule.rule_id
    assert restored.rule_text == rule.rule_text
    assert restored.confidence == 0.98


def test_evolution_engine_crud(temp_evolution_engine: EvolutionEngine):
    engine = temp_evolution_engine
    # Initial seed rules
    rules = engine.list_rules()
    assert len(rules) >= 2

    # Add new rule
    new_rule = engine.add_rule(
        rule_text="Never call python 2.",
        category="tool_usage",
        trigger_pattern="python",
        source="explicit_teach",
    )
    assert new_rule.rule_id.startswith("rule-")
    assert any(r["rule_id"] == new_rule.rule_id for r in engine.list_rules())

    # Delete rule
    ok = engine.delete_rule(new_rule.rule_id)
    assert ok is True
    assert not any(r["rule_id"] == new_rule.rule_id for r in engine.list_rules())

    # Delete non-existent
    assert engine.delete_rule("fake-id") is False


def test_correction_reflection(temp_evolution_engine: EvolutionEngine):
    engine = temp_evolution_engine

    # 1. "Instead of X, use Y"
    learned = engine.inspect_and_learn_from_correction("Instead of Edge, use Chrome for web links.")
    assert learned is not None
    assert "Chrome" in learned.rule_text
    assert "Edge" in learned.rule_text

    # 2. "From now on..."
    learned2 = engine.inspect_and_learn_from_correction("From now on, speak in bullet points.")
    assert learned2 is not None
    assert "Speak in bullet points" in learned2.rule_text

    # 3. Non-correction utterance returns None
    assert engine.inspect_and_learn_from_correction("What is the weather today?") is None


def test_prompt_section_builder(temp_evolution_engine: EvolutionEngine):
    engine = temp_evolution_engine
    engine.add_rule(
        rule_text="Always format currency in INR.",
        category="preference",
        trigger_pattern="price",
    )

    # General query gets universal rules (*)
    section = engine.build_prompt_section("hello jarvis")
    assert "SELF-EVOLVED RULES" in section

    # Query with trigger word 'price' gets currency rule
    section_price = engine.build_prompt_section("what is the price of apple?")
    assert "INR" in section_price


def test_fine_tuning_turn_recording(temp_evolution_engine: EvolutionEngine, tmp_path: Path):
    engine = temp_evolution_engine
    ok = engine.record_turn_for_fine_tuning(
        user_text="Open calculator",
        assistant_reply="Opening calculator now, sir.",
        tools_used=[{"name": "app_launch"}],
    )
    assert ok is True


@pytest.mark.asyncio
async def test_generate_daily_briefing(temp_evolution_engine: EvolutionEngine):
    engine = temp_evolution_engine
    mock_service = MagicMock()
    mock_service.profile.get.return_value = "Tony"
    mock_service.health.return_value = {
        "brain": {"name": "ultimate"},
        "hardware": {"cpu_percent": 12.5, "ram_gb": "8.0/16.0 GB"},
    }
    mock_service.scheduler.list_tasks.return_value = [
        {"message": "Board meeting review"}
    ]

    briefing = await engine.generate_daily_briefing(service=mock_service)
    assert "Good morning, Tony" in briefing["text"]
    assert "Board meeting review" in briefing["text"]
    assert briefing["scheduled_tasks_count"] == 1


def test_skill_create_tool(tmp_path: Path, monkeypatch):
    import apps.gateway.tools.evolution_tools as evo_tools
    monkeypatch.setattr(evo_tools, "CUSTOM_SKILLS_DIR", tmp_path / "skills")

    ctx = ToolContext(settings=MagicMock(), memory=MagicMock(), policy=MagicMock(), audit=MagicMock(), bus=MagicMock())
    out = skill_create(
        ctx,
        name="test-automation-skill",
        description="Automates nightly test executions.",
        instructions="Step 1: Run pytest. Step 2: Report failures.",
        script_code="print('Running test automation')",
    )
    assert out.ok is True
    skill_file = tmp_path / "skills" / "test-automation-skill" / "SKILL.md"
    assert skill_file.exists()
    assert "test-automation-skill" in skill_file.read_text(encoding="utf-8")


def test_evolution_tools_lifecycle():
    ctx = ToolContext(settings=MagicMock(), memory=MagicMock(), policy=MagicMock(), audit=MagicMock(), bus=MagicMock())

    # Record rule
    rec = record_learned_rule(ctx, rule="Keep replies under 30 words.", category="tone")
    assert rec.ok is True
    rule_id = rec.data["rule_id"]

    # List rules
    listing = list_evolution_rules(ctx)
    assert listing.ok is True
    assert any(r["rule_id"] == rule_id for r in listing.data["rules"])

    # Delete rule
    del_out = delete_evolution_rule(ctx, rule_id=rule_id)
    assert del_out.ok is True


@pytest.mark.asyncio
async def test_trigger_daily_briefing_tool():
    ctx = ToolContext(settings=MagicMock(), memory=MagicMock(), policy=MagicMock(), audit=MagicMock(), bus=MagicMock())
    ctx.service = MagicMock()
    ctx.service.profile.get.return_value = "Commander"
    ctx.service.scheduler.list_tasks.return_value = []

    out = await trigger_daily_briefing(ctx, deliver_toast=False)
    assert out.ok is True
    assert "Good morning" in out.data["text"]
