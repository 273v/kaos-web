"""Sitemap parser — XML, text, and gzip formats with robots.txt discovery.

Parses sitemap XML (with/without namespace), plain text sitemaps, gzip-compressed
sitemaps, and sitemap index files with recursive descent (depth-limited, cycle-detected).

Uses lxml (already a dependency) and stdlib gzip. No third-party sitemap library
(the only good one — ultimate-sitemap-parser — is GPL).
"""

from __future__ import annotations

import gzip
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as etree

from kaos_core.logging import get_logger
from kaos_web.models import WebRequest, WebResponse

if TYPE_CHECKING:
    from kaos_web.settings import KaosWebSettings

logger = get_logger(__name__)

# Type alias for the async fetch function
FetchFn = Callable[[WebRequest], Coroutine[Any, Any, WebResponse]]

# Sitemap XML namespace
_SM_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
_MAX_SITEMAP_DEPTH = 3  # Prevent infinite recursion on sitemap indexes


@dataclass(frozen=True, slots=True)
class SitemapEntry:
    """A URL entry from a sitemap."""

    url: str
    lastmod: datetime | None = None
    changefreq: str | None = None
    priority: float | None = None


@dataclass(slots=True)
class SitemapResult:
    """Result of parsing one or more sitemaps."""

    entries: list[SitemapEntry] = field(default_factory=list)
    sitemap_urls: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _parse_lastmod(text: str | None) -> datetime | None:
    """Parse lastmod date, tolerant of common formats."""
    if not text:
        return None
    text = text.strip()
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    # Try common truncated formats
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _parse_priority(text: str | None) -> float | None:
    """Parse priority value (0.0 - 1.0)."""
    if not text:
        return None
    try:
        val = float(text.strip())
        return val if 0.0 <= val <= 1.0 else None
    except ValueError:
        return None


def _local_name(tag: str) -> str:
    """Return the local element name without any XML namespace."""
    return tag.split("}", 1)[-1]


def _find_text(el: etree.Element, tag: str) -> str | None:
    """Find text of a child element, handling optional namespace."""
    # Try with namespace first
    child = el.find(f"{{{_SM_NS}}}{tag}")
    if child is not None and child.text:
        return child.text.strip()
    # Try without namespace (common in real-world sitemaps)
    child = el.find(tag)
    if child is not None and child.text:
        return child.text.strip()
    return None


def _parse_xml_sitemap(content: bytes) -> tuple[list[SitemapEntry], list[str]]:
    """Parse an XML sitemap. Returns (entries, sub_sitemap_urls)."""
    try:
        root = etree.fromstring(content)
    except etree.ParseError:
        return [], []

    if root is None:
        return [], []

    entries: list[SitemapEntry] = []
    sub_sitemaps: list[str] = []
    local_tag = _local_name(root.tag) if isinstance(root.tag, str) else ""

    if local_tag == "sitemapindex":
        # Sitemap index — collect child sitemap URLs
        for sm in root.iter():
            tag = _local_name(sm.tag) if isinstance(sm.tag, str) else ""
            if tag == "sitemap":
                loc = _find_text(sm, "loc")
                if loc:
                    sub_sitemaps.append(loc)

    elif local_tag == "urlset":
        # Regular sitemap — collect URL entries
        for url_el in root.iter():
            tag = _local_name(url_el.tag) if isinstance(url_el.tag, str) else ""
            if tag == "url":
                loc = _find_text(url_el, "loc")
                if loc:
                    entries.append(
                        SitemapEntry(
                            url=loc,
                            lastmod=_parse_lastmod(_find_text(url_el, "lastmod")),
                            changefreq=_find_text(url_el, "changefreq"),
                            priority=_parse_priority(_find_text(url_el, "priority")),
                        )
                    )

    return entries, sub_sitemaps


def _parse_text_sitemap(content: bytes) -> list[SitemapEntry]:
    """Parse a plain text sitemap (one URL per line)."""
    entries: list[SitemapEntry] = []
    try:
        text = content.decode("utf-8", errors="replace")
    except Exception:
        return []
    for line in text.splitlines():
        url = line.strip()
        if url and url.startswith(("http://", "https://")):
            entries.append(SitemapEntry(url=url))
    return entries


def _decompress_gzip(content: bytes) -> bytes:
    """Decompress gzip content, return original if not gzip."""
    try:
        return gzip.decompress(content)
    except (gzip.BadGzipFile, OSError):
        return content


async def parse_sitemap(
    url: str,
    fetch_fn: FetchFn,
    *,
    _depth: int = 0,
    _visited: set[str] | None = None,
    settings: KaosWebSettings | None = None,
) -> SitemapResult:
    """Fetch and parse a sitemap URL (XML, text, or gzip).

    Args:
        url: Sitemap URL to fetch and parse.
        fetch_fn: Async callable(WebRequest) -> WebResponse.
        _depth: Internal recursion depth counter.
        _visited: Internal cycle detection set.
        settings: Optional ``KaosWebSettings`` for timeout/depth overrides.

    Returns:
        SitemapResult with entries, source URLs, and any errors.
    """
    from kaos_web.settings import KaosWebSettings as _Settings

    s = settings or _Settings()

    if _visited is None:
        _visited = set()

    result = SitemapResult()
    result.sitemap_urls.append(url)

    # Cycle detection
    if url in _visited:
        result.errors.append(f"Cycle detected: {url}")
        return result
    _visited.add(url)

    # Depth limit
    max_depth = s.sitemap_max_depth
    if _depth > max_depth:
        result.errors.append(f"Max sitemap depth ({max_depth}) exceeded at {url}")
        return result

    # Fetch
    try:
        resp = await fetch_fn(WebRequest(url=url, timeout=s.sitemap_fetch_timeout))
    except Exception as exc:
        result.errors.append(f"Failed to fetch {url}: {exc}")
        return result

    if not resp.ok or not resp.html:
        result.errors.append(f"HTTP {resp.status_code} for {url}")
        return result

    # Get raw bytes — html field is already decoded string
    content = resp.html.encode("utf-8")

    # Decompress if gzip
    if url.endswith(".gz"):
        content = _decompress_gzip(content)

    # Determine format: XML or text
    is_xml = content.lstrip()[:5] == b"<?xml" or content.lstrip()[:1] == b"<"

    if is_xml:
        entries, sub_sitemaps = _parse_xml_sitemap(content)
        result.entries.extend(entries)

        # Recurse into sub-sitemaps
        for sub_url in sub_sitemaps:
            sub_result = await parse_sitemap(
                sub_url, fetch_fn, _depth=_depth + 1, _visited=_visited, settings=s
            )
            result.entries.extend(sub_result.entries)
            result.sitemap_urls.extend(sub_result.sitemap_urls)
            result.errors.extend(sub_result.errors)
    else:
        result.entries.extend(_parse_text_sitemap(content))

    return result


async def discover_sitemaps(
    domain: str,
    fetch_fn: FetchFn,
    *,
    settings: KaosWebSettings | None = None,
) -> list[str]:
    """Discover sitemap URLs for a domain via robots.txt + well-known paths.

    Strategy:
    1. Fetch robots.txt and parse Sitemap: directives
    2. If no sitemaps found, try well-known paths: /sitemap.xml, /sitemap_index.xml

    Args:
        domain: Domain name or base URL (e.g. "example.com" or "https://example.com").
        fetch_fn: Async callable(WebRequest) -> WebResponse.
        settings: Optional ``KaosWebSettings`` for timeout overrides.

    Returns:
        List of sitemap URLs found.
    """
    from urllib.robotparser import RobotFileParser

    from kaos_web.settings import KaosWebSettings as _Settings

    s = settings or _Settings()

    # Normalize domain to base URL
    if not domain.startswith(("http://", "https://")):
        base_url = f"https://{domain}"
    else:
        parsed = urlparse(domain)
        base_url = f"{parsed.scheme}://{parsed.netloc}"

    sitemap_urls: list[str] = []

    # 1. Try robots.txt
    robots_url = f"{base_url}/robots.txt"
    try:
        resp = await fetch_fn(WebRequest(url=robots_url, timeout=s.sitemap_robots_timeout))
        if resp.ok and resp.html:
            parser = RobotFileParser()
            parser.parse(resp.html.splitlines())
            # site_maps() returns list of sitemap URLs from Sitemap: directives
            sitemaps = parser.site_maps()
            if sitemaps:
                sitemap_urls.extend(sitemaps)
    except Exception:
        logger.debug("Failed to fetch robots.txt for %s", base_url)

    # 2. Fallback: try well-known paths if robots.txt had none
    if not sitemap_urls:
        for path in ("/sitemap.xml", "/sitemap_index.xml"):
            candidate = urljoin(base_url, path)
            try:
                resp = await fetch_fn(WebRequest(url=candidate, timeout=s.sitemap_fallback_timeout))
                if resp.ok and resp.html and resp.html.strip():
                    sitemap_urls.append(candidate)
                    break  # First one found is sufficient
            except Exception:
                continue

    return sitemap_urls
