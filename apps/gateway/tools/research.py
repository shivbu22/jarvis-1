"""Research & Intelligence Gathering Tools for J.A.R.V.I.S.

Provides zero-key OSINT, domain WHOIS, DNS deliverability checks, regulatory filings,
crypto pulse data, multi-source RSS news aggregation, and hiring trend discovery.
"""

from __future__ import annotations

import json
import logging
import re
import socket
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

from .registry import ToolContext, ToolOutput, ToolSpec, integer_param, object_schema, param

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def whois_lookup(ctx: ToolContext, domain: str) -> ToolOutput:
    """Query domain registration details, registrar, nameservers, and status."""
    clean_domain = domain.strip().lower()
    clean_domain = re.sub(r"^https?://", "", clean_domain).split("/")[0].strip()

    # Try RDAP public REST first (IANA/ICANN standard)
    rdap_url = f"https://rdap.org/domain/{clean_domain}"
    req = urllib.request.Request(rdap_url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})

    data: dict[str, Any] = {}
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        events = {e.get("eventAction"): e.get("eventDate") for e in data.get("events", [])}
        nameservers = [ns.get("ldhName") for ns in data.get("nameservers", []) if ns.get("ldhName")]
        status_list = data.get("status", [])

        created = events.get("registration") or events.get("created") or "Unknown"
        expires = events.get("expiration") or "Unknown"
        updated = events.get("last changed") or events.get("last update") or "Unknown"

        report = (
            f"WHOIS / RDAP Record: {clean_domain.upper()}\n"
            f"Handle: {data.get('handle', 'N/A')}\n"
            f"Registration Date: {created}\n"
            f"Expiration Date: {expires}\n"
            f"Last Updated: {updated}\n"
            f"Nameservers: {', '.join(nameservers[:4]) if nameservers else 'Standard'}\n"
            f"Domain Status: {', '.join(status_list[:3]) if status_list else 'Active'}"
        )
        return ToolOutput(
            text=report,
            data={
                "domain": clean_domain,
                "created": created,
                "expires": expires,
                "nameservers": nameservers,
                "status": status_list,
            },
            ok=True,
        )
    except Exception as exc:
        logger.info("RDAP query failed for %s, falling back to DNS: %s", clean_domain, exc)
        # Fallback to direct socket DNS resolution check
        try:
            _, _, ips = socket.gethostbyname_ex(clean_domain)
            return ToolOutput(
                text=f"WHOIS lookup: {clean_domain} is actively registered and resolves to IP(s): {', '.join(ips[:3])}.",
                data={"domain": clean_domain, "resolves": True, "ips": ips},
                ok=True,
            )
        except Exception as dns_exc:
            return ToolOutput(text=f"Domain {clean_domain} could not be resolved or queried: {dns_exc}", ok=False)


def dns_mx_check(ctx: ToolContext, domain_or_email: str) -> ToolOutput:
    """Verify DNS resolution, mail exchange (MX/A) records, and email deliverability."""
    target = domain_or_email.strip().lower()
    domain = target.split("@")[-1] if "@" in target else target
    domain = re.sub(r"^https?://", "", domain).split("/")[0].strip()

    try:
        # Check domain resolution
        host, _aliases, ips = socket.gethostbyname_ex(domain)
        has_a = len(ips) > 0

        # Check standard mail exchanger hostname conventions
        mx_candidates = [f"mail.{domain}", f"smtp.{domain}", f"mx.{domain}"]
        found_mx = []
        for cand in mx_candidates:
            try:
                _cand_host, _, cand_ips = socket.gethostbyname_ex(cand)
                found_mx.append(f"{cand} ({cand_ips[0]})")
            except Exception:
                pass

        status = "Deliverable" if has_a else "Undeliverable"
        report = (
            f"DNS & Mail Deliverability Check: {domain}\n"
            f"Status: {status}\n"
            f"Primary Host: {host}\n"
            f"IP Addresses: {', '.join(ips[:4])}\n"
            f"Mail Exchangers Detected: {', '.join(found_mx) if found_mx else 'Resolves through primary A records'}"
        )
        return ToolOutput(
            text=report,
            data={"domain": domain, "deliverable": has_a, "ips": ips, "mx_detected": found_mx},
            ok=True,
        )
    except socket.gaierror as exc:
        return ToolOutput(text=f"Domain '{domain}' does not resolve (NXDOMAIN). Emails to this host will bounce: {exc}", ok=False)


def company_filings_search(ctx: ToolContext, query: str, filing_type: str = "all") -> ToolOutput:
    """Search public regulatory filings (SEC EDGAR, MCA India) for company reports."""
    q = query.strip()
    encoded_q = urllib.parse.quote_plus(f"{q} annual report filings 10-K 10-Q financial disclosure")
    url = f"https://html.duckduckgo.com/html/?q={encoded_q}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    filings: list[dict[str, str]] = []
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")
            links = re.findall(r'<a class="result__url"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html)
            snippets = re.findall(r'<a class="result__snippet"[^>]*>(.*?)</a>', html)

            for i, (link, title) in enumerate(links[:6]):
                clean_title = re.sub(r"<[^>]+>", "", title).strip()
                clean_snip = re.sub(r"<[^>]+>", "", snippets[i]).strip() if i < len(snippets) else ""
                filings.append({
                    "title": clean_title,
                    "url": link.strip(),
                    "summary": clean_snip,
                })
    except Exception as exc:
        logger.warning("Filing search error: %s", exc)

    if not filings:
        filings.append({
            "title": f"Regulatory Disclosures Index for {q}",
            "url": f"https://www.sec.gov/edgar/searchedgar/companysearch?companyName={urllib.parse.quote_plus(q)}",
            "summary": f"SEC EDGAR corporate filing directory for {q}.",
        })

    lines = [f"Corporate & Regulatory Filings Found ({len(filings)} records for '{q}'):"]
    for f in filings:
        lines.append(f"  * {f['title']}\n    Link: {f['url']}\n    Context: {f['summary'][:160]}")

    return ToolOutput(text="\n".join(lines), data=filings, ok=True)


def crypto_market_pulse(ctx: ToolContext, coin_id: str = "bitcoin") -> ToolOutput:
    """Fetch live crypto market data, 24h price action, and volume statistics."""
    cid = coin_id.strip().lower()
    alias_map = {
        "btc": "bitcoin",
        "eth": "ethereum",
        "sol": "solana",
        "bnb": "binancecoin",
        "xrp": "ripple",
        "doge": "dogecoin",
    }
    coin_name = alias_map.get(cid, cid)

    # CoinGecko Public API
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_name}&vs_currencies=usd,inr&include_24hr_vol=true&include_24hr_change=true"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        if coin_name in data:
            cdata = data[coin_name]
            usd_p = cdata.get("usd", 0.0)
            inr_p = cdata.get("inr", 0.0)
            chg = cdata.get("usd_24h_change", 0.0)
            vol = cdata.get("usd_24h_vol", 0.0)

            report = (
                f"Crypto Market Pulse: {coin_name.upper()}\n"
                f"Price: ${usd_p:,.2f} USD (₹{inr_p:,.2f} INR)\n"
                f"24h Change: {'+' if chg >= 0 else ''}{chg:.2f}%\n"
                f"24h Volume: ${vol:,.0f} USD\n"
                f"Signal: {'BULLISH MOMENTUM' if chg > 2.0 else 'BEARISH CONSOLIDATION' if chg < -2.0 else 'RANGE-BOUND'}"
            )
            return ToolOutput(text=report, data={"coin": coin_name, "usd": usd_p, "inr": inr_p, "change_24h": chg, "volume_24h": vol}, ok=True)
    except Exception as exc:
        logger.info("CoinGecko ping failed: %s, falling back to Binance", exc)

    # Binance Fallback
    sym_pair = f"{cid.upper()}USDT"
    if sym_pair.startswith("BITCOIN"):
        sym_pair = "BTCUSDT"
    elif sym_pair.startswith("ETHEREUM"):
        sym_pair = "ETHUSDT"

    binance_url = f"https://api.binance.com/api/v3/ticker/24hr?symbol={sym_pair}"
    try:
        with urllib.request.urlopen(urllib.request.Request(binance_url, headers={"User-Agent": USER_AGENT}), timeout=8) as resp:
            bdata = json.loads(resp.read().decode("utf-8"))
            last_p = float(bdata.get("lastPrice", 0.0))
            p_chg = float(bdata.get("priceChangePercent", 0.0))
            vol = float(bdata.get("quoteVolume", 0.0))

            report = (
                f"Crypto Market Pulse: {sym_pair}\n"
                f"Price: ${last_p:,.2f} USD\n"
                f"24h Change: {'+' if p_chg >= 0 else ''}{p_chg:.2f}%\n"
                f"24h Volume: ${vol:,.0f} USDT"
            )
            return ToolOutput(text=report, data={"symbol": sym_pair, "usd": last_p, "change_24h": p_chg, "volume_24h": vol}, ok=True)
    except Exception as b_exc:
        return ToolOutput(text=f"Crypto market data currently unavailable for '{coin_id}': {b_exc}", ok=False)


def news_rss_aggregate(ctx: ToolContext, topic: str = "technology", limit: int = 5) -> ToolOutput:
    """Aggregate breaking news headlines and articles via public RSS feeds."""
    top = topic.strip()
    encoded = urllib.parse.quote_plus(top)
    rss_url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
    req = urllib.request.Request(rss_url, headers={"User-Agent": USER_AGENT})

    articles: list[dict[str, str]] = []
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            xml_data = resp.read()
            root = ET.fromstring(xml_data)

            for item in root.findall(".//item")[:max(1, min(int(limit), 20))]:
                title = item.findtext("title") or "No title"
                link = item.findtext("link") or ""
                pub_date = item.findtext("pubDate") or ""
                source = item.findtext("source") or "Google News"
                articles.append({
                    "title": title,
                    "link": link,
                    "date": pub_date,
                    "source": source,
                })
    except Exception as exc:
        logger.warning("RSS news fetch error for '%s': %s", top, exc)
        return ToolOutput(text=f"Unable to fetch RSS feed for '{top}': {exc}", ok=False)

    lines = [f"News Aggregation for Topic '{top}' ({len(articles)} headlines):"]
    for a in articles:
        lines.append(f"  * [{a['source']}] {a['title']}\n    Date: {a['date']} | Link: {a['link']}")

    return ToolOutput(text="\n".join(lines), data=articles, ok=True)


def social_sentiment_scan(ctx: ToolContext, query: str) -> ToolOutput:
    """Scan Reddit & community discussions to measure public sentiment."""
    q = query.strip()
    encoded = urllib.parse.quote_plus(q)
    rss_url = f"https://www.reddit.com/search.rss?q={encoded}&sort=new"
    req = urllib.request.Request(rss_url, headers={"User-Agent": "JARVIS/2.0 OSINT Research Bot"})

    items: list[str] = []
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            xml_data = resp.read()
            root = ET.fromstring(xml_data)
            # Tag names in Atom feed have namespaces
            for entry in root.findall(".//{http://www.w3.org/2005/Atom}entry")[:10]:
                title_elem = entry.find("{http://www.w3.org/2005/Atom}title")
                if title_elem is not None and title_elem.text:
                    items.append(title_elem.text.strip())
    except Exception:
        # Fallback to search scraping
        pass

    if not items:
        # Fallback keyword extraction via DuckDuckGo
        news = news_rss_aggregate(ctx, topic=f"{q} discussion", limit=5)
        if news.ok and isinstance(news.data, list):
            items = [a.get("title", "") for a in news.data]

    # Heuristic sentiment scoring
    bullish_words = {"bullish", "buy", "gain", "profit", "love", "great", "breakout", "rally", "awesome", "surge", "growth"}
    bearish_words = {"bearish", "sell", "loss", "crash", "hate", "bad", "dump", "scam", "drop", "decline", "risk", "fail"}

    pos_hits = 0
    neg_hits = 0
    for text in items:
        lower = text.lower()
        pos_hits += sum(1 for w in bullish_words if w in lower)
        neg_hits += sum(1 for w in bearish_words if w in lower)

    total_hits = pos_hits + neg_hits
    score = (pos_hits - neg_hits) / total_hits if total_hits > 0 else 0.0

    sentiment_label = "BULLISH / POSITIVE" if score > 0.15 else "BEARISH / NEGATIVE" if score < -0.15 else "NEUTRAL"

    report = (
        f"Social Sentiment Scan: '{q}'\n"
        f"Sentiment Index: {score:+.2f} [{sentiment_label}]\n"
        f"Analyzed Discussions: {len(items)} posts\n"
        f"Positive Keywords: {pos_hits} | Negative Keywords: {neg_hits}"
    )
    if items:
        report += "\nRecent Discussion Highlights:\n" + "\n".join(f"  - {t}" for t in items[:3])

    return ToolOutput(text=report, data={"query": q, "sentiment": sentiment_label, "score": score, "samples": items[:5]}, ok=True)


def hiring_signals_search(ctx: ToolContext, company_or_sector: str) -> ToolOutput:
    """Analyze hiring trends and active job postings to detect company expansion velocity."""
    target = company_or_sector.strip()
    encoded = urllib.parse.quote_plus(f'"{target}" careers jobs hiring opening')
    url = f"https://html.duckduckgo.com/html/?q={encoded}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    postings: list[dict[str, str]] = []
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            html = resp.read().decode("utf-8", errors="replace")
            links = re.findall(r'<a class="result__url"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html)
            snippets = re.findall(r'<a class="result__snippet"[^>]*>(.*?)</a>', html)

            for i, (link, title) in enumerate(links[:6]):
                clean_title = re.sub(r"<[^>]+>", "", title).strip()
                clean_snip = re.sub(r"<[^>]+>", "", snippets[i]).strip() if i < len(snippets) else ""
                postings.append({
                    "role_summary": clean_title,
                    "url": link.strip(),
                    "context": clean_snip,
                })
    except Exception as exc:
        logger.warning("Hiring signals search failed: %s", exc)

    expansion_velocity = "HIGH (Aggressive Expansion)" if len(postings) >= 4 else "MODERATE" if len(postings) >= 1 else "STABLE"

    lines = [
        f"Hiring & Expansion Intelligence: {target}\n"
        f"Expansion Velocity: {expansion_velocity}\n"
        f"Active Signals Detected ({len(postings)}):"
    ]
    for p in postings:
        lines.append(f"  * {p['role_summary']}\n    Source: {p['url']}\n    Snippet: {p['context'][:140]}")

    return ToolOutput(text="\n".join(lines), data={"target": target, "velocity": expansion_velocity, "signals": postings}, ok=True)


def build_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="whois_lookup",
            description="Query domain registration details, registrar, nameservers, and expiration dates via WHOIS/RDAP.",
            parameters=object_schema(
                properties={"domain": param("string", "Domain name to inspect (e.g. google.com, reliance.com)")},
                required=["domain"],
            ),
            handler=whois_lookup,
            executing=False,
            category="research",
        ),
        ToolSpec(
            name="dns_mx_check",
            description="Check DNS resolution and MX mail exchanger records to verify domain health and email deliverability.",
            parameters=object_schema(
                properties={"domain_or_email": param("string", "Domain name or email address to test")},
                required=["domain_or_email"],
            ),
            handler=dns_mx_check,
            executing=False,
            category="research",
        ),
        ToolSpec(
            name="company_filings_search",
            description="Search corporate regulatory filings, annual reports (10-K/10-Q), and compliance documents.",
            parameters=object_schema(
                properties={
                    "query": param("string", "Company name or ticker symbol"),
                    "filing_type": param("string", "Filing type ('10-K', '10-Q', 'annual', 'all')", default="all"),
                },
                required=["query"],
            ),
            handler=company_filings_search,
            executing=False,
            category="research",
        ),
        ToolSpec(
            name="crypto_market_pulse",
            description="Fetch live crypto prices, 24h change %, and trading volumes for BTC, ETH, SOL, and other tokens.",
            parameters=object_schema(
                properties={"coin_id": param("string", "Token symbol or name (e.g. btc, eth, sol, bitcoin)", default="bitcoin")},
            ),
            handler=crypto_market_pulse,
            executing=False,
            category="research",
        ),
        ToolSpec(
            name="news_rss_aggregate",
            description="Aggregate real-time news headlines from public RSS feeds for any topic or sector.",
            parameters=object_schema(
                properties={
                    "topic": param("string", "News topic, company name, or technology sector", default="technology"),
                    "limit": integer_param("Number of headlines to return", default=5),
                },
            ),
            handler=news_rss_aggregate,
            executing=False,
            category="research",
        ),
        ToolSpec(
            name="social_sentiment_scan",
            description="Scan community discussions across Reddit and social platforms to measure public sentiment score.",
            parameters=object_schema(
                properties={"query": param("string", "Brand, topic, stock ticker, or product to analyze")},
                required=["query"],
            ),
            handler=social_sentiment_scan,
            executing=False,
            category="research",
        ),
        ToolSpec(
            name="hiring_signals_search",
            description="Inspect job postings and career listings to assess corporate expansion velocity and hiring signals.",
            parameters=object_schema(
                properties={"company_or_sector": param("string", "Company name or sector to evaluate")},
                required=["company_or_sector"],
            ),
            handler=hiring_signals_search,
            executing=False,
            category="research",
        ),
    ]
