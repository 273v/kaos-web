"""HTTP and browser client configuration models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class HttpClientConfig(BaseModel):
    """Configuration for HttpClient."""

    model_config = ConfigDict(frozen=True)

    # Connection pooling
    max_connections: int = 100
    max_keepalive_connections: int = 20
    keepalive_expiry: float = 30.0

    # Timeouts (seconds)
    connect_timeout: float = 10.0
    read_timeout: float = 30.0
    write_timeout: float = 10.0
    pool_timeout: float = 5.0

    # TLS
    verify_ssl: bool = True
    ca_bundle: str | None = None
    client_cert: str | None = None
    client_key: str | None = None

    # Proxy
    proxy: str | None = None

    # Behavior
    follow_redirects: bool = True
    max_redirects: int = 10
    user_agent: str = ""
    """User-Agent string. Empty = random realistic desktop browser UA per request."""

    randomize_user_agent: bool = True
    """If True and user_agent is empty, use a random desktop browser UA."""

    # Authentication (mutually exclusive — first non-None wins)
    basic_auth: tuple[str, str] | None = None
    bearer_token: str | None = None
    api_key: str | None = None
    api_key_header: str = "X-API-Key"

    # Middleware (enabled by default for production use)
    enable_retry: bool = True
    """Enable automatic retry with exponential backoff on transient failures."""

    enable_rate_limit: bool = True
    """Enable per-domain rate limiting."""

    enable_robots: bool = False
    """Enable robots.txt checking (disabled by default — opt-in for scraping)."""

    enable_cache: bool = False
    """Enable in-memory HTTP response caching."""

    max_retries: int = 3
    """Maximum retry attempts (when enable_retry=True)."""

    requests_per_second: float = 10.0
    """Per-domain rate limit (when enable_rate_limit=True)."""

    cache_ttl: int = 300
    """Default cache TTL in seconds (when enable_cache=True)."""


class BrowserClientConfig(BaseModel):
    """Configuration for BrowserClient."""

    model_config = ConfigDict(frozen=True)

    # Browser
    browser_type: Literal["chromium", "firefox", "webkit"] = "chromium"
    headless: bool = True
    channel: str | None = None

    # Viewport
    viewport_width: int = 1280
    viewport_height: int = 720
    device_scale_factor: float = 1.0
    is_mobile: bool = False

    # Navigation
    default_wait_until: Literal["load", "domcontentloaded", "networkidle", "commit"] = "load"
    navigation_timeout: int = 30000
    default_timeout: int = 30000

    # Network
    block_resources: list[str] = []
    proxy: str | None = None
    ignore_https_errors: bool = False
    extra_headers: dict[str, str] = {}

    # Auth
    storage_state: str | None = None
    http_credentials: tuple[str, str] | None = None

    # Context
    user_agent: str | None = None
    locale: str | None = None
    timezone: str | None = None
    color_scheme: Literal["light", "dark"] | None = None
