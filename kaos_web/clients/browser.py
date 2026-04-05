"""Browser client using Playwright for JavaScript-rendered pages.

Requires optional ``[browser]`` extra: ``pip install kaos-web[browser]``

Features:
- Lazy browser launch (~200-500ms, only on first fetch)
- Context-per-request isolation (separate cookies, storage)
- Named context pooling (session persistence via context_id)
- Resource blocking (images, fonts, CSS, media)
- Wait strategies (load, domcontentloaded, networkidle, selector)
- Screenshot capture
- Auth state persistence via storage_state
- Interactive mode: click, fill, evaluate, accessibility snapshots on persistent pages
"""

from __future__ import annotations

import contextlib
from typing import Any, Never, Self

from kaos_core.logging import get_logger
from kaos_web.clients.config import BrowserClientConfig
from kaos_web.errors import WebBrowserError, WebNetworkError, WebTimeoutError
from kaos_web.models import WebRequest, WebResponse

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Response body capture defaults
# ---------------------------------------------------------------------------
_DEFAULT_CAPTURE_RESOURCE_TYPES = frozenset({"fetch", "xhr"})
_DEFAULT_CAPTURE_CONTENT_TYPES = frozenset(
    {
        "application/json",
        "text/html",
        "text/plain",
        "text/xml",
        "application/xml",
        "text/csv",
        "application/ld+json",
    }
)
_DEFAULT_MAX_BODY_SIZE = 1_048_576  # 1 MB


class BrowserClient:
    """Async browser client wrapping Playwright.

    Launches a browser on first use (lazy) and creates an isolated context
    per request. Supports Chromium, Firefox, and WebKit.

    Named contexts (via ``context_id``) persist pages across calls, enabling
    multi-step interactive workflows: navigate → click → fill → screenshot.

    Usage::

        async with BrowserClient() as client:
            # Simple fetch (page cleaned up after)
            response = await client.fetch(WebRequest(url="https://example.com"))

            # Interactive session (page persists for interaction)
            response = await client.fetch(
                WebRequest(url="https://example.com", extra={"context_id": "s1"})
            )
            await client.click("s1", "button#accept-cookies")
            await client.fill("s1", "input#search", "hello")
            snapshot = await client.get_snapshot("s1")
            await client.close_context("s1")
    """

    def __init__(self, config: BrowserClientConfig | None = None) -> None:
        self._config = config or BrowserClientConfig()
        self._playwright: Any = None
        self._browser: Any = None
        self._contexts: dict[str, Any] = {}  # Named context pool
        self._pages: dict[str, Any] = {}  # Active pages by context_id

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

    async def _get_or_create_context(
        self, browser: Any, context_id: str | None
    ) -> tuple[Any, bool]:
        """Get existing named context or create a new one.

        Returns (context, owns_context) where owns_context=True means the caller
        should close it (unnamed/ephemeral contexts).
        """
        cfg = self._config

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

        # Named context: reuse or create
        owns_context = context_id is None
        if context_id and context_id in self._contexts:
            context = self._contexts[context_id]
        else:
            context = await browser.new_context(**context_opts)
            if context_id:
                self._contexts[context_id] = context

            # Block unwanted resources on new contexts
            if cfg.block_resources:
                resource_types = set(cfg.block_resources)

                async def _block_route(route: Any) -> None:
                    if route.request.resource_type in resource_types:
                        await route.abort()
                    else:
                        await route.continue_()

                await context.route("**/*", _block_route)

        return context, owns_context

    async def fetch(self, request: WebRequest) -> WebResponse:
        """Fetch a URL using browser rendering.

        By default, creates an isolated browser context per request.
        Use ``request.extra["context_id"]`` for a named persistent context
        (cookies, storage, and the page persist across requests with the same ID).

        Page lifecycle is managed via a ``page_stored`` flag:
        - On success with ``context_id``: page is stored in ``self._pages``
        - On success without ``context_id``: page is closed
        - On any failure: the inner ``finally`` block closes the page

        Supported ``request.extra`` keys:
        - ``context_id``: Named context for persistent sessions
        - ``wait_until``: Navigation wait strategy
        - ``wait_for_selector``: CSS selector to wait for after navigation
        - ``dismiss_overlays``: Auto-dismiss known cookie consent banners
        """
        browser = await self._ensure_browser()
        context_id = request.extra.get("context_id")
        context, owns_context = await self._get_or_create_context(browser, context_id)

        try:
            # For named contexts, close any existing page before creating a new one
            if context_id and context_id in self._pages:
                old_page = self._pages.pop(context_id)
                await old_page.close()

            page = await context.new_page()
            page_stored = False

            try:
                # Navigate
                wait_until = request.extra.get("wait_until", self._config.default_wait_until)
                timeout = int(request.timeout * 1000)  # ms

                try:
                    response = await page.goto(
                        request.url,
                        wait_until=wait_until,
                        timeout=timeout,
                    )
                except Exception as exc:
                    _raise_browser_error(exc, request.url, "navigation")

                # Dismiss known cookie consent banners before content extraction.
                # Must happen before wait_for_selector — overlays can block content.
                if request.extra.get("dismiss_overlays", False):
                    try:
                        from kaos_web.browser_page_prep import dismiss_cookie_banners

                        await dismiss_cookie_banners(page)
                    except Exception:
                        pass  # Never let banner dismissal break content extraction

                # Wait for content to settle on JS-rendered pages.
                # Skipped when wait_for_selector is explicit (caller knows what to wait for).
                selector = request.extra.get("wait_for_selector")
                if not selector and request.extra.get("wait_for_settled", False):
                    try:
                        from kaos_web.browser_page_prep import wait_for_content_settled

                        await wait_for_content_settled(page)
                    except Exception:
                        pass  # Never let settling detection break extraction

                # Wait for selector if specified
                if selector:
                    try:
                        await page.wait_for_selector(selector, timeout=timeout)
                    except Exception as exc:
                        _raise_browser_error(exc, request.url, "wait_for_selector")

                # Extract content
                html = await page.content()
                title = await page.title()

                # Screenshot if requested
                screenshot: bytes | None = None
                if request.screenshot:
                    screenshot = await page.screenshot(full_page=True)

                # Response metadata
                status_code = response.status if response else 200
                headers: dict[str, str] = dict(response.headers) if response else {}

                # For named contexts, keep the page alive for interaction
                if context_id:
                    self._pages[context_id] = page
                    page_stored = True

                return WebResponse(
                    url=page.url if context_id else request.url,
                    status_code=status_code,
                    content_type=headers.get("content-type", "text/html"),
                    html=html,
                    headers=headers,
                    title=title,
                    screenshot=screenshot,
                )

            finally:
                # Centralized page cleanup: if the page was not stored in a
                # named context (success or failure), close it to prevent leaks.
                if not page_stored:
                    with contextlib.suppress(Exception):
                        await page.close()

        finally:
            # Only close unnamed (per-request) contexts
            if owns_context:
                await context.close()

    # ── Interactive methods (require named context with active page) ──

    def _require_page(self, context_id: str) -> Any:
        """Get the active page for a context, raising if not found."""
        page = self._pages.get(context_id)
        if page is None:
            available = list(self._pages.keys()) if self._pages else []
            raise WebBrowserError(
                f"No active page for context '{context_id}'. "
                f"First navigate with fetch(WebRequest(url=..., "
                f"extra={{'context_id': '{context_id}'}})) "
                f"to establish a page. Active contexts: {available}",
                url="",
                retryable=False,
            )
        return page

    async def click(self, context_id: str, selector: str, **kwargs: Any) -> None:
        """Click an element on the active page in a named context.

        Args:
            context_id: Named context with an active page.
            selector: CSS selector for the element to click.
            **kwargs: Extra Playwright click options (timeout, force, etc.).
        """
        page = self._require_page(context_id)
        timeout = kwargs.pop("timeout", self._config.default_timeout)
        try:
            await page.click(selector, timeout=timeout, **kwargs)
        except Exception as exc:
            _raise_browser_error(exc, page.url, "click")

    async def fill(self, context_id: str, selector: str, value: str, **kwargs: Any) -> None:
        """Fill an input field on the active page.

        Args:
            context_id: Named context with an active page.
            selector: CSS selector for the input element.
            value: Text value to fill.
            **kwargs: Extra Playwright fill options.
        """
        page = self._require_page(context_id)
        timeout = kwargs.pop("timeout", self._config.default_timeout)
        try:
            await page.fill(selector, value, timeout=timeout, **kwargs)
        except Exception as exc:
            _raise_browser_error(exc, page.url, "fill")

    async def select_option(
        self, context_id: str, selector: str, value: str, **kwargs: Any
    ) -> list[str]:
        """Select an option from a <select> element.

        Args:
            context_id: Named context with an active page.
            selector: CSS selector for the select element.
            value: Option value to select.
            **kwargs: Extra Playwright select options.
        """
        page = self._require_page(context_id)
        timeout = kwargs.pop("timeout", self._config.default_timeout)
        try:
            return await page.select_option(selector, value, timeout=timeout, **kwargs)
        except Exception as exc:
            _raise_browser_error(exc, page.url, "select_option")

    async def type_text(self, context_id: str, selector: str, text: str, **kwargs: Any) -> None:
        """Type text character-by-character (simulating keystrokes).

        Unlike fill(), this fires keydown/keypress/keyup events for each character.
        Useful for inputs with JavaScript listeners (autocomplete, etc.).

        Args:
            context_id: Named context with an active page.
            selector: CSS selector for the input element.
            text: Text to type character-by-character.
            **kwargs: Extra options (delay between keystrokes, etc.).
        """
        page = self._require_page(context_id)
        timeout = kwargs.pop("timeout", self._config.default_timeout)
        delay = kwargs.pop("delay", 0)
        try:
            await page.type(selector, text, timeout=timeout, delay=delay, **kwargs)
        except Exception as exc:
            _raise_browser_error(exc, page.url, "type")

    async def press_key(self, context_id: str, selector: str, key: str, **kwargs: Any) -> None:
        """Press a keyboard key on an element (e.g., 'Enter', 'Tab', 'Escape').

        Args:
            context_id: Named context with an active page.
            selector: CSS selector for the element to focus.
            key: Key to press (Playwright key name, e.g., 'Enter', 'ArrowDown').
            **kwargs: Extra Playwright press options.
        """
        page = self._require_page(context_id)
        timeout = kwargs.pop("timeout", self._config.default_timeout)
        try:
            await page.press(selector, key, timeout=timeout, **kwargs)
        except Exception as exc:
            _raise_browser_error(exc, page.url, "press_key")

    async def get_snapshot(self, context_id: str) -> str:
        """Get the accessibility tree of the active page.

        Returns a text representation of the page's ARIA tree (via Playwright's
        ``locator.aria_snapshot()``). The output lists interactive elements like
        headings, links, buttons, and inputs in an indented text format — ideal
        for agents to understand page structure and find selectors.
        """
        page = self._require_page(context_id)
        try:
            snapshot = await page.locator("body").aria_snapshot()
            return snapshot or ""
        except Exception as exc:
            _raise_browser_error(exc, page.url, "snapshot")

    async def get_content(self, context_id: str) -> str:
        """Get the current HTML content of the active page."""
        page = self._require_page(context_id)
        return await page.content()

    async def get_url(self, context_id: str) -> str:
        """Get the current URL of the active page."""
        page = self._require_page(context_id)
        return page.url

    async def screenshot_context(
        self,
        context_id: str,
        *,
        full_page: bool = True,
        format: str = "png",
        quality: int | None = None,
    ) -> bytes:
        """Take a screenshot of the active page in a named context."""
        page = self._require_page(context_id)
        kwargs: dict[str, Any] = {
            "full_page": full_page,
            "type": format,
        }
        if quality is not None and format == "jpeg":
            kwargs["quality"] = quality
        return await page.screenshot(**kwargs)

    async def evaluate_in_context(self, context_id: str, expression: str) -> Any:
        """Evaluate a JavaScript expression on the active page in a named context."""
        page = self._require_page(context_id)
        try:
            return await page.evaluate(expression)
        except Exception as exc:
            _raise_browser_error(exc, page.url, "evaluate")

    # ── Standalone convenience methods (no context_id needed) ──

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

    # ── Cookie / Storage methods ──

    async def get_cookies(self, context_id: str, urls: list[str] | None = None) -> list[dict]:
        """Get cookies from a named context.

        Args:
            context_id: Named context.
            urls: Optional list of URLs to filter cookies by. If omitted, returns all.
        """
        if context_id not in self._contexts:
            raise WebBrowserError(
                f"No context '{context_id}'. Use kaos-web-browser-navigate first.",
                url="",
                retryable=False,
            )
        context = self._contexts[context_id]
        if urls:
            return await context.cookies(urls)
        return await context.cookies()

    async def set_cookies(self, context_id: str, cookies: list[dict]) -> None:
        """Add cookies to a named context.

        Args:
            context_id: Named context.
            cookies: List of cookie dicts (name, value, domain/url required).
        """
        if context_id not in self._contexts:
            raise WebBrowserError(
                f"No context '{context_id}'. Use kaos-web-browser-navigate first.",
                url="",
                retryable=False,
            )
        context = self._contexts[context_id]
        await context.add_cookies(cookies)

    async def save_storage_state(self, context_id: str, path: str) -> str:
        """Save the browser context storage state (cookies + localStorage) to a file.

        Args:
            context_id: Named context.
            path: File path to save state to (JSON).

        Returns:
            The path where state was saved.
        """
        if context_id not in self._contexts:
            raise WebBrowserError(
                f"No context '{context_id}'. Use kaos-web-browser-navigate first.",
                url="",
                retryable=False,
            )
        context = self._contexts[context_id]
        await context.storage_state(path=path)
        return path

    # ── Network monitoring ──

    async def enable_request_logging(
        self,
        context_id: str,
        *,
        capture_bodies: bool = False,
        resource_types: frozenset[str] | None = None,
        max_body_size: int = _DEFAULT_MAX_BODY_SIZE,
    ) -> None:
        """Start recording network requests for a named context.

        Must be called before navigation to capture all requests.
        Results are stored in ``_request_logs[context_id]``.

        Args:
            context_id: Named browser context.
            capture_bodies: Also capture response bodies for matching requests.
            resource_types: Resource types to capture bodies for (default: fetch, xhr).
            max_body_size: Maximum body size in bytes (default: 1 MB).
        """
        if context_id not in self._contexts:
            raise WebBrowserError(
                f"No context '{context_id}'. Use kaos-web-browser-navigate first.",
                url="",
                retryable=False,
            )
        page = self._require_page(context_id)

        # Initialize log storage
        if not hasattr(self, "_request_logs"):
            self._request_logs: dict[str, list[dict]] = {}
        self._request_logs[context_id] = []

        log = self._request_logs[context_id]

        # Initialize body storage when capture is enabled
        if capture_bodies:
            if not hasattr(self, "_response_bodies"):
                self._response_bodies: dict[str, dict[int, dict[str, Any]]] = {}
            self._response_bodies[context_id] = {}

        capture_resource_types = resource_types or _DEFAULT_CAPTURE_RESOURCE_TYPES
        capture_ct_prefixes = _DEFAULT_CAPTURE_CONTENT_TYPES

        def _on_request(request: Any) -> None:
            log.append(
                {
                    "id": len(log),
                    "url": request.url,
                    "method": request.method,
                    "resource_type": request.resource_type,
                    "headers": dict(request.headers),
                    "post_data": request.post_data,
                    "is_navigation_request": request.is_navigation_request(),
                }
            )

        if capture_bodies:
            bodies = self._response_bodies[context_id]

            async def _on_response(response: Any) -> None:
                # Phase 1: metadata matching (same as sync handler)
                matched_entry: dict[str, Any] | None = None
                for entry in reversed(log):
                    if entry["url"] == response.url and "status" not in entry:
                        entry["status"] = response.status
                        entry["status_text"] = response.status_text
                        entry["response_headers"] = dict(response.headers)
                        matched_entry = entry
                        break

                if matched_entry is None:
                    return

                # Phase 2: sync filters — decide whether to capture body
                if 300 <= response.status < 400:
                    return  # redirects have no body

                rt = response.request.resource_type
                if rt not in capture_resource_types:
                    return

                ct = response.headers.get("content-type", "")
                ct_base = ct.lower().split(";")[0].strip()
                if not any(ct_base.startswith(prefix) for prefix in capture_ct_prefixes):
                    return

                # Check Content-Length header before async body fetch
                cl_str = response.headers.get("content-length", "")
                if cl_str.isdigit() and int(cl_str) > max_body_size:
                    matched_entry["has_body"] = False
                    matched_entry["body_reason"] = "too_large"
                    matched_entry["body_content_length"] = int(cl_str)
                    return

                # Phase 3: async body fetch
                try:
                    body_bytes = await response.body()
                except Exception:
                    matched_entry["has_body"] = False
                    matched_entry["body_reason"] = "fetch_failed"
                    return

                truncated = False
                if len(body_bytes) > max_body_size:
                    body_bytes = body_bytes[:max_body_size]
                    truncated = True

                bodies[matched_entry["id"]] = {
                    "body": body_bytes,
                    "content_type": ct,
                    "size": len(body_bytes),
                    "truncated": truncated,
                }
                matched_entry["has_body"] = True
                matched_entry["body_size"] = len(body_bytes)
                matched_entry["body_truncated"] = truncated

        else:

            def _on_response(response: Any) -> None:  # type: ignore[misc]
                # Match response to request by URL
                for entry in reversed(log):
                    if entry["url"] == response.url and "status" not in entry:
                        entry["status"] = response.status
                        entry["status_text"] = response.status_text
                        entry["response_headers"] = dict(response.headers)
                        break

        page.on("request", _on_request)
        page.on("response", _on_response)

    async def get_request_log(self, context_id: str) -> list[dict]:
        """Get recorded network requests for a context."""
        if not hasattr(self, "_request_logs"):
            return []
        return self._request_logs.get(context_id, [])

    async def get_request_detail(self, context_id: str, request_id: int) -> dict | None:
        """Get details of a specific logged request by ID."""
        log = await self.get_request_log(context_id)
        for entry in log:
            if entry["id"] == request_id:
                return entry
        return None

    async def get_response_body(self, context_id: str, request_id: int) -> dict[str, Any] | None:
        """Get the captured response body for a specific request.

        Returns:
            Dict with ``body`` (bytes), ``content_type``, ``size``, ``truncated``,
            or ``None`` if no body was captured for this request.
        """
        if not hasattr(self, "_response_bodies"):
            return None
        ctx_bodies = self._response_bodies.get(context_id, {})
        return ctx_bodies.get(request_id)

    async def get_captured_responses(
        self,
        context_id: str,
        *,
        resource_type: str | None = None,
        content_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """List responses that have captured bodies, with metadata.

        Returns summary dicts (no body bytes) for filtering and discovery.
        Use :meth:`get_response_body` to retrieve the actual body.
        """
        log = await self.get_request_log(context_id)
        if not hasattr(self, "_response_bodies"):
            return []
        ctx_bodies = self._response_bodies.get(context_id, {})

        results: list[dict[str, Any]] = []
        for entry in log:
            if entry["id"] not in ctx_bodies:
                continue
            body_info = ctx_bodies[entry["id"]]
            if resource_type and entry.get("resource_type") != resource_type:
                continue
            ct = body_info.get("content_type", "")
            if content_type and content_type.lower() not in ct.lower():
                continue
            results.append(
                {
                    "id": entry["id"],
                    "url": entry["url"],
                    "method": entry["method"],
                    "resource_type": entry.get("resource_type"),
                    "status": entry.get("status"),
                    "content_type": ct,
                    "body_size": body_info["size"],
                    "truncated": body_info["truncated"],
                }
            )
        return results

    # ── Lifecycle ──

    async def close_context(self, context_id: str) -> None:
        """Close a named browser context and its active page."""
        page = self._pages.pop(context_id, None)
        if page is not None:
            await page.close()
        context = self._contexts.pop(context_id, None)
        if context is not None:
            await context.close()
        if hasattr(self, "_request_logs"):
            self._request_logs.pop(context_id, None)
        if hasattr(self, "_response_bodies"):
            self._response_bodies.pop(context_id, None)

    async def close(self) -> None:
        """Release browser resources including all named contexts and pages."""
        for page in self._pages.values():
            await page.close()
        self._pages.clear()
        for ctx in self._contexts.values():
            await ctx.close()
        self._contexts.clear()
        if hasattr(self, "_request_logs"):
            self._request_logs.clear()
        if hasattr(self, "_response_bodies"):
            self._response_bodies.clear()
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

    @property
    def active_contexts(self) -> list[str]:
        """List of context IDs with active pages."""
        return list(self._pages.keys())


def _raise_browser_error(exc: Exception, url: str, operation: str = "navigation") -> Never:
    """Map Playwright exceptions to WebError hierarchy.

    Args:
        exc: The original Playwright exception.
        url: The URL being operated on.
        operation: What was being done (navigation, click, fill, type, etc.).
    """
    msg = str(exc).lower()
    if "timeout" in msg:
        raise WebTimeoutError(
            f"Browser {operation} timed out for {url}. "
            f"The element may not exist, be hidden, or the page may be slow to respond.",
            url=url,
            timeout_type=operation,
        ) from exc
    if "net::" in msg or "networkerror" in msg:
        raise WebNetworkError(
            f"Browser network error for {url}: {exc}",
            url=url,
        ) from exc
    raise WebBrowserError(
        f"Browser error during {operation} for {url}: {exc}",
        url=url,
        retryable=False,
    ) from exc
