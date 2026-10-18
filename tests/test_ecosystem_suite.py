"""Comprehensive verification suite for JARVIS Extended Tools Ecosystem:
1. Research & Intelligence Gathering Engine
2. Advanced Financial Analytics & Risk Engine
3. Productivity, Document & System Automation Engine
4. Intelligence, NLP & Statistical Analytics Engine
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from apps.gateway.tools.finance_extended import (
    candlestick_pattern_detect,
    company_fundamentals,
    portfolio_rebalance,
    portfolio_risk_calc,
    technical_indicators_calc,
)
from apps.gateway.tools.intelligence import (
    anomaly_detect,
    dataset_stats_analyze,
    learning_analytics,
    nlp_extract_entities,
    nlp_sentiment_analyze,
)
from apps.gateway.tools.productivity import (
    cron_task_schedule,
    data_validate_clean,
    document_extract_text,
    document_generate_invoice,
    expense_track,
)
from apps.gateway.tools.research import (
    company_filings_search,
    crypto_market_pulse,
    dns_mx_check,
    hiring_signals_search,
    news_rss_aggregate,
    social_sentiment_scan,
    whois_lookup,
)


def test_research_tools_suite(harness, monkeypatch) -> None:
    # 1. WHOIS Lookup (Mock RDAP)
    mock_rdap = {
        "handle": "DOMAIN-GOOGLE-COM",
        "events": [{"eventAction": "registration", "eventDate": "1997-09-15T04:00:00Z"}],
        "nameservers": [{"ldhName": "ns1.google.com"}, {"ldhName": "ns2.google.com"}],
        "status": ["clientTransferProhibited", "active"],
    }
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(mock_rdap).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = False

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=0: mock_resp)

    whois_res = whois_lookup(harness.ctx, domain="google.com")
    assert whois_res.ok is True
    assert "GOOGLE.COM" in whois_res.text
    assert whois_res.data["created"] == "1997-09-15T04:00:00Z"
    assert "ns1.google.com" in whois_res.data["nameservers"]

    # 2. DNS MX Deliverability
    dns_res = dns_mx_check(harness.ctx, domain_or_email="ceo@google.com")
    assert dns_res.ok is True
    assert dns_res.data["domain"] == "google.com"
    assert "google.com" in dns_res.text

    # 3. Regulatory Filings Search
    filings_res = company_filings_search(harness.ctx, query="Apple Inc")
    assert filings_res.ok is True
    assert len(filings_res.data) >= 1

    # 4. Crypto Market Pulse (Mock CoinGecko)
    mock_cg = {
        "bitcoin": {
            "usd": 68500.0,
            "inr": 5700000.0,
            "usd_24h_change": 3.45,
            "usd_24h_vol": 25000000000.0,
        }
    }
    mock_resp.read.return_value = json.dumps(mock_cg).encode("utf-8")
    crypto_res = crypto_market_pulse(harness.ctx, coin_id="btc")
    assert crypto_res.ok is True
    assert crypto_res.data["usd"] == 68500.0
    assert crypto_res.data["change_24h"] == 3.45
    assert "BULLISH MOMENTUM" in crypto_res.text

    # 5. News RSS Aggregation (Mock RSS XML)
    sample_rss = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <title>Tech News</title>
        <item>
          <title>AI Advances Breakthrough in Silicon Photonics</title>
          <link>https://news.example.com/ai-silicon</link>
          <pubDate>Thu, 18 Sep 2026 06:00:00 GMT</pubDate>
          <source>Global Tech Review</source>
        </item>
      </channel>
    </rss>
    """
    mock_resp.read.return_value = sample_rss.encode("utf-8")
    news_res = news_rss_aggregate(harness.ctx, topic="artificial intelligence", limit=5)
    assert news_res.ok is True
    assert len(news_res.data) >= 1
    assert "AI Advances Breakthrough" in news_res.text

    # 6. Social Sentiment Scanner
    sent_res = social_sentiment_scan(harness.ctx, query="NVIDIA GPU")
    assert sent_res.ok is True
    assert "Sentiment Index:" in sent_res.text

    # 7. Hiring Signals Search
    hire_res = hiring_signals_search(harness.ctx, company_or_sector="OpenAI Robotics")
    assert hire_res.ok is True
    assert "Expansion Velocity:" in hire_res.text


def test_finance_extended_suite(harness, monkeypatch) -> None:
    # 1. Technical Indicators Calculation (MACD, Bollinger Bands)
    mock_chart = {
        "chart": {
            "result": [
                {
                    "indicators": {
                        "quote": [
                            {
                                "close": [100.0 + (i * 1.2) for i in range(35)],
                            }
                        ]
                    }
                }
            ]
        }
    }
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(mock_chart).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = False
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=0: mock_resp)

    tech_res = technical_indicators_calc(harness.ctx, symbol="NVDA")
    assert tech_res.ok is True
    assert "Bollinger Bands" in tech_res.text
    assert "MACD" in tech_res.text
    assert tech_res.data["bollinger"]["upper"] > tech_res.data["bollinger"]["lower"]
    assert "macd" in tech_res.data

    # 2. Candlestick Pattern Detection
    candle_res = candlestick_pattern_detect(harness.ctx, symbol="NVDA")
    assert candle_res.ok is True
    assert "Candlestick Analysis" in candle_res.text
    assert len(candle_res.data["patterns"]) >= 1

    # 3. Portfolio Risk & VaR Calculation
    # Execute a sample trade so portfolio has holdings
    harness.memory.record_trade("AAPL", "buy", 20, 150.0)
    risk_res = portfolio_risk_calc(harness.ctx)
    assert risk_res.ok is True
    assert "PORTFOLIO RISK" in risk_res.text
    assert risk_res.data["total_value"] > 0
    assert "var_95_usd" in risk_res.data
    assert "sharpe_ratio" in risk_res.data

    # 4. Portfolio Rebalancing
    rebal_res = portfolio_rebalance(harness.ctx, target_weights="equities:70,cash:30")
    assert rebal_res.ok is True
    assert "Rebalancing Plan" in rebal_res.text
    assert len(rebal_res.data["recommendations"]) >= 1

    # 5. Company Fundamentals
    mock_fund = {
        "chart": {
            "result": [
                {
                    "meta": {
                        "regularMarketPrice": 220.50,
                        "currency": "USD",
                        "fiftyTwoWeekHigh": 235.0,
                        "fiftyTwoWeekLow": 165.0,
                        "marketCap": 3400000000000,
                        "instrumentType": "EQUITY",
                        "exchangeName": "NASDAQ",
                    }
                }
            ]
        }
    }
    mock_resp.read.return_value = json.dumps(mock_fund).encode("utf-8")
    fund_res = company_fundamentals(harness.ctx, symbol="AAPL")
    assert fund_res.ok is True
    assert "Market Cap:" in fund_res.text
    assert fund_res.data["price"] == 220.50
    assert fund_res.data["52w_high"] == 235.0


def test_productivity_and_automation_suite(harness, tmp_path) -> None:
    # 1. Document Text Extraction
    sample_file = tmp_path / "summary.md"
    sample_file.write_text("# Project Roadmap\n\nDeliver autonomous agent capabilities.", encoding="utf-8")
    extract_res = document_extract_text(harness.ctx, file_path=str(sample_file))
    assert extract_res.ok is True
    assert "Project Roadmap" in extract_res.text

    # CSV extract
    csv_file = tmp_path / "leads.csv"
    csv_file.write_text("name,company,email\nShiv,Swachta,shiv@swachta.in\n", encoding="utf-8")
    extract_csv = document_extract_text(harness.ctx, file_path=str(csv_file))
    assert extract_csv.ok is True
    assert "CSV Table" in extract_csv.text

    # 2. Document Invoice Generation
    inv_res = document_generate_invoice(
        harness.ctx,
        client_name="Global Logistics Corp",
        items="AI Autonomous Dispatch:1:3500;Fleet Optimization:2:1200",
        invoice_num="INV-2026-9901",
        currency="USD",
        tax_rate=10.0,
    )
    assert inv_res.ok is True
    assert inv_res.data["total"] == 6490.0  # (3500 + 2400) * 1.10 = 6490.0
    assert "INV-2026-9901" in inv_res.text

    # Verify invoice in database
    db_invoices = harness.memory.list_invoices(status="issued")
    assert len(db_invoices) >= 1
    assert db_invoices[0]["invoice_num"] == "INV-2026-9901"

    # 3. Data Validation & Cleansing
    dirty_csv = "id,name,role\n1,Alice,Dev\n2,Bob,Ops\n1,Alice,Dev\n3,Charlie,Lead\n4,Malformed\n"
    clean_res = data_validate_clean(harness.ctx, content=dirty_csv, format="csv")
    assert clean_res.ok is True
    assert clean_res.data["duplicates"] == 1
    assert clean_res.data["malformed"] == 1
    assert clean_res.data["cleaned_rows"] == 4  # Header + 3 unique rows

    # 4. Expense Tracking
    exp_res = expense_track(
        harness.ctx,
        category="cloud",
        amount=450.0,
        vendor="RunPod Compute",
        description="Dedicated GPU instance for local inference",
    )
    assert exp_res.ok is True
    assert exp_res.data["expense_id"] > 0
    assert "RunPod Compute" in exp_res.text

    exp_res2 = expense_track(
        harness.ctx,
        category="cloud",
        amount=150.0,
        vendor="Cloudflare",
        description="Workers and tunnels",
    )
    assert exp_res2.ok is True

    summary = harness.memory.get_expense_summary()
    assert summary["grand_total"] == 600.0
    assert summary["by_category"]["cloud"]["total"] == 600.0

    # 5. Cron Task Scheduling
    cron_res = cron_task_schedule(
        harness.ctx,
        name="daily_market_pulse_cron",
        interval_minutes=60,
        tool_name="daily_executive_briefing",
        args=json.dumps({"sym": "^NSEI"}),
    )
    assert cron_res.ok is True
    assert cron_res.data["name"] == "daily_market_pulse_cron"

    active_crons = harness.memory.list_scheduled_crons()
    assert len(active_crons) >= 1
    assert active_crons[0]["name"] == "daily_market_pulse_cron"


def test_intelligence_and_nlp_suite(harness) -> None:
    # 1. NLP Sentiment Analysis
    pos_res = nlp_sentiment_analyze(harness.ctx, text="We achieved exceptional profit, massive revenue growth, and wonderful efficiency!")
    assert pos_res.ok is True
    assert pos_res.data["classification"] == "POSITIVE"
    assert pos_res.data["score"] > 0.5

    neg_res = nlp_sentiment_analyze(harness.ctx, text="Severe system failure, terrible latency, and horrible security vulnerability.")
    assert neg_res.ok is True
    assert neg_res.data["classification"] == "NEGATIVE"
    assert neg_res.data["score"] < -0.5

    # 2. Named Entity Extraction
    text_sample = "Contact director Vikram Sharma at vikram@robotics.in or call (555) 234-5678. Budget approved is $250,000 on March 25, 2026."
    ent_res = nlp_extract_entities(harness.ctx, text=text_sample)
    assert ent_res.ok is True
    assert "vikram@robotics.in" in ent_res.data["emails"]
    assert any("$250,000" in m for m in ent_res.data["monetary_amounts"])
    assert len(ent_res.data["dates"]) >= 1

    # 3. Descriptive Statistical Analysis
    stats_res = dataset_stats_analyze(harness.ctx, data_values="10, 20, 30, 40, 50, 60, 70, 80, 90, 100")
    assert stats_res.ok is True
    assert stats_res.data["mean"] == 55.0
    assert stats_res.data["median"] == 55.0
    assert stats_res.data["sample_size"] == 10

    # 4. Anomaly Detection
    # 10, 11, 10, 12, 10, 100 (extreme outlier)
    anomaly_res = anomaly_detect(harness.ctx, values="10, 11, 10, 12, 10, 11, 10, 95", sensitivity=1.8)
    assert anomaly_res.ok is True
    assert anomaly_res.data["anomalies_count"] >= 1
    assert anomaly_res.data["anomalies"][0]["value"] == 95.0

    # 5. Autonomous Learning Analytics
    # Log a few executions
    harness.memory.log_execution(
        goal="Extract filings",
        tools_used="company_filings_search",
        outcome="success",
        result_quality=9,
        lessons="SEC EDGAR links resolved cleanly",
        adjustments="None",
    )
    harness.memory.log_execution(
        goal="Calculate risk",
        tools_used="portfolio_risk_calc",
        outcome="success",
        result_quality=10,
        lessons="VaR computation matched target",
        adjustments="None",
    )

    analytics_res = learning_analytics(harness.ctx)
    assert analytics_res.ok is True
    assert "AUTONOMOUS LEARNING" in analytics_res.text
    assert analytics_res.data["total_tasks"] >= 2
    assert analytics_res.data["win_rate_pct"] == 100.0
    assert analytics_res.data["hours_saved"] > 0
