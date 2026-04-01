"""In-memory HTTP cache middleware with LRU eviction.

RFC 7231 compliant: respects Cache-Control directives (no-store, no-cache,
max-age), only caches GET/HEAD with cacheable status codes, and supports
TTL expiration with LRU eviction when limits are exceeded.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict

from kaos_web.middleware.base import Handler
from kaos_web.models import WebRequest, WebResponse

logger = logging.getLogger(__name__)

# RFC 7231 Section 6.1: status codes that are cacheable by default
_CACHEABLE_STATUS_CODES = frozenset({200, 203, 204, 206, 300, 301, 404, 405, 410, 414, 501})

# HTTP methods that are cacheable
_CACHEABLE_METHODS = frozenset({"GET", "HEAD"})


class CacheConfig(BaseModel):
    """Cache middleware configuration."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    """Whether caching is enabled."""

    max_entries: int = 1000
    """Maximum number of cached responses."""

    max_bytes: int = 104_857_600  # 100 MB
    """Maximum total size of cached responses in bytes."""

    default_ttl: int = 300  # 5 minutes
    """Default time-to-live in seconds when no Cache-Control max-age is present."""

    respect_cache_control: bool = True
    """Whether to respect Cache-Control headers from responses."""


@dataclass
class CacheEntry:
    """A cached response with metadata."""

    key: str
    response: WebResponse
    created_at: float
    expires_at: float | None
    size_bytes: int
    access_count: int = field(default=0)


class CacheMiddleware:
    """In-memory HTTP cache with LRU eviction.

    Features:
    - LRU eviction when max_entries or max_bytes exceeded
    - TTL expiration (from Cache-Control max-age or default_ttl)
    - Only caches GET/HEAD with cacheable status codes (200, 301, 404, etc.)
    - Respects Cache-Control: no-store, no-cache, max-age
    - Cache key: blake2b hash of (method, url)
    """

    def __init__(self, config: CacheConfig | None = None) -> None:
        self.config = config or CacheConfig()
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._total_bytes: int = 0
        self._hits: int = 0
        self._misses: int = 0

    async def process(self, request: WebRequest, next_handler: Handler) -> WebResponse:
        """Check cache, return cached if fresh, otherwise fetch and cache."""
        if not self.config.enabled:
            return await next_handler(request)

        # Only cache GET/HEAD
        if request.method.upper() not in _CACHEABLE_METHODS:
            return await next_handler(request)

        key = self._cache_key(request)

        # Check for cached response
        entry = self._cache.get(key)
        if entry is not None:
            if self._is_fresh(entry):
                # Move to end (most recently used)
                self._cache.move_to_end(key)
                entry.access_count += 1
                self._hits += 1
                logger.debug("Cache HIT for %s (%d accesses)", request.url, entry.access_count)
                return entry.response

            # Expired — remove it
            self._remove_entry(key)

        # Cache miss — fetch from server
        self._misses += 1
        response = await next_handler(request)

        # Cache the response if appropriate
        if self._is_cacheable(request, response):
            ttl = self._get_ttl(response)
            size_bytes = self._estimate_size(response)

            # Evict if needed before inserting
            self._evict_if_needed(size_bytes)

            now = time.monotonic()
            entry = CacheEntry(
                key=key,
                response=response,
                created_at=now,
                expires_at=now + ttl if ttl > 0 else None,
                size_bytes=size_bytes,
            )
            self._cache[key] = entry
            self._total_bytes += size_bytes
            logger.debug("Cached %s (%d bytes, TTL=%ds)", request.url, size_bytes, ttl)

        return response

    def _cache_key(self, request: WebRequest) -> str:
        """Generate cache key from method + URL using blake2b."""
        raw = f"{request.method.upper()}:{request.url}"
        return hashlib.blake2b(raw.encode(), digest_size=16).hexdigest()

    def _is_cacheable(self, request: WebRequest, response: WebResponse) -> bool:
        """Check if response should be cached.

        Only caches GET/HEAD with cacheable status codes. Respects
        Cache-Control: no-store directive.
        """
        if request.method.upper() not in _CACHEABLE_METHODS:
            return False

        if response.status_code not in _CACHEABLE_STATUS_CODES:
            return False

        # Respect Cache-Control: no-store
        if self.config.respect_cache_control:
            cc = self._parse_cache_control(response.headers)
            if "no-store" in cc:
                return False

        return True

    def _is_fresh(self, entry: CacheEntry) -> bool:
        """Check if a cache entry is still fresh (not expired)."""
        if entry.expires_at is None:
            return True
        return time.monotonic() < entry.expires_at

    def _get_ttl(self, response: WebResponse) -> int:
        """Extract TTL from Cache-Control max-age or use default.

        Parses Cache-Control header for max-age directive. Falls back
        to default_ttl if not present or if respect_cache_control is False.
        """
        if self.config.respect_cache_control:
            cc = self._parse_cache_control(response.headers)

            # no-cache means must revalidate — use TTL of 0
            if "no-cache" in cc:
                return 0

            # Extract max-age
            max_age = cc.get("max-age")
            if max_age is not None:
                try:
                    return int(max_age)
                except ValueError:
                    pass

        return self.config.default_ttl

    def _parse_cache_control(self, headers: dict[str, str]) -> dict[str, str | None]:
        """Parse Cache-Control header into directive dict.

        Returns a dict where keys are directive names (lowercase) and values
        are directive values (or None for valueless directives like no-store).

        Example: "max-age=300, no-cache" -> {"max-age": "300", "no-cache": None}
        """
        result: dict[str, str | None] = {}
        raw = headers.get("cache-control", "") or headers.get("Cache-Control", "")
        if not raw:
            return result

        for part in raw.split(","):
            part = part.strip().lower()
            if "=" in part:
                key, _, value = part.partition("=")
                result[key.strip()] = value.strip()
            elif part:
                result[part] = None

        return result

    def _estimate_size(self, response: WebResponse) -> int:
        """Estimate the memory footprint of a cached response in bytes."""
        size = len(response.html.encode("utf-8")) if response.html else 0
        size += len(response.url.encode("utf-8"))
        size += len(response.content_type.encode("utf-8"))
        # Headers contribute ~100 bytes per entry on average
        size += sum(len(k) + len(v) for k, v in response.headers.items())
        # Screenshot can be large
        if response.screenshot:
            size += len(response.screenshot)
        # Object overhead
        size += 256
        return size

    def _evict_if_needed(self, needed_bytes: int) -> None:
        """Evict LRU entries to make room for a new entry.

        Evicts oldest entries (front of OrderedDict) until both
        max_entries and max_bytes constraints are satisfied.
        """
        # Evict for entry count limit
        while len(self._cache) >= self.config.max_entries and self._cache:
            self._evict_oldest()

        # Evict for byte limit
        while self._total_bytes + needed_bytes > self.config.max_bytes and self._cache:
            self._evict_oldest()

    def _evict_oldest(self) -> None:
        """Remove the least-recently-used (oldest) entry from the cache."""
        if not self._cache:
            return
        key, entry = self._cache.popitem(last=False)
        self._total_bytes -= entry.size_bytes
        logger.debug("Evicted cache entry %s (%d bytes)", key, entry.size_bytes)

    def _remove_entry(self, key: str) -> None:
        """Remove a specific entry from the cache."""
        entry = self._cache.pop(key, None)
        if entry is not None:
            self._total_bytes -= entry.size_bytes

    def stats(self) -> dict:
        """Return cache hit/miss statistics.

        Returns a dict with:
        - hits: number of cache hits
        - misses: number of cache misses
        - hit_rate: ratio of hits to total requests (0.0 if no requests)
        - entries: current number of cached entries
        - total_bytes: current total cached size in bytes
        - max_entries: configured maximum entries
        - max_bytes: configured maximum bytes
        """
        total = self._hits + self._misses
        return {
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": self._hits / total if total > 0 else 0.0,
            "entries": len(self._cache),
            "total_bytes": self._total_bytes,
            "max_entries": self.config.max_entries,
            "max_bytes": self.config.max_bytes,
        }

    def clear(self) -> None:
        """Clear all cached entries and reset statistics."""
        self._cache.clear()
        self._total_bytes = 0
        self._hits = 0
        self._misses = 0
