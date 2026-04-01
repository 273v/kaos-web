"""Link extraction from HTML with classification.

Extracts all links from a page and classifies them as navigation, content,
pagination, social, download, or anchor links. Works on both raw HTML strings
and lxml element trees.
"""

from __future__ import annotations

import re
from typing import Literal
from urllib.parse import urljoin, urlparse

from lxml import html as lxml_html
from lxml.html import HtmlElement
from pydantic import BaseModel, ConfigDict

LinkType = Literal["navigation", "content", "pagination", "social", "download", "anchor", "other"]
LinkPosition = Literal["nav", "header", "footer", "sidebar", "body"]

_SOCIAL_DOMAINS = frozenset(
    {
        "twitter.com",
        "x.com",
        "facebook.com",
        "fb.com",
        "instagram.com",
        "linkedin.com",
        "youtube.com",
        "github.com",
        "reddit.com",
        "tiktok.com",
        "pinterest.com",
        "mastodon.social",
        "threads.net",
        "bsky.app",
        "discord.com",
        "discord.gg",
    }
)

_DOWNLOAD_EXTENSIONS = frozenset(
    {
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".csv",
        ".zip",
        ".tar",
        ".gz",
        ".tar.gz",
        ".rar",
        ".7z",
        ".pptx",
        ".ppt",
        ".txt",
        ".rtf",
        ".epub",
        ".mobi",
    }
)

_NAV_ANCESTOR_TAGS = frozenset({"nav", "header", "footer"})
_NAV_CLASS_PATTERNS = re.compile(
    r"nav|menu|sidebar|footer|breadcrumb|pagination|pager", re.IGNORECASE
)

_PAGINATION_TEXT = re.compile(
    r"^(next|prev|previous|newer|older|\u00bb|\u00ab|\u203a|\u2039|>>|<<|\d+)$",
    re.IGNORECASE,
)
_PAGINATION_REL = frozenset({"next", "prev"})

_UNSAFE_SCHEMES = frozenset({"javascript", "data", "vbscript"})


class ExtractedLink(BaseModel):
    """A link extracted from a web page with classification."""

    model_config = ConfigDict(frozen=True)

    url: str
    """Resolved absolute URL."""

    text: str
    """Visible link text."""

    title: str | None = None
    """Title attribute."""

    rel: list[str] = []
    """Rel attribute values (nofollow, noopener, etc.)."""

    is_internal: bool = False
    """Whether the link points to the same domain."""

    link_type: LinkType = "content"
    """Classification: navigation, content, pagination, social, download, anchor."""

    position: LinkPosition = "body"
    """Position on the page: nav, header, footer, sidebar, body."""


def extract_links(html: str, *, url: str = "") -> list[ExtractedLink]:
    """Extract all links from HTML with classification.

    Args:
        html: Raw HTML string.
        url: Page URL for resolving relative links and determining internal/external.

    Returns:
        List of ExtractedLink objects, deduplicated by URL.
    """
    if not html:
        return []

    try:
        doc = lxml_html.document_fromstring(html)
    except Exception:
        return []

    page_host = urlparse(url).netloc.lower().removeprefix("www.") if url else ""
    links: list[ExtractedLink] = []
    seen_urls: set[str] = set()

    # Extract <a> links
    for el in doc.iter("a"):
        href = el.get("href", "")
        if not href:
            continue

        # Skip dangerous schemes
        stripped = href.strip().lower()
        if any(stripped.startswith(f"{s}:") for s in _UNSAFE_SCHEMES):
            continue

        # Resolve URL
        resolved = (
            urljoin(url, href) if url and not href.startswith(("#", "mailto:", "tel:")) else href
        )

        # Deduplicate
        if resolved in seen_urls:
            continue
        seen_urls.add(resolved)

        # Text
        text = (el.text_content() or "").strip()
        if not text:
            # Try alt text from child image
            img = el.find(".//img")
            if img is not None:
                text = (img.get("alt") or "").strip()

        # Rel attribute
        rel_str = el.get("rel", "")
        rel = [r.strip().lower() for r in rel_str.split() if r.strip()] if rel_str else []

        # Title
        title = el.get("title")

        # Classification
        is_internal = _is_internal(resolved, page_host)
        position = _detect_position(el)
        link_type = _classify_link(resolved, text, rel, position, href)

        links.append(
            ExtractedLink(
                url=resolved,
                text=text,
                title=title,
                rel=rel,
                is_internal=is_internal,
                link_type=link_type,
                position=position,
            )
        )

    # Extract <link rel="next/prev"> from head for pagination
    for el in doc.iter("link"):
        rel_str = (el.get("rel") or "").lower()
        href = el.get("href", "")
        if rel_str in _PAGINATION_REL and href:
            resolved = urljoin(url, href) if url else href
            if resolved not in seen_urls:
                seen_urls.add(resolved)
                links.append(
                    ExtractedLink(
                        url=resolved,
                        text=rel_str,
                        rel=[rel_str],
                        is_internal=_is_internal(resolved, page_host),
                        link_type="pagination",
                        position="header",
                    )
                )

    return links


def _is_internal(link_url: str, page_host: str) -> bool:
    """Check if a link is internal (same domain)."""
    if not page_host:
        return False
    if link_url.startswith("#"):
        return True
    link_host = urlparse(link_url).netloc.lower().removeprefix("www.")
    return link_host == page_host or link_host == ""


def _detect_position(el: HtmlElement) -> LinkPosition:
    """Detect the position of a link on the page by checking ancestors."""
    parent = el.getparent()
    while parent is not None:
        tag = parent.tag.lower() if isinstance(parent.tag, str) else ""
        if tag == "nav":
            return "nav"
        if tag == "header":
            return "header"
        if tag == "footer":
            return "footer"
        if tag == "aside":
            return "sidebar"
        # Check class/id for nav patterns
        cls = (parent.get("class") or "") + " " + (parent.get("id") or "")
        if cls.strip() and _NAV_CLASS_PATTERNS.search(cls):
            if "footer" in cls.lower():
                return "footer"
            if "sidebar" in cls.lower() or "aside" in cls.lower():
                return "sidebar"
            return "nav"
        parent = parent.getparent()
    return "body"


def _classify_link(
    url: str, text: str, rel: list[str], position: LinkPosition, raw_href: str
) -> LinkType:
    """Classify a link by type."""
    # Anchor links
    if raw_href.startswith("#"):
        return "anchor"

    # Pagination
    if any(r in _PAGINATION_REL for r in rel):
        return "pagination"
    if position == "nav" and _PAGINATION_TEXT.match(text):
        return "pagination"

    # Social media
    link_host = urlparse(url).netloc.lower().removeprefix("www.")
    if link_host in _SOCIAL_DOMAINS:
        return "social"

    # Downloads
    path = urlparse(url).path.lower()
    if any(path.endswith(ext) for ext in _DOWNLOAD_EXTENSIONS):
        return "download"

    # Navigation (based on position)
    if position in ("nav", "header", "footer"):
        return "navigation"

    return "content"
