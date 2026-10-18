"""Comprehensive verification suite for JARVIS Autonomous Agent Framework:
1. Market Intelligence & Paper Trading Engine
2. Lead Generation & Outreach Engine
3. Executive Operator & Self-Learning Engine
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from apps.gateway.tools.leads import (
    lead_outreach_draft,
    lead_pipeline_list,
    lead_qualify,
    lead_search,
)
from apps.gateway.tools.operator import (
    daily_executive_briefing,
    load_history,
    log_execution,
    run_advisor_consensus,
)
from apps.gateway.tools.trading import (
    _calculate_rsi,
    check_portfolio,
    execute_trade,
    fetch_market_data,
)


def test_memory_layer_autonomous_tables(harness) -> None:
    mem = harness.memory

    # 1. Execution Logs
    log_id = mem.log_execution(
        goal="Audit portfolio risk",
        tools_used=["fetch_market_data"],
        outcome="success",
        result_quality=9,
        lessons="No action required during consolidation",
        adjustments="Monitor key support levels",
    )
    assert log_id > 0

    logs = mem.list_execution_logs(limit=5)
    assert len(logs) >= 1
    assert logs[0]["goal"] == "Audit portfolio risk"
    assert logs[0]["tools_used"] == "fetch_market_data"
    assert logs[0]["result_quality"] == 9

    # 2. Portfolio Cash & Trading
    initial_cash = mem.get_cash_balance()
    assert initial_cash == 100000.0

    # Execute BUY
    trade1 = mem.record_trade("AAPL", "buy", 10, 150.0)
    assert trade1["ok"] is True
    assert trade1["trade_id"] > 0
    assert trade1["quantity"] == 10
    assert trade1["price"] == 150.0
    assert mem.get_cash_balance() == 100000.0 - 1500.0

    # Verify Portfolio Positions
    port = mem.get_portfolio()
    assert len(port["positions"]) == 1
    assert port["positions"][0]["symbol"] == "AAPL"
    assert port["positions"][0]["quantity"] == 10
    assert port["positions"][0]["avg_price"] == 150.0

    # Execute SELL (5 shares at 200.0 -> Profit = 5 * (200 - 150) = 250.0)
    trade2 = mem.record_trade("AAPL", "sell", 5, 200.0)
    assert trade2["ok"] is True
    assert trade2["pnl"] == 250.0
    assert mem.get_cash_balance() == 98500.0 + 1000.0

    port2 = mem.get_portfolio()
    assert port2["positions"][0]["quantity"] == 5

    # 3. Lead Pipeline
    lead = mem.add_or_update_lead(
        company_name="Nordic Logistics",
        website="https://nordiclogistics.eu",
        contact_name="Elena Rostova",
        contact_email="elena@nordiclogistics.eu",
        vertical="Logistics",
        fit_score=85,
        status="qualified",
        outreach_copy="High demand for autonomous fleet coordination",
    )
    assert lead["id"] > 0

    leads = mem.list_leads(status="qualified")
    assert len(leads) >= 1
    assert leads[0]["company_name"] == "Nordic Logistics"
    assert leads[0]["fit_score"] == 85

    # Update Lead Status
    mem.update_lead_status(lead["id"], "contacted", "Sent initial cold pitch")
    updated_lead = mem.list_leads(status="contacted")[0]
    assert updated_lead["status"] == "contacted"
    assert "Sent initial cold pitch" in updated_lead["outreach_copy"]


def test_trading_engine_rsi_and_market_data(harness, monkeypatch) -> None:
    # 1. Pure RSI calculation check
    prices_up = [100.0 + i for i in range(25)]
    rsi_up = _calculate_rsi(prices_up)
    assert rsi_up is not None and rsi_up > 70.0  # Strong uptrend

    prices_down = [200.0 - i for i in range(25)]
    rsi_down = _calculate_rsi(prices_down)
    assert rsi_down is not None and rsi_down < 30.0  # Strong downtrend

    # 2. Mock Yahoo Finance response for deterministic testing
    mock_yahoo_data = {
        "chart": {
            "result": [
                {
                    "meta": {
                        "regularMarketPrice": 2850.50,
                        "currency": "INR",
                        "symbol": "RELIANCE.NS",
                    },
                    "indicators": {
                        "quote": [
                            {
                                "close": [2700.0 + (i * 5.0) for i in range(25)],
                            }
                        ]
                    },
                }
            ]
        }
    }

    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(mock_yahoo_data).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = False

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=0: mock_resp)

    # Call fetch_market_data
    res = fetch_market_data(harness.ctx, symbol="RELIANCE.NS")
    assert res.ok is True
    assert "RELIANCE.NS" in res.text
    assert res.data["price"] == 2850.50
    assert "rsi" in res.data
    assert "sma_20" in res.data
    assert "signal" in res.data


def test_trading_execution_and_portfolio_accounting(harness) -> None:
    # Check starting portfolio
    p_init = check_portfolio(harness.ctx)
    assert p_init.ok is True
    assert p_init.data["cash_balance"] == 100000.0

    # Buy without sufficient cash should fail
    fail_trade = execute_trade(harness.ctx, symbol="TSLA", quantity=10000, side="buy", price=250.0)
    assert fail_trade.ok is False
    assert "Insufficient paper cash" in fail_trade.text

    # Valid Buy
    buy_res = execute_trade(harness.ctx, symbol="NIFTY_ETF", quantity=100, side="buy", price=250.0)
    assert buy_res.ok is True
    assert "Trade Executed: BUY" in buy_res.text
    assert buy_res.data["quantity"] == 100

    # Sell more than owned should fail
    over_sell = execute_trade(harness.ctx, symbol="NIFTY_ETF", quantity=200, side="sell", price=260.0)
    assert over_sell.ok is False
    assert "Insufficient position" in over_sell.text

    # Valid partial sell
    sell_res = execute_trade(harness.ctx, symbol="NIFTY_ETF", quantity=40, side="sell", price=280.0)
    assert sell_res.ok is True
    assert "Trade Executed: SELL" in sell_res.text
    assert sell_res.data["pnl"] == 40 * (280.0 - 250.0)  # +$1200 profit

    # Check portfolio state
    p_after = check_portfolio(harness.ctx)
    assert p_after.ok is True
    assert len(p_after.data["positions"]) == 1
    assert p_after.data["positions"][0]["symbol"] == "NIFTY_ETF"
    assert p_after.data["positions"][0]["quantity"] == 60


def test_lead_generation_and_icp_qualification(harness, monkeypatch) -> None:
    # 1. Mock DuckDuckGo for lead_search
    sample_html = """
    <html>
      <body>
        <div class="result__body">
          <a class="result__url" href="https://apexrobotics.in">https://apexrobotics.in</a>
          <a class="result__snippet" href="https://apexrobotics.in">Apex Robotics provides enterprise automation.</a>
        </div>
      </body>
    </html>
    """
    mock_resp = MagicMock()
    mock_resp.read.return_value = sample_html.encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = False

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=0: mock_resp)

    # Lead Search
    search_res = lead_search(harness.ctx, vertical="Robotics & Automation", keywords="warehouse", location="India", limit=5)
    assert search_res.ok is True
    assert len(search_res.data) >= 1

    # Lead Qualification
    qual_res = lead_qualify(
        harness.ctx,
        company_name="Apex Robotics",
        url="https://apexrobotics.in",
        criteria="enterprise scale logistics automation and funding",
    )
    assert qual_res.ok is True
    assert qual_res.data["score"] >= 80
    assert qual_res.data["status"] == "qualified"

    # Lead Outreach Drafting
    draft_res = lead_outreach_draft(
        harness.ctx,
        company_name="Apex Robotics",
        lead_name="Vikram Sharma",
        value_prop="Autonomous warehouse fleet coordination and waste optimization",
        tone="consultative",
    )
    assert draft_res.ok is True
    assert "Vikram Sharma" in draft_res.text
    assert "Apex Robotics" in draft_res.text
    assert "Subject:" in draft_res.data["copy"]

    # Pipeline Verification
    pipe_res = lead_pipeline_list(harness.ctx, status="")
    assert pipe_res.ok is True
    assert len(pipe_res.data) >= 1


@pytest.mark.asyncio
async def test_executive_operator_briefing_and_learning(harness) -> None:
    # 1. Autonomous execution logging
    log_res = log_execution(
        harness.ctx,
        goal="Scale outbound campaign for Medimin Health",
        outcome="success",
        result_quality=9,
        lessons="Clinic administrators respond best to compliance and speed benefits",
        adjustments="Refine cold email hook to emphasize audit trail",
        tools_used="lead_qualify, lead_outreach_draft",
    )
    assert log_res.ok is True
    assert log_res.data["log_id"] > 0

    # 2. History retrieval
    hist_res = load_history(harness.ctx, query="Medimin Health", limit=5)
    assert hist_res.ok is True
    assert len(hist_res.data) >= 1
    assert "Medimin Health" in hist_res.text

    # 3. Multi-Agent Advisor Consensus
    consensus_res = await run_advisor_consensus(
        harness.ctx,
        action="Deploy $15,000 into NIFTY50 options strategy with 10% stop loss",
        reasoning="Market is near all-time highs; RSI indicates overbought condition at 74.5",
        risk_level="high",
    )
    assert consensus_res.ok is True
    assert "ADVISOR CONSENSUS" in consensus_res.text
    assert "CONSENSUS APPROVED" in consensus_res.text or "ADVISOR CAUTION" in consensus_res.text

    # 4. Daily Executive Briefing
    briefing_res = daily_executive_briefing(harness.ctx)
    assert briefing_res.ok is True
    assert "EXECUTIVE BRIEFING" in briefing_res.text
    assert "PORTFOLIO & CAPITAL" in briefing_res.text
    assert "MARKET PULSE" in briefing_res.text
    assert "VENTURE PIPELINE" in briefing_res.text
