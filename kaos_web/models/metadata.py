"""Page metadata model."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class PageMetadata(BaseModel):
    """Structured metadata extracted from a web page."""

    model_config = ConfigDict(frozen=True)

    title: str | None = None
    """Page title (from <title>, og:title, or schema.org)."""

    author: str | None = None
    """Author (from meta author, schema.org, or byline detection)."""

    description: str | None = None
    """Page description (from meta description or og:description)."""

    date_published: str | None = None
    """Publication date (ISO 8601 string if available)."""

    date_modified: str | None = None
    """Last modified date (ISO 8601 string if available)."""

    url: str | None = None
    """Canonical URL (from <link rel="canonical"> or og:url)."""

    language: str | None = None
    """Content language (from <html lang> or Content-Language header)."""

    site_name: str | None = None
    """Site name (from og:site_name)."""

    image: str | None = None
    """Primary image URL (from og:image)."""

    structured_data: list[dict] = []
    """JSON-LD structured data extracted from the page."""

    opengraph: dict[str, str] = {}
    """OpenGraph metadata as key-value pairs."""
