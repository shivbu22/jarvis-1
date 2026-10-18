import pytest
from unittest.mock import MagicMock
from apps.gateway.tools.registry import ToolContext
from apps.gateway.tools.operator import learning_performance_analytics, strategy_recommend, log_execution, load_history
from apps.gateway.memory import MemoryStore


@pytest.fixture
def test_mem(tmp_path):
    db_path = tmp_path / "memory.sqlite3"
    return MemoryStore(db_path)


def make_ctx(mem):
    return ToolContext(
        settings=MagicMock(),
        memory=mem,
        policy=MagicMock(),
        audit=MagicMock(),
        bus=MagicMock(),
    )


def test_operator_learning_analytics_empty(test_mem):
    ctx = make_ctx(test_mem)
    res = learning_performance_analytics(ctx)
    assert res.ok is True
    assert "AUTONOMOUS SELF-LEARNING ANALYTICS" in res.text
    assert res.data["total_executions"] == 0


def test_operator_log_and_analytics(test_mem):
    ctx = make_ctx(test_mem)
    log_execution(
        ctx,
        goal="Lead scrape for Swachta",
        outcome="success",
        result_quality=9,
        lessons="Personalization worked well",
        adjustments="Target decision-makers directly",
        tools_used="lead_search,lead_qualify",
    )
    history = load_history(ctx, query="Swachta")
    assert history.ok is True
    assert len(history.data) == 1

    analytics = learning_performance_analytics(ctx)
    assert analytics.ok is True
    assert analytics.data["total_executions"] == 1
    assert analytics.data["success_rate_percent"] == 100.0
    assert analytics.data["avg_quality_score"] == 9.0


def test_strategy_recommend(test_mem):
    ctx = make_ctx(test_mem)
    rec_leads = strategy_recommend(ctx, task_type="lead_gen")
    assert rec_leads.ok is True
    assert "lead_search" in rec_leads.text

    rec_trade = strategy_recommend(ctx, task_type="trading")
    assert rec_trade.ok is True
    assert "fetch_market_data" in rec_trade.text
