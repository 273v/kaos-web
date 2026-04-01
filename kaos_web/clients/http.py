"""HTTP client using httpx with HTTP/2 support."""

from __future__ import annotations

import httpx

from kaos_web.models import WebRequest, WebResponse

_USER_AGENT = "KAOS-Web/0.1 (+https://273ventures.com/kaos-web)"


class HttpClient:
    """Async HTTP client wrapping httpx.AsyncClient."""

    def __init__(
        self,
        *,
        user_agent: str = _USER_AGENT,
        http2: bool = True,
        max_redirects: int = 10,
    ) -> None:
        self._client = httpx.AsyncClient(
            http2=http2,
            follow_redirects=True,
            max_redirects=max_redirects,
            headers={"User-Agent": user_agent},
        )

    async def fetch(self, request: WebRequest) -> WebResponse:
        """Fetch a URL and return the response."""
        headers = {**request.headers}

        resp = await self._client.request(
            method=request.method,
            url=request.url,
            headers=headers,
            timeout=request.timeout,
            follow_redirects=request.follow_redirects,
        )

        return WebResponse(
            url=str(resp.url),
            status_code=resp.status_code,
            content_type=resp.headers.get("content-type", ""),
            html=resp.text,
            headers=dict(resp.headers),
            elapsed_ms=resp.elapsed.total_seconds() * 1000 if resp.elapsed else 0.0,
        )

    async def close(self) -> None:
        """Release client resources."""
        await self._client.aclose()
