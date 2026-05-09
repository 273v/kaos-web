"""Tests for browser response body capture and artifact storage.

Tests verify:
- Response body capture in BrowserClient (filtering, truncation, error handling)
- Retrieval methods (get_response_body, get_captured_responses)
- Cleanup on close_context / close
- Updated MCP tools (EnableRequestLogging, ListRequests, GetRequestDetail)
- New ListCapturedResponsesTool (metadata, execution, artifact creation)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kaos_web.clients.browser import (
    _DEFAULT_CAPTURE_CONTENT_TYPES,
    _DEFAULT_CAPTURE_RESOURCE_TYPES,
    _DEFAULT_MAX_BODY_SIZE,
    BrowserClient,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
# NOTE: The autouse `_block_real_playwright_launch` guard that used to live
# here was promoted to `tests/unit/conftest.py` per audit-03 WEB3-003 so it
# covers the whole unit tier. Setup helpers in this file still must seed
# `client._browser = MagicMock()` to short-circuit `_ensure_browser()`.


def _mock_page(url: str = "https://example.com") -> MagicMock:
    """Create a mock Playwright page with on() support."""
    page = AsyncMock()
    page.url = url
    page.on = MagicMock()
    page.close = AsyncMock()
    return page


def _mock_context(page: MagicMock | None = None) -> MagicMock:
    ctx = AsyncMock()
    ctx.new_page = AsyncMock(return_value=page or _mock_page())
    ctx.close = AsyncMock()
    return ctx


def _setup_client_with_context(
    context_id: str = "s1",
) -> tuple[BrowserClient, MagicMock]:
    """Create a BrowserClient with a fully-mocked browser, context, and page.

    Seeds ``_browser`` and ``_playwright`` so ``_ensure_browser()`` short-
    circuits and never reaches ``async_playwright().start()`` — keeping these
    tests offline and bounded.
    """
    client = BrowserClient()
    page = _mock_page()
    ctx = _mock_context(page)
    client._contexts[context_id] = ctx
    client._pages[context_id] = page
    client._browser = MagicMock()
    client._playwright = MagicMock()
    return client, page


def _mock_response(
    *,
    url: str = "https://api.example.com/data",
    status: int = 200,
    resource_type: str = "fetch",
    content_type: str = "application/json",
    content_length: str | None = None,
    body: bytes = b'{"results": []}',
    body_error: Exception | None = None,
) -> MagicMock:
    """Create a mock Playwright Response."""
    resp = MagicMock()
    resp.url = url
    resp.status = status
    resp.status_text = "OK" if status == 200 else "Moved"
    headers = {"content-type": content_type}
    if content_length is not None:
        headers["content-length"] = content_length
    resp.headers = headers

    # Mock the request object (for resource_type)
    req = MagicMock()
    req.resource_type = resource_type
    resp.request = req

    if body_error:
        resp.body = AsyncMock(side_effect=body_error)
    else:
        resp.body = AsyncMock(return_value=body)

    return resp


# ---------------------------------------------------------------------------
# Tests — Constants
# ---------------------------------------------------------------------------


class TestCaptureConstants:
    def test_default_resource_types(self):
        assert "fetch" in _DEFAULT_CAPTURE_RESOURCE_TYPES
        assert "xhr" in _DEFAULT_CAPTURE_RESOURCE_TYPES
        assert "image" not in _DEFAULT_CAPTURE_RESOURCE_TYPES

    def test_default_content_types(self):
        assert "application/json" in _DEFAULT_CAPTURE_CONTENT_TYPES
        assert "text/html" in _DEFAULT_CAPTURE_CONTENT_TYPES
        assert "text/plain" in _DEFAULT_CAPTURE_CONTENT_TYPES

    def test_default_max_body_size(self):
        assert _DEFAULT_MAX_BODY_SIZE == 1_048_576


# ---------------------------------------------------------------------------
# Tests — BrowserClient body capture
# ---------------------------------------------------------------------------


class TestResponseBodyCapture:
    @pytest.mark.asyncio
    async def test_capture_bodies_initializes_storage(self):
        """capture_bodies=True initializes _response_bodies dict."""
        client, _page = _setup_client_with_context()
        await client.enable_request_logging("s1", capture_bodies=True)
        assert hasattr(client, "_response_bodies")
        assert "s1" in client._response_bodies
        assert client._response_bodies["s1"] == {}

    @pytest.mark.asyncio
    async def test_no_capture_does_not_initialize_bodies(self):
        """capture_bodies=False (default) does not create _response_bodies."""
        client, _page = _setup_client_with_context()
        await client.enable_request_logging("s1")
        assert not hasattr(client, "_response_bodies")

    @pytest.mark.asyncio
    async def test_async_handler_captures_json_body(self):
        """Async handler captures JSON fetch response body."""
        client, page = _setup_client_with_context()
        await client.enable_request_logging("s1", capture_bodies=True)

        # Get the handlers registered on the page
        assert page.on.call_count == 2
        request_handler = page.on.call_args_list[0][0][1]
        response_handler = page.on.call_args_list[1][0][1]

        # Simulate a request event
        mock_req = MagicMock()
        mock_req.url = "https://api.example.com/data"
        mock_req.method = "GET"
        mock_req.resource_type = "fetch"
        mock_req.headers = {}
        mock_req.post_data = None
        mock_req.is_navigation_request = MagicMock(return_value=False)
        request_handler(mock_req)

        # Simulate a response event
        body_bytes = b'{"people": [{"name": "Alice"}]}'
        resp = _mock_response(body=body_bytes)
        await response_handler(resp)

        # Verify body was captured
        assert 0 in client._response_bodies["s1"]
        captured = client._response_bodies["s1"][0]
        assert captured["body"] == body_bytes
        assert captured["content_type"] == "application/json"
        assert captured["size"] == len(body_bytes)
        assert captured["truncated"] is False

        # Verify log entry has body metadata
        log = client._request_logs["s1"]
        assert log[0]["has_body"] is True
        assert log[0]["body_size"] == len(body_bytes)

    @pytest.mark.asyncio
    async def test_skips_redirect_responses(self):
        """302 redirect responses are skipped (no body)."""
        client, page = _setup_client_with_context()
        await client.enable_request_logging("s1", capture_bodies=True)
        request_handler = page.on.call_args_list[0][0][1]
        response_handler = page.on.call_args_list[1][0][1]

        mock_req = MagicMock()
        mock_req.url = "https://example.com/old"
        mock_req.method = "GET"
        mock_req.resource_type = "fetch"
        mock_req.headers = {}
        mock_req.post_data = None
        mock_req.is_navigation_request = MagicMock(return_value=False)
        request_handler(mock_req)

        resp = _mock_response(url="https://example.com/old", status=302)
        await response_handler(resp)

        assert 0 not in client._response_bodies["s1"]

    @pytest.mark.asyncio
    async def test_skips_non_matching_resource_type(self):
        """Image resource type is not captured."""
        client, page = _setup_client_with_context()
        await client.enable_request_logging("s1", capture_bodies=True)
        request_handler = page.on.call_args_list[0][0][1]
        response_handler = page.on.call_args_list[1][0][1]

        mock_req = MagicMock()
        mock_req.url = "https://example.com/logo.png"
        mock_req.method = "GET"
        mock_req.resource_type = "image"
        mock_req.headers = {}
        mock_req.post_data = None
        mock_req.is_navigation_request = MagicMock(return_value=False)
        request_handler(mock_req)

        resp = _mock_response(
            url="https://example.com/logo.png",
            resource_type="image",
            content_type="image/png",
        )
        await response_handler(resp)

        assert 0 not in client._response_bodies["s1"]

    @pytest.mark.asyncio
    async def test_skips_non_matching_content_type(self):
        """Fetch with image/png content type is not captured."""
        client, page = _setup_client_with_context()
        await client.enable_request_logging("s1", capture_bodies=True)
        request_handler = page.on.call_args_list[0][0][1]
        response_handler = page.on.call_args_list[1][0][1]

        mock_req = MagicMock()
        mock_req.url = "https://example.com/image"
        mock_req.method = "GET"
        mock_req.resource_type = "fetch"
        mock_req.headers = {}
        mock_req.post_data = None
        mock_req.is_navigation_request = MagicMock(return_value=False)
        request_handler(mock_req)

        resp = _mock_response(
            url="https://example.com/image",
            resource_type="fetch",
            content_type="image/png",
        )
        await response_handler(resp)

        assert 0 not in client._response_bodies["s1"]

    @pytest.mark.asyncio
    async def test_skips_oversized_by_content_length(self):
        """Responses with Content-Length > max_body_size are skipped."""
        client, page = _setup_client_with_context()
        await client.enable_request_logging("s1", capture_bodies=True, max_body_size=1000)
        request_handler = page.on.call_args_list[0][0][1]
        response_handler = page.on.call_args_list[1][0][1]

        mock_req = MagicMock()
        mock_req.url = "https://api.example.com/big"
        mock_req.method = "GET"
        mock_req.resource_type = "fetch"
        mock_req.headers = {}
        mock_req.post_data = None
        mock_req.is_navigation_request = MagicMock(return_value=False)
        request_handler(mock_req)

        resp = _mock_response(
            url="https://api.example.com/big",
            content_length="5000",
        )
        await response_handler(resp)

        # Body not captured, but reason logged
        assert 0 not in client._response_bodies["s1"]
        log_entry = client._request_logs["s1"][0]
        assert log_entry["has_body"] is False
        assert log_entry["body_reason"] == "too_large"
        assert log_entry["body_content_length"] == 5000

    @pytest.mark.asyncio
    async def test_truncates_oversized_body(self):
        """Bodies over max_body_size are truncated (no Content-Length header)."""
        client, page = _setup_client_with_context()
        max_size = 100
        await client.enable_request_logging("s1", capture_bodies=True, max_body_size=max_size)
        request_handler = page.on.call_args_list[0][0][1]
        response_handler = page.on.call_args_list[1][0][1]

        mock_req = MagicMock()
        mock_req.url = "https://api.example.com/large"
        mock_req.method = "GET"
        mock_req.resource_type = "fetch"
        mock_req.headers = {}
        mock_req.post_data = None
        mock_req.is_navigation_request = MagicMock(return_value=False)
        request_handler(mock_req)

        big_body = b"x" * 500
        resp = _mock_response(url="https://api.example.com/large", body=big_body)
        await response_handler(resp)

        captured = client._response_bodies["s1"][0]
        assert len(captured["body"]) == max_size
        assert captured["truncated"] is True
        assert client._request_logs["s1"][0]["body_truncated"] is True

    @pytest.mark.asyncio
    async def test_handles_body_fetch_failure(self):
        """If response.body() raises, entry is marked with body_reason."""
        client, page = _setup_client_with_context()
        await client.enable_request_logging("s1", capture_bodies=True)
        request_handler = page.on.call_args_list[0][0][1]
        response_handler = page.on.call_args_list[1][0][1]

        mock_req = MagicMock()
        mock_req.url = "https://api.example.com/fail"
        mock_req.method = "GET"
        mock_req.resource_type = "fetch"
        mock_req.headers = {}
        mock_req.post_data = None
        mock_req.is_navigation_request = MagicMock(return_value=False)
        request_handler(mock_req)

        resp = _mock_response(
            url="https://api.example.com/fail",
            body_error=Exception("Target closed"),
        )
        await response_handler(resp)

        assert 0 not in client._response_bodies["s1"]
        log_entry = client._request_logs["s1"][0]
        assert log_entry["has_body"] is False
        assert log_entry["body_reason"] == "fetch_failed"

    @pytest.mark.asyncio
    async def test_custom_resource_types(self):
        """Custom resource_types filter controls which types are captured."""
        client, page = _setup_client_with_context()
        await client.enable_request_logging(
            "s1",
            capture_bodies=True,
            resource_types=frozenset({"document"}),
        )
        request_handler = page.on.call_args_list[0][0][1]
        response_handler = page.on.call_args_list[1][0][1]

        # fetch type — should be skipped with custom filter
        mock_req = MagicMock()
        mock_req.url = "https://api.example.com/data"
        mock_req.method = "GET"
        mock_req.resource_type = "fetch"
        mock_req.headers = {}
        mock_req.post_data = None
        mock_req.is_navigation_request = MagicMock(return_value=False)
        request_handler(mock_req)

        resp = _mock_response(resource_type="fetch")
        await response_handler(resp)

        assert 0 not in client._response_bodies["s1"]


# ---------------------------------------------------------------------------
# Tests — Retrieval methods
# ---------------------------------------------------------------------------


class TestResponseRetrieval:
    @pytest.mark.asyncio
    async def test_get_response_body_returns_none_unknown(self):
        """get_response_body returns None for unknown request_id."""
        client = BrowserClient()
        result = await client.get_response_body("s1", 999)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_response_body_returns_captured(self):
        """get_response_body returns stored body info."""
        client = BrowserClient()
        client._response_bodies = {
            "s1": {
                0: {
                    "body": b'{"ok": true}',
                    "content_type": "application/json",
                    "size": 13,
                    "truncated": False,
                }
            }
        }
        result = await client.get_response_body("s1", 0)
        assert result is not None
        assert result["body"] == b'{"ok": true}'
        assert result["content_type"] == "application/json"

    @pytest.mark.asyncio
    async def test_get_captured_responses_basic(self):
        """get_captured_responses returns summaries without body bytes."""
        client = BrowserClient()
        client._request_logs = {
            "s1": [
                {
                    "id": 0,
                    "url": "https://a.com/api",
                    "method": "GET",
                    "resource_type": "fetch",
                    "status": 200,
                    "has_body": True,
                },
                {
                    "id": 1,
                    "url": "https://a.com/style.css",
                    "method": "GET",
                    "resource_type": "stylesheet",
                    "status": 200,
                },
            ]
        }
        client._response_bodies = {
            "s1": {
                0: {
                    "body": b"{}",
                    "content_type": "application/json",
                    "size": 2,
                    "truncated": False,
                }
            }
        }
        results = await client.get_captured_responses("s1")
        assert len(results) == 1
        assert results[0]["id"] == 0
        assert "body" not in results[0]  # summary, no bytes

    @pytest.mark.asyncio
    async def test_get_captured_responses_filter_resource_type(self):
        """Filter by resource_type."""
        client = BrowserClient()
        client._request_logs = {
            "s1": [
                {
                    "id": 0,
                    "url": "https://a.com/a",
                    "method": "GET",
                    "resource_type": "fetch",
                    "status": 200,
                },
                {
                    "id": 1,
                    "url": "https://a.com/b",
                    "method": "GET",
                    "resource_type": "xhr",
                    "status": 200,
                },
            ]
        }
        client._response_bodies = {
            "s1": {
                0: {
                    "body": b"{}",
                    "content_type": "application/json",
                    "size": 2,
                    "truncated": False,
                },
                1: {
                    "body": b"{}",
                    "content_type": "application/json",
                    "size": 2,
                    "truncated": False,
                },
            }
        }
        results = await client.get_captured_responses("s1", resource_type="xhr")
        assert len(results) == 1
        assert results[0]["resource_type"] == "xhr"

    @pytest.mark.asyncio
    async def test_get_captured_responses_filter_content_type(self):
        """Filter by content_type substring."""
        client = BrowserClient()
        client._request_logs = {
            "s1": [
                {
                    "id": 0,
                    "url": "https://a.com/a",
                    "method": "GET",
                    "resource_type": "fetch",
                    "status": 200,
                },
                {
                    "id": 1,
                    "url": "https://a.com/b",
                    "method": "GET",
                    "resource_type": "fetch",
                    "status": 200,
                },
            ]
        }
        client._response_bodies = {
            "s1": {
                0: {
                    "body": b"{}",
                    "content_type": "application/json",
                    "size": 2,
                    "truncated": False,
                },
                1: {"body": b"<html>", "content_type": "text/html", "size": 6, "truncated": False},
            }
        }
        results = await client.get_captured_responses("s1", content_type="json")
        assert len(results) == 1
        assert "json" in results[0]["content_type"]


# ---------------------------------------------------------------------------
# Tests — Cleanup
# ---------------------------------------------------------------------------


class TestResponseCleanup:
    @pytest.mark.asyncio
    async def test_close_context_clears_bodies(self):
        """close_context removes response bodies for that context."""
        client, _page = _setup_client_with_context()
        client._response_bodies = {"s1": {0: {"body": b"data"}}}
        client._logging_config = {"s1": {"capture_bodies": True}}
        await client.close_context("s1")
        assert "s1" not in client._response_bodies
        assert "s1" not in client._logging_config

    @pytest.mark.asyncio
    async def test_close_clears_all_bodies(self):
        """close() clears all response bodies and logging config."""
        client = BrowserClient()
        client._response_bodies = {
            "s1": {0: {"body": b"a"}},
            "s2": {0: {"body": b"b"}},
        }
        client._logging_config = {"s1": {}, "s2": {}}
        await client.close()
        assert client._response_bodies == {}
        assert client._logging_config == {}


# ---------------------------------------------------------------------------
# Tests — Page replacement re-attachment
# ---------------------------------------------------------------------------


class TestLoggingHookReattachment:
    """Verify logging hooks survive page replacement in fetch()."""

    @pytest.mark.asyncio
    async def test_logging_config_stored(self):
        """enable_request_logging stores config for re-attachment."""
        client, _page = _setup_client_with_context()
        await client.enable_request_logging("s1", capture_bodies=True)
        assert hasattr(client, "_logging_config")
        assert "s1" in client._logging_config
        cfg = client._logging_config["s1"]
        assert cfg["capture_bodies"] is True
        assert "fetch" in cfg["resource_types"]

    @pytest.mark.asyncio
    async def test_fetch_reattaches_handlers(self):
        """fetch() re-attaches logging handlers when replacing a page."""
        client, page = _setup_client_with_context()

        # Enable logging — handlers attached to first page
        await client.enable_request_logging("s1", capture_bodies=True)
        assert page.on.call_count == 2  # request + response

        # Simulate fetch() creating a new page
        new_page = _mock_page("https://example.com/people")
        new_ctx = client._contexts["s1"]
        new_ctx.new_page = AsyncMock(return_value=new_page)

        # Mock goto response
        goto_response = AsyncMock()
        goto_response.status = 200
        goto_response.headers = {"content-type": "text/html"}
        new_page.goto = AsyncMock(return_value=goto_response)
        new_page.content = AsyncMock(return_value="<html></html>")
        new_page.title = AsyncMock(return_value="People")

        from kaos_web.models import WebRequest

        await client.fetch(WebRequest(url="https://example.com/people", extra={"context_id": "s1"}))

        # New page should have handlers re-attached
        assert new_page.on.call_count == 2  # request + response on new page

    @pytest.mark.asyncio
    async def test_logs_accumulate_across_navigations(self):
        """Logs from both pages accumulate in the same list."""
        client, page = _setup_client_with_context()
        await client.enable_request_logging("s1")

        # Simulate a request on the first page
        request_handler = page.on.call_args_list[0][0][1]
        mock_req = MagicMock()
        mock_req.url = "https://example.com/page1"
        mock_req.method = "GET"
        mock_req.resource_type = "document"
        mock_req.headers = {}
        mock_req.post_data = None
        mock_req.is_navigation_request = MagicMock(return_value=True)
        request_handler(mock_req)

        assert len(client._request_logs["s1"]) == 1

        # Simulate fetch() replacing the page
        new_page = _mock_page("https://example.com/page2")
        new_ctx = client._contexts["s1"]
        new_ctx.new_page = AsyncMock(return_value=new_page)
        goto_response = AsyncMock()
        goto_response.status = 200
        goto_response.headers = {"content-type": "text/html"}
        new_page.goto = AsyncMock(return_value=goto_response)
        new_page.content = AsyncMock(return_value="<html></html>")
        new_page.title = AsyncMock(return_value="Page 2")

        from kaos_web.models import WebRequest

        await client.fetch(WebRequest(url="https://example.com/page2", extra={"context_id": "s1"}))

        # Simulate a request on the new page
        new_request_handler = new_page.on.call_args_list[0][0][1]
        mock_req2 = MagicMock()
        mock_req2.url = "https://example.com/api/data"
        mock_req2.method = "GET"
        mock_req2.resource_type = "fetch"
        mock_req2.headers = {}
        mock_req2.post_data = None
        mock_req2.is_navigation_request = MagicMock(return_value=False)
        new_request_handler(mock_req2)

        # Both requests should be in the same log
        assert len(client._request_logs["s1"]) == 2
        assert client._request_logs["s1"][0]["url"] == "https://example.com/page1"
        assert client._request_logs["s1"][1]["url"] == "https://example.com/api/data"

    @pytest.mark.asyncio
    async def test_fetch_without_logging_does_not_attach(self):
        """fetch() does not attach handlers when logging was not enabled."""
        client, _page = _setup_client_with_context()

        # No enable_request_logging call
        new_page = _mock_page("https://example.com/page")
        new_ctx = client._contexts["s1"]
        new_ctx.new_page = AsyncMock(return_value=new_page)
        goto_response = AsyncMock()
        goto_response.status = 200
        goto_response.headers = {"content-type": "text/html"}
        new_page.goto = AsyncMock(return_value=goto_response)
        new_page.content = AsyncMock(return_value="<html></html>")
        new_page.title = AsyncMock(return_value="Page")

        from kaos_web.models import WebRequest

        await client.fetch(WebRequest(url="https://example.com/page", extra={"context_id": "s1"}))

        # No handlers should be attached
        new_page.on.assert_not_called()


# ---------------------------------------------------------------------------
# Tests — Updated MCP tools
# ---------------------------------------------------------------------------


class TestEnableRequestLoggingToolCapture:
    @pytest.mark.asyncio
    async def test_passes_capture_params(self):
        """EnableRequestLoggingTool threads capture params to client."""
        from kaos_web.browser_tools import EnableRequestLoggingTool

        tool = EnableRequestLoggingTool()
        with patch("kaos_web.browser_tools._get_browser_client") as mock_get:
            mock_client = AsyncMock()
            mock_get.return_value = mock_client

            result = await tool.execute(
                {
                    "context_id": "s1",
                    "capture_bodies": True,
                    "resource_types": "fetch,xhr,document",
                    "max_body_size": 500000,
                }
            )
            assert not result.isError

            mock_client.enable_request_logging.assert_awaited_once()
            call_kwargs = mock_client.enable_request_logging.call_args[1]
            assert call_kwargs["capture_bodies"] is True
            assert call_kwargs["resource_types"] == frozenset({"fetch", "xhr", "document"})
            assert call_kwargs["max_body_size"] == 500000

    @pytest.mark.asyncio
    async def test_success_message_mentions_capture(self):
        """Success message mentions body capture when enabled."""
        from kaos_web.browser_tools import EnableRequestLoggingTool

        tool = EnableRequestLoggingTool()
        with patch("kaos_web.browser_tools._get_browser_client") as mock_get:
            mock_client = AsyncMock()
            mock_get.return_value = mock_client

            result = await tool.execute(
                {
                    "context_id": "s1",
                    "capture_bodies": True,
                }
            )
            assert result.structuredContent is not None
            msg = result.structuredContent["message"]
            assert "Body capture active" in msg
            assert "captured-responses" in msg


class TestListRequestsToolHasBody:
    @pytest.mark.asyncio
    async def test_summary_includes_has_body(self):
        """ListRequestsTool summary includes has_body indicator."""
        from kaos_web.browser_tools import ListRequestsTool

        tool = ListRequestsTool()
        with patch("kaos_web.browser_tools._get_browser_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.get_request_log = AsyncMock(
                return_value=[
                    {
                        "id": 0,
                        "url": "https://api.example.com/data",
                        "method": "GET",
                        "resource_type": "fetch",
                        "status": 200,
                        "has_body": True,
                        "body_size": 1234,
                    },
                    {
                        "id": 1,
                        "url": "https://example.com/style.css",
                        "method": "GET",
                        "resource_type": "stylesheet",
                        "status": 200,
                    },
                ]
            )
            mock_get.return_value = mock_client

            result = await tool.execute({"context_id": "s1"})
            assert not result.isError
            assert result.structuredContent is not None
            output = result.structuredContent
            assert output["requests"][0]["has_body"] is True
            assert output["requests"][0]["body_size"] == 1234
            assert output["requests"][1]["has_body"] is False


class TestGetRequestDetailToolBody:
    @pytest.mark.asyncio
    async def test_includes_json_body_decoded(self):
        """GetRequestDetailTool decodes JSON body as UTF-8 string."""
        from kaos_web.browser_tools import GetRequestDetailTool

        tool = GetRequestDetailTool()
        with patch("kaos_web.browser_tools._get_browser_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.get_request_detail = AsyncMock(
                return_value={
                    "id": 0,
                    "url": "https://api.example.com/data",
                    "method": "GET",
                    "has_body": True,
                }
            )
            mock_client.get_response_body = AsyncMock(
                return_value={
                    "body": b'{"people": []}',
                    "content_type": "application/json",
                    "size": 15,
                    "truncated": False,
                }
            )
            mock_get.return_value = mock_client

            result = await tool.execute({"context_id": "s1", "request_id": 0})
            assert not result.isError
            assert result.structuredContent is not None
            output = result.structuredContent
            assert output["body"] == '{"people": []}'
            assert "body_encoding" not in output

    @pytest.mark.asyncio
    async def test_binary_body_base64_encoded(self):
        """Non-text body is returned as base64."""
        from kaos_web.browser_tools import GetRequestDetailTool

        tool = GetRequestDetailTool()
        with patch("kaos_web.browser_tools._get_browser_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.get_request_detail = AsyncMock(
                return_value={
                    "id": 0,
                    "url": "https://example.com/data.bin",
                    "method": "GET",
                    "has_body": True,
                }
            )
            mock_client.get_response_body = AsyncMock(
                return_value={
                    "body": b"\x89PNG\r\n\x1a\n",
                    "content_type": "image/png",
                    "size": 8,
                    "truncated": False,
                }
            )
            mock_get.return_value = mock_client

            result = await tool.execute({"context_id": "s1", "request_id": 0})
            assert not result.isError
            assert result.structuredContent is not None
            output = result.structuredContent
            assert output["body_encoding"] == "base64"

    @pytest.mark.asyncio
    async def test_include_body_false_omits_body(self):
        """include_body=False skips body retrieval."""
        from kaos_web.browser_tools import GetRequestDetailTool

        tool = GetRequestDetailTool()
        with patch("kaos_web.browser_tools._get_browser_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.get_request_detail = AsyncMock(
                return_value={
                    "id": 0,
                    "url": "https://api.example.com/data",
                    "method": "GET",
                    "has_body": True,
                }
            )
            mock_get.return_value = mock_client

            result = await tool.execute(
                {
                    "context_id": "s1",
                    "request_id": 0,
                    "include_body": False,
                }
            )
            assert not result.isError
            # get_response_body should not have been called
            mock_client.get_response_body.assert_not_called()


# ---------------------------------------------------------------------------
# Tests — ListCapturedResponsesTool
# ---------------------------------------------------------------------------


class TestListCapturedResponsesTool:
    def test_metadata_conventions(self):
        """Tool follows naming and annotation conventions."""
        from kaos_web.browser_tools import ListCapturedResponsesTool

        tool = ListCapturedResponsesTool()
        meta = tool.metadata
        assert meta.name == "kaos-web-browser-captured-responses"
        assert meta.annotations is not None
        assert meta.annotations.readOnlyHint is True

    @pytest.mark.asyncio
    async def test_no_captured_bodies_returns_message(self):
        """Empty capture list returns helpful message."""
        from kaos_web.browser_tools import ListCapturedResponsesTool

        tool = ListCapturedResponsesTool()
        with patch("kaos_web.browser_tools._get_browser_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.get_captured_responses = AsyncMock(return_value=[])
            mock_get.return_value = mock_client

            result = await tool.execute({"context_id": "s1"})
            assert not result.isError
            assert result.structuredContent is not None
            msg = result.structuredContent["message"]
            assert "No captured response bodies" in msg
            assert "capture_bodies=true" in msg

    @pytest.mark.asyncio
    async def test_returns_captured_response_summaries(self):
        """Returns list of captured response summaries."""
        from kaos_web.browser_tools import ListCapturedResponsesTool

        tool = ListCapturedResponsesTool()
        with patch("kaos_web.browser_tools._get_browser_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.get_captured_responses = AsyncMock(
                return_value=[
                    {
                        "id": 0,
                        "url": "https://api.example.com/people",
                        "method": "GET",
                        "resource_type": "fetch",
                        "status": 200,
                        "content_type": "application/json",
                        "body_size": 5000,
                        "truncated": False,
                    }
                ]
            )
            mock_get.return_value = mock_client

            result = await tool.execute({"context_id": "s1"})
            assert not result.isError
            assert result.structuredContent is not None
            output = result.structuredContent
            assert output["total_captured"] == 1
            assert output["responses"][0]["url"] == "https://api.example.com/people"

    @pytest.mark.asyncio
    async def test_store_artifacts_without_context_is_noop(self):
        """store_artifacts=True without runtime context still works."""
        from kaos_web.browser_tools import ListCapturedResponsesTool

        tool = ListCapturedResponsesTool()
        with patch("kaos_web.browser_tools._get_browser_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.get_captured_responses = AsyncMock(
                return_value=[
                    {
                        "id": 0,
                        "url": "https://api.example.com/data",
                        "method": "GET",
                        "resource_type": "fetch",
                        "status": 200,
                        "content_type": "application/json",
                        "body_size": 100,
                        "truncated": False,
                    }
                ]
            )
            mock_get.return_value = mock_client

            # No context → no artifacts, but no error
            result = await tool.execute(
                {"context_id": "s1", "store_artifacts": True},
                context=None,
            )
            assert not result.isError
            assert result.structuredContent is not None
            output = result.structuredContent
            assert output["total_captured"] == 1
            assert "artifacts_created" not in output

    @pytest.mark.asyncio
    async def test_store_artifacts_creates_vfs_entries(self):
        """store_artifacts=True with runtime creates VFS artifacts."""
        from kaos_web.browser_tools import ListCapturedResponsesTool

        tool = ListCapturedResponsesTool()
        with patch("kaos_web.browser_tools._get_browser_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.get_captured_responses = AsyncMock(
                return_value=[
                    {
                        "id": 0,
                        "url": "https://api.example.com/v1/people",
                        "method": "GET",
                        "resource_type": "fetch",
                        "status": 200,
                        "content_type": "application/json",
                        "body_size": 50,
                        "truncated": False,
                    }
                ]
            )
            mock_client.get_response_body = AsyncMock(
                return_value={
                    "body": b'{"people": []}',
                    "content_type": "application/json",
                    "size": 15,
                    "truncated": False,
                }
            )
            mock_get.return_value = mock_client

            # Mock context and runtime
            mock_vfs_path = AsyncMock()
            mock_vfs_path.write_bytes = AsyncMock()

            mock_context = MagicMock()
            mock_context.session_id = "test-session"
            mock_context.get_vfs_path = MagicMock(return_value=mock_vfs_path)

            mock_manifest = MagicMock()
            mock_manifest.artifact_id = "art-123"
            mock_manifest.body_uri = "kaos://artifacts/art-123/body"

            mock_runtime = MagicMock()
            mock_runtime.artifacts.create_from_path = AsyncMock(return_value=mock_manifest)
            mock_context.runtime = mock_runtime

            result = await tool.execute(
                {"context_id": "s1", "store_artifacts": True},
                context=mock_context,
            )
            assert not result.isError

            # Verify VFS write
            mock_vfs_path.write_bytes.assert_awaited_once_with(b'{"people": []}')

            # Verify artifact creation
            mock_runtime.artifacts.create_from_path.assert_awaited_once()
            call_kwargs = mock_runtime.artifacts.create_from_path.call_args[1]
            assert call_kwargs["mime_type"] == "application/json"
            assert call_kwargs["session_id"] == "test-session"

            assert result.structuredContent is not None
            output = result.structuredContent
            assert output["artifacts_created"] == 1
            assert output["artifacts"][0]["artifact_id"] == "art-123"

    @pytest.mark.asyncio
    async def test_store_artifacts_skips_non_json(self):
        """store_artifacts only stores responses with JSON content type."""
        from kaos_web.browser_tools import ListCapturedResponsesTool

        tool = ListCapturedResponsesTool()
        with patch("kaos_web.browser_tools._get_browser_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.get_captured_responses = AsyncMock(
                return_value=[
                    {
                        "id": 0,
                        "url": "https://example.com/page",
                        "method": "GET",
                        "resource_type": "fetch",
                        "status": 200,
                        "content_type": "text/html",
                        "body_size": 100,
                        "truncated": False,
                    }
                ]
            )
            mock_get.return_value = mock_client

            mock_context = MagicMock()
            mock_context.session_id = "test-session"
            mock_context.runtime = MagicMock()

            result = await tool.execute(
                {"context_id": "s1", "store_artifacts": True},
                context=mock_context,
            )
            assert not result.isError
            # No artifacts created for non-JSON
            mock_context.runtime.artifacts.create_from_path.assert_not_called()


# ---------------------------------------------------------------------------
# Tests — Error-message 3-part contract (WEB2-004)
# ---------------------------------------------------------------------------


def _error_text(result) -> str:
    """Best-effort extract human-readable error text from a ToolResult."""
    # ToolResult.create_error stores the message in .content[0].text
    if result.content:
        for block in result.content:
            if hasattr(block, "text"):
                return block.text  # type: ignore[no-any-return]
    return ""


class TestGetRequestDetailErrorContract:
    """WEB2-004: GetRequestDetailTool's catch-all error must include
    what failed, how to recover, and an alternative tool."""

    @pytest.mark.asyncio
    async def test_error_includes_recovery_guidance(self):
        from kaos_web.browser_tools import GetRequestDetailTool

        tool = GetRequestDetailTool()
        with patch("kaos_web.browser_tools._get_browser_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.get_request_detail = AsyncMock(side_effect=RuntimeError("boom"))
            mock_get.return_value = mock_client

            result = await tool.execute({"context_id": "s1", "request_id": 0})
            assert result.isError
            text = _error_text(result)
            # What failed
            assert "Failed to get request" in text
            assert "boom" in text
            # How to recover (lists active contexts, re-enables logging)
            assert "kaos-web-browser-list-contexts" in text
            assert "kaos-web-browser-log-requests" in text
            # Alternative tool
            assert "kaos-web-browser-requests" in text


class TestListCapturedResponsesErrorContract:
    """WEB2-004: ListCapturedResponsesTool's catch-all error must include
    what failed, how to recover, and an alternative tool."""

    @pytest.mark.asyncio
    async def test_error_includes_recovery_guidance(self):
        from kaos_web.browser_tools import ListCapturedResponsesTool

        tool = ListCapturedResponsesTool()
        with patch("kaos_web.browser_tools._get_browser_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.get_captured_responses = AsyncMock(side_effect=RuntimeError("boom"))
            mock_get.return_value = mock_client

            result = await tool.execute({"context_id": "s1"})
            assert result.isError
            text = _error_text(result)
            assert "Failed to list captured responses" in text
            assert "boom" in text
            # Recovery: confirm context, re-enable capture
            assert "kaos-web-browser-list-contexts" in text
            assert "kaos-web-browser-log-requests" in text
            assert "capture_bodies" in text
            # Alternative
            assert "kaos-web-browser-requests" in text
