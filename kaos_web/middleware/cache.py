"""HTTP cache middleware with memory and disk backends.

RFC 7231 compliant: respects Cache-Control directives (no-store, no-cache,
max-age), only caches GET/HEAD with cacheable status codes, and supports
TTL expiration with LRU eviction when limits are exceeded.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from kaos_core.logging import get_logger
from kaos_web.middleware.base import Handler
from kaos_web.models import WebRequest, WebResponse

logger = get_logger(__name__)

# RFC 7231 Section 6.1: status codes that are cacheable by default
_CACHEABLE_STATUS_CODES = frozenset({200, 203, 204, 206, 300, 301, 404, 405, 410, 414, 501})

# HTTP methods that are cacheable
_CACHEABLE_METHODS = frozenset({"GET", "HEAD"})

# WEB5-009: any request bearing one of these headers is treated as
# auth-varied and bypasses the cache entirely (no LOOKUP, no STORE).
# Comparison is case-insensitive on the header NAME — values aren't
# inspected (a present-but-empty header still triggers bypass; cheaper
# and more conservative than parsing).
_AUTH_HEADER_NAMES = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "x-api-key",
        "x-auth-token",
        "x-csrf-token",
    }
)


def _has_auth_header(request: WebRequest) -> bool:
    """Return True if the request carries any auth-shaped header."""
    headers = request.headers or {}
    return any(name.lower() in _AUTH_HEADER_NAMES for name in headers)


class CacheConfig(BaseModel):
    """Cache middleware configuration."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    """Whether caching is enabled."""

    backend: Literal["memory", "disk"] = "memory"
    """Cache backend: 'memory' (in-process LRU) or 'disk' (persistent files)."""

    max_entries: int = 1000
    """Maximum number of cached responses."""

    max_bytes: int = 104_857_600  # 100 MB
    """Maximum total size of cached responses in bytes."""

    default_ttl: int = 300  # 5 minutes
    """Default time-to-live in seconds when no Cache-Control max-age is present."""

    respect_cache_control: bool = True
    """Whether to respect Cache-Control headers from responses."""

    cache_dir: str | None = None
    """Directory for disk cache (required when backend='disk')."""


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
    """HTTP cache with memory and disk backends.

    Features:
    - LRU eviction when max_entries or max_bytes exceeded
    - TTL expiration (from Cache-Control max-age or default_ttl)
    - Only caches GET/HEAD with cacheable status codes (200, 301, 404, etc.)
    - Respects Cache-Control: no-store, no-cache, max-age
    - Cache key: blake2b hash of (method, url)
    - Disk backend persists across process restarts
    """

    def __init__(self, config: CacheConfig | None = None) -> None:
        self.config = config or CacheConfig()
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._total_bytes: int = 0
        self._hits: int = 0
        self._misses: int = 0

        # Disk backend: load existing cache from disk
        if self.config.backend == "disk":
            self._cache_dir = Path(self.config.cache_dir or ".kaos-web-cache")
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            self._load_disk_index()

    async def process(self, request: WebRequest, next_handler: Handler) -> WebResponse:
        """Check cache, return cached if fresh, otherwise fetch and cache."""
        if not self.config.enabled:
            return await next_handler(request)

        # Only cache GET/HEAD
        if request.method.upper() not in _CACHEABLE_METHODS:
            return await next_handler(request)

        # WEB5-009: requests carrying auth-shaped headers vary the
        # response by caller and must NEVER hit the shared cache —
        # neither LOOKUP (would return another caller's response) nor
        # STORE (would later poison the same key for another caller).
        # The conservative policy is to bypass cache entirely for any
        # request whose headers include Authorization, Cookie,
        # X-API-Key, X-Auth-Token, X-CSRF-Token, or Proxy-Authorization.
        if _has_auth_header(request):
            logger.debug("Cache BYPASS for %s — request carries auth-shaped headers", request.url)
            return await next_handler(request)

        key = self._cache_key(request)

        # Check for cached response
        entry = self._cache.get(key)
        if entry is not None:
            if self._is_fresh(entry):
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

            # TTL of 0 means don't cache (no-cache directive)
            if ttl <= 0:
                return response

            size_bytes = self._estimate_size(response)
            self._evict_if_needed(size_bytes)

            now = time.monotonic()
            entry = CacheEntry(
                key=key,
                response=response,
                created_at=now,
                expires_at=now + ttl,
                size_bytes=size_bytes,
            )
            self._cache[key] = entry
            self._total_bytes += size_bytes
            logger.debug("Cached %s (%d bytes, TTL=%ds)", request.url, size_bytes, ttl)

            # Persist to disk
            if self.config.backend == "disk":
                self._save_to_disk(key, entry)

        return response

    def _cache_key(self, request: WebRequest) -> str:
        """Generate cache key from method + URL using blake2b."""
        raw = f"{request.method.upper()}:{request.url}"
        return hashlib.blake2b(raw.encode(), digest_size=16).hexdigest()

    def _is_cacheable(self, request: WebRequest, response: WebResponse) -> bool:
        """Check if response should be cached."""
        if request.method.upper() not in _CACHEABLE_METHODS:
            return False
        if response.status_code not in _CACHEABLE_STATUS_CODES:
            return False
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
        """Extract TTL from Cache-Control max-age or use default."""
        if self.config.respect_cache_control:
            cc = self._parse_cache_control(response.headers)
            if "no-cache" in cc:
                return 0
            max_age = cc.get("max-age")
            if max_age is not None:
                try:
                    return int(max_age)
                except ValueError:
                    pass
        return self.config.default_ttl

    def _parse_cache_control(self, headers: dict[str, str]) -> dict[str, str | None]:
        """Parse Cache-Control header into directive dict."""
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
        size += sum(len(k) + len(v) for k, v in response.headers.items())
        if response.screenshot:
            size += len(response.screenshot)
        size += 256  # object overhead
        return size

    def _evict_if_needed(self, needed_bytes: int) -> None:
        """Evict LRU entries to make room."""
        while len(self._cache) >= self.config.max_entries and self._cache:
            self._evict_oldest()
        while self._total_bytes + needed_bytes > self.config.max_bytes and self._cache:
            self._evict_oldest()

    def _evict_oldest(self) -> None:
        """Remove the least-recently-used entry."""
        if not self._cache:
            return
        key, entry = self._cache.popitem(last=False)
        self._total_bytes -= entry.size_bytes
        if self.config.backend == "disk":
            self._delete_from_disk(key)
        logger.debug("Evicted cache entry %s (%d bytes)", key, entry.size_bytes)

    def _remove_entry(self, key: str) -> None:
        """Remove a specific entry from the cache."""
        entry = self._cache.pop(key, None)
        if entry is not None:
            self._total_bytes -= entry.size_bytes
            if self.config.backend == "disk":
                self._delete_from_disk(key)

    def stats(self) -> dict:
        """Return cache hit/miss statistics."""
        total = self._hits + self._misses
        return {
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": self._hits / total if total > 0 else 0.0,
            "entries": len(self._cache),
            "total_bytes": self._total_bytes,
            "max_entries": self.config.max_entries,
            "max_bytes": self.config.max_bytes,
            "backend": self.config.backend,
        }

    def clear(self) -> None:
        """Clear all cached entries and reset statistics."""
        if self.config.backend == "disk":
            for key in list(self._cache.keys()):
                self._delete_from_disk(key)
        self._cache.clear()
        self._total_bytes = 0
        self._hits = 0
        self._misses = 0

    # ─── Disk backend ────────────────────────────────────────────────────

    def _entry_path(self, key: str) -> Path:
        """Get the file path for a cache entry (sharded by first 2 chars)."""
        shard = key[:2]
        return self._cache_dir / shard / f"{key}.json"

    def _save_to_disk(self, key: str, entry: CacheEntry) -> None:
        """Persist a cache entry to disk as JSON."""
        path = self._entry_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "key": key,
            "url": entry.response.url,
            "status_code": entry.response.status_code,
            "content_type": entry.response.content_type,
            "html": entry.response.html,
            "headers": entry.response.headers,
            "elapsed_ms": entry.response.elapsed_ms,
            "cookies": entry.response.cookies,
            "created_at": entry.created_at,
            "expires_at": entry.expires_at,
            "size_bytes": entry.size_bytes,
            "content_hash": hashlib.blake2b(
                (entry.response.html or "").encode(), digest_size=16
            ).hexdigest(),
        }
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def _delete_from_disk(self, key: str) -> None:
        """Remove a cache entry from disk."""
        path = self._entry_path(key)
        if path.exists():
            path.unlink()

    def _load_disk_index(self) -> None:
        """Load existing cache entries from disk on startup."""
        if not self._cache_dir.exists():
            return
        now = time.monotonic()
        loaded = 0
        for json_file in self._cache_dir.rglob("*.json"):
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
                key = data["key"]

                # Verify content hash
                content_hash = hashlib.blake2b(
                    (data.get("html") or "").encode(), digest_size=16
                ).hexdigest()
                if content_hash != data.get("content_hash"):
                    logger.warning("Corrupted cache entry %s, removing", key)
                    json_file.unlink()
                    continue

                response = WebResponse(
                    url=data["url"],
                    status_code=data["status_code"],
                    content_type=data.get("content_type", ""),
                    html=data.get("html", ""),
                    headers=data.get("headers", {}),
                    elapsed_ms=data.get("elapsed_ms", 0.0),
                    cookies=data.get("cookies", {}),
                )
                entry = CacheEntry(
                    key=key,
                    response=response,
                    created_at=now,  # Reset monotonic clock on load
                    expires_at=now + self.config.default_ttl,  # Re-apply TTL
                    size_bytes=data.get("size_bytes", 0),
                )
                self._cache[key] = entry
                self._total_bytes += entry.size_bytes
                loaded += 1
            except Exception:
                logger.debug("Failed to load cache entry %s", json_file)

        if loaded:
            logger.info("Loaded %d cached entries from disk (%s)", loaded, self._cache_dir)
