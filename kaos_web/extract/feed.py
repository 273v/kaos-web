"""RSS 2.0 + Atom 1.0 feed parser.

Many publishers (SEC press releases, Federal Register, agency news, GitHub
releases, blogs) expose their freshest content via RSS/Atom feeds long
before a sitemap.xml gets refreshed. Without a feed parser, the agent has
to either grep raw XML out of ``html_to_document`` output or fall through
to slower HTML scraping — both fail-prone.

Uses ``lxml`` (already a kaos-web dependency, used by the sitemap parser).
No new third-party dependency. Mirrors :mod:`kaos_web.discover.sitemap`'s
defensive parsing posture: silent-skip malformed entries, hard-cap entry
count to bound memory, treat namespace-prefixed and bare tags identically.

Public surface:

* :class:`FeedItem` — frozen result type for a single entry
* :class:`FeedResult` — top-level result (title, link, items)
* :func:`parse_feed` — auto-detect RSS vs Atom from the document root

This module is **pure parse**. Fetching is the caller's responsibility
(usually :class:`kaos_web.clients.http.HttpClient`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING

from kaos_core.logging import get_logger

if TYPE_CHECKING:
    from xml.etree import ElementTree as etree

logger = get_logger("kaos.web.feed")


@dataclass(frozen=True, slots=True)
class FeedItem:
    """One entry from an RSS or Atom feed.

    Field names are the union of the two formats so downstream consumers
    can ignore which format the publisher used.
    """

    title: str
    link: str
    pub_date: datetime | None = None
    description: str | None = None
    author: str | None = None
    guid: str | None = None
    categories: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class FeedResult:
    """Parsed feed metadata + entries."""

    format: str  # "rss" | "atom" | "unknown"
    title: str | None
    link: str | None
    description: str | None
    items: tuple[FeedItem, ...]


# Hard caps — same posture as sitemap.py.
_MAX_ITEMS = 1000
_MAX_DESCRIPTION_CHARS = 2000


def _local_name(tag: str) -> str:
    """Return the local part of a possibly-namespaced ElementTree tag."""
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


def _find_first(el: etree.Element, *names: str) -> etree.Element | None:
    """Return the first direct child whose local name matches any of ``names``."""
    target = set(names)
    for child in el:
        if _local_name(child.tag) in target:
            return child
    return None


def _text(el: etree.Element | None) -> str | None:
    """Return ``el.text`` stripped, or ``None`` for missing / whitespace-only."""
    if el is None:
        return None
    text = el.text
    if text is None:
        return None
    stripped = text.strip()
    return stripped if stripped else None


def _atom_link_href(el: etree.Element) -> str | None:
    """Extract the canonical link URL from an Atom <entry> or <feed>.

    Atom links are ``<link href="..." rel="..."/>``. Prefer ``rel="alternate"``
    (or missing rel — the default per RFC 4287 §4.2.7.2) and skip ``rel="self"``.
    """
    fallback: str | None = None
    for child in el:
        if _local_name(child.tag) != "link":
            continue
        href = child.get("href")
        if not href:
            continue
        rel = child.get("rel", "alternate")
        if rel == "alternate":
            return href
        if fallback is None and rel != "self":
            fallback = href
    return fallback


def _parse_date(text: str | None) -> datetime | None:
    """Parse an RSS 2.0 (RFC-822) or Atom (RFC-3339 / ISO-8601) date."""
    if not text:
        return None
    text = text.strip()
    if not text:
        return None
    # RFC-822 (RSS 2.0): "Thu, 21 May 2026 08:51:10 -0400"
    try:
        return parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        pass
    # RFC-3339 (Atom): "2026-05-21T08:51:10-04:00"; tolerate the "Z" suffix
    # by mapping it to "+00:00" which ``fromisoformat`` understands.
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _parse_rss(channel: etree.Element) -> FeedResult:
    """Parse an RSS 2.0 ``<channel>`` element."""
    title = _text(_find_first(channel, "title"))
    link = _text(_find_first(channel, "link"))
    description = _text(_find_first(channel, "description"))
    items: list[FeedItem] = []
    for child in channel:
        if _local_name(child.tag) != "item" or len(items) >= _MAX_ITEMS:
            continue
        item_title = _text(_find_first(child, "title")) or ""
        item_link = _text(_find_first(child, "link")) or ""
        if not item_link:
            # link can sometimes only appear via <guid isPermaLink="true">
            guid_el = _find_first(child, "guid")
            if guid_el is not None and guid_el.get("isPermaLink", "true").lower() == "true":
                item_link = _text(guid_el) or ""
        if not (item_title or item_link):
            continue
        item_desc = _text(_find_first(child, "description"))
        if item_desc and len(item_desc) > _MAX_DESCRIPTION_CHARS:
            item_desc = item_desc[:_MAX_DESCRIPTION_CHARS] + "…"
        # author can be <author> or <dc:creator>
        author = _text(_find_first(child, "author", "creator"))
        guid_text = _text(_find_first(child, "guid"))
        categories = tuple(
            _text(c) or "" for c in child if _local_name(c.tag) == "category" and _text(c)
        )
        items.append(
            FeedItem(
                title=item_title,
                link=item_link,
                pub_date=_parse_date(_text(_find_first(child, "pubDate", "date"))),
                description=item_desc,
                author=author,
                guid=guid_text,
                categories=categories,
            )
        )
    return FeedResult(
        format="rss",
        title=title,
        link=link,
        description=description,
        items=tuple(items),
    )


def _parse_atom(feed_el: etree.Element) -> FeedResult:
    """Parse an Atom 1.0 ``<feed>`` element."""
    title = _text(_find_first(feed_el, "title"))
    link = _atom_link_href(feed_el)
    description = _text(_find_first(feed_el, "subtitle", "summary"))
    items: list[FeedItem] = []
    for child in feed_el:
        if _local_name(child.tag) != "entry" or len(items) >= _MAX_ITEMS:
            continue
        item_title = _text(_find_first(child, "title")) or ""
        item_link = _atom_link_href(child) or ""
        if not (item_title or item_link):
            continue
        # Atom entry body can be <summary> or <content>
        item_desc = _text(_find_first(child, "summary", "content"))
        if item_desc and len(item_desc) > _MAX_DESCRIPTION_CHARS:
            item_desc = item_desc[:_MAX_DESCRIPTION_CHARS] + "…"
        # Atom author is <author><name>...</name></author>
        author_el = _find_first(child, "author")
        author = _text(_find_first(author_el, "name")) if author_el is not None else None
        guid_text = _text(_find_first(child, "id"))
        # Atom <category term="..."/>
        categories = tuple(
            c.get("term", "") for c in child if _local_name(c.tag) == "category" and c.get("term")
        )
        items.append(
            FeedItem(
                title=item_title,
                link=item_link,
                pub_date=_parse_date(_text(_find_first(child, "published", "updated"))),
                description=item_desc,
                author=author,
                guid=guid_text,
                categories=categories,
            )
        )
    return FeedResult(
        format="atom",
        title=title,
        link=link,
        description=description,
        items=tuple(items),
    )


def parse_feed(content: bytes | str) -> FeedResult:
    """Auto-detect RSS vs Atom and parse.

    Returns a :class:`FeedResult` with ``format="unknown"`` and zero items
    when the input doesn't look like either format — does NOT raise. This
    matches the sitemap parser's posture: an empty result is a useful
    signal for the agent to try a different discovery path, but a crash
    forces the agent to wrap every fetch in try/except.
    """
    if isinstance(content, str):
        content = content.encode("utf-8")
    if not content.strip():
        return FeedResult(format="unknown", title=None, link=None, description=None, items=())
    # lxml import is deferred so the module is import-safe in pure type-checking.
    from lxml import etree as lxml_etree  # ty: ignore[unresolved-import]

    parser = lxml_etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        recover=True,
        huge_tree=False,
    )
    try:
        root = lxml_etree.fromstring(content, parser=parser)
    except (lxml_etree.XMLSyntaxError, ValueError) as exc:
        logger.warning("feed parse failed: %s", exc)
        return FeedResult(format="unknown", title=None, link=None, description=None, items=())

    if root is None:
        return FeedResult(format="unknown", title=None, link=None, description=None, items=())

    root_local = _local_name(root.tag)
    if root_local == "rss":
        channel = _find_first(root, "channel")
        if channel is not None:
            return _parse_rss(channel)
    if root_local == "feed":
        return _parse_atom(root)
    # Some publishers wrap RSS in a non-standard root; try one level down.
    for child in root:
        if _local_name(child.tag) == "channel":
            return _parse_rss(child)
    return FeedResult(format="unknown", title=None, link=None, description=None, items=())


__all__ = ["FeedItem", "FeedResult", "parse_feed"]
