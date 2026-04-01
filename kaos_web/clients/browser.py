"""Browser client using Playwright for JavaScript-rendered pages.

Requires optional ``[browser]`` extra: ``pip install kaos-web[browser]``

Features:
- Lazy browser launch (~200-500ms, only on first fetch)
- Context-per-request isolation (separate cookies, storage)
- Configurable resource blocking (images, fonts, CSS)
- Wait strategies (load, domcontentloaded, networkidle, selector)
- Screenshot capture
- Authentication state persistence via storage_state
"""

from __future__ import annotations

import logging
from typing import Any, Self

from kaos_web.clients.config import BrowserClientConfig
from kaos_web.errors import WebBrowserError, WebNetworkError, WebTimeoutError
from kaos_web.models import WebRequest, WebResponse

logger = logging.getLogger(__name__)


class BrowserClient:
    """Async browser client wrapping Playwright.

    Launches a browser on first use (lazy) and creates an isolated context
    per request. Supports Chromium, Firefox, and WebKit.

    Usage::

        async with BrowserClient() as client:
            response = await client.fetch(WebRequest(url="https://example.com"))
            print(response.html)
    """

    def __init__(self, config: BrowserClientConfig | None = None) -> None:
        self._config = config or BrowserClientConfig()
        self._playwright: Any = None
        self._browser: Any = None

    async def _ensure_browser(self) -> Any:
        """Launch browser lazily on first use."""
        if self._browser is not None:
            return self._browser

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            msg = "Playwright is not installed. Install with: pip install kaos-web[browser]"
            raise ImportError(msg)  # noqa: B904

        self._playwright = await async_playwright().start()

        # Select browser engine
        cfg = self._config
        engine = getattr(self._playwright, cfg.browser_type)

        launch_kwargs: dict[str, Any] = {"headless": cfg.headless}
        if cfg.channel:
            launch_kwargs["channel"] = cfg.channel
        if cfg.proxy:
            launch_kwargs["proxy"] = {"server": cfg.proxy}

        self._browser = await engine.launch(**launch_kwargs)
        logger.debug("Launched %s browser (headless=%s)", cfg.browser_type, cfg.headless)
        return self._browser

    async def fetch(self, request: WebRequest) -> WebResponse:
        """Fetch a URL using browser rendering.

        Creates an isolated browser context per request. Supports
        wait_until, wait_for_selector, and screenshot options via
        the WebRequest model.
        """
        browser = await self._ensure_browser()
        cfg = self._config

        # Build context options
        context_opts: dict[str, Any] = {
            "viewport": {
                "width": cfg.viewport_width,
                "height": cfg.viewport_height,
            },
            "device_scale_factor": cfg.device_scale_factor,
            "is_mobile": cfg.is_mobile,
            "ignore_https_errors": cfg.ignore_https_errors,
        }

        if cfg.user_agent:
            context_opts["user_agent"] = cfg.user_agent
        if cfg.locale:
            context_opts["locale"] = cfg.locale
        if cfg.timezone:
            context_opts["timezone_id"] = cfg.timezone
        if cfg.color_scheme:
            context_opts["color_scheme"] = cfg.color_scheme
        if cfg.storage_state:
            context_opts["storage_state"] = cfg.storage_state
        if cfg.http_credentials:
            context_opts["http_credentials"] = {
                "username": cfg.http_credentials[0],
                "password": cfg.http_credentials[1],
            }
        if cfg.extra_headers:
            context_opts["extra_http_headers"] = cfg.extra_headers

        context = await browser.new_context(**context_opts)

        try:
            # Block unwanted resources
            if cfg.block_resources:
                resource_types = set(cfg.block_resources)

                async def _block_route(route: Any) -> None:
                    if route.request.resource_type in resource_types:
                        await route.abort()
                    else:
                        await route.continue_()

                await context.route("**/*", _block_route)

            page = await context.new_page()

            # Navigate
            wait_until = request.extra.get("wait_until", cfg.default_wait_until)
            timeout = int(request.timeout * 1000)  # ms

            try:
                response = await page.goto(
                    request.url,
                    wait_until=wait_until,
                    timeout=timeout,
                )
            except Exception as exc:
                _raise_browser_error(exc, request.url)

            # Wait for selector if specified
            selector = request.extra.get("wait_for_selector")
            if selector:
                try:
                    await page.wait_for_selector(selector, timeout=timeout)
                except Exception as exc:
                    _raise_browser_error(exc, request.url)

            # Extract content
            html = await page.content()
            title = await page.title()

            # Screenshot if requested
            screenshot: bytes | None = None
            if request.screenshot:
                screenshot = await page.screenshot(full_page=True)

            # Status code from navigation response
            status_code = response.status if response else 200

            # Headers from navigation response
            headers: dict[str, str] = {}
            if response:
                headers = dict(response.headers)

            return WebResponse(
                url=page.url,
                status_code=status_code,
                content_type=headers.get("content-type", "text/html"),
                html=html,
                headers=headers,
                title=title,
                screenshot=screenshot,
            )

        finally:
            await context.close()

    async def screenshot(
        self,
        url: str,
        *,
        full_page: bool = True,
        format: str = "png",
        quality: int | None = None,
    ) -> bytes:
        """Take a screenshot of a URL. Returns PNG or JPEG bytes."""
        browser = await self._ensure_browser()
        cfg = self._config

        context = await browser.new_context(
            viewport={
                "width": cfg.viewport_width,
                "height": cfg.viewport_height,
            },
        )
        try:
            page = await context.new_page()
            await page.goto(url, wait_until=cfg.default_wait_until)

            kwargs: dict[str, Any] = {
                "full_page": full_page,
                "type": format,
            }
            if quality is not None and format == "jpeg":
                kwargs["quality"] = quality

            return await page.screenshot(**kwargs)
        finally:
            await context.close()

    async def evaluate(self, url: str, expression: str) -> Any:
        """Navigate to URL and evaluate a JavaScript expression."""
        browser = await self._ensure_browser()

        context = await browser.new_context()
        try:
            page = await context.new_page()
            await page.goto(url, wait_until=self._config.default_wait_until)
            return await page.evaluate(expression)
        finally:
            await context.close()

    async def close(self) -> None:
        """Release browser resources."""
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    @property
    def config(self) -> BrowserClientConfig:
        """Current browser configuration."""
        return self._config


def _raise_browser_error(exc: Exception, url: str) -> None:
    """Map Playwright exceptions to WebError hierarchy."""
    msg = str(exc).lower()
    if "timeout" in msg:
        raise WebTimeoutError(
            f"Browser navigation timed out for {url}. "
            "The page may be slow to load or require different wait_until strategy.",
            url=url,
            timeout_type="navigation",
        ) from exc
    if "net::" in msg or "networkerror" in msg:
        raise WebNetworkError(
            f"Browser network error for {url}: {exc}",
            url=url,
        ) from exc
    raise WebBrowserError(
        f"Browser error for {url}: {exc}",
        url=url,
        retryable=False,
    ) from exc
