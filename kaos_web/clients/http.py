"""HTTP client using httpx with connection pooling, auth, proxy, SSL, and error mapping."""

from __future__ import annotations

import ssl
from typing import Self

import httpx

from kaos_core.logging import get_logger
from kaos_web.clients.config import HttpClientConfig
from kaos_web.errors import (
    BodyTooLargeError,
    WebClientError,
    WebNetworkError,
    WebProxyError,
    WebRateLimitError,
    WebRedirectError,
    WebServerError,
    WebTimeoutError,
)
from kaos_web.models import WebRequest, WebResponse

logger = get_logger(__name__)


class HttpClient:
    """Async HTTP client wrapping httpx.AsyncClient.

    Features:
    - Connection pooling with configurable limits
    - HTTP/2 support
    - Fine-grained timeouts (connect, read, write, pool)
    - SSL/TLS configuration (custom CA bundles, client certs, disable verification)
    - Proxy support (HTTP, HTTPS, SOCKS)
    - Authentication (Basic, Bearer token, API key)
    - Cookie persistence across requests
    - Structured error mapping to WebError hierarchy
    """

    def __init__(self, config: HttpClientConfig | None = None) -> None:
        self._config = config or HttpClientConfig()
        self._client = self._build_client()
        self._chain = self._build_middleware_chain()

    def _build_client(self) -> httpx.AsyncClient:
        """Build httpx.AsyncClient from config."""
        cfg = self._config

        # Timeouts
        timeout = httpx.Timeout(
            connect=cfg.connect_timeout,
            read=cfg.read_timeout,
            write=cfg.write_timeout,
            pool=cfg.pool_timeout,
        )

        # Connection pool limits
        limits = httpx.Limits(
            max_connections=cfg.max_connections,
            max_keepalive_connections=cfg.max_keepalive_connections,
            keepalive_expiry=cfg.keepalive_expiry,
        )

        # Validate SSL config
        if cfg.client_cert and not cfg.client_key:
            msg = "client_cert requires client_key"
            raise ValueError(msg)

        # SSL verification
        verify: bool | ssl.SSLContext = cfg.verify_ssl
        if cfg.ca_bundle:
            ctx = ssl.create_default_context(cafile=cfg.ca_bundle)
            if cfg.client_cert:
                ctx.load_cert_chain(certfile=cfg.client_cert, keyfile=cfg.client_key)
            verify = ctx
        elif cfg.client_cert:
            ctx = ssl.create_default_context()
            ctx.load_cert_chain(certfile=cfg.client_cert, keyfile=cfg.client_key)
            verify = ctx
        elif not cfg.verify_ssl:
            verify = False

        # Authentication
        auth = self._build_auth()

        # Default headers — realistic UA by default
        from kaos_web.clients.user_agents import KAOS_BOT_UA, random_desktop_ua

        if cfg.user_agent:
            ua = cfg.user_agent
        elif cfg.randomize_user_agent:
            ua = random_desktop_ua()
        else:
            ua = KAOS_BOT_UA
        headers = {"User-Agent": ua}

        # API key goes in headers, not in httpx auth
        if cfg.api_key:
            headers[cfg.api_key_header] = cfg.api_key

        return httpx.AsyncClient(
            http2=True,
            timeout=timeout,
            limits=limits,
            verify=verify,
            proxy=cfg.proxy,
            auth=auth,
            headers=headers,
            follow_redirects=cfg.follow_redirects,
            max_redirects=cfg.max_redirects,
        )

    def _build_auth(self) -> httpx.Auth | None:
        """Build httpx auth from config (first non-None wins)."""
        cfg = self._config
        if cfg.basic_auth:
            return httpx.BasicAuth(username=cfg.basic_auth[0], password=cfg.basic_auth[1])
        if cfg.bearer_token:
            return _BearerAuth(cfg.bearer_token)
        return None

    def _build_middleware_chain(self):
        """Build the middleware chain from config.

        Chain order (outermost first): retry → rate_limit → robots → cache → _raw_fetch
        - Retry wraps everything: if inner middleware or fetch fails, retry catches it
        - Rate limit throttles before hitting the network
        - Robots checks before fetching
        - Cache is innermost: returns cached response before network, caches after
        """
        from kaos_web.middleware.base import MiddlewareChain

        chain = MiddlewareChain(self._raw_fetch)
        cfg = self._config

        # Add middleware in reverse order of desired execution
        # (MiddlewareChain reverses internally — first added = outermost)
        if cfg.enable_retry:
            from kaos_web.middleware.retry import RetryConfig, RetryMiddleware

            chain.add(RetryMiddleware(RetryConfig(max_retries=cfg.max_retries)))

        if cfg.enable_rate_limit:
            from kaos_web.middleware.rate_limit import RateLimitConfig, RateLimitMiddleware

            chain.add(
                RateLimitMiddleware(RateLimitConfig(requests_per_second=cfg.requests_per_second))
            )

        if cfg.enable_robots:
            from kaos_web.middleware.robots import RobotsMiddleware

            chain.add(RobotsMiddleware())

        if cfg.enable_cache:
            from kaos_web.middleware.cache import CacheConfig, CacheMiddleware

            chain.add(CacheMiddleware(CacheConfig(default_ttl=cfg.cache_ttl)))

        return chain

    async def fetch(self, request: WebRequest) -> WebResponse:
        """Fetch a URL through the middleware chain.

        Goes through: retry → rate_limit → robots → cache → raw HTTP.
        Configure middleware via HttpClientConfig flags.
        """
        return await self._chain.execute(request)

    async def _raw_fetch(self, request: WebRequest) -> WebResponse:
        """Raw HTTP fetch without middleware. Maps httpx exceptions to WebError.

        Streams the response body and enforces ``KaosWebSettings.max_body_bytes``
        (WEB5-007 / audit-04 finding #7) — pre-checks ``Content-Length`` then
        accumulates chunked bytes with a running tally. Aborts with
        ``BodyTooLargeError`` before materializing an oversized body.

        WEB5-001: gate the outbound URL through ``validate_url`` BEFORE
        any socket I/O. Strict by default — blocks link-local metadata,
        loopback, RFC1918 private ranges, and non-(http|https) schemes.
        Note: ``follow_redirects=True`` only validates the original URL;
        the redirect target is NOT re-validated (httpx auto-follows
        without a per-hop hook). Closing this gap requires a connect-
        time hook on the HTTP client (kaos-core follow-up).
        """
        from kaos_web.security import validate_url
        from kaos_web.settings import KaosWebSettings

        max_body_bytes = KaosWebSettings().max_body_bytes
        url = validate_url(request.url)
        headers = {**request.headers} if request.headers else {}

        # Per-domain UA routing — gov hosts (sec.gov, govinfo, eCFR,
        # courtlistener, …) prefer the honest ``KAOS_BOT_UA`` identifier
        # over randomized Chrome strings. Applied here for the rare
        # httpx-direct path; the canonical anti-bot stack lives in
        # ``BrowserClient`` (Playwright + viewport + locale + sec-ch-ua
        # headers + UA rotation, ported from kelvin-legal-intelligence).
        # Caller-supplied User-Agent always wins.
        if "User-Agent" not in headers and "user-agent" not in headers:
            from urllib.parse import urlsplit

            from kaos_web.clients.user_agents import (
                KAOS_BOT_UA,
                _host_matches_bot_friendly,
            )

            try:
                host = urlsplit(url).hostname or ""
            except (ValueError, AttributeError):
                host = ""
            if _host_matches_bot_friendly(host):
                headers["User-Agent"] = KAOS_BOT_UA

        try:
            resp, body_bytes = await self._streamed_request(
                method=request.method,
                url=url,
                headers=headers,
                timeout=request.timeout,
                follow_redirects=request.follow_redirects,
                max_body_bytes=max_body_bytes,
            )
        except httpx.ConnectTimeout as exc:
            raise WebTimeoutError(
                f"Connection timed out for {url}. "
                f"The server may be unreachable or DNS resolution is slow. "
                f"Try increasing connect_timeout (current: {self._config.connect_timeout}s).",
                url=url,
                timeout_type="connect",
            ) from exc
        except httpx.ReadTimeout as exc:
            raise WebTimeoutError(
                f"Read timed out for {url}. "
                f"The server accepted the connection but stopped sending data. "
                f"Try increasing read_timeout (current: {self._config.read_timeout}s).",
                url=url,
                timeout_type="read",
            ) from exc
        except httpx.WriteTimeout as exc:
            raise WebTimeoutError(
                f"Write timed out for {url}.",
                url=url,
                timeout_type="write",
            ) from exc
        except httpx.PoolTimeout as exc:
            raise WebTimeoutError(
                f"Connection pool exhausted for {url}. "
                f"All {self._config.max_connections} connections are in use. "
                f"Try increasing max_connections or reducing concurrency.",
                url=url,
                timeout_type="pool",
            ) from exc
        except httpx.ConnectError as exc:
            raise WebNetworkError(
                f"Failed to connect to {url}. "
                f"DNS resolution failed, connection refused, or network is unreachable.",
                url=url,
            ) from exc
        except httpx.ProxyError as exc:
            raise WebProxyError(
                f"Proxy error for {url}: {exc}. "
                f"Check proxy configuration (current: {self._config.proxy}).",
                url=url,
            ) from exc
        except httpx.TooManyRedirects as exc:
            raise WebRedirectError(
                f"Too many redirects for {url} (max: {self._config.max_redirects}). "
                f"The server may be in a redirect loop.",
                url=url,
            ) from exc
        except httpx.NetworkError as exc:
            raise WebNetworkError(
                f"Network error for {url}: {exc}",
                url=url,
            ) from exc
        except httpx.TimeoutException as exc:
            raise WebTimeoutError(
                f"Request timed out for {url}: {exc}",
                url=url,
                timeout_type="unknown",
            ) from exc

        # Map HTTP status codes to errors
        final_url = str(resp.url)
        status = resp.status_code

        if status == 429:
            retry_after = _parse_retry_after(resp.headers.get("retry-after"))
            raise WebRateLimitError(
                f"Rate limited (429) by {final_url}. "
                + (f"Retry after {retry_after:.0f}s." if retry_after else "No Retry-After header.")
                + " Reduce request frequency or add rate limiting middleware.",
                url=final_url,
                retry_after=retry_after,
            )

        if status >= 500:
            raise WebServerError(
                f"Server error ({status}) from {final_url}. "
                f"The server encountered an internal error. This may be transient.",
                url=final_url,
                status_code=status,
            )

        if status >= 400:
            raise WebClientError(
                f"Client error ({status}) from {final_url}. "
                "The request was rejected. Check URL, authentication, or parameters.",
                url=final_url,
                status_code=status,
            )

        # Decode the streamed body using the response's declared encoding,
        # falling back to UTF-8 with a replace-error policy so partial /
        # mis-declared encodings don't crash the pipeline.
        encoding = resp.encoding or "utf-8"
        html = body_bytes.decode(encoding, errors="replace")

        return WebResponse(
            url=final_url,
            status_code=status,
            content_type=resp.headers.get("content-type", ""),
            html=html,
            headers=dict(resp.headers),
            elapsed_ms=resp.elapsed.total_seconds() * 1000 if resp.elapsed else 0.0,
            cookies=dict(resp.cookies.items()),
        )

    async def _streamed_request(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str],
        timeout: float | None,
        follow_redirects: bool,
        max_body_bytes: int,
    ) -> tuple[httpx.Response, bytes]:
        """Issue a streamed request and accumulate the body with a size cap.

        Two layers of defense (WEB5-007):

        1. **Pre-check ``Content-Length``** — if the server declares the
           body size and it exceeds the cap, raise immediately without
           reading any body bytes. Catches the well-behaved-but-large
           case efficiently.
        2. **Streamed accumulation with running tally** — for chunked
           responses (no ``Content-Length``) or responses that lie about
           their size, accumulate ``aiter_bytes()`` chunks and abort as
           soon as the total exceeds the cap.

        Either path raises ``BodyTooLargeError`` with the URL, observed
        size, and the configured cap so the agent can adjust.

        audit-04 F-001 (SECURITY): when ``follow_redirects`` is True we
        do manual redirect handling so each redirect target is
        revalidated against ``kaos_web.security.validate_url``. httpx's
        built-in redirect handling only validates the original URL —
        which the security policy gate previously documented as a known
        gap. The manual loop preserves httpx's method-rewriting +
        cross-origin header stripping (via ``response.next_request``)
        while inserting the policy check on the redirect chain. Each
        hop counts against ``self._config.max_redirects``; exhaustion
        raises ``httpx.TooManyRedirects`` (mapped to ``WebRedirectError``
        by the caller).
        """
        # Manual redirect loop with per-hop validate_url, per audit-04
        # F-001. We always set follow_redirects=False on the inner
        # httpx call and re-validate any Location target ourselves.
        from kaos_web.security import validate_url

        current_method = method
        current_url = url
        current_headers = headers
        redirects_followed = 0
        max_redirects = self._config.max_redirects

        while True:
            async with self._client.stream(
                method=current_method,
                url=current_url,
                headers=current_headers,
                timeout=timeout,
                follow_redirects=False,
            ) as resp:
                if follow_redirects and resp.is_redirect:
                    if redirects_followed >= max_redirects:
                        raise httpx.TooManyRedirects(
                            f"exceeded {max_redirects} redirects from {url}",
                            request=resp.request,
                        )
                    nxt = resp.next_request
                    if nxt is None:
                        # 3xx with no Location — terminate at this response
                        # rather than infinite-loop.
                        pass
                    else:
                        next_url = str(nxt.url)
                        # Re-validate against the policy gate. Raises
                        # ``InvalidURLError`` (a ``WebError`` subclass) for
                        # SSRF targets (loopback / RFC1918 / link-local /
                        # metadata-service / non-http(s) schemes). The
                        # caller's ``_raw_fetch`` exception mapping turns
                        # that into the right user-facing error.
                        validate_url(next_url)
                        redirects_followed += 1
                        current_url = next_url
                        current_method = nxt.method
                        current_headers = dict(nxt.headers.items())
                        continue

                declared = resp.headers.get("content-length")
                if declared is not None:
                    try:
                        declared_int = int(declared)
                    except ValueError:
                        declared_int = -1
                    if declared_int > max_body_bytes:
                        raise BodyTooLargeError(
                            f"Response body for {current_url} declares "
                            f"Content-Length: {declared_int} bytes (cap: "
                            f"{max_body_bytes}). Aborting before body read. "
                            f"Increase KAOS_WEB_MAX_BODY_BYTES if you intend "
                            f"to fetch payloads of this size.",
                            url=current_url,
                            size_bytes=declared_int,
                            max_bytes=max_body_bytes,
                        )

                chunks: list[bytes] = []
                total = 0
                async for chunk in resp.aiter_bytes():
                    total += len(chunk)
                    if total > max_body_bytes:
                        raise BodyTooLargeError(
                            f"Response body for {current_url} streamed past "
                            f"{max_body_bytes} bytes (no Content-Length, or "
                            f"server lied). Aborted at {total} bytes. "
                            f"Increase KAOS_WEB_MAX_BODY_BYTES if you intend "
                            f"to fetch payloads of this size.",
                            url=current_url,
                            size_bytes=total,
                            max_bytes=max_body_bytes,
                        )
                    chunks.append(chunk)
                return resp, b"".join(chunks)

    async def close(self) -> None:
        """Release client resources."""
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    @property
    def config(self) -> HttpClientConfig:
        """Current client configuration."""
        return self._config


class _BearerAuth(httpx.Auth):
    """Bearer token authentication."""

    def __init__(self, token: str) -> None:
        self._token = token

    def auth_flow(self, request: httpx.Request):
        request.headers["Authorization"] = f"Bearer {self._token}"
        yield request


def _parse_retry_after(value: str | None) -> float | None:
    """Parse Retry-After header (seconds or HTTP-date)."""
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None
