"""URL / sitemap / page discovery and BFS-style crawling.

A coherent subsystem composed of four modules with mutual imports:

- ``sitemap`` — XML/text/gzip sitemap parser, sitemap-index recursion,
  ``robots.txt``-driven discovery.
- ``discovery`` — combined URL discovery (sitemaps + page-link extraction)
  with pattern filters and same-domain scoping.
- ``batch`` — concurrent multi-URL fetch with extraction, bounded by an
  ``asyncio.Semaphore``.
- ``crawl`` — BFS site-crawl orchestrator (depth + page caps, sitemap-first
  seeding, URL normalization).

The package re-exports the canonical names so callers can import from
``kaos_web.discover`` directly. Submodules remain importable for the
tests and tools that need access to private helpers (regex compilers,
URL normalizers, etc.).
"""

from kaos_web.discover.batch import BatchError, BatchResult, batch_fetch
from kaos_web.discover.crawl import (
    CrawlError,
    CrawlPage,
    CrawlResult,
    crawl_site,
)
from kaos_web.discover.discovery import (
    DiscoveredUrl,
    DiscoveryResult,
    discover_urls,
)
from kaos_web.discover.sitemap import (
    SitemapEntry,
    SitemapResult,
    discover_sitemaps,
    parse_sitemap,
)

__all__ = [
    "BatchError",
    "BatchResult",
    "CrawlError",
    "CrawlPage",
    "CrawlResult",
    "DiscoveredUrl",
    "DiscoveryResult",
    "SitemapEntry",
    "SitemapResult",
    "batch_fetch",
    "crawl_site",
    "discover_sitemaps",
    "discover_urls",
    "parse_sitemap",
]
