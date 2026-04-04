"""Pluggable web search backends.

Supports:
- **Brave Search API** — Independent index, ~1000 free queries/month, API key
- **SearXNG** — Self-hosted meta-search, free, no auth, aggregates 70+ engines

All backends use httpx (already a kaos-web dependency). No SDK packages needed.

Configuration via environment variables:
- ``KAOS_SEARCH_BACKEND``: ``"brave"`` or ``"searxng"`` (default: auto-detect)
- ``BRAVE_API_KEY``: API key for Brave Search
- ``SEARXNG_URL``: URL of SearXNG instance (e.g., ``http://localhost:8080``)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass(frozen=True, slots=True)
class SearchResult:
    """A single web search result."""

    title: str
    url: str
    snippet: str
    source: str = ""  # Which backend returned this result
    position: int = 0  # 1-based rank position
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Backend protocol
# ---------------------------------------------------------------------------


async def search_web(
    query: str,
    *,
    max_results: int = 10,
    backend: str | None = None,
) -> list[SearchResult]:
    """Execute a web search using the configured backend.

    Args:
        query: Search query string.
        max_results: Maximum number of results to return.
        backend: Force a specific backend (``"brave"``, ``"searxng"``).
            If None, auto-detects from environment variables.

    Returns:
        List of SearchResult objects.

    Raises:
        ValueError: If no backend is configured or available.
    """
    if not query or not query.strip():
        msg = "Search query must not be empty."
        raise ValueError(msg)

    resolved = backend or os.environ.get("KAOS_SEARCH_BACKEND", "").lower()

    if resolved == "brave" or (not resolved and os.environ.get("BRAVE_API_KEY")):
        return await _search_brave(query, max_results=max_results)

    if resolved == "searxng" or (not resolved and os.environ.get("SEARXNG_URL")):
        return await _search_searxng(query, max_results=max_results)

    # Try Brave first if key exists, then SearXNG
    if os.environ.get("BRAVE_API_KEY"):
        return await _search_brave(query, max_results=max_results)
    if os.environ.get("SEARXNG_URL"):
        return await _search_searxng(query, max_results=max_results)

    msg = (
        "No search backend configured. Set one of:\n"
        "  BRAVE_API_KEY=your_key  (Brave Search API — https://brave.com/search/api/)\n"
        "  SEARXNG_URL=http://localhost:8080  (self-hosted SearXNG instance)\n"
        "Or pass backend='brave' or backend='searxng' explicitly."
    )
    raise ValueError(msg)


# ---------------------------------------------------------------------------
# Brave Search API
# ---------------------------------------------------------------------------


async def _search_brave(
    query: str,
    *,
    max_results: int = 10,
) -> list[SearchResult]:
    """Search using the Brave Search API.

    Requires ``BRAVE_API_KEY`` environment variable.
    Free tier: ~1000 queries/month. Rate limit: 50 QPS.
    """
    api_key = os.environ.get("BRAVE_API_KEY", "")
    if not api_key:
        msg = (
            "BRAVE_API_KEY environment variable not set. "
            "Get a free API key at https://brave.com/search/api/"
        )
        raise ValueError(msg)

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": min(max_results, 20)},
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": api_key,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    results: list[SearchResult] = []
    for i, item in enumerate(data.get("web", {}).get("results", [])):
        results.append(
            SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("description", ""),
                source="brave",
                position=i + 1,
                extra={
                    k: v for k, v in item.items() if k not in ("title", "url", "description") and v
                },
            )
        )

    return results[:max_results]


# ---------------------------------------------------------------------------
# SearXNG (self-hosted)
# ---------------------------------------------------------------------------


async def _search_searxng(
    query: str,
    *,
    max_results: int = 10,
) -> list[SearchResult]:
    """Search using a SearXNG instance.

    Requires ``SEARXNG_URL`` environment variable.
    Self-hosted, free, aggregates 70+ search engines.
    JSON format must be enabled in SearXNG settings.yaml.
    """
    base_url = os.environ.get("SEARXNG_URL", "")
    if not base_url:
        msg = (
            "SEARXNG_URL environment variable not set. "
            "Deploy SearXNG: docker run -p 8080:8080 searxng/searxng"
        )
        raise ValueError(msg)

    search_url = f"{base_url.rstrip('/')}/search"

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            search_url,
            params={
                "q": query,
                "format": "json",
                "pageno": 1,
            },
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()

    results: list[SearchResult] = []
    for i, item in enumerate(data.get("results", [])):
        if i >= max_results:
            break
        results.append(
            SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("content", ""),
                source=f"searxng:{item.get('engine', 'unknown')}",
                position=i + 1,
                extra={
                    k: v
                    for k, v in item.items()
                    if k not in ("title", "url", "content", "engine") and v
                },
            )
        )

    return results
