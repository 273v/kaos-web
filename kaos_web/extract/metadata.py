"""Extract structured metadata from HTML pages.

Supports JSON-LD, OpenGraph, and standard HTML meta tags.
"""

from __future__ import annotations

import json

from lxml import html as lxml_html

from kaos_web.models.metadata import PageMetadata


def extract_metadata(html: str, *, url: str = "") -> PageMetadata:
    """Extract structured metadata from HTML.

    Extracts JSON-LD, OpenGraph, and standard meta tags, merging them with
    the following precedence: JSON-LD > OpenGraph > standard HTML tags.

    Args:
        html: Raw HTML string.
        url: Source URL (used as fallback for canonical URL).

    Returns:
        PageMetadata with all extracted fields.
    """
    if not html or not html.strip():
        return PageMetadata()

    try:
        doc = lxml_html.document_fromstring(html)
    except Exception:
        return PageMetadata()

    # --- JSON-LD ---
    structured_data: list[dict] = []
    for script in doc.iter("script"):
        if script.get("type", "").lower() == "application/ld+json":
            raw = script.text_content()
            if raw:
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        structured_data.append(parsed)
                    elif isinstance(parsed, list):
                        structured_data.extend(d for d in parsed if isinstance(d, dict))
                except (json.JSONDecodeError, ValueError):
                    continue

    # --- OpenGraph ---
    opengraph: dict[str, str] = {}
    for meta in doc.iter("meta"):
        prop = meta.get("property", "")
        if prop.startswith("og:"):
            content = meta.get("content", "")
            if content:
                key = prop[3:]  # Strip "og:" prefix.
                opengraph[key] = content

    # --- Standard meta tags ---
    title_el = doc.find(".//title")
    html_title = (title_el.text or "").strip() if title_el is not None else None

    description: str | None = None
    author: str | None = None
    canonical: str | None = None
    language: str | None = None
    date_published: str | None = None
    date_modified: str | None = None

    for meta in doc.iter("meta"):
        name = (meta.get("name") or "").lower()
        content = meta.get("content", "")
        if not content:
            continue
        if name == "description":
            description = content
        elif name == "author":
            author = content
        elif name in ("date", "article:published_time", "dc.date"):
            date_published = content
        elif name in ("article:modified_time", "dc.date.modified"):
            date_modified = content

    # Also check <meta property="article:published_time"> (often used with OG).
    for meta in doc.iter("meta"):
        prop = meta.get("property", "")
        content = meta.get("content", "")
        if not content:
            continue
        if prop == "article:published_time" and not date_published:
            date_published = content
        elif prop == "article:modified_time" and not date_modified:
            date_modified = content
        elif prop == "article:author" and not author:
            author = content

    # Canonical URL from <link rel="canonical">.
    for link in doc.iter("link"):
        if (link.get("rel") or "").lower() == "canonical":
            href = link.get("href", "")
            if href:
                canonical = href
                break

    # Language from <html lang="...">.
    # lxml.html.fromstring returns <html> as root, so check root directly
    html_el = doc if doc.tag == "html" else doc.find(".//html")
    if html_el is not None:
        lang = html_el.get("lang", "").strip()
        if lang:
            language = lang

    # --- Merge (OG > standard) ---
    final_title = opengraph.get("title") or html_title or None
    final_description = opengraph.get("description") or description
    final_url = opengraph.get("url") or canonical or url or None
    site_name = opengraph.get("site_name")
    image = opengraph.get("image")

    # Try to extract author/dates from JSON-LD as well.
    for ld in structured_data:
        if not author:
            ld_author = ld.get("author")
            if isinstance(ld_author, dict):
                author = ld_author.get("name")
            elif isinstance(ld_author, str):
                author = ld_author
        if not date_published:
            dp = ld.get("datePublished")
            if isinstance(dp, str):
                date_published = dp
        if not date_modified:
            dm = ld.get("dateModified")
            if isinstance(dm, str):
                date_modified = dm
        if not final_title:
            ld_title = ld.get("headline") or ld.get("name")
            if isinstance(ld_title, str):
                final_title = ld_title

    return PageMetadata(
        title=final_title,
        author=author,
        description=final_description,
        date_published=date_published,
        date_modified=date_modified,
        url=final_url,
        language=language,
        site_name=site_name,
        image=image,
        structured_data=structured_data,
        opengraph=opengraph,
    )
