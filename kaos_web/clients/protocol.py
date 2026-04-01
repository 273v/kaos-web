"""Web client protocol — abstract interface for HTTP and browser clients."""

from __future__ import annotations

from typing import Protocol

from kaos_web.models import WebRequest, WebResponse


class WebClientProtocol(Protocol):
    """Protocol that both HttpClient and BrowserClient implement."""

    async def fetch(self, request: WebRequest) -> WebResponse:
        """Fetch a URL and return the response."""
        ...

    async def close(self) -> None:
        """Release client resources."""
        ...
