"""Typed settings for kaos-web.

Centralises all environment variable reads into a single ``KaosWebSettings``
model.  New-style env vars use the ``KAOS_WEB_`` prefix; legacy env var
names (``KAOS_BROWSER_CHANNEL``, ``SERPAPI_API_KEY``, etc.) are supported
via a ``model_validator`` fallback for backward compatibility.

Usage::

    from kaos_web.settings import KaosWebSettings

    settings = KaosWebSettings()              # from env + defaults
    config   = settings.to_browser_config()   # -> BrowserClientConfig
"""

from __future__ import annotations

import os
import platform
import shutil
from typing import Any, Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import SettingsConfigDict

from kaos_core.config.module_settings import ModuleSettings


def _detect_browser_channel() -> str | None:
    """Auto-detect the best browser channel for this platform.

    Priority:
    1. System Chrome if bundled Chromium is known-broken (Ubuntu 24.04+)
    2. None (use Playwright's bundled Chromium — the default)
    """
    if platform.system() == "Linux" and shutil.which("google-chrome"):
        return "chrome"
    return None


class KaosWebSettings(ModuleSettings):
    """Web module settings — browser, search, and API key configuration.

    Env vars:
        ``KAOS_WEB_BROWSER_TYPE``, ``KAOS_WEB_BROWSER_HEADLESS``,
        ``KAOS_WEB_BROWSER_CHANNEL``, ``KAOS_WEB_BROWSER_AUTO_DETECT_CHANNEL``,
        ``KAOS_WEB_SEARCH_BACKEND``, ``KAOS_WEB_SERPAPI_API_KEY``,
        ``KAOS_WEB_EXA_API_KEY``, ``KAOS_WEB_BRAVE_API_KEY``

    Legacy env vars (backward compatible):
        ``KAOS_BROWSER_TYPE``, ``KAOS_BROWSER_HEADLESS``, ``KAOS_BROWSER_CHANNEL``,
        ``KAOS_SEARCH_BACKEND``, ``SERPAPI_API_KEY``, ``EXA_API_KEY``, ``BRAVE_API_KEY``
    """

    # Browser
    browser_type: Literal["chromium", "firefox", "webkit"] = "chromium"
    browser_headless: bool = True
    browser_channel: str | None = None
    browser_auto_detect_channel: bool = True

    # Search
    search_backend: str = ""
    serpapi_api_key: SecretStr | None = None
    exa_api_key: SecretStr | None = None
    brave_api_key: SecretStr | None = None

    # Search backend tuning
    search_timeout: float = 30.0
    """Default timeout (seconds) for search backend API calls."""
    search_ddg_timeout: float = 15.0
    """Timeout for DuckDuckGo HTML scraping (separate since it's slower)."""
    search_ddg_user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
    """User-Agent string for DuckDuckGo HTML scraping."""

    # Discovery
    discovery_robots_timeout: float = 10.0
    """Timeout for fetching robots.txt during discovery."""
    discovery_page_timeout: float = 15.0
    """Timeout for fetching the start page during link discovery."""

    # Sitemap
    sitemap_max_depth: int = 3
    """Maximum recursion depth for sitemap index traversal."""
    sitemap_fetch_timeout: float = 15.0
    """Timeout for fetching individual sitemaps."""
    sitemap_robots_timeout: float = 10.0
    """Timeout for fetching robots.txt during sitemap discovery."""
    sitemap_fallback_timeout: float = 10.0
    """Timeout for probing well-known sitemap paths."""

    # Crawl
    crawl_max_depth: int = 2
    """Default maximum link-following depth."""
    crawl_max_pages: int = 50
    """Default maximum pages to extract."""
    crawl_concurrency: int = 5
    """Default concurrent request limit."""
    crawl_page_timeout: float = 30.0
    """Timeout for fetching each page during crawl."""
    crawl_enable_cache: bool = True
    """Enable HTTP cache during crawl by default."""
    crawl_over_discover_factor: int = 3
    """Factor to multiply max_pages for over-discovery."""

    # Response-size memory-safety cap (WEB5-007 / audit-04 finding #7)
    max_body_bytes: int = 50_000_000
    """Maximum response body size accepted from any fetch site.

    A hostile or misconfigured endpoint can stream gigabytes of content.
    Without a cap, ``HttpClient.fetch`` materializes ``resp.text``,
    ``BrowserClient.fetch`` materializes ``page.content()``, and
    ``sitemap`` gzip decompression all run unbounded — OOM territory.

    Default 50 MB is generous for typical web pages (a large news
    article HTML is ~200 KB; a feature-rich SPA HTML is ~5 MB; a
    competitive sitemap.xml is ~5-10 MB; a gzipped sitemap-index can
    decompress to ~30 MB). Raise the cap explicitly when working with
    bulk data (legal corpus pages, large data exports, archival
    snapshots).

    Enforced at three sites:
    - ``HttpClient._raw_fetch``: pre-checks ``Content-Length``, then
      streams via ``client.stream() + aiter_bytes()`` with a running
      tally; raises ``BodyTooLargeError`` on overflow.
    - ``BrowserClient.fetch``: post-checks ``len(page.content())``.
    - ``kaos_web.discover.sitemap._decompress_gzip``: bounded read.

    Env var: ``KAOS_WEB_MAX_BODY_BYTES``.
    """

    # Domain intelligence
    domain_verify_tls: bool = True
    """Whether to verify TLS certificates on domain-intelligence probes
    (``analyze_headers``, ``extract_org_entity``).

    Defaults to ``True`` (secure-by-default per WEB5-006 / audit-04
    finding #6): the typical use case is observing healthy public sites,
    where CA validation is the right behavior and a MITM-acceptable
    cert is the abnormal state.

    Set to ``False`` (or ``KAOS_WEB_DOMAIN_VERIFY_TLS=false``) when you
    explicitly need to inspect hosts whose TLS configuration is the
    *subject* of inspection — self-signed certs, expired certs,
    mismatched SANs, or staging environments. Disabling cert
    verification on these probes returns metadata for hosts you would
    otherwise be blocked from observing; it does NOT make any returned
    response trustworthy.

    Note: kaos-web's content-extraction tools (``HttpClient``,
    ``BrowserClient``) keep TLS verification on independently of this
    setting; this flag only relaxes the two domain-intel HTTP probes.
    """

    # Middleware defaults
    middleware_retry_max_retries: int = 3
    middleware_retry_initial_delay: float = 1.0
    middleware_retry_max_delay: float = 60.0
    middleware_retry_exponential_base: float = 2.0
    middleware_rate_limit_rps: float = 10.0
    middleware_rate_limit_burst: int | None = None
    middleware_robots_user_agent: str = "KAOS-Web"
    middleware_robots_cache_ttl: int = 3600
    middleware_robots_fetch_timeout: float = 10.0

    model_config = SettingsConfigDict(
        env_prefix="KAOS_WEB_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @model_validator(mode="before")
    @classmethod
    def _legacy_env_fallback(cls, values: dict[str, Any]) -> dict[str, Any]:
        """Support legacy env var names for backward compatibility."""
        _LEGACY_MAP: dict[str, str] = {
            "browser_channel": "KAOS_BROWSER_CHANNEL",
            "browser_type": "KAOS_BROWSER_TYPE",
            "search_backend": "KAOS_SEARCH_BACKEND",
            "serpapi_api_key": "SERPAPI_API_KEY",
            "exa_api_key": "EXA_API_KEY",
            "brave_api_key": "BRAVE_API_KEY",
        }
        for field, env_name in _LEGACY_MAP.items():
            if not values.get(field):
                legacy = os.environ.get(env_name)
                if legacy:
                    values[field] = legacy

        # Special case: KAOS_BROWSER_HEADLESS is a boolean with string parsing
        if "browser_headless" not in values or values.get("browser_headless") is None:
            legacy_headless = os.environ.get("KAOS_BROWSER_HEADLESS")
            if legacy_headless is not None:
                values["browser_headless"] = legacy_headless.lower() != "false"

        return values

    def to_browser_config(self) -> Any:
        """Build a ``BrowserClientConfig`` from these settings.

        Applies browser channel auto-detection if ``browser_auto_detect_channel``
        is ``True`` and no explicit channel is set.
        """
        from kaos_web.clients.config import BrowserClientConfig

        channel = self.browser_channel
        if channel == "auto":
            channel = None
        if channel is None and self.browser_auto_detect_channel:
            channel = _detect_browser_channel()

        return BrowserClientConfig(
            browser_type=self.browser_type,
            headless=self.browser_headless,
            channel=channel,
        )

    def to_retry_config(self) -> Any:
        """Build a ``RetryConfig`` from middleware settings."""
        from kaos_web.middleware.retry import RetryConfig

        return RetryConfig(
            max_retries=self.middleware_retry_max_retries,
            initial_delay=self.middleware_retry_initial_delay,
            max_delay=self.middleware_retry_max_delay,
            exponential_base=self.middleware_retry_exponential_base,
        )

    def to_rate_limit_config(self) -> Any:
        """Build a ``RateLimitConfig`` from middleware settings."""
        from kaos_web.middleware.rate_limit import RateLimitConfig

        return RateLimitConfig(
            requests_per_second=self.middleware_rate_limit_rps,
            burst_size=self.middleware_rate_limit_burst,
        )

    def to_robots_config(self) -> Any:
        """Build a ``RobotsConfig`` from middleware settings."""
        from kaos_web.middleware.robots import RobotsConfig

        return RobotsConfig(
            user_agent=self.middleware_robots_user_agent,
            cache_ttl=self.middleware_robots_cache_ttl,
            fetch_timeout=self.middleware_robots_fetch_timeout,
        )

    def get_search_api_key(self, backend: str) -> str | None:
        """Get the API key for a specific search backend.

        Returns the secret value (plain string) or None.
        """
        key_map: dict[str, SecretStr | None] = {
            "serpapi": self.serpapi_api_key,
            "exa": self.exa_api_key,
            "brave": self.brave_api_key,
        }
        secret = key_map.get(backend)
        if secret is not None:
            return secret.get_secret_value()
        return None

    def detect_search_backend(self) -> str:
        """Auto-detect the best available search backend.

        Returns the backend name based on configured API keys, or ``"duckduckgo"``
        as the free fallback.
        """
        for backend in ("serpapi", "exa", "brave"):
            if self.get_search_api_key(backend):
                return backend
        return "duckduckgo"
