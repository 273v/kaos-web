"""Pluggable web search backends — all via httpx, no SDK dependencies.

Supports:
- **SerpAPI** — Google results, 250 free/month, API key
- **DuckDuckGo** — Free, no auth, HTML scraping (rate-limited, no guarantees)
- **Exa** — Neural/semantic search, 1000 free/month, API key
- **Brave** — Independent index, ~1000 free/month, API key

Configuration via ``KaosWebSettings`` (see ``kaos_web.settings``):
- New env vars: ``KAOS_WEB_SEARCH_BACKEND``, ``KAOS_WEB_SERPAPI_API_KEY``, etc.
- Legacy env vars: ``KAOS_SEARCH_BACKEND``, ``SERPAPI_API_KEY``, ``EXA_API_KEY``,
  ``BRAVE_API_KEY`` (backward compatible)

Usage::

    from kaos_web.search import search_web

    results = await search_web("SEC 10-K filings Tesla", max_results=10)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from kaos_web.settings import KaosWebSettings


@dataclass(frozen=True, slots=True)
class SearchResult:
    """A single web search result."""

    title: str
    url: str
    snippet: str
    source: str = ""
    position: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_BACKENDS = ("serpapi", "duckduckgo", "exa", "brave")


def _resolve_settings(settings: KaosWebSettings | None = None) -> KaosWebSettings:
    """Resolve web settings (lazy import to avoid circular deps at module level)."""
    if settings is not None:
        return settings
    from kaos_web.settings import KaosWebSettings

    return KaosWebSettings()


async def search_web(
    query: str,
    *,
    max_results: int = 10,
    backend: str | None = None,
    settings: KaosWebSettings | None = None,
) -> list[SearchResult]:
    """Execute a web search.

    Args:
        query: Search query string.
        max_results: Max results (1-20).
        backend: Force a backend. If None, auto-detects from settings/env vars
            in order: SERPAPI → EXA → BRAVE → duckduckgo.
        settings: Optional ``KaosWebSettings`` instance. If None, one is created
            from environment variables.

    Returns:
        List of SearchResult.

    Raises:
        ValueError: If query is empty.
    """
    if not query or not query.strip():
        msg = "Search query must not be empty."
        raise ValueError(msg)

    s = _resolve_settings(settings)
    resolved = (backend or s.search_backend or "").lower()
    # 0.1.1: the literal string "auto" is treated as a synonym for the
    # auto-detect path (None / empty). LLMs (gpt-5.4-mini, Haiku 4.5)
    # frequently pass the value "auto" because the public MCP tool
    # description says "Default: auto-detect from env vars" — even
    # though "auto" was never a real enum value. Fixing the tool
    # description is necessary but not sufficient (training cutoff
    # propagation, copy-paste from docs, etc.), so the dispatcher
    # also recognizes the synonym deterministically. See #545.
    if resolved == "auto":
        resolved = ""  # fall through to auto-detect below

    if resolved:
        if resolved not in _BACKENDS:
            available = ", ".join(_BACKENDS)
            msg = f"Unknown search backend: {resolved!r}. Available: {available}"
            raise ValueError(msg)
        return await _dispatch(resolved, query, max_results, s)

    # Auto-detect: try backends with configured keys, fall back to DDG
    detected = s.detect_search_backend()
    return await _dispatch(detected, query, max_results, s)


async def _dispatch(
    backend: str, query: str, max_results: int, settings: KaosWebSettings
) -> list[SearchResult]:
    if backend == "serpapi":
        return await _search_serpapi(query, max_results=max_results, settings=settings)
    if backend == "duckduckgo":
        return await _search_duckduckgo(query, max_results=max_results, settings=settings)
    if backend == "exa":
        return await _search_exa(query, max_results=max_results, settings=settings)
    if backend == "brave":
        return await _search_brave(query, max_results=max_results, settings=settings)
    msg = f"Unknown search backend: {backend!r}. Available: {', '.join(_BACKENDS)}"
    raise ValueError(msg)


# ═══════════════════════════════════════════════════════════════════════════
# SerpAPI — Google results via REST API
# ═══════════════════════════════════════════════════════════════════════════


async def _search_serpapi(
    query: str,
    *,
    max_results: int = 10,
    settings: KaosWebSettings | None = None,
) -> list[SearchResult]:
    """Search via SerpAPI (Google results).

    Endpoint: GET https://serpapi.com/search
    Free tier: 250 searches/month
    """
    s = _resolve_settings(settings)
    api_key = s.get_search_api_key("serpapi") or ""
    if not api_key:
        msg = "SERPAPI_API_KEY not set. Get a key at https://serpapi.com/manage-api-key"
        raise ValueError(msg)

    async with httpx.AsyncClient(timeout=s.search_timeout) as client:
        resp = await client.get(
            "https://serpapi.com/search",
            params={
                "api_key": api_key,
                "engine": "google",
                "q": query,
                "num": min(max_results, 100),
                "output": "json",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    if "error" in data:
        msg = f"SerpAPI error: {data['error']}"
        raise RuntimeError(msg)

    results: list[SearchResult] = []

    # Organic results
    for item in data.get("organic_results", []):
        results.append(
            SearchResult(
                title=item.get("title", ""),
                url=item.get("link", ""),
                snippet=item.get("snippet", ""),
                source="serpapi",
                position=item.get("position", len(results) + 1),
                extra={
                    k: v
                    for k, v in item.items()
                    if k not in ("title", "link", "snippet", "position") and v
                },
            )
        )

    # Answer box (if present, prepend as first result)
    answer_box = data.get("answer_box")
    if answer_box and answer_box.get("snippet"):
        results.insert(
            0,
            SearchResult(
                title=answer_box.get("title", "Answer"),
                url=answer_box.get("link", ""),
                snippet=answer_box.get("snippet", ""),
                source="serpapi:answer_box",
                position=0,
            ),
        )

    return results[:max_results]


# ═══════════════════════════════════════════════════════════════════════════
# DuckDuckGo — Free, no auth, HTML scraping
# ═══════════════════════════════════════════════════════════════════════════

_DDG_URL = "https://html.duckduckgo.com/html/"
_DDG_RESULT_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>'
    r'.*?<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
    re.DOTALL,
)
_DDG_TITLE_CLEAN = re.compile(r"<[^>]+>")


async def _search_duckduckgo(
    query: str,
    *,
    max_results: int = 10,
    settings: KaosWebSettings | None = None,
) -> list[SearchResult]:
    """Search via DuckDuckGo HTML endpoint.

    No auth needed. Rate-limited by IP. No guarantees on availability.
    Parses HTML results from html.duckduckgo.com/html/.
    """
    s = _resolve_settings(settings)
    async with httpx.AsyncClient(
        timeout=s.search_ddg_timeout,
        follow_redirects=True,
        headers={
            "User-Agent": s.search_ddg_user_agent,
            "Referer": "https://html.duckduckgo.com/",
        },
    ) as client:
        resp = await client.post(
            _DDG_URL,
            data={"q": query, "b": "", "kl": ""},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        html = resp.text

    results: list[SearchResult] = []

    # Parse with lxml for robustness
    try:
        from lxml import etree  # ty: ignore[unresolved-import]

        parser = etree.HTMLParser()
        tree = etree.fromstring(html.encode(), parser)

        # Each result is in a div.result — deduplicate by URL
        seen_urls: set[str] = set()
        for result_div in tree.xpath("//div[contains(@class, 'result')]"):
            if len(results) >= max_results:
                break

            # Title + URL
            link_els = result_div.xpath(".//a[contains(@class, 'result__a')]")
            if not link_els:
                continue
            link_el = link_els[0]
            title = "".join(link_el.itertext()).strip()
            href = link_el.get("href", "")

            # Skip duplicates
            if href in seen_urls:
                continue
            seen_urls.add(href)

            # Snippet
            snippet_els = result_div.xpath(".//a[contains(@class, 'result__snippet')]")
            snippet = ""
            if snippet_els:
                snippet = "".join(snippet_els[0].itertext()).strip()

            if not href or not title:
                continue

            results.append(
                SearchResult(
                    title=title,
                    url=href,
                    snippet=snippet,
                    source="duckduckgo",
                    position=len(results) + 1,
                )
            )
    except ImportError:
        # Fallback: regex parsing if lxml not available (shouldn't happen in kaos-web)
        for i, m in enumerate(_DDG_RESULT_RE.finditer(html)):
            if i >= max_results:
                break
            href, raw_title, raw_snippet = m.groups()
            title = _DDG_TITLE_CLEAN.sub("", raw_title).strip()
            snippet = _DDG_TITLE_CLEAN.sub("", raw_snippet).strip()
            results.append(
                SearchResult(
                    title=title,
                    url=href,
                    snippet=snippet,
                    source="duckduckgo",
                    position=len(results) + 1,
                )
            )

    return results


# ═══════════════════════════════════════════════════════════════════════════
# Exa — Neural / semantic search
# ═══════════════════════════════════════════════════════════════════════════


async def _search_exa(
    query: str,
    *,
    max_results: int = 10,
    settings: KaosWebSettings | None = None,
) -> list[SearchResult]:
    """Search via Exa (neural/semantic search).

    Endpoint: POST https://api.exa.ai/search
    Free tier: 1000 requests/month, 10 QPS
    """
    s = _resolve_settings(settings)
    api_key = s.get_search_api_key("exa") or ""
    if not api_key:
        msg = "EXA_API_KEY not set. Get a key at https://dashboard.exa.ai/api-keys"
        raise ValueError(msg)

    async with httpx.AsyncClient(timeout=s.search_timeout) as client:
        resp = await client.post(
            "https://api.exa.ai/search",
            json={
                "query": query,
                "numResults": min(max_results, 100),
                "type": "auto",
                "contents": {
                    "highlights": True,
                },
            },
            headers={
                "x-api-key": api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    results: list[SearchResult] = []
    for _i, item in enumerate(data.get("results", [])):
        # Build snippet from highlights or title
        highlights = item.get("highlights", [])
        snippet = highlights[0] if highlights else ""

        results.append(
            SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=snippet,
                source="exa",
                position=len(results) + 1,
                extra={
                    k: v for k, v in item.items() if k not in ("title", "url", "highlights") and v
                },
            )
        )

    return results[:max_results]


# ═══════════════════════════════════════════════════════════════════════════
# Brave Search — Independent index
# ═══════════════════════════════════════════════════════════════════════════


async def _search_brave(
    query: str,
    *,
    max_results: int = 10,
    settings: KaosWebSettings | None = None,
) -> list[SearchResult]:
    """Search via Brave Search API.

    Endpoint: GET https://api.search.brave.com/res/v1/web/search
    Free tier: ~1000 queries/month
    """
    s = _resolve_settings(settings)
    api_key = s.get_search_api_key("brave") or ""
    if not api_key:
        msg = "BRAVE_API_KEY not set. Get a key at https://brave.com/search/api/"
        raise ValueError(msg)

    async with httpx.AsyncClient(timeout=s.search_timeout) as client:
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
    for _i, item in enumerate(data.get("web", {}).get("results", [])):
        results.append(
            SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("description", ""),
                source="brave",
                position=len(results) + 1,
            )
        )

    return results[:max_results]
