"""Web search backends for kaos-web.

Four pluggable backends behind a common interface:

- **SerpAPI** — Google results, 250 free/month (SERPAPI_API_KEY)
- **DuckDuckGo** — Free, no auth, HTML scraping fallback
- **Exa** — Neural/semantic search, 1000 free/month (EXA_API_KEY)
- **Brave** — Independent index, ~1000 free/month (BRAVE_API_KEY)

Auto-detects backend from environment variables, falls back to DuckDuckGo.

Usage::

    from kaos_web.search import search_web, SearchResult

    results = await search_web("python tabular data", max_results=10)
    for r in results:
        print(f"{r.title} — {r.url}")
"""

from kaos_web.search.backends import SearchResult, search_web

__all__ = ["SearchResult", "search_web"]
