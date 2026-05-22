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


def _default_extra_headers() -> dict[str, str]:
    # Imported lazily so the user_agents module isn't loaded at import
    # time for HttpClient consumers that don't need Playwright defaults.
    from kaos_web.clients.user_agents import DEFAULT_EXTRA_HEADERS

    return dict(DEFAULT_EXTRA_HEADERS)


class BrowserClientConfig(BaseModel):
    """Configuration for BrowserClient.

    Defaults mirror the production-validated anti-bot context from
    ``kelvin-legal-intelligence`` — realistic 1365x768 laptop viewport,
    en-US locale, America/New_York timezone, full Chrome sec-ch-ua +
    sec-fetch + accept-language header set, and round-robin UA
    rotation across :data:`kaos_web.clients.user_agents.DEFAULT_DESKTOP_UAS`.
    These choices are what get the BrowserClient past SEC.gov,
    Cloudflare, and most government anti-bot stacks; do NOT downgrade
    them without a verified-on-target-sites reason.
    """

    model_config = ConfigDict(frozen=True)

    # Browser
    browser_type: Literal["chromium", "firefox", "webkit"] = "chromium"
    headless: bool = True
    channel: str | None = None

    # Viewport — kelvin reference defaults
    viewport_width: int = 1365
    viewport_height: int = 768
    device_scale_factor: float = 1.0
    is_mobile: bool = False

    # Navigation
    default_wait_until: Literal["load", "domcontentloaded", "networkidle", "commit"] = "networkidle"
    navigation_timeout: int = 30000
    default_timeout: int = 30000

    # Network
    block_resources: list[str] = []
    proxy: str | None = None
    ignore_https_errors: bool = False
    extra_headers: dict[str, str] = {}
    """Caller-supplied additional headers. Merged on TOP of
    :data:`DEFAULT_EXTRA_HEADERS` (caller wins for collisions). Empty
    by default; the anti-bot Chrome header set is applied automatically
    unless ``use_default_anti_bot_headers=False``."""

    use_default_anti_bot_headers: bool = True
    """Apply :data:`DEFAULT_EXTRA_HEADERS` (sec-ch-ua, sec-fetch-*,
    accept-language, cache-control, …) to every browser context.
    Set False only for testing or to deliberately fingerprint as
    "non-Chrome"."""

    # Auth
    storage_state: str | None = None
    http_credentials: tuple[str, str] | None = None

    # Context — kelvin reference defaults
    user_agent: str | None = None
    """Explicit User-Agent string. None = rotate through
    :data:`DEFAULT_DESKTOP_UAS` round-robin per fetch."""

    randomize_user_agent: bool = True
    """Cycle through :data:`DEFAULT_DESKTOP_UAS` when ``user_agent``
    is None. False + ``user_agent=None`` = use Playwright's default UA
    (Chromium headless string — easily fingerprinted as a bot)."""

    locale: str | None = "en-US"
    timezone: str | None = "America/New_York"
    color_scheme: Literal["light", "dark"] | None = None

    # Browser pool — amortize the 2-3s launch cost across requests
    enable_browser_pool: bool = True
    pool_max_browsers: int = 3
    pool_idle_timeout_seconds: float = 300.0
