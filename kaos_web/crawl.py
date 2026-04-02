"""Site crawl — BFS orchestrator with sitemap-first discovery.

Combines URL discovery → batch fetch → content extraction into a single
crawl operation. BFS ensures important pages (homepage, top-level sections)
are crawled before deep pages.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlparse

from kaos_web.clients.config import HttpClientConfig
from kaos_web.clients.http import HttpClient
from kaos_web.models import WebRequest, WebResponse

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CrawlError:
    """Error for a single URL during crawl."""

    url: str
    error: str
    depth: int = 0


@dataclass(slots=True)
class CrawlPage:
    """A single crawled and extracted page."""

    url: str
    depth: int
    title: str | None = None
    content_text: str = ""
    content_markdown: str = ""
    links: list[str] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)
    status_code: int = 0


@dataclass(slots=True)
class CrawlResult:
    """Results from a site crawl."""

    pages: list[CrawlPage] = field(default_factory=list)
    total_discovered: int = 0
    total_crawled: int = 0
    total_extracted: int = 0
    errors: list[CrawlError] = field(default_factory=list)
    sitemap_entries: int = 0
    elapsed_ms: float = 0.0


async def crawl_site(
    start_url: str,
    *,
    max_depth: int = 2,
    max_pages: int = 50,
    concurrency: int = 5,
    sitemap: Literal["include", "skip", "only"] = "include",
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
    respect_robots: bool = True,
    client_config: HttpClientConfig | None = None,
) -> CrawlResult:
    """Crawl a site with sitemap-first discovery.

    Strategy:
    1. discover_urls() — sitemaps + start page links
    2. BFS: dequeue URL → fetch → extract → enqueue new links
    3. Stop at max_depth or max_pages
    4. Deduplicate throughout via seen-set

    Args:
        start_url: Starting URL for the crawl.
        max_depth: Maximum link-following depth (0 = start page only).
        max_pages: Maximum pages to extract.
        concurrency: Max concurrent requests.
        sitemap: Control sitemap usage.
        include_patterns: Regex patterns for URL paths to include.
        exclude_patterns: Regex patterns for URL paths to exclude.
        respect_robots: Whether to respect robots.txt Disallow rules.
        client_config: Optional HTTP client configuration.

    Returns:
        CrawlResult with pages, errors, and statistics.
    """
    from kaos_web.discovery import _compile_patterns, _matches_patterns, discover_urls

    if not start_url.startswith(("http://", "https://")):
        start_url = f"https://{start_url}"

    config = client_config or HttpClientConfig(enable_cache=True)
    result = CrawlResult()
    seen: set[str] = set()
    inc = _compile_patterns(include_patterns)
    exc = _compile_patterns(exclude_patterns)
    start_time = time.monotonic()

    parsed = urlparse(start_url)
    base_domain = parsed.netloc.lower().removeprefix("www.")

    async with HttpClient(config) as client:
        # Step 1: Discovery
        discovery = await discover_urls(
            start_url,
            client.fetch,
            sitemap=sitemap,
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            max_urls=max_pages * 3,  # Over-discover for filtering
            respect_robots=respect_robots,
        )
        result.total_discovered = discovery.total
        result.sitemap_entries = discovery.sitemap_count
        result.errors.extend(CrawlError(url="discovery", error=e) for e in discovery.errors)

        # Build initial BFS queue from discovery results
        queue: deque[tuple[str, int]] = deque()  # (url, depth)

        # Start URL always goes first at depth 0
        queue.append((start_url, 0))
        seen.add(start_url)

        # Add discovered URLs at depth 1 (they're one hop from start)
        for disc_url in discovery.urls:
            if disc_url.url not in seen:
                seen.add(disc_url.url)
                queue.append((disc_url.url, 1))

        # Step 2: BFS crawl
        semaphore = asyncio.Semaphore(concurrency)

        async def _crawl_one(url: str, depth: int) -> CrawlPage | None:
            async with semaphore:
                try:
                    resp = await client.fetch(WebRequest(url=url, timeout=30.0))
                except Exception as exc:
                    result.errors.append(CrawlError(url=url, error=str(exc), depth=depth))
                    return None

                if not resp.ok:
                    result.errors.append(
                        CrawlError(
                            url=url,
                            error=f"HTTP {resp.status_code}",
                            depth=depth,
                        )
                    )
                    return None

                result.total_crawled += 1
                return await _extract_page(resp, url, depth)

        async def _extract_page(resp: WebResponse, url: str, depth: int) -> CrawlPage:
            """Extract content from a fetched page."""
            try:
                from kaos_content.serializers.markdown import serialize_markdown
                from kaos_content.serializers.text import serialize_text
                from kaos_web.extract import extract_metadata, html_to_document
                from kaos_web.extract.links import extract_links

                doc = html_to_document(resp.html, url=resp.url)
                text = serialize_text(doc) if doc.body else ""
                markdown = serialize_markdown(doc) if doc.body else ""

                meta = extract_metadata(resp.html, url=resp.url)
                links_found = extract_links(resp.html, url=resp.url)
                internal_links = [
                    lnk.url
                    for lnk in links_found
                    if lnk.is_internal and lnk.url.startswith(("http://", "https://"))
                ]

                result.total_extracted += 1
                return CrawlPage(
                    url=resp.url,
                    depth=depth,
                    title=meta.title or doc.metadata.title,
                    content_text=text,
                    content_markdown=markdown,
                    links=internal_links,
                    metadata=meta.model_dump(exclude_none=True),
                    status_code=resp.status_code,
                )
            except Exception as exc:
                logger.warning("Extraction failed for %s: %s", url, exc)
                return CrawlPage(
                    url=resp.url,
                    depth=depth,
                    status_code=resp.status_code,
                )

        # Process BFS queue in batches
        while queue and len(result.pages) < max_pages:
            # Take a batch from the queue
            batch_size = min(concurrency, max_pages - len(result.pages), len(queue))
            batch: list[tuple[str, int]] = []
            for _ in range(batch_size):
                if queue:
                    batch.append(queue.popleft())

            # Fetch batch concurrently
            tasks = [asyncio.create_task(_crawl_one(u, d)) for u, d in batch]
            pages = await asyncio.gather(*tasks)

            # Process results: add pages, enqueue new links
            for page in pages:
                if page is None:
                    continue
                result.pages.append(page)

                # Enqueue new internal links at depth+1
                if page.depth < max_depth:
                    for link_url in page.links:
                        norm = _normalize_url(link_url)
                        if norm in seen:
                            continue
                        if not _same_domain(link_url, base_domain):
                            continue
                        if not _matches_patterns(link_url, inc, exc):
                            continue
                        seen.add(norm)
                        queue.append((link_url, page.depth + 1))

    result.elapsed_ms = (time.monotonic() - start_time) * 1000
    return result


def _normalize_url(url: str) -> str:
    """Normalize URL for deduplication (strip fragment, trailing slash)."""
    parsed = urlparse(url)
    # Remove fragment
    path = parsed.path.rstrip("/") or "/"
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def _same_domain(url: str, base_domain: str) -> bool:
    """Check if URL belongs to the same domain."""
    host = urlparse(url).netloc.lower().removeprefix("www.")
    return host == base_domain
