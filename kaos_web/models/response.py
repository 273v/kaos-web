"""Web response model."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class WebResponse(BaseModel):
    """Response from a web fetch."""

    model_config = ConfigDict(frozen=True)

    url: str
    """Final URL after redirects."""

    status_code: int
    """HTTP status code."""

    content_type: str = ""
    """Content-Type header value."""

    html: str = ""
    """Raw HTML content (decoded to string)."""

    headers: dict[str, str] = {}
    """Response headers."""

    elapsed_ms: float = 0.0
    """Request duration in milliseconds."""
