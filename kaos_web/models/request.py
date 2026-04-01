"""Web request model."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class WebRequest(BaseModel):
    """A web request to fetch content."""

    model_config = ConfigDict(frozen=True)

    url: str
    """The URL to fetch."""

    method: str = "GET"
    """HTTP method."""

    headers: dict[str, str] = {}
    """Additional HTTP headers."""

    timeout: float = 30.0
    """Request timeout in seconds."""

    follow_redirects: bool = True
    """Whether to follow HTTP redirects."""

    use_browser: bool = False
    """If True, use browser rendering (requires playwright)."""
