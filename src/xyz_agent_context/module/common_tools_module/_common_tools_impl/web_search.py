"""
@file_name: web_search.py
@author: Bin Liang
@date: 2026-04-17
@description: DuckDuckGo-backed web search implementation

Runs a list of queries through the `ddgs` library in parallel worker threads
(DDGS is sync, so we wrap each call in asyncio.to_thread to avoid blocking
the event loop).

Isolation:
- No per-agent state. Pure function over a query list.
- One DDGS() context per query — the library is cheap to instantiate and
  individual sessions keep cookies isolated, reducing rate-limit collateral.
"""

import asyncio
from typing import Any

from loguru import logger

MAX_RESULTS_CAP = 10
DEFAULT_REGION = "wt-wt"  # worldwide, any language
DEFAULT_SAFESEARCH = "moderate"


def _search_sync(query: str, max_results: int) -> list[dict[str, Any]]:
    """Blocking DuckDuckGo text search. Run this under asyncio.to_thread."""
    from ddgs import DDGS

    with DDGS() as ddgs:
        raw = ddgs.text(
            query=query,
            region=DEFAULT_REGION,
            safesearch=DEFAULT_SAFESEARCH,
            max_results=max_results,
        )
        return list(raw or [])


async def search_many(queries: list[str], max_results_per_query: int) -> list[dict[str, Any]]:
    """Fan queries out in parallel, return per-query result bundles.

    Each bundle:
        {"query": str, "error": str | None, "results": [{"title", "url", "snippet"}...]}

    Never raises — errors are reported per query so one dead query doesn't
    take down the rest.
    """
    capped = max(1, min(int(max_results_per_query), MAX_RESULTS_CAP))
    cleaned = [q.strip() for q in queries if q and q.strip()]
    if not cleaned:
        return []

    async def _one(q: str) -> dict[str, Any]:
        try:
            raw = await asyncio.to_thread(_search_sync, q, capped)
            normalized = [
                {
                    "title": (r.get("title") or "").strip(),
                    "url": (r.get("href") or r.get("url") or "").strip(),
                    "snippet": (r.get("body") or r.get("snippet") or "").strip(),
                }
                for r in raw
            ]
            return {"query": q, "error": None, "results": normalized}
        except Exception as e:  # noqa: BLE001 — surface to caller, don't crash
            logger.warning(f"web_search query failed: {q!r} → {e}")
            return {"query": q, "error": str(e), "results": []}

    return await asyncio.gather(*(_one(q) for q in cleaned))


def format_results(bundles: list[dict[str, Any]]) -> str:
    """Render search bundles into a compact markdown block for the LLM."""
    if not bundles:
        return "No queries provided."

    lines: list[str] = []
    for idx, bundle in enumerate(bundles, start=1):
        lines.append(f"### Query {idx}: {bundle['query']}")
        if bundle["error"]:
            lines.append(f"_search error: {bundle['error']}_")
            lines.append("")
            continue
        if not bundle["results"]:
            lines.append("_no results_")
            lines.append("")
            continue
        for i, hit in enumerate(bundle["results"], start=1):
            title = hit["title"] or "(untitled)"
            url = hit["url"] or "(no url)"
            snippet = hit["snippet"] or "(no snippet)"
            lines.append(f"{i}. **{title}**")
            lines.append(f"   {url}")
            lines.append(f"   {snippet}")
        lines.append("")
    return "\n".join(lines).rstrip()
