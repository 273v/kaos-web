"""Unit tests for batch fetch."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from kaos_web.discover.batch import BatchError, BatchResult, batch_fetch
from kaos_web.models import WebResponse


class TestBatchResult:
    def test_empty(self):
        r = BatchResult()
        assert r.total == 0
        assert r.succeeded == 0
        assert r.failed == 0

    def test_with_responses(self):
        r = BatchResult(
            responses=[
                WebResponse(url="https://a.com", status_code=200),
                WebResponse(url="https://b.com", status_code=200),
            ],
            errors=[BatchError(url="https://c.com", error="timeout")],
        )
        assert r.total == 3
        assert r.succeeded == 2
        assert r.failed == 1


class TestBatchFetch:
    @pytest.mark.asyncio
    async def test_empty_urls(self):
        result = await batch_fetch([])
        assert result.total == 0

    @pytest.mark.asyncio
    async def test_single_url(self):
        resp = WebResponse(url="https://example.com", status_code=200, html="<html></html>")
        with patch("kaos_web.discover.batch.HttpClient") as MockClient:
            instance = AsyncMock()
            instance.fetch = AsyncMock(return_value=resp)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await batch_fetch(["https://example.com"])
            assert result.succeeded == 1
            assert result.failed == 0

    @pytest.mark.asyncio
    async def test_mixed_success_and_failure(self):
        call_count = 0

        async def mock_fetch(request):
            nonlocal call_count
            call_count += 1
            if "fail" in request.url:
                raise ConnectionError("Network error")
            return WebResponse(url=request.url, status_code=200, html="<html></html>")

        with patch("kaos_web.discover.batch.HttpClient") as MockClient:
            instance = AsyncMock()
            instance.fetch = mock_fetch
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await batch_fetch(
                [
                    "https://example.com/ok1",
                    "https://example.com/fail",
                    "https://example.com/ok2",
                ]
            )
            assert result.succeeded == 2
            assert result.failed == 1
            assert result.errors[0].url == "https://example.com/fail"

    @pytest.mark.asyncio
    async def test_concurrency_limit(self):
        """Verify semaphore limits concurrent requests."""
        max_concurrent = 0
        current_concurrent = 0
        import asyncio

        lock = asyncio.Lock()

        async def mock_fetch(request):
            nonlocal max_concurrent, current_concurrent
            async with lock:
                current_concurrent += 1
                if current_concurrent > max_concurrent:
                    max_concurrent = current_concurrent
            await asyncio.sleep(0.01)
            async with lock:
                current_concurrent -= 1
            return WebResponse(url=request.url, status_code=200, html="ok")

        with patch("kaos_web.discover.batch.HttpClient") as MockClient:
            instance = AsyncMock()
            instance.fetch = mock_fetch
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            urls = [f"https://example.com/{i}" for i in range(10)]
            await batch_fetch(urls, concurrency=3)
            assert max_concurrent <= 3

    @pytest.mark.asyncio
    async def test_elapsed_ms_tracked(self):
        resp = WebResponse(url="https://example.com", status_code=200, html="ok")
        with patch("kaos_web.discover.batch.HttpClient") as MockClient:
            instance = AsyncMock()
            instance.fetch = AsyncMock(return_value=resp)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await batch_fetch(["https://example.com"])
            assert result.elapsed_ms > 0
