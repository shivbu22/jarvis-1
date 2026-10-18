"""Market Intelligence & Paper Trading Tools for J.A.R.V.I.S.

Provides zero-key real-time market data across NSE/BSE, US Equities, and Crypto,
technical indicator derivation (RSI, SMA), paper trading execution, and portfolio tracking.
"""

from __future__ import annotations

import json
import logging
import urllib.request

from .registry import ToolContext, ToolOutput, ToolSpec, object_schema, param

logger = logging.getLogger(__name__)


def _calculate_rsi(prices: list[float], period: int = 14) -> float | None:
    """Calculate Relative Strength Index (RSI) over closing prices."""
    if len(prices) < period + 1:
        return None
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


def fetch_market_data(ctx: ToolContext, symbol: str, interval: str = "1d") -> ToolOutput:
    """Fetch current market price, technical indicators (RSI, SMAs), and trend signals."""
    sym = symbol.strip().upper()
    # Canonical aliases
    if sym in {"NIFTY", "NIFTY50", "^NSEI"}:
        sym = "^NSEI"
    elif sym in {"SENSEX", "^BSESN"}:
        sym = "^BSESN"
    elif sym in {"BTC", "BITCOIN"}:
        sym = "BTC-USD"
    elif sym in {"ETH", "ETHEREUM"}:
        sym = "ETH-USD"

    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=1mo&interval=1d"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        result = data.get("chart", {}).get("result", [])
        if not result:
            return ToolOutput(text=f"No market data found for symbol '{sym}'.", ok=False)

        meta = result[0].get("meta", {})
        indicators = result[0].get("indicators", {}).get("quote", [{}])[0]
        closes = [float(p) for p in indicators.get("close", []) if p is not None]

        current_price = float(meta.get("regularMarketPrice", closes[-1] if closes else 0.0))
        prev_close = float(meta.get("previousClose", closes[-2] if len(closes) > 1 else current_price))
        currency = meta.get("currency", "USD")

        chg = current_price - prev_close
        chg_pct = (chg / prev_close * 100.0) if prev_close else 0.0

        rsi = _calculate_rsi(closes, 14) if len(closes) >= 15 else None
        sma_20 = round(sum(closes[-20:]) / 20, 2) if len(closes) >= 20 else None

        signal_notes = []
        if rsi is not None:
            if rsi < 30:
                signal_notes.append(f"RSI={rsi} [Oversold / Bullish]")
            elif rsi > 70:
                signal_notes.append(f"RSI={rsi} [Overbought / Bearish]")
            else:
                signal_notes.append(f"RSI={rsi} [Neutral]")

        if sma_20 is not None:
            trend = "Above SMA-20 (Short-term Bullish)" if current_price > sma_20 else "Below SMA-20 (Short-term Bearish)"
            signal_notes.append(trend)

        signals_str = " | ".join(signal_notes) if signal_notes else "Sufficient history for indicators pending"

        text_report = (
            f"Market Quote: {sym}\n"
            f"Price: {current_price:,.2f} {currency} ({'+' if chg >= 0 else ''}{chg:.2f}, {chg_pct:+.2f}%)\n"
            f"52-Week High/Low: {meta.get('fiftyTwoWeekHigh', 'N/A')} / {meta.get('fiftyTwoWeekLow', 'N/A')}\n"
            f"Technicals: {signals_str}"
        )

        return ToolOutput(
            text=text_report,
            data={
                "symbol": sym,
                "price": current_price,
                "currency": currency,
                "change_percent": chg_pct,
                "rsi": rsi,
                "sma_20": sma_20,
                "signal": signals_str,
            },
            ok=True,
        )
    except Exception as exc:
        logger.warning("failed to fetch market quote for %s: %s", sym, exc)
        return ToolOutput(text=f"Market data fetch failed for '{sym}': {exc}", ok=False)


def execute_trade(
    ctx: ToolContext,
    symbol: str,
    quantity: float,
    side: str,
    price: float = 0.0,
    trade_type: str = "market",
    advisor_notes: str = "",
) -> ToolOutput:
    """Place a paper trade (buy/sell) with position tracking and P&L accounting."""
    sym = symbol.strip().upper()
    qty = float(quantity)
    side_clean = side.strip().lower()

    if qty <= 0:
        return ToolOutput(text="Trade quantity must be greater than zero.", ok=False)
    if side_clean not in {"buy", "sell"}:
        return ToolOutput(text="Trade side must be 'buy' or 'sell'.", ok=False)

    exec_price = float(price)
    if exec_price <= 0:
        quote = fetch_market_data(ctx, sym)
        if quote.ok and isinstance(quote.data, dict) and "price" in quote.data:
            exec_price = float(quote.data["price"])
        else:
            return ToolOutput(text=f"Could not retrieve current market price for {sym}. Please specify execution price.", ok=False)

    res = ctx.memory.record_trade(
        symbol=sym,
        side=side_clean,
        quantity=qty,
        price=exec_price,
        trade_type=trade_type,
        advisor_notes=advisor_notes,
    )

    if not res.get("ok"):
        return ToolOutput(text=f"Trade execution failed: {res.get('error')}", ok=False)

    total_val = qty * exec_price
    summary = (
        f"Trade Executed: {side_clean.upper()} {qty} {sym} @ ${exec_price:,.2f} "
        f"(Total: ${total_val:,.2f})\n"
        f"Position: {res.get('position_qty')} {sym} | Remaining Cash: ${res.get('cash_remaining'):,.2f}"
    )
    if res.get("pnl"):
        summary += f" | Realized P&L: ${res.get('pnl'):+,.2f}"
    if advisor_notes.strip():
        summary += f"\nAdvisor Note: {advisor_notes.strip()}"

    return ToolOutput(text=summary, data=res, ok=True)


def check_portfolio(ctx: ToolContext) -> ToolOutput:
    """Inspect active paper holdings, cash balance, and trade ledger."""
    port = ctx.memory.get_portfolio()
    positions = port.get("positions", [])
    trades = port.get("recent_trades", [])

    lines = [
        "Paper Portfolio Summary:",
        f"Available Cash: ${port.get('cash_balance', 0.0):,.2f}",
        f"Cost Basis in Holdings: ${port.get('cost_basis', 0.0):,.2f}",
        f"Total Account Value: ${port.get('total_portfolio_value', 0.0):,.2f}",
        f"Open Positions ({len(positions)}):",
    ]

    if not positions:
        lines.append("  (No open positions)")
    else:
        for p in positions:
            lines.append(f"  * {p['symbol']}: {p['quantity']} units @ avg ${p['avg_price']:,.2f} (Total: ${p['quantity'] * p['avg_price']:,.2f})")

    if trades:
        lines.append("\nRecent Trades:")
        for t in trades[:5]:
            lines.append(f"  * [{t['side'].upper()}] {t['quantity']} {t['symbol']} @ ${t['price']:,.2f} (P&L: ${t.get('pnl', 0.0):+,.2f})")

    return ToolOutput(text="\n".join(lines), data=port, ok=True)


def build_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="fetch_market_data",
            description="Fetch real-time stock/crypto quote, 24h change, and RSI/SMA technical indicators for NSE/BSE, US stocks, or crypto.",
            parameters=object_schema(
                properties={
                    "symbol": param("string", "Ticker symbol (e.g. ^NSEI, RELIANCE.NS, TCS.NS, AAPL, NVDA, BTC-USD)"),
                    "interval": param("string", "Data resolution ('1d', '1h')", default="1d"),
                },
                required=["symbol"],
            ),
            handler=fetch_market_data,
            executing=False,
            category="finance",
        ),
        ToolSpec(
            name="execute_trade",
            description="Execute a simulated paper trade (buy or sell) with automated cash balancing, position updates, and P&L tracking.",
            parameters=object_schema(
                properties={
                    "symbol": param("string", "Stock or crypto symbol to trade (e.g. AAPL, RELIANCE.NS, BTC-USD)"),
                    "quantity": param("number", "Number of shares or coins to buy/sell"),
                    "side": param("string", "Order direction: 'buy' or 'sell'"),
                    "price": param("number", "Execution price. If 0 or omitted, live market price is used.", default=0.0),
                    "trade_type": param("string", "Order type ('market', 'limit')", default="market"),
                    "advisor_notes": param("string", "Reasoning or advisor consensus notes behind the trade.", default=""),
                },
                required=["symbol", "quantity", "side"],
            ),
            handler=execute_trade,
            executing=True,
            category="finance",
        ),
        ToolSpec(
            name="check_portfolio",
            description="View paper trading account status, open positions, cash balance, and recent trade history.",
            parameters=object_schema(properties={}),
            handler=check_portfolio,
            executing=False,
            category="finance",
        ),
    ]
