"""Web search backends for kaos-web.

Pluggable search backends behind a common protocol. Each backend
wraps a search API (Brave, SearXNG, etc.) and returns uniform results.

Usage::

    from kaos_web.search import search_web, SearchResult

    results = await search_web("python tabular data", max_results=10)
    for r in results:
        print(f"{r.title} — {r.url}")
"""

from kaos_web.search.backends import SearchResult, search_web

__all__ = ["SearchResult", "search_web"]
