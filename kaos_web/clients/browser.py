"""Browser client using Playwright for JavaScript-rendered pages.

Requires optional ``[browser]`` extra: ``pip install kaos-web[browser]``

Features:
- Lazy browser launch (~200-500ms, only on first fetch)
- Context-per-request isolation (separate cookies, storage)
- Named context pooling (session persistence via context_id)
- Session-scoped context pooling: every named context is bound to a
  ``(session_id, context_id)`` tuple so a different KaosContext.session_id
  cannot access another caller's pages, cookies, or captured traffic
  (audit-04 finding #2 / WEB5-002).
- Resource blocking (images, fonts, CSS, media)
- Wait strategies (load, domcontentloaded, networkidle, selector)
- Screenshot capture
- Auth state persistence via storage_state
- Interactive mode: click, fill, evaluate, accessibility snapshots on persistent pages
"""

from __future__ import annotations

import contextlib
import re
from typing import Any, Never, Self

from kaos_core.logging import get_logger
from kaos_web.clients.config import BrowserClientConfig
from kaos_web.errors import (
    BodyTooLargeError,
    WebBrowserError,
    WebNetworkError,
    WebTimeoutError,
)
from kaos_web.models import WebRequest, WebResponse

logger = get_logger(__name__)

# WEB5-002 / audit-04 finding #2: every browser-state lookup is keyed by
# the tuple ``(session_id, context_id)``. The MCP tool layer derives
# ``session_id`` from ``KaosContext.session_id``. Library callers that
# don't have a runtime context (single-user stdio mode is the original
# use case) fall back to this sentinel — so the surface stays usable
# without forcing every script to invent a session ID. Two callers
# without runtimes share the anonymous bucket; cross-bucket isolation
# only kicks in once the MCP layer is involved (which is the threat
# model — a remote MCP HTTP server fronting multiple agents).
ANONYMOUS_SESSION_ID = "__anonymous__"

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

# WEB5-003: header-name allowlist for redaction in OBSERVED third-party
# traffic. Mask only on capture (request log + response headers in log);
# never mask the agent's own cookie/header surfaces (GetCookiesTool,
# SetCookieTool, navigation outbound headers).
_REDACT_HEADER_NAMES = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "x-auth-token",
        "x-csrf-token",
    }
)
_REDACT_NAME_PATTERN = re.compile(r"(?i).*(?:secret|token|api[_-]?key|password|auth).*")


def _should_redact_header(name: str) -> bool:
    """Return True if a header name is auth-shaped or in the allowlist."""
    lower = name.lower()
    return lower in _REDACT_HEADER_NAMES or bool(_REDACT_NAME_PATTERN.match(lower))


def _maybe_redact_headers(headers: dict[str, str], enabled: bool) -> dict[str, str]:
    """Return a copy of ``headers`` with sensitive values masked when enabled.

    Mask format: ``<redacted: N bytes>`` preserves the original byte
    length (useful for "what kind of token did this site set?" pattern
    detection) without leaking the value itself.
    """
    if not enabled:
        return dict(headers)
    redacted: dict[str, str] = {}
    for name, value in headers.items():
        if _should_redact_header(name):
            redacted[name] = f"<redacted: {len(value)} bytes>"
        else:
            redacted[name] = value
    return redacted


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
        # WEB5-002: every map is keyed by ``(session_id, context_id)``.
        # Cross-session lookups intentionally miss — see ``_require_page``,
        # ``get_cookies``, ``set_cookies``, ``save_storage_state``,
        # ``get_storage_state``, ``enable_request_logging``, and
        # ``close_context`` for the uniform "No context '<id>'" error
        # path that does not leak the existence of another session's
        # context.
        self._contexts: dict[tuple[str, str], Any] = {}  # Named context pool
        self._pages: dict[tuple[str, str], Any] = {}  # Active pages by (session_id, context_id)
        self._request_logs: dict[tuple[str, str], list[dict]] = {}
        self._response_bodies: dict[tuple[str, str], dict[int, dict[str, Any]]] = {}
        self._logging_config: dict[tuple[str, str], dict[str, Any]] = {}

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

        # Resolve effective browser channel. Explicit ``cfg.channel``
        # always wins. Otherwise consult :class:`KaosWebSettings` —
        # the existing ``browser_channel`` env override + auto-detect
        # (uses system ``google-chrome`` on Linux when Playwright's
        # bundled Chromium isn't supported, e.g. Ubuntu 26.04+).
        # Without this wiring, ``BrowserClient()`` ignored the env
        # var and the auto-detect, leaving the bundled-Chromium
        # failure surface live.
        effective_channel: str | None = cfg.channel
        if effective_channel is None:
            try:
                from kaos_web.settings import (
                    KaosWebSettings,
                    _detect_browser_channel,
                )

                settings = KaosWebSettings()
                effective_channel = settings.browser_channel
                if effective_channel is None and settings.browser_auto_detect_channel:
                    effective_channel = _detect_browser_channel()
            except Exception as exc:  # settings is best-effort; never block fetch
                logger.debug("browser_channel auto-detect failed: %s", exc)

        launch_kwargs: dict[str, Any] = {"headless": cfg.headless}
        if effective_channel:
            launch_kwargs["channel"] = effective_channel
        if cfg.proxy:
            launch_kwargs["proxy"] = {"server": cfg.proxy}

        self._browser = await engine.launch(**launch_kwargs)
        logger.debug(
            "Launched %s browser (channel=%s, headless=%s)",
            cfg.browser_type,
            effective_channel or "<bundled>",
            cfg.headless,
        )
        return self._browser

    async def _get_or_create_context(
        self, browser: Any, session_id: str, context_id: str | None
    ) -> tuple[Any, bool]:
        """Get existing named context or create a new one.

        Returns (context, owns_context) where owns_context=True means the caller
        should close it (unnamed/ephemeral contexts). Named contexts are
        scoped to ``(session_id, context_id)`` so a different
        ``KaosContext.session_id`` never resolves another session's
        context (WEB5-002).
        """
        cfg = self._config

        # Resolve effective User-Agent. Explicit config.user_agent wins;
        # otherwise rotate through the curated DEFAULT_DESKTOP_UAS pool
        # when randomize_user_agent=True so consecutive fetches don't
        # share a fingerprint. Falling all the way through (no UA,
        # randomize=False) lets Playwright apply its built-in headless
        # Chromium UA — fine for testing, easily fingerprinted in prod.
        effective_user_agent: str | None = cfg.user_agent
        if effective_user_agent is None and cfg.randomize_user_agent:
            from kaos_web.clients.user_agents import next_default_desktop_ua

            effective_user_agent = next_default_desktop_ua()

        context_opts: dict[str, Any] = {
            "viewport": {
                "width": cfg.viewport_width,
                "height": cfg.viewport_height,
            },
            "device_scale_factor": cfg.device_scale_factor,
            "is_mobile": cfg.is_mobile,
            "ignore_https_errors": cfg.ignore_https_errors,
        }

        if effective_user_agent:
            context_opts["user_agent"] = effective_user_agent
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

        # Merge default anti-bot headers (sec-ch-ua / sec-fetch-* /
        # accept-language / cache-control — the kelvin reference set)
        # under any caller-supplied overrides. Caller wins on collision.
        merged_headers: dict[str, str] = {}
        if cfg.use_default_anti_bot_headers:
            from kaos_web.clients.user_agents import DEFAULT_EXTRA_HEADERS

            merged_headers.update(DEFAULT_EXTRA_HEADERS)
        if cfg.extra_headers:
            merged_headers.update(cfg.extra_headers)
        if merged_headers:
            context_opts["extra_http_headers"] = merged_headers

        # Named context: reuse or create. Keyed by (session_id, context_id)
        # so the same context_id from a different session resolves to a
        # different bucket (WEB5-002).
        owns_context = context_id is None
        scope = (session_id, context_id) if context_id else None
        if scope is not None and scope in self._contexts:
            context = self._contexts[scope]
        else:
            context = await browser.new_context(**context_opts)
            if scope is not None:
                self._contexts[scope] = context

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
        - ``session_id``: Owning session for the named context (WEB5-002).
          Defaults to ``ANONYMOUS_SESSION_ID``. The MCP tool layer always
          sets this from ``KaosContext.session_id``.
        - ``wait_until``: Navigation wait strategy
        - ``wait_for_selector``: CSS selector to wait for after navigation
        - ``dismiss_overlays``: Auto-dismiss known cookie consent banners
        """
        # WEB5-001: gate the navigation URL BEFORE launching the browser
        # context. Strict by default — blocks link-local metadata,
        # loopback, RFC1918 private ranges, and non-(http|https) schemes.
        from kaos_web.security import validate_url

        validate_url(request.url)
        browser = await self._ensure_browser()
        context_id = request.extra.get("context_id")
        session_id = request.extra.get("session_id", ANONYMOUS_SESSION_ID)
        context, owns_context = await self._get_or_create_context(browser, session_id, context_id)

        scope = (session_id, context_id) if context_id else None

        try:
            # For named contexts, close any existing page before creating a new one
            if scope is not None and scope in self._pages:
                old_page = self._pages.pop(scope)
                await old_page.close()

            page = await context.new_page()
            page_stored = False

            # Re-attach logging handlers if logging was enabled for this context
            if scope is not None and scope in self._logging_config:
                # scope is non-None ⇒ context_id is non-None.
                _, scoped_context_id = scope
                self._attach_logging_handlers(session_id, scoped_context_id, page)

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
                        from kaos_web.clients.page_prep import dismiss_cookie_banners

                        await dismiss_cookie_banners(page)
                    except Exception:
                        pass  # Never let banner dismissal break content extraction

                # Wait for content to settle on JS-rendered pages.
                # Skipped when wait_for_selector is explicit (caller knows what to wait for).
                selector = request.extra.get("wait_for_selector")
                if not selector and request.extra.get("wait_for_settled", False):
                    try:
                        from kaos_web.clients.page_prep import wait_for_content_settled

                        await wait_for_content_settled(page)
                    except Exception:
                        pass  # Never let settling detection break extraction

                # Wait for selector if specified
                if selector:
                    try:
                        await page.wait_for_selector(selector, timeout=timeout)
                    except Exception as exc:
                        _raise_browser_error(exc, request.url, "wait_for_selector")

                # Extract content (cap-checked per WEB5-007).
                html = await page.content()
                _check_body_cap(html, request.url)
                title = await page.title()

                # Screenshot if requested
                screenshot: bytes | None = None
                if request.screenshot:
                    screenshot = await page.screenshot(full_page=True)

                # Response metadata
                status_code = response.status if response else 200
                headers: dict[str, str] = dict(response.headers) if response else {}

                # For named contexts, keep the page alive for interaction
                if scope is not None:
                    self._pages[scope] = page
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

    def _require_page(self, context_id: str, *, session_id: str = ANONYMOUS_SESSION_ID) -> Any:
        """Get the active page for ``(session_id, context_id)``, raising if not found.

        The error message lists only the calling session's contexts —
        cross-session existence is never disclosed (WEB5-002).
        """
        page = self._pages.get((session_id, context_id))
        if page is None:
            available = [cid for (sid, cid) in self._pages if sid == session_id]
            raise WebBrowserError(
                f"No active page for context '{context_id}'. "
                f"First navigate with fetch(WebRequest(url=..., "
                f"extra={{'context_id': '{context_id}'}})) "
                f"to establish a page. Active contexts: {available}",
                url="",
                retryable=False,
            )
        return page

    async def click(
        self,
        context_id: str,
        selector: str,
        *,
        session_id: str = ANONYMOUS_SESSION_ID,
        **kwargs: Any,
    ) -> None:
        """Click an element on the active page in a named context.

        Args:
            context_id: Named context with an active page.
            selector: CSS selector for the element to click.
            session_id: Owning session (WEB5-002). MCP tools pass
                ``KaosContext.session_id``; library callers without a
                runtime fall back to ``ANONYMOUS_SESSION_ID``.
            **kwargs: Extra Playwright click options (timeout, force, etc.).
        """
        page = self._require_page(context_id, session_id=session_id)
        timeout = kwargs.pop("timeout", self._config.default_timeout)
        try:
            await page.click(selector, timeout=timeout, **kwargs)
        except Exception as exc:
            _raise_browser_error(exc, page.url, "click")

    async def fill(
        self,
        context_id: str,
        selector: str,
        value: str,
        *,
        session_id: str = ANONYMOUS_SESSION_ID,
        **kwargs: Any,
    ) -> None:
        """Fill an input field on the active page.

        Args:
            context_id: Named context with an active page.
            selector: CSS selector for the input element.
            value: Text value to fill.
            session_id: Owning session (WEB5-002).
            **kwargs: Extra Playwright fill options.
        """
        page = self._require_page(context_id, session_id=session_id)
        timeout = kwargs.pop("timeout", self._config.default_timeout)
        try:
            await page.fill(selector, value, timeout=timeout, **kwargs)
        except Exception as exc:
            _raise_browser_error(exc, page.url, "fill")

    async def select_option(
        self,
        context_id: str,
        selector: str,
        value: str,
        *,
        session_id: str = ANONYMOUS_SESSION_ID,
        **kwargs: Any,
    ) -> list[str]:
        """Select an option from a <select> element.

        Args:
            context_id: Named context with an active page.
            selector: CSS selector for the select element.
            value: Option value to select.
            session_id: Owning session (WEB5-002).
            **kwargs: Extra Playwright select options.
        """
        page = self._require_page(context_id, session_id=session_id)
        timeout = kwargs.pop("timeout", self._config.default_timeout)
        try:
            return await page.select_option(selector, value, timeout=timeout, **kwargs)
        except Exception as exc:
            _raise_browser_error(exc, page.url, "select_option")

    async def type_text(
        self,
        context_id: str,
        selector: str,
        text: str,
        *,
        session_id: str = ANONYMOUS_SESSION_ID,
        **kwargs: Any,
    ) -> None:
        """Type text character-by-character (simulating keystrokes).

        Unlike fill(), this fires keydown/keypress/keyup events for each character.
        Useful for inputs with JavaScript listeners (autocomplete, etc.).

        Args:
            context_id: Named context with an active page.
            selector: CSS selector for the input element.
            text: Text to type character-by-character.
            session_id: Owning session (WEB5-002).
            **kwargs: Extra options (delay between keystrokes, etc.).
        """
        page = self._require_page(context_id, session_id=session_id)
        timeout = kwargs.pop("timeout", self._config.default_timeout)
        delay = kwargs.pop("delay", 0)
        try:
            await page.type(selector, text, timeout=timeout, delay=delay, **kwargs)
        except Exception as exc:
            _raise_browser_error(exc, page.url, "type")

    async def press_key(
        self,
        context_id: str,
        selector: str,
        key: str,
        *,
        session_id: str = ANONYMOUS_SESSION_ID,
        **kwargs: Any,
    ) -> None:
        """Press a keyboard key on an element (e.g., 'Enter', 'Tab', 'Escape').

        Args:
            context_id: Named context with an active page.
            selector: CSS selector for the element to focus.
            key: Key to press (Playwright key name, e.g., 'Enter', 'ArrowDown').
            session_id: Owning session (WEB5-002).
            **kwargs: Extra Playwright press options.
        """
        page = self._require_page(context_id, session_id=session_id)
        timeout = kwargs.pop("timeout", self._config.default_timeout)
        try:
            await page.press(selector, key, timeout=timeout, **kwargs)
        except Exception as exc:
            _raise_browser_error(exc, page.url, "press_key")

    async def get_snapshot(self, context_id: str, *, session_id: str = ANONYMOUS_SESSION_ID) -> str:
        """Get the accessibility tree of the active page.

        Returns a text representation of the page's ARIA tree (via Playwright's
        ``locator.aria_snapshot()``). The output lists interactive elements like
        headings, links, buttons, and inputs in an indented text format — ideal
        for agents to understand page structure and find selectors.
        """
        page = self._require_page(context_id, session_id=session_id)
        try:
            snapshot = await page.locator("body").aria_snapshot()
            return snapshot or ""
        except Exception as exc:
            _raise_browser_error(exc, page.url, "snapshot")

    async def get_content(self, context_id: str, *, session_id: str = ANONYMOUS_SESSION_ID) -> str:
        """Get the current HTML content of the active page."""
        page = self._require_page(context_id, session_id=session_id)
        return await page.content()

    async def get_url(self, context_id: str, *, session_id: str = ANONYMOUS_SESSION_ID) -> str:
        """Get the current URL of the active page."""
        page = self._require_page(context_id, session_id=session_id)
        return page.url

    async def screenshot_context(
        self,
        context_id: str,
        *,
        session_id: str = ANONYMOUS_SESSION_ID,
        full_page: bool = True,
        format: str = "png",
        quality: int | None = None,
    ) -> bytes:
        """Take a screenshot of the active page in a named context."""
        page = self._require_page(context_id, session_id=session_id)
        kwargs: dict[str, Any] = {
            "full_page": full_page,
            "type": format,
        }
        if quality is not None and format == "jpeg":
            kwargs["quality"] = quality
        return await page.screenshot(**kwargs)

    async def evaluate_in_context(
        self,
        context_id: str,
        expression: str,
        *,
        session_id: str = ANONYMOUS_SESSION_ID,
    ) -> Any:
        """Evaluate a JavaScript expression on the active page in a named context."""
        page = self._require_page(context_id, session_id=session_id)
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
        # WEB5-001: gate the URL before any browser I/O.
        from kaos_web.security import validate_url

        validate_url(url)
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
        # WEB5-001: gate the URL before any browser I/O.
        from kaos_web.security import validate_url

        validate_url(url)
        browser = await self._ensure_browser()

        context = await browser.new_context()
        try:
            page = await context.new_page()
            await page.goto(url, wait_until=self._config.default_wait_until)
            return await page.evaluate(expression)
        finally:
            await context.close()

    # ── Cookie / Storage methods ──

    def _require_context(self, context_id: str, session_id: str) -> Any:
        """Look up a context by ``(session_id, context_id)`` or raise.

        Cross-session lookups raise the same uniform error as missing
        contexts so a caller cannot distinguish "exists in another
        session" from "doesn't exist at all" (WEB5-002).
        """
        scope = (session_id, context_id)
        if scope not in self._contexts:
            raise WebBrowserError(
                f"No context '{context_id}'. Use kaos-web-browser-navigate first.",
                url="",
                retryable=False,
            )
        return self._contexts[scope]

    async def get_cookies(
        self,
        context_id: str,
        urls: list[str] | None = None,
        *,
        session_id: str = ANONYMOUS_SESSION_ID,
    ) -> list[dict]:
        """Get cookies from a named context.

        Args:
            context_id: Named context.
            urls: Optional list of URLs to filter cookies by. If omitted, returns all.
            session_id: Owning session (WEB5-002).
        """
        context = self._require_context(context_id, session_id)
        if urls:
            return await context.cookies(urls)
        return await context.cookies()

    async def set_cookies(
        self,
        context_id: str,
        cookies: list[dict],
        *,
        session_id: str = ANONYMOUS_SESSION_ID,
    ) -> None:
        """Add cookies to a named context.

        Args:
            context_id: Named context.
            cookies: List of cookie dicts (name, value, domain/url required).
            session_id: Owning session (WEB5-002).
        """
        context = self._require_context(context_id, session_id)
        await context.add_cookies(cookies)

    async def save_storage_state(
        self,
        context_id: str,
        path: str,
        *,
        session_id: str = ANONYMOUS_SESSION_ID,
    ) -> str:
        """Save the browser context storage state (cookies + localStorage) to a file.

        Args:
            context_id: Named context.
            path: File path to save state to (JSON). The caller is
                responsible for path safety; this is a library API
                accepting whatever path the in-process caller provides.
            session_id: Owning session (WEB5-002).

        Returns:
            The path where state was saved.

        Note:
            The MCP-tool surface (``SaveAuthStateTool``) does NOT use
            this path-accepting API — it routes through
            :meth:`get_storage_state` and writes the result to a
            session-scoped artifact via the kaos-core artifact store
            (WEB5-004). Library users with their own filesystem
            authority can still call ``save_storage_state(path)``
            directly.
        """
        context = self._require_context(context_id, session_id)
        await context.storage_state(path=path)
        return path

    async def get_storage_state(
        self, context_id: str, *, session_id: str = ANONYMOUS_SESSION_ID
    ) -> dict[str, Any]:
        """Return the browser context storage state as an in-memory dict.

        Used by ``SaveAuthStateTool`` to capture cookies + localStorage
        without writing to a caller-supplied filesystem path (WEB5-004).
        The returned dict is the same Playwright storage_state shape
        (``{"cookies": [...], "origins": [...]}``).
        """
        context = self._require_context(context_id, session_id)
        # Playwright: storage_state() with no `path` returns the dict.
        state = await context.storage_state()
        return state if isinstance(state, dict) else {}

    # ── Network monitoring ──

    def _attach_logging_handlers(self, session_id: str, context_id: str, page: Any) -> None:
        """Attach request/response logging handlers to a page.

        Uses the ``(session_id, context_id)``-keyed ``_request_logs`` list
        and ``_response_bodies`` dict (if body capture is enabled).
        Called by :meth:`enable_request_logging` and by :meth:`fetch`
        when a page is replaced in a context that already has logging
        enabled.
        """
        scope = (session_id, context_id)
        log = self._request_logs[scope]
        config = self._logging_config[scope]
        capture_bodies: bool = config["capture_bodies"]
        capture_resource_types: frozenset[str] = config["resource_types"]
        max_body_size: int = config["max_body_size"]
        # WEB5-003: per-context redaction posture (frozen at log-enable time).
        redact: bool = config.get("redact", True)
        capture_ct_prefixes = _DEFAULT_CAPTURE_CONTENT_TYPES

        def _on_request(request: Any) -> None:
            log.append(
                {
                    "id": len(log),
                    "url": request.url,
                    "method": request.method,
                    "resource_type": request.resource_type,
                    # Mask sensitive request headers at capture time. The
                    # agent's outbound headers may include legitimate auth
                    # to the target site; we don't want them returned as
                    # raw bytes via list-requests / get-request-detail.
                    "headers": _maybe_redact_headers(dict(request.headers), redact),
                    "post_data": request.post_data,
                    "is_navigation_request": request.is_navigation_request(),
                }
            )

        if capture_bodies:
            bodies = self._response_bodies[scope]

            async def _on_response(response: Any) -> None:
                # Phase 1: metadata matching (same as sync handler)
                matched_entry: dict[str, Any] | None = None
                for entry in reversed(log):
                    if entry["url"] == response.url and "status" not in entry:
                        entry["status"] = response.status
                        entry["status_text"] = response.status_text
                        # WEB5-003: mask Set-Cookie, Set-Cookie2, and any
                        # auth-shaped response headers at capture.
                        entry["response_headers"] = _maybe_redact_headers(
                            dict(response.headers), redact
                        )
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

            def _on_response(response: Any) -> None:
                # Match response to request by URL
                for entry in reversed(log):
                    if entry["url"] == response.url and "status" not in entry:
                        entry["status"] = response.status
                        entry["status_text"] = response.status_text
                        # WEB5-003: same redaction posture as the
                        # body-capturing variant above.
                        entry["response_headers"] = _maybe_redact_headers(
                            dict(response.headers), redact
                        )
                        break

        page.on("request", _on_request)
        page.on("response", _on_response)

    async def enable_request_logging(
        self,
        context_id: str,
        *,
        session_id: str = ANONYMOUS_SESSION_ID,
        capture_bodies: bool = False,
        resource_types: frozenset[str] | None = None,
        max_body_size: int = _DEFAULT_MAX_BODY_SIZE,
    ) -> None:
        """Start recording network requests for a named context.

        Logging survives page replacement — when :meth:`fetch` creates a
        new page for this context, handlers are automatically re-attached
        and logs accumulate across navigations.

        Args:
            context_id: Named browser context.
            session_id: Owning session (WEB5-002).
            capture_bodies: Also capture response bodies for matching requests.
            resource_types: Resource types to capture bodies for (default: fetch, xhr).
            max_body_size: Maximum body size in bytes (default: 1 MB).
        """
        scope = (session_id, context_id)
        if scope not in self._contexts:
            raise WebBrowserError(
                f"No context '{context_id}'. Use kaos-web-browser-navigate first.",
                url="",
                retryable=False,
            )
        page = self._require_page(context_id, session_id=session_id)

        resolved_resource_types = resource_types or _DEFAULT_CAPTURE_RESOURCE_TYPES

        # WEB5-003: read the redaction setting once at log-enable time so
        # the same posture applies to every captured request in this
        # context (a flip of the env var mid-session won't change the
        # masking behavior for an already-running log).
        from kaos_web.settings import KaosWebSettings

        redact = KaosWebSettings().redact_observed_traffic

        # Store config so fetch() can re-attach handlers on page replacement
        self._logging_config[scope] = {
            "capture_bodies": capture_bodies,
            "resource_types": resolved_resource_types,
            "max_body_size": max_body_size,
            "redact": redact,
        }

        # Initialize log storage
        self._request_logs[scope] = []

        # Initialize body storage when capture is enabled
        if capture_bodies:
            self._response_bodies[scope] = {}

        self._attach_logging_handlers(session_id, context_id, page)

    async def get_request_log(
        self, context_id: str, *, session_id: str = ANONYMOUS_SESSION_ID
    ) -> list[dict]:
        """Get recorded network requests for a context."""
        return self._request_logs.get((session_id, context_id), [])

    async def get_request_detail(
        self,
        context_id: str,
        request_id: int,
        *,
        session_id: str = ANONYMOUS_SESSION_ID,
    ) -> dict | None:
        """Get details of a specific logged request by ID."""
        log = await self.get_request_log(context_id, session_id=session_id)
        for entry in log:
            if entry["id"] == request_id:
                return entry
        return None

    async def get_response_body(
        self,
        context_id: str,
        request_id: int,
        *,
        session_id: str = ANONYMOUS_SESSION_ID,
    ) -> dict[str, Any] | None:
        """Get the captured response body for a specific request.

        Returns:
            Dict with ``body`` (bytes), ``content_type``, ``size``, ``truncated``,
            or ``None`` if no body was captured for this request.
        """
        ctx_bodies = self._response_bodies.get((session_id, context_id), {})
        return ctx_bodies.get(request_id)

    async def get_captured_responses(
        self,
        context_id: str,
        *,
        session_id: str = ANONYMOUS_SESSION_ID,
        resource_type: str | None = None,
        content_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """List responses that have captured bodies, with metadata.

        Returns summary dicts (no body bytes) for filtering and discovery.
        Use :meth:`get_response_body` to retrieve the actual body.
        """
        log = await self.get_request_log(context_id, session_id=session_id)
        ctx_bodies = self._response_bodies.get((session_id, context_id), {})

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

    async def close_context(
        self, context_id: str, *, session_id: str = ANONYMOUS_SESSION_ID
    ) -> None:
        """Close a named browser context and its active page.

        Cross-session calls silently no-op — the lookup misses and there
        is nothing to close. The owning session's context is untouched
        (WEB5-002).
        """
        scope = (session_id, context_id)
        page = self._pages.pop(scope, None)
        if page is not None:
            await page.close()
        context = self._contexts.pop(scope, None)
        if context is not None:
            await context.close()
        self._request_logs.pop(scope, None)
        self._response_bodies.pop(scope, None)
        self._logging_config.pop(scope, None)

    async def close(self) -> None:
        """Release browser resources including all named contexts and pages.

        Process-shutdown path: closes every context across every session.
        Per-session cleanup uses :meth:`close_context`.
        """
        for page in self._pages.values():
            await page.close()
        self._pages.clear()
        for ctx in self._contexts.values():
            await ctx.close()
        self._contexts.clear()
        self._request_logs.clear()
        self._response_bodies.clear()
        self._logging_config.clear()
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

    def active_contexts(self, session_id: str = ANONYMOUS_SESSION_ID) -> list[str]:
        """List the context IDs with active pages owned by ``session_id``.

        WEB5-002: filtered by ``session_id`` — a caller never sees
        another session's contexts. Use ``ANONYMOUS_SESSION_ID`` (the
        default) to inspect contexts created by library callers without
        a runtime context.
        """
        return [cid for (sid, cid) in self._pages if sid == session_id]


def _check_body_cap(html: str, url: str) -> None:
    """Raise BodyTooLargeError if rendered HTML exceeds the configured cap.

    Playwright's ``page.content()`` materializes the full DOM HTML as a
    Python string, so by the time we measure it we've already paid the
    memory cost. The check still defends downstream parsers and
    serializers from running on a 5 GB string. WEB5-007 / audit-04
    finding #7.

    A streaming variant is not available in Playwright; the next-best
    defense (request-level Content-Length or chunked-byte cap) lives in
    ``HttpClient`` for the non-browser path. For browser fetches, the
    cap is purely a post-render guard.
    """
    from kaos_web.settings import KaosWebSettings

    cap = KaosWebSettings().max_body_bytes
    # Approximate byte size as 4x character count (UTF-8 worst case for
    # non-ASCII). Cheap upper bound; if we're under the cap on this
    # estimate we're definitely safe. If we're over, do the precise
    # encoded-length check before raising.
    approx = len(html) * 4
    if approx <= cap:
        return
    actual = len(html.encode("utf-8", errors="replace"))
    if actual > cap:
        raise BodyTooLargeError(
            f"Browser-rendered HTML for {url} is {actual} bytes (cap: "
            f"{cap}). Increase KAOS_WEB_MAX_BODY_BYTES if you intend "
            f"to fetch payloads of this size.",
            url=url,
            size_bytes=actual,
            max_bytes=cap,
        )


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
