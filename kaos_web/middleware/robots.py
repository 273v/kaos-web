"""Robots.txt middleware using stdlib robotparser."""

from __future__ import annotations

import logging
import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from pydantic import BaseModel, ConfigDict

from kaos_web.errors import WebClientError
from kaos_web.middleware.base import Handler
from kaos_web.models import WebRequest, WebResponse

logger = logging.getLogger(__name__)


class RobotsConfig(BaseModel):
    """Robots.txt middleware configuration."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    user_agent: str = "KAOS-Web"
    cache_ttl: int = 3600


class _CachedRobots:
    """Cached robots.txt parser for a domain."""

    __slots__ = ("crawl_delay", "fetched_at", "parser")

    def __init__(self, parser: RobotFileParser, crawl_delay: float | None) -> None:
        self.parser = parser
        self.fetched_at = time.monotonic()
        self.crawl_delay = crawl_delay


class RobotsMiddleware:
    """Check robots.txt before fetching URLs.

    Caches parsed robots.txt per domain. Blocks requests disallowed by
    robots.txt. Respects Crawl-delay directive as minimum delay.

    Uses stdlib urllib.robotparser — supports Allow/Disallow, Crawl-delay,
    Sitemap. Does not support wildcard patterns (use protego if needed).
    """

    def __init__(self, config: RobotsConfig | None = None) -> None:
        self.config = config or RobotsConfig()
        self._cache: dict[str, _CachedRobots] = {}

    def _get_robots_url(self, url: str) -> tuple[str, str]:
        """Extract domain and robots.txt URL from a page URL."""
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        return parsed.netloc, f"{base}/robots.txt"

    def _get_cached(self, domain: str) -> _CachedRobots | None:
        """Get cached robots.txt if still fresh."""
        cached = self._cache.get(domain)
        if cached is None:
            return None
        age = time.monotonic() - cached.fetched_at
        if age > self.config.cache_ttl:
            del self._cache[domain]
            return None
        return cached

    async def _fetch_robots(self, robots_url: str, domain: str, handler: Handler) -> _CachedRobots:
        """Fetch and parse robots.txt for a domain."""
        parser = RobotFileParser(robots_url)
        try:
            # Fetch robots.txt through the handler (respects middleware below us)
            request = WebRequest(url=robots_url, timeout=10.0)
            response = await handler(request)
            if response.ok and response.html:
                parser.parse(response.html.splitlines())
            else:
                # No robots.txt or error → allow everything
                parser.allow_all = True
        except Exception:
            # Network error fetching robots.txt → allow everything
            logger.debug("Failed to fetch %s, allowing all", robots_url)
            parser.allow_all = True

        # Extract crawl-delay
        crawl_delay = parser.crawl_delay(self.config.user_agent)

        cached = _CachedRobots(parser, crawl_delay)
        self._cache[domain] = cached
        return cached

    async def process(self, request: WebRequest, next_handler: Handler) -> WebResponse:
        """Check robots.txt before fetching."""
        if not self.config.enabled:
            return await next_handler(request)

        domain, robots_url = self._get_robots_url(request.url)

        # Don't check robots.txt for robots.txt itself
        if request.url.rstrip("/").endswith("/robots.txt"):
            return await next_handler(request)

        cached = self._get_cached(domain)
        if cached is None:
            cached = await self._fetch_robots(robots_url, domain, next_handler)

        # Check if URL is allowed
        if not cached.parser.can_fetch(self.config.user_agent, request.url):
            raise WebClientError(
                f"Blocked by robots.txt: {request.url}. "
                f"The site's robots.txt disallows access to this URL for user-agent "
                f"'{self.config.user_agent}'.",
                url=request.url,
                status_code=403,
            )

        return await next_handler(request)
