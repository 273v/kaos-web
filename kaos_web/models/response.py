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

    cookies: dict[str, str] = {}
    """Response cookies."""

    error: str | None = None
    """Error message if request failed but produced a partial response."""

    @property
    def ok(self) -> bool:
        """True if status code is 2xx or 3xx."""
        return 200 <= self.status_code < 400
