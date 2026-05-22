"""Tests for web search backends — mocked HTTP responses."""

from __future__ import annotations

import json
from typing import ClassVar
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from kaos_web.search.backends import (
    SearchResult,
    _search_brave,
    _search_duckduckgo,
    _search_exa,
    _search_serpapi,
    search_web,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(data: dict | str, status: int = 200) -> httpx.Response:
    """Create a mock httpx.Response."""
    content = json.dumps(data).encode() if isinstance(data, dict) else data.encode()
    return httpx.Response(
        status_code=status,
        content=content,
        request=httpx.Request("GET", "https://mock"),
    )


# ---------------------------------------------------------------------------
# SerpAPI
# ---------------------------------------------------------------------------


class TestSerpAPI:
    MOCK_RESPONSE: ClassVar[dict] = {
        "search_metadata": {"id": "test", "status": "Success"},
        "search_information": {"total_results": 100},
        "organic_results": [
            {
                "position": 1,
                "title": "Python Guide",
                "link": "https://python.org",
                "snippet": "Welcome to Python",
            },
            {
                "position": 2,
                "title": "Learn Python",
                "link": "https://learn.python.org",
                "snippet": "Interactive tutorial",
            },
        ],
    }

    @pytest.mark.asyncio()
    async def test_basic_search(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SERPAPI_API_KEY", "test_key")

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=_mock_response(self.MOCK_RESPONSE))

        with patch("kaos_web.search.backends.httpx.AsyncClient", return_value=mock_client):
            results = await _search_serpapi("python", max_results=10)

        assert len(results) == 2
        assert results[0].title == "Python Guide"
        assert results[0].url == "https://python.org"
        assert results[0].source == "serpapi"

    @pytest.mark.asyncio()
    async def test_no_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
        with pytest.raises(ValueError, match="SERPAPI_API_KEY"):
            await _search_serpapi("test")

    @pytest.mark.asyncio()
    async def test_answer_box_prepended(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SERPAPI_API_KEY", "test_key")
        data = {
            **self.MOCK_RESPONSE,
            "answer_box": {
                "title": "Direct Answer",
                "link": "https://answer.com",
                "snippet": "The answer is 42",
            },
        }
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=_mock_response(data))

        with patch("kaos_web.search.backends.httpx.AsyncClient", return_value=mock_client):
            results = await _search_serpapi("test")

        assert results[0].source == "serpapi:answer_box"
        assert results[0].snippet == "The answer is 42"


# ---------------------------------------------------------------------------
# DuckDuckGo
# ---------------------------------------------------------------------------


class TestDuckDuckGo:
    MOCK_HTML: ClassVar[str] = """
    <html><body>
    <div class="result results_links results_links_deep web-result">
        <a class="result__a" href="https://example.com">Example Site</a>
        <a class="result__snippet">This is an example snippet.</a>
    </div>
    <div class="result results_links results_links_deep web-result">
        <a class="result__a" href="https://other.com">Other Site</a>
        <a class="result__snippet">Another snippet here.</a>
    </div>
    </body></html>
    """

    @pytest.mark.asyncio()
    async def test_basic_search(self) -> None:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=_mock_response(self.MOCK_HTML))

        with patch("kaos_web.search.backends.httpx.AsyncClient", return_value=mock_client):
            results = await _search_duckduckgo("test")

        assert len(results) == 2
        assert results[0].title == "Example Site"
        assert results[0].url == "https://example.com"
        assert results[0].source == "duckduckgo"


# ---------------------------------------------------------------------------
# Exa
# ---------------------------------------------------------------------------


class TestExa:
    MOCK_RESPONSE: ClassVar[dict] = {
        "requestId": "test",
        "results": [
            {
                "title": "AI Research Paper",
                "url": "https://arxiv.org/paper1",
                "highlights": ["Important finding about AI"],
            },
            {
                "title": "ML Tutorial",
                "url": "https://ml.org/tutorial",
                "highlights": ["Learn machine learning"],
            },
        ],
    }

    @pytest.mark.asyncio()
    async def test_basic_search(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EXA_API_KEY", "test_key")

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=_mock_response(self.MOCK_RESPONSE))

        with patch("kaos_web.search.backends.httpx.AsyncClient", return_value=mock_client):
            results = await _search_exa("AI research")

        assert len(results) == 2
        assert results[0].title == "AI Research Paper"
        assert results[0].snippet == "Important finding about AI"
        assert results[0].source == "exa"

    @pytest.mark.asyncio()
    async def test_no_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("EXA_API_KEY", raising=False)
        with pytest.raises(ValueError, match="EXA_API_KEY"):
            await _search_exa("test")


# ---------------------------------------------------------------------------
# Brave
# ---------------------------------------------------------------------------


class TestBrave:
    MOCK_RESPONSE: ClassVar[dict] = {
        "web": {
            "results": [
                {
                    "title": "Brave Result",
                    "url": "https://brave.com",
                    "description": "A brave snippet",
                },
            ],
        },
    }

    @pytest.mark.asyncio()
    async def test_basic_search(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BRAVE_API_KEY", "test_key")

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=_mock_response(self.MOCK_RESPONSE))

        with patch("kaos_web.search.backends.httpx.AsyncClient", return_value=mock_client):
            results = await _search_brave("test")

        assert len(results) == 1
        assert results[0].source == "brave"


# ---------------------------------------------------------------------------
# Dispatcher (search_web)
# ---------------------------------------------------------------------------


class TestSearchWebDispatcher:
    @pytest.mark.asyncio()
    async def test_empty_query_raises(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            await search_web("")

    @pytest.mark.asyncio()
    async def test_auto_detect_serpapi(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SERPAPI_API_KEY", "key")
        monkeypatch.delenv("EXA_API_KEY", raising=False)
        monkeypatch.delenv("BRAVE_API_KEY", raising=False)

        with patch("kaos_web.search.backends._search_serpapi", new_callable=AsyncMock) as mock:
            mock.return_value = [SearchResult(title="t", url="u", snippet="s")]
            results = await search_web("test")
            mock.assert_called_once()
            assert results[0].title == "t"

    @pytest.mark.asyncio()
    async def test_explicit_backend(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BRAVE_API_KEY", "key")

        with patch("kaos_web.search.backends._search_brave", new_callable=AsyncMock) as mock:
            mock.return_value = []
            await search_web("test", backend="brave")
            mock.assert_called_once()

    @pytest.mark.asyncio()
    async def test_fallback_to_duckduckgo(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
        monkeypatch.delenv("EXA_API_KEY", raising=False)
        monkeypatch.delenv("BRAVE_API_KEY", raising=False)
        monkeypatch.delenv("KAOS_SEARCH_BACKEND", raising=False)

        with patch("kaos_web.search.backends._search_duckduckgo", new_callable=AsyncMock) as mock:
            mock.return_value = []
            await search_web("test")
            mock.assert_called_once()

    @pytest.mark.asyncio()
    async def test_unknown_backend_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown search backend"):
            await search_web("test", backend="nonexistent")

    # 0.1.1 (#545) — the LLM literal "auto" is treated as auto-detect.
    @pytest.mark.asyncio()
    async def test_auto_string_falls_through_to_detect(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The string ``"auto"`` must be a synonym for omission.

        gpt-5.4-mini and Haiku 4.5 both observed passing the literal
        string ``"auto"`` because the public MCP tool description said
        "Default: auto-detect from env vars". On 0.1.0 the dispatcher
        rejected that with ``Unknown search backend: 'auto'`` even
        though every other surface accepted it. 0.1.1 normalizes the
        literal to the auto-detect path.
        """
        monkeypatch.setenv("SERPAPI_API_KEY", "key")
        monkeypatch.delenv("EXA_API_KEY", raising=False)
        monkeypatch.delenv("BRAVE_API_KEY", raising=False)

        with patch("kaos_web.search.backends._search_serpapi", new_callable=AsyncMock) as mock:
            mock.return_value = [SearchResult(title="t", url="u", snippet="s")]
            results = await search_web("test", backend="auto")
            mock.assert_called_once()
            assert results[0].title == "t"

    @pytest.mark.asyncio()
    async def test_auto_synonym_with_no_keys_uses_duckduckgo(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``"auto"`` with no env keys must hit the DDG fallback path."""
        monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
        monkeypatch.delenv("EXA_API_KEY", raising=False)
        monkeypatch.delenv("BRAVE_API_KEY", raising=False)
        monkeypatch.delenv("KAOS_SEARCH_BACKEND", raising=False)

        with patch("kaos_web.search.backends._search_duckduckgo", new_callable=AsyncMock) as mock:
            mock.return_value = []
            await search_web("test", backend="auto")
            mock.assert_called_once()

    @pytest.mark.asyncio()
    async def test_auto_uppercase_also_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Casing variants of "auto" must also fall through — the
        dispatcher lowercases the input before comparison."""
        monkeypatch.setenv("SERPAPI_API_KEY", "key")
        with patch("kaos_web.search.backends._search_serpapi", new_callable=AsyncMock) as mock:
            mock.return_value = []
            await search_web("test", backend="AUTO")
            mock.assert_called_once()
