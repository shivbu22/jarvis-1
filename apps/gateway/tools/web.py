"""Web tools: search, news, weather. All keyless.

Search uses the `ddgs` package when importable and falls back to a direct scrape
of DuckDuckGo's HTML endpoint, so a dependency problem degrades result quality
instead of removing the capability. Weather uses Open-Meteo, which needs no key
and no signup.

These tools are read-only, so they are tier ``auto`` -- but note they are the
front door for prompt injection: text arriving from a page is untrusted, and the
agent's system prompt tells the model that anything it reads is data, not
instructions. The real enforcement is in policy.py.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import unquote

import httpx

from .registry import ToolContext, ToolOutput, ToolSpec, object_schema, param

logger = logging.getLogger(__name__)

USER_AGENT = "Jarvis/0.1 (local assistant; +https://localhost)"

WMO_CODES: dict[int, str] = {
    0: "clear", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog", 51: "light drizzle", 53: "drizzle",
    55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain",
    67: "freezing rain", 71: "light snow", 73: "snow", 75: "heavy snow",
    77: "snow grains", 80: "light rain showers", 81: "rain showers",
    82: "violent rain showers", 85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with hail",
}


# --------------------------------------------------------------------------
# search
# --------------------------------------------------------------------------


def web_search(ctx: ToolContext, query: str, max_results: int = 5) -> ToolOutput:
    max_results = max(1, min(int(max_results or 5), 10))
    results, engine = _search(query, max_results)
    if not results:
        return ToolOutput(
            text=f"Nothing useful came back for '{query}'. Try different words.",
            data={"query": query, "results": [], "engine": engine},
        )
    lines = [f"Results for '{query}':"]
    for index, item in enumerate(results, 1):
        lines.append(f"{index}. {item['title']} -- {item['snippet']} ({item['url']})")
    return ToolOutput(
        text="\n".join(lines),
        data={"query": query, "engine": engine, "results": results},
    )


def _search(query: str, max_results: int) -> tuple[list[dict[str, str]], str]:
    try:
        from ddgs import DDGS  # preferred: handles parsing and rate limits

        with DDGS() as client:
            raw = list(client.text(query, max_results=max_results))
        results = [
            {
                "title": str(item.get("title", "")).strip(),
                "url": str(item.get("href") or item.get("url") or "").strip(),
                "snippet": str(item.get("body", "")).strip(),
            }
            for item in raw
        ]
        return [r for r in results if r["title"]], "ddgs"
    except Exception as exc:
        logger.info("ddgs unavailable (%s); scraping DuckDuckGo directly", exc)

    return _scrape_search(query, max_results), "html-scrape"


def _scrape_search(query: str, max_results: int) -> list[dict[str, str]]:
    try:
        response = httpx.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers={"User-Agent": USER_AGENT},
            timeout=15.0,
            follow_redirects=True,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.info("duckduckgo scrape failed: %s", exc)
        return []

    html = response.text
    titles = re.findall(r'result__a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.DOTALL)
    snippets = re.findall(r'result__snippet[^>]*>(.*?)</a>', html, re.DOTALL)
    results: list[dict[str, str]] = []
    for index, (href, title) in enumerate(titles[:max_results]):
        snippet = snippets[index] if index < len(snippets) else ""
        results.append(
            {
                "title": _strip_tags(title),
                "url": _clean_ddg_url(href),
                "snippet": _strip_tags(snippet),
            }
        )
    return results


def _clean_ddg_url(href: str) -> str:
    match = re.search(r"uddg=([^&]+)", href)
    return unquote(match.group(1)) if match else href


def _strip_tags(value: str) -> str:
    return re.sub(r"<[^>]+>", "", value).replace("&amp;", "&").strip()


# --------------------------------------------------------------------------
# news
# --------------------------------------------------------------------------


def web_news(ctx: ToolContext, query: str = "", max_results: int = 5) -> ToolOutput:
    max_results = max(1, min(int(max_results or 5), 10))
    try:
        from ddgs import DDGS

        with DDGS() as client:
            raw = list(client.news(query or "top stories", max_results=max_results))
        items = [
            {
                "title": str(item.get("title", "")).strip(),
                "url": str(item.get("url") or item.get("href") or "").strip(),
                "source": str(item.get("source", "")).strip(),
                "date": str(item.get("date", "")).strip(),
            }
            for item in raw
        ]
    except Exception as exc:
        logger.info("news lookup failed: %s", exc)
        return ToolOutput(
            text="I could not reach a news source just now.",
            data={"items": []},
            ok=False,
        )

    if not items:
        return ToolOutput(text=f"No headlines for '{query}'.", data={"items": []})

    lines = [f"Headlines for '{query}':"] if query else ["Top headlines:"]
    for index, item in enumerate(items, 1):
        source = f" -- {item['source']}" if item["source"] else ""
        lines.append(f"{index}. {item['title']}{source}")
    return ToolOutput(text="\n".join(lines), data={"query": query, "items": items})


# --------------------------------------------------------------------------
# weather
# --------------------------------------------------------------------------


def get_weather(ctx: ToolContext, city: str) -> ToolOutput:
    try:
        place = _geocode(city)
    except httpx.HTTPError as exc:
        return ToolOutput(text=f"Weather lookup failed: {exc}", ok=False)

    if place is None:
        return ToolOutput(text=f"I could not find a place called '{city}'.", ok=False)

    try:
        forecast = _forecast(place["latitude"], place["longitude"])
    except httpx.HTTPError as exc:
        return ToolOutput(text=f"Weather lookup failed: {exc}", ok=False)

    current = forecast.get("current") or {}
    daily = forecast.get("daily") or {}
    condition = WMO_CODES.get(int(current.get("weather_code") or -1), "unclear")
    place_name = ", ".join(
        part for part in (place.get("name"), place.get("country")) if part
    )

    bits = [f"{place_name}: {condition}"]
    if current.get("temperature_2m") is not None:
        bits.append(f"{round(float(current['temperature_2m']))} degrees")
    if current.get("apparent_temperature") is not None:
        bits.append(f"feels like {round(float(current['apparent_temperature']))}")
    if current.get("relative_humidity_2m") is not None:
        bits.append(f"humidity {round(float(current['relative_humidity_2m']))} percent")
    if current.get("wind_speed_10m") is not None:
        bits.append(f"wind {round(float(current['wind_speed_10m']))} kilometres per hour")

    text = ", ".join(bits) + "."
    if daily.get("temperature_2m_max") and daily.get("temperature_2m_min"):
        high = round(float(daily["temperature_2m_max"][0]))
        low = round(float(daily["temperature_2m_min"][0]))
        text += f" Today between {low} and {high}."

    return ToolOutput(
        text=text,
        data={
            "place": place_name,
            "condition": condition,
            "current": current,
            "daily": daily,
        },
    )


def _geocode(city: str) -> dict[str, Any] | None:
    response = httpx.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": city, "count": 1, "language": "en", "format": "json"},
        timeout=15.0,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    results = response.json().get("results") or []
    return results[0] if results else None


def _forecast(latitude: float, longitude: float) -> dict[str, Any]:
    response = httpx.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": latitude,
            "longitude": longitude,
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,"
            "weather_code,wind_speed_10m",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "timezone": "auto",
            "forecast_days": 2,
        },
        timeout=15.0,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    return response.json()


# --------------------------------------------------------------------------
# specs
# --------------------------------------------------------------------------


def build_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="web_search",
            description=(
                "Search the web. Use for facts about the world, current events, prices, "
                "documentation, or anything you are unsure of. Returns titles, snippets "
                "and links."
            ),
            parameters=object_schema(
                {
                    "query": param("query", "string", "The search query, phrased as keywords."),
                    "max_results": param(
                        "max_results", "integer", "How many results to return (1-10).",
                        default=5, minimum=1, maximum=10,
                    ),
                },
                ["query"],
            ),
            handler=web_search,
            executing=False,  # no interpreter ever sees these bytes
            category="web",
        ),
        ToolSpec(
            name="web_news",
            description="Read current news headlines, optionally on a topic.",
            parameters=object_schema(
                {
                    "query": param(
                        "query", "string", "Topic, e.g. 'AI chips'. Empty for top stories.",
                        default="",
                    ),
                    "max_results": param(
                        "max_results", "integer", "How many headlines (1-10).",
                        default=5, minimum=1, maximum=10,
                    ),
                },
                [],
            ),
            handler=web_news,
            executing=False,
            category="web",
        ),
        ToolSpec(
            name="get_weather",
            description="Current weather and today's range for a city.",
            parameters=object_schema(
                {"city": param("city", "string", "City name, e.g. 'Mumbai'.")},
                ["city"],
            ),
            handler=get_weather,
            executing=False,
            category="web",
        ),
    ]
