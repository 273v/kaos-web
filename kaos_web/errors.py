"""Web error hierarchy for kaos-web.

All errors subclass WebError, which carries structured details (url, status_code,
retryable flag) for middleware decision-making and agent-friendly error messages.
"""

from __future__ import annotations

from kaos_core.exceptions import KaosCoreError


class WebError(KaosCoreError):
    """Base error for all kaos-web operations."""

    def __init__(
        self,
        message: str,
        *,
        url: str = "",
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        self.url = url
        self.status_code = status_code
        self.retryable = retryable
        super().__init__(message)


class WebTimeoutError(WebError):
    """Request timed out (connect, read, write, or pool)."""

    def __init__(
        self,
        message: str,
        *,
        url: str = "",
        timeout_type: str = "unknown",
    ) -> None:
        self.timeout_type = timeout_type
        super().__init__(message, url=url, retryable=True)


class WebNetworkError(WebError):
    """Network-level failure (DNS, connection refused, etc.)."""

    def __init__(self, message: str, *, url: str = "") -> None:
        super().__init__(message, url=url, retryable=True)


class WebServerError(WebError):
    """Server returned 5xx status code."""

    def __init__(self, message: str, *, url: str = "", status_code: int = 500) -> None:
        super().__init__(message, url=url, status_code=status_code, retryable=True)


class WebClientError(WebError):
    """Server returned 4xx status code (not retryable)."""

    def __init__(self, message: str, *, url: str = "", status_code: int = 400) -> None:
        super().__init__(message, url=url, status_code=status_code, retryable=False)


class WebRateLimitError(WebError):
    """Server returned 429 Too Many Requests."""

    def __init__(
        self,
        message: str,
        *,
        url: str = "",
        retry_after: float | None = None,
    ) -> None:
        self.retry_after = retry_after
        super().__init__(message, url=url, status_code=429, retryable=True)


class WebProxyError(WebError):
    """Proxy connection or authentication failure."""

    def __init__(self, message: str, *, url: str = "") -> None:
        super().__init__(message, url=url, retryable=False)


class WebRedirectError(WebError):
    """Too many redirects."""

    def __init__(self, message: str, *, url: str = "") -> None:
        super().__init__(message, url=url, retryable=False)


class WebBrowserError(WebError):
    """Browser automation error (Playwright)."""

    def __init__(self, message: str, *, url: str = "", retryable: bool = False) -> None:
        super().__init__(message, url=url, retryable=retryable)


class BodyTooLargeError(WebError):
    """Response body exceeded the configured ``KAOS_WEB_MAX_BODY_BYTES`` cap.

    Raised by ``HttpClient.fetch`` (when ``Content-Length`` declares a body
    over the cap, OR when streamed bytes accumulate past it),
    ``BrowserClient.fetch`` (when ``page.content()`` returns oversized HTML),
    and the sitemap gzip decompressor (when the decompressed payload would
    exceed the cap).

    The cap is a memory-safety boundary, not a quality signal. If you're
    legitimately working with large bodies (data exports, large sitemaps,
    archival pages), raise ``KAOS_WEB_MAX_BODY_BYTES`` for the run.
    """

    def __init__(
        self,
        message: str,
        *,
        url: str = "",
        size_bytes: int | None = None,
        max_bytes: int | None = None,
    ) -> None:
        self.size_bytes = size_bytes
        self.max_bytes = max_bytes
        super().__init__(message, url=url, retryable=False)
