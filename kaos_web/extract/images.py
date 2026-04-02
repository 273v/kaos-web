"""Image extraction from HTML with classification.

Extracts all images with lazy-load detection, srcset parsing,
and content vs decorative classification.
"""

from __future__ import annotations

import re
from typing import Literal
from urllib.parse import urljoin

from lxml import html as lxml_html
from lxml.html import HtmlElement
from pydantic import BaseModel, ConfigDict

ImageType = Literal["content", "decorative", "icon", "social_card", "tracking"]

_TRACKING_PATTERNS = re.compile(
    r"pixel|track|beacon|spacer|blank|(?<![a-z0-9-])1x1(?![a-z0-9-])|transparent\.gif",
    re.IGNORECASE,
)
_ICON_PATTERNS = re.compile(r"icon|logo|avatar|sprite|favicon", re.IGNORECASE)

# Tiny placeholder data URIs to skip
_PLACEHOLDER_PREFIX = "data:image/gif;base64,R0lGODlh"


class SrcSetEntry(BaseModel):
    """A single entry from an img srcset attribute."""

    model_config = ConfigDict(frozen=True)

    url: str
    descriptor: str = ""


class ExtractedImage(BaseModel):
    """An image extracted from a web page with metadata."""

    model_config = ConfigDict(frozen=True)

    src: str
    """Resolved primary image URL."""

    alt: str | None = None
    """Alt text."""

    title: str | None = None
    """Title attribute."""

    width: int | None = None
    """Declared width in pixels."""

    height: int | None = None
    """Declared height in pixels."""

    srcset: list[SrcSetEntry] = []
    """Responsive image entries from srcset."""

    loading: str | None = None
    """Loading attribute: 'lazy' or 'eager'."""

    image_type: ImageType = "content"
    """Classification: content, decorative, icon, social_card, tracking."""

    context: str = ""
    """Caption or surrounding text for context."""


def extract_images(html: str, *, url: str = "") -> list[ExtractedImage]:
    """Extract all images from HTML with classification.

    Handles lazy-loaded images (data-src, data-lazy-src), responsive
    images (srcset), and classifies images as content, decorative,
    icon, or tracking pixel.

    Args:
        html: Raw HTML string.
        url: Page URL for resolving relative image URLs.

    Returns:
        List of ExtractedImage objects.
    """
    if not html:
        return []

    try:
        doc = lxml_html.document_fromstring(html)
    except Exception:
        return []

    images: list[ExtractedImage] = []
    seen_srcs: set[str] = set()

    # Extract <img> elements
    for el in doc.iter("img"):
        src = _get_image_src(el)
        if not src:
            continue

        # Resolve URL
        resolved = urljoin(url, src) if url else src

        # Skip unsafe schemes
        lower_src = resolved.lower()
        if lower_src.startswith(("javascript:", "vbscript:")):
            continue

        # Skip tiny data URI placeholders
        if lower_src.startswith(_PLACEHOLDER_PREFIX) and len(resolved) < 100:
            continue

        # Deduplicate
        if resolved in seen_srcs:
            continue
        seen_srcs.add(resolved)

        # Parse dimensions
        width = _parse_int(el.get("width"))
        height = _parse_int(el.get("height"))

        # Parse srcset
        srcset = _parse_srcset(el.get("srcset", ""), url)

        # Also check <picture> parent for sources
        parent = el.getparent()
        if parent is not None and parent.tag == "picture":
            for source in parent.iter("source"):
                srcset.extend(_parse_srcset(source.get("srcset", ""), url))

        # Context: figcaption or surrounding text
        context = _get_image_context(el)

        # Classify
        image_type = _classify_image(resolved, el, width, height)

        images.append(
            ExtractedImage(
                src=resolved,
                alt=el.get("alt"),
                title=el.get("title"),
                width=width,
                height=height,
                srcset=srcset,
                loading=el.get("loading"),
                image_type=image_type,
                context=context,
            )
        )

    # Extract OG/Twitter card images from meta
    for meta in doc.iter("meta"):
        prop = (meta.get("property") or meta.get("name") or "").lower()
        content = meta.get("content", "")
        if prop in ("og:image", "twitter:image") and content:
            resolved = urljoin(url, content) if url else content
            if resolved not in seen_srcs:
                seen_srcs.add(resolved)
                images.append(
                    ExtractedImage(
                        src=resolved,
                        alt=meta.get("content", ""),
                        image_type="social_card",
                    )
                )

    return images


def _get_image_src(el: HtmlElement) -> str:
    """Get the best image source, preferring lazy-load attributes."""
    for attr in ("data-src", "data-lazy-src", "data-original"):
        val = el.get(attr, "").strip()
        if val:
            return val
    return el.get("src", "").strip()


def _parse_int(value: str | None) -> int | None:
    """Parse an integer from a string, returning None on failure."""
    if not value:
        return None
    try:
        return int(value.strip().rstrip("px"))
    except ValueError:
        return None


def _parse_srcset(srcset: str, base_url: str) -> list[SrcSetEntry]:
    """Parse an srcset attribute into entries."""
    if not srcset:
        return []
    entries: list[SrcSetEntry] = []
    for part in srcset.split(","):
        part = part.strip()
        if not part:
            continue
        pieces = part.split()
        if not pieces:
            continue
        src = urljoin(base_url, pieces[0]) if base_url else pieces[0]
        descriptor = pieces[1] if len(pieces) > 1 else ""
        entries.append(SrcSetEntry(url=src, descriptor=descriptor))
    return entries


def _get_image_context(el: HtmlElement) -> str:
    """Get contextual text for an image (figcaption, alt, or surrounding text)."""
    # Check for figcaption in parent figure
    parent = el.getparent()
    if parent is not None and parent.tag == "figure":
        caption = parent.find("figcaption")
        if caption is not None:
            return (caption.text_content() or "").strip()[:200]

    # Use alt text as context
    alt = el.get("alt", "").strip()
    if alt:
        return alt

    # Try parent paragraph text
    if parent is not None and parent.tag == "p":
        return (parent.text_content() or "").strip()[:200]

    return ""


def _classify_image(src: str, el: HtmlElement, width: int | None, height: int | None) -> ImageType:
    """Classify an image as content, decorative, icon, or tracking."""
    # Tracking pixels: 1x1 or matching patterns
    if width == 1 and height == 1:
        return "tracking"
    if width is not None and height is not None and width <= 3 and height <= 3:
        return "tracking"
    if _TRACKING_PATTERNS.search(src):
        return "tracking"

    # Decorative: role="presentation" or empty alt with small size
    role = el.get("role", "")
    if role == "presentation":
        return "decorative"
    alt = el.get("alt", "")
    if alt == "" and el.get("alt") is not None:
        # Explicitly empty alt = decorative per HTML spec
        return "decorative"

    # Icons: small images in nav/buttons or matching class patterns
    cls = el.get("class", "")
    if _ICON_PATTERNS.search(cls) or _ICON_PATTERNS.search(src):
        return "icon"
    if width is not None and height is not None and max(width, height) < 48:
        return "icon"

    return "content"
