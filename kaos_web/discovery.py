"""URL discovery — combine sitemaps, page links, and robots.txt into a unified pipeline.

Firecrawl-style ``sitemap`` enum controls strategy: ``include`` (default) uses both
sitemaps and page links, ``skip`` ignores sitemaps entirely, ``only`` uses only sitemaps.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from urllib.parse import urlparse

from kaos_web.models import WebRequest
from kaos_web.sitemap import FetchFn

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DiscoveredUrl:
    """A URL with discovery metadata."""

    url: str
    source: str  # "sitemap", "page_link", "robots"
    lastmod: datetime | None = None
    link_type: str | None = None  # navigation, content, pagination, etc.
    depth: int = 0


@dataclass(slots=True)
class DiscoveryResult:
    """URLs discovered from a domain."""

    urls: list[DiscoveredUrl] = field(default_factory=list)
    sitemap_count: int = 0
    page_link_count: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.urls)


def _matches_patterns(
    url: str,
    include_patterns: list[re.Pattern[str]] | None,
    exclude_patterns: list[re.Pattern[str]] | None,
) -> bool:
    """Check if URL matches include patterns and doesn't match exclude patterns."""
    path = urlparse(url).path
    if include_patterns and not any(p.search(path) for p in include_patterns):
        return False
    return not (exclude_patterns and any(p.search(path) for p in exclude_patterns))


def _compile_patterns(patterns: list[str] | None) -> list[re.Pattern[str]] | None:
    """Compile regex patterns, returning None if empty."""
    if not patterns:
        return None
    compiled = []
    for p in patterns:
        try:
            compiled.append(re.compile(p))
        except re.error:
            logger.warning("Invalid regex pattern: %s", p)
    return compiled or None


def _same_domain(url: str, base_domain: str) -> bool:
    """Check if URL belongs to the same domain."""
    host = urlparse(url).netloc.lower().removeprefix("www.")
    return host == base_domain


async def discover_urls(
    url: str,
    fetch_fn: FetchFn,
    *,
    sitemap: Literal["include", "skip", "only"] = "include",
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
    max_urls: int = 1000,
    respect_robots: bool = True,
) -> DiscoveryResult:
    """Discover all URLs from a domain.

    Strategy:
    1. Fetch robots.txt → extract Sitemap: directives
    2. Parse sitemaps (if sitemap != "skip")
    3. Fetch start page → extract_links() (if sitemap != "only")
    4. Deduplicate, filter, sort by lastmod (newest first)

    Args:
        url: Starting URL or domain.
        fetch_fn: Async callable(WebRequest) -> WebResponse.
        sitemap: Control sitemap usage — "include", "skip", or "only".
        include_patterns: Regex patterns to include (matched against URL path).
        exclude_patterns: Regex patterns to exclude.
        max_urls: Maximum URLs to return.
        respect_robots: Whether to check robots.txt Disallow rules.

    Returns:
        DiscoveryResult with deduplicated, filtered URLs.
    """
    from kaos_web.sitemap import discover_sitemaps, parse_sitemap

    result = DiscoveryResult()
    seen: set[str] = set()

    parsed = urlparse(url if url.startswith(("http://", "https://")) else f"https://{url}")
    base_domain = parsed.netloc.lower().removeprefix("www.")
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    inc = _compile_patterns(include_patterns)
    exc = _compile_patterns(exclude_patterns)

    # Optional: robots.txt check
    robots_parser = None
    if respect_robots:
        try:
            from urllib.robotparser import RobotFileParser

            resp = await fetch_fn(WebRequest(url=f"{base_url}/robots.txt", timeout=10.0))
            if resp.ok and resp.html:
                robots_parser = RobotFileParser()
                robots_parser.parse(resp.html.splitlines())
        except Exception:
            pass

    def _is_allowed(check_url: str) -> bool:
        if not robots_parser:
            return True
        return robots_parser.can_fetch("*", check_url)

    # Step 1-2: Sitemap discovery and parsing
    if sitemap != "skip":
        try:
            sm_urls = await discover_sitemaps(base_domain, fetch_fn)
            for sm_url in sm_urls:
                sm_result = await parse_sitemap(sm_url, fetch_fn)
                result.errors.extend(sm_result.errors)
                for entry in sm_result.entries:
                    if entry.url in seen:
                        continue
                    if not _same_domain(entry.url, base_domain):
                        continue
                    if not _matches_patterns(entry.url, inc, exc):
                        continue
                    if not _is_allowed(entry.url):
                        continue
                    seen.add(entry.url)
                    result.urls.append(
                        DiscoveredUrl(
                            url=entry.url,
                            source="sitemap",
                            lastmod=entry.lastmod,
                        )
                    )
                    result.sitemap_count += 1
                    if len(result.urls) >= max_urls:
                        break
                if len(result.urls) >= max_urls:
                    break
        except Exception as exc_err:
            result.errors.append(f"Sitemap discovery failed: {exc_err}")

    # Step 3: Page link extraction (if not sitemap-only)
    if sitemap != "only" and len(result.urls) < max_urls:
        try:
            from kaos_web.extract.links import extract_links

            start_url = url if url.startswith(("http://", "https://")) else f"https://{url}"
            resp = await fetch_fn(WebRequest(url=start_url, timeout=15.0))
            if resp.ok and resp.html:
                links = extract_links(resp.html, url=resp.url)
                for link in links:
                    if link.url in seen:
                        continue
                    if not link.is_internal:
                        continue
                    if not link.url.startswith(("http://", "https://")):
                        continue
                    if not _matches_patterns(link.url, inc, exc):
                        continue
                    if not _is_allowed(link.url):
                        continue
                    seen.add(link.url)
                    result.urls.append(
                        DiscoveredUrl(
                            url=link.url,
                            source="page_link",
                            link_type=link.link_type,
                            depth=1,
                        )
                    )
                    result.page_link_count += 1
                    if len(result.urls) >= max_urls:
                        break
        except Exception as exc_err:
            result.errors.append(f"Page link extraction failed: {exc_err}")

    # Sort: sitemap entries with lastmod first (newest), then page links
    result.urls.sort(
        key=lambda u: (
            u.lastmod is not None,
            u.lastmod or datetime.min,
        ),
        reverse=True,
    )

    return result
