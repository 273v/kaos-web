"""Tests for CacheMiddleware."""

from __future__ import annotations

from kaos_web.middleware.cache import CacheConfig, CacheMiddleware
from kaos_web.models import WebRequest, WebResponse


def _make_response(url: str = "https://example.com", **kwargs) -> WebResponse:
    return WebResponse(url=url, status_code=200, html="<p>test</p>", **kwargs)


def _make_handler(response: WebResponse | None = None, call_count: list | None = None):
    """Create a mock handler that returns a fixed response and tracks calls."""
    resp = response or _make_response()
    calls = call_count if call_count is not None else []

    async def handler(request: WebRequest) -> WebResponse:
        calls.append(request)
        return resp

    return handler, calls


class TestCacheHitMiss:
    async def test_cache_miss_then_hit(self):
        calls: list = []
        handler, calls = _make_handler(call_count=calls)
        cache = CacheMiddleware()

        request = WebRequest(url="https://example.com")

        # First call — miss, handler called
        resp1 = await cache.process(request, handler)
        assert len(calls) == 1
        assert resp1.status_code == 200

        # Second call — hit, handler NOT called
        resp2 = await cache.process(request, handler)
        assert len(calls) == 1  # Still 1 — cached
        assert resp2.status_code == 200

    async def test_different_urls_miss_separately(self):
        calls: list = []
        handler, calls = _make_handler(call_count=calls)
        cache = CacheMiddleware()

        await cache.process(WebRequest(url="https://a.com"), handler)
        await cache.process(WebRequest(url="https://b.com"), handler)
        assert len(calls) == 2  # Both miss

    async def test_post_not_cached(self):
        calls: list = []
        handler, calls = _make_handler(call_count=calls)
        cache = CacheMiddleware()

        request = WebRequest(url="https://example.com", method="POST")
        await cache.process(request, handler)
        await cache.process(request, handler)
        assert len(calls) == 2  # POST always fetches

    async def test_disabled_cache_always_fetches(self):
        calls: list = []
        handler, calls = _make_handler(call_count=calls)
        cache = CacheMiddleware(CacheConfig(enabled=False))

        request = WebRequest(url="https://example.com")
        await cache.process(request, handler)
        await cache.process(request, handler)
        assert len(calls) == 2


class TestCacheExpiration:
    async def test_ttl_expiration(self):
        import asyncio

        calls: list = []
        handler, calls = _make_handler(call_count=calls)
        cache = CacheMiddleware(CacheConfig(default_ttl=1))  # 1 second TTL

        request = WebRequest(url="https://example.com")
        await cache.process(request, handler)
        assert len(calls) == 1

        # Still fresh
        await cache.process(request, handler)
        assert len(calls) == 1  # Cache hit

        # Wait for expiration
        await asyncio.sleep(1.1)
        await cache.process(request, handler)
        assert len(calls) == 2  # Expired, re-fetched

    async def test_cache_control_max_age(self):
        resp = _make_response(headers={"cache-control": "max-age=3600"})
        handler, calls = _make_handler(response=resp)
        cache = CacheMiddleware()

        request = WebRequest(url="https://example.com")
        await cache.process(request, handler)
        await cache.process(request, handler)
        assert len(calls) == 1  # Cached for 3600s

    async def test_cache_control_no_store(self):
        resp = _make_response(headers={"cache-control": "no-store"})
        handler, calls = _make_handler(response=resp)
        cache = CacheMiddleware()

        request = WebRequest(url="https://example.com")
        await cache.process(request, handler)
        await cache.process(request, handler)
        assert len(calls) == 2  # no-store means never cache

    async def test_cache_control_no_cache(self):
        resp = _make_response(headers={"cache-control": "no-cache"})
        handler, calls = _make_handler(response=resp)
        cache = CacheMiddleware()

        request = WebRequest(url="https://example.com")
        await cache.process(request, handler)
        await cache.process(request, handler)
        assert len(calls) == 2  # no-cache → TTL 0 → always stale


class TestNonCacheableResponses:
    async def test_500_not_cached(self):
        resp = WebResponse(url="https://example.com", status_code=500, html="error")
        handler, calls = _make_handler(response=resp)
        cache = CacheMiddleware()

        request = WebRequest(url="https://example.com")
        await cache.process(request, handler)
        await cache.process(request, handler)
        assert len(calls) == 2  # 500 not in cacheable status codes

    async def test_301_cached(self):
        resp = WebResponse(url="https://example.com", status_code=301, html="")
        handler, calls = _make_handler(response=resp)
        cache = CacheMiddleware()

        request = WebRequest(url="https://example.com")
        await cache.process(request, handler)
        await cache.process(request, handler)
        assert len(calls) == 1  # 301 is cacheable

    async def test_404_cached(self):
        resp = WebResponse(url="https://example.com", status_code=404, html="not found")
        handler, calls = _make_handler(response=resp)
        cache = CacheMiddleware()

        request = WebRequest(url="https://example.com")
        await cache.process(request, handler)
        await cache.process(request, handler)
        assert len(calls) == 1  # 404 is cacheable per RFC 7231


class TestLRUEviction:
    async def test_evict_when_max_entries_exceeded(self):
        handler, _ = _make_handler()
        cache = CacheMiddleware(CacheConfig(max_entries=2))

        await cache.process(WebRequest(url="https://a.com"), handler)
        await cache.process(WebRequest(url="https://b.com"), handler)
        await cache.process(WebRequest(url="https://c.com"), handler)

        # Only 2 entries should remain
        assert cache.stats()["entries"] == 2

    async def test_lru_order_preserved(self):
        handler, calls = _make_handler()
        cache = CacheMiddleware(CacheConfig(max_entries=2))

        # Insert a, b
        await cache.process(WebRequest(url="https://a.com"), handler)
        await cache.process(WebRequest(url="https://b.com"), handler)

        # Access a (moves to most recent)
        await cache.process(WebRequest(url="https://a.com"), handler)
        assert len(calls) == 2  # a was a cache hit

        # Insert c — should evict b (least recently used), not a
        await cache.process(WebRequest(url="https://c.com"), handler)
        assert cache.stats()["entries"] == 2

        # b should be evicted (miss), a should still be cached (hit)
        calls.clear()
        await cache.process(WebRequest(url="https://a.com"), handler)
        assert len(calls) == 0  # a is still cached (hit)

        await cache.process(WebRequest(url="https://b.com"), handler)
        assert len(calls) == 1  # b was evicted (miss)

    async def test_evict_when_max_bytes_exceeded(self):
        # Large response to test byte-based eviction
        big_resp = WebResponse(url="https://example.com", status_code=200, html="x" * 5000)
        handler, _ = _make_handler(response=big_resp)
        # Set max_bytes low enough that 2 entries won't fit
        cache = CacheMiddleware(CacheConfig(max_bytes=6000))

        await cache.process(WebRequest(url="https://a.com"), handler)
        stats1 = cache.stats()
        assert stats1["entries"] == 1

        await cache.process(WebRequest(url="https://b.com"), handler)
        stats2 = cache.stats()
        # Second entry should evict first (only room for 1)
        assert stats2["entries"] == 1


class TestCacheStats:
    async def test_stats_tracking(self):
        handler, _ = _make_handler()
        cache = CacheMiddleware()

        request = WebRequest(url="https://example.com")
        await cache.process(request, handler)  # miss
        await cache.process(request, handler)  # hit
        await cache.process(request, handler)  # hit

        stats = cache.stats()
        assert stats["hits"] == 2
        assert stats["misses"] == 1
        assert stats["hit_rate"] == 2 / 3
        assert stats["entries"] == 1

    async def test_clear_resets_everything(self):
        handler, _ = _make_handler()
        cache = CacheMiddleware()

        await cache.process(WebRequest(url="https://example.com"), handler)
        cache.clear()

        stats = cache.stats()
        assert stats["hits"] == 0
        assert stats["misses"] == 0
        assert stats["entries"] == 0
        assert stats["total_bytes"] == 0
