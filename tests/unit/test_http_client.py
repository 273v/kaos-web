"""Tests for HttpClient — HTTP fetching with connection pooling, auth, and error mapping."""

from __future__ import annotations

import httpx
import pytest
from pytest_httpx import HTTPXMock

from kaos_web.clients.config import HttpClientConfig
from kaos_web.clients.http import HttpClient
from kaos_web.errors import (
    WebClientError,
    WebNetworkError,
    WebRateLimitError,
    WebServerError,
    WebTimeoutError,
)
from kaos_web.models import WebRequest, WebResponse


class TestBasicFetch:
    async def test_basic_get(self, httpx_mock: HTTPXMock) -> None:
        """Mock a 200 response and verify all WebResponse fields are populated."""
        httpx_mock.add_response(
            url="https://example.com/page",
            status_code=200,
            html="<html><body>Hello</body></html>",
            headers={"content-type": "text/html; charset=utf-8"},
        )

        async with HttpClient() as client:
            resp = await client.fetch(WebRequest(url="https://example.com/page"))

        assert isinstance(resp, WebResponse), "fetch should return a WebResponse"
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        assert resp.url == "https://example.com/page"
        assert "Hello" in resp.html, "Response HTML should contain the body content"
        assert "text/html" in resp.content_type
        assert isinstance(resp.headers, dict), "Headers should be a dict"
        assert resp.elapsed_ms >= 0, "Elapsed time should be non-negative"

    async def test_custom_user_agent(self, httpx_mock: HTTPXMock) -> None:
        """Verify the configured user-agent header is sent with every request."""
        custom_ua = "TestBot/1.0"
        httpx_mock.add_response(status_code=200)

        config = HttpClientConfig(user_agent=custom_ua)
        async with HttpClient(config) as client:
            await client.fetch(WebRequest(url="https://example.com/"))

        request = httpx_mock.get_request()
        assert request is not None, "A request should have been made"
        assert request.headers["user-agent"] == custom_ua, (
            f"Expected user-agent '{custom_ua}', got '{request.headers.get('user-agent')}'"
        )


class TestConnectionPooling:
    async def test_connection_pooling_config(self) -> None:
        """Create client with custom HttpClientConfig and verify limits are applied."""
        config = HttpClientConfig(
            max_connections=50,
            max_keepalive_connections=10,
            keepalive_expiry=15.0,
        )
        client = HttpClient(config)
        try:
            pool = client._client._transport._pool
            assert pool._max_connections == 50, (
                f"Expected max_connections=50, got {pool._max_connections}"
            )
            assert pool._max_keepalive_connections == 10, (
                f"Expected max_keepalive=10, got {pool._max_keepalive_connections}"
            )
        finally:
            await client.close()


class TestAuthentication:
    async def test_bearer_auth(self, httpx_mock: HTTPXMock) -> None:
        """Verify Authorization: Bearer header is set when bearer_token is configured."""
        httpx_mock.add_response(status_code=200)

        config = HttpClientConfig(bearer_token="my-secret-token")
        async with HttpClient(config) as client:
            await client.fetch(WebRequest(url="https://example.com/api"))

        request = httpx_mock.get_request()
        assert request is not None
        assert request.headers["authorization"] == "Bearer my-secret-token", (
            "Authorization header should contain the bearer token"
        )

    async def test_api_key_auth(self, httpx_mock: HTTPXMock) -> None:
        """Verify API key header is set when api_key is configured."""
        httpx_mock.add_response(status_code=200)

        config = HttpClientConfig(api_key="key-12345", api_key_header="X-Custom-Key")
        async with HttpClient(config) as client:
            await client.fetch(WebRequest(url="https://example.com/api"))

        request = httpx_mock.get_request()
        assert request is not None
        assert request.headers["x-custom-key"] == "key-12345", "Custom API key header should be set"

    async def test_basic_auth(self, httpx_mock: HTTPXMock) -> None:
        """Verify basic auth credentials are sent."""
        httpx_mock.add_response(status_code=200)

        config = HttpClientConfig(basic_auth=("user", "pass"))
        async with HttpClient(config) as client:
            await client.fetch(WebRequest(url="https://example.com/api"))

        request = httpx_mock.get_request()
        assert request is not None
        auth_header = request.headers.get("authorization", "")
        assert auth_header.startswith("Basic "), f"Expected Basic auth header, got: {auth_header}"


class TestRedirects:
    async def test_follow_redirects(self, httpx_mock: HTTPXMock) -> None:
        """Mock 301 -> 200 chain and verify final URL is reported."""
        httpx_mock.add_response(
            url="https://example.com/old",
            status_code=301,
            headers={"location": "https://example.com/new"},
        )
        httpx_mock.add_response(
            url="https://example.com/new",
            status_code=200,
            html="<html><body>New page</body></html>",
        )

        config = HttpClientConfig(follow_redirects=True)
        async with HttpClient(config) as client:
            resp = await client.fetch(WebRequest(url="https://example.com/old"))

        assert resp.status_code == 200, f"Expected 200 after redirect, got {resp.status_code}"
        assert resp.url == "https://example.com/new", (
            f"Expected final URL to be /new, got {resp.url}"
        )
        assert "New page" in resp.html


class TestErrorMapping:
    async def test_timeout_error(self, httpx_mock: HTTPXMock) -> None:
        """Mock a timeout and verify WebTimeoutError is raised."""
        httpx_mock.add_exception(
            httpx.ReadTimeout("read timed out"),
            url="https://example.com/slow",
        )

        async with HttpClient() as client:
            with pytest.raises(WebTimeoutError) as exc_info:
                await client.fetch(WebRequest(url="https://example.com/slow"))

        assert exc_info.value.retryable is True, "Timeout errors should be retryable"
        assert exc_info.value.timeout_type == "read", (
            f"Expected timeout_type='read', got '{exc_info.value.timeout_type}'"
        )
        assert "example.com/slow" in exc_info.value.url

    async def test_network_error(self, httpx_mock: HTTPXMock) -> None:
        """Mock a connection failure and verify WebNetworkError is raised."""
        httpx_mock.add_exception(
            httpx.ConnectError("Connection refused"),
            url="https://example.com/down",
        )

        async with HttpClient() as client:
            with pytest.raises(WebNetworkError) as exc_info:
                await client.fetch(WebRequest(url="https://example.com/down"))

        assert exc_info.value.retryable is True, "Network errors should be retryable"
        assert "example.com/down" in exc_info.value.url

    async def test_server_error_500(self, httpx_mock: HTTPXMock) -> None:
        """Mock a 500 response and verify WebServerError is raised."""
        httpx_mock.add_response(
            url="https://example.com/error",
            status_code=500,
        )

        async with HttpClient() as client:
            with pytest.raises(WebServerError) as exc_info:
                await client.fetch(WebRequest(url="https://example.com/error"))

        assert exc_info.value.status_code == 500
        assert exc_info.value.retryable is True, "Server errors should be retryable"

    async def test_client_error_404(self, httpx_mock: HTTPXMock) -> None:
        """Mock a 404 response and verify WebClientError is raised."""
        httpx_mock.add_response(
            url="https://example.com/missing",
            status_code=404,
        )

        async with HttpClient() as client:
            with pytest.raises(WebClientError) as exc_info:
                await client.fetch(WebRequest(url="https://example.com/missing"))

        assert exc_info.value.status_code == 404
        assert exc_info.value.retryable is False, "Client errors should NOT be retryable"

    async def test_rate_limit_429(self, httpx_mock: HTTPXMock) -> None:
        """Mock a 429 with Retry-After header and verify WebRateLimitError."""
        httpx_mock.add_response(
            url="https://example.com/api",
            status_code=429,
            headers={"retry-after": "30"},
        )

        async with HttpClient() as client:
            with pytest.raises(WebRateLimitError) as exc_info:
                await client.fetch(WebRequest(url="https://example.com/api"))

        assert exc_info.value.status_code == 429
        assert exc_info.value.retry_after == 30.0, (
            f"Expected retry_after=30.0, got {exc_info.value.retry_after}"
        )
        assert exc_info.value.retryable is True, "Rate limit errors should be retryable"


class TestContextManager:
    async def test_context_manager(self, httpx_mock: HTTPXMock) -> None:
        """Test the async context manager pattern opens and closes cleanly."""
        httpx_mock.add_response(status_code=200, html="<html></html>")

        async with HttpClient() as client:
            resp = await client.fetch(WebRequest(url="https://example.com/"))
            assert resp.status_code == 200

        # After exiting context, the underlying client should be closed
        assert client._client.is_closed, "Client should be closed after context manager exit"
