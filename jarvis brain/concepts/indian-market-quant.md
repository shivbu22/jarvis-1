---
title: Indian Market Quantitative Trading
created: 2026-09-18
updated: 2026-09-18
type: concept
tags: [trading, quant, indian-markets, nse, bse]
confidence: high
sources: [entities/user-profile.md]
---

# Indian Market Quantitative Trading

## Market Structure
- **Exchanges:** National Stock Exchange (NSE), Bombay Stock Exchange (BSE).
- **Segments:** Equities (Cash), Futures & Options (F&O), Currency Derivatives (CDS), Commodities (MCX).
- **Settlement & Trading Hours:** 09:15 to 15:30 IST; T+1 rolling settlement.

## Execution Order Mechanics
- **Product Types:**
  - `CNC` (Cash and Carry): Full-margin equity delivery.
  - `MIS` (Margin Intraday Square-off): Leveraged intraday equity and derivatives.
  - `NRML` (Normal): Standard overnight derivative positions.
- **Order Types:** `MARKET`, `LIMIT`, `SL` (Stop Loss Limit), `SL-M` (Stop Loss Market).

## Broker API Ecosystem
- **Zerodha Kite Connect:** Python API (`kiteconnect`), WebSocket tick streaming (`kiteconnect.ticker`).
- **Angel One SmartAPI:** REST API and WebSocket SmartStream for live market depth.
- **DhanHQ API:** High-speed order routing and live market feed.
- **Upstox & Fyers:** V2 APIs for multi-leg option strategies and Greeks analysis.
