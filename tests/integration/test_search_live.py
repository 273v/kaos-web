"""Live integration tests for web search backends.

Run with: pytest tests/integration/test_search_live.py -v -m live

Each test hits the real search API. API keys are expected in environment variables:
- SERPAPI_API_KEY (or KAOS_WEB_SERPAPI_API_KEY)
- BRAVE_API_KEY (or KAOS_WEB_BRAVE_API_KEY)
- EXA_API_KEY (or KAOS_WEB_EXA_API_KEY)
- DuckDuckGo requires no API key.

Tests skip gracefully when the required API key is missing.
"""

from __future__ import annotations

import os

import pytest

from kaos_web.search import SearchResult, search_web

pytestmark = [pytest.mark.integration, pytest.mark.live]

# ── Helpers ──────────────────────────────────────────────────────────────────

QUERY = "Python programming language"


def _has_key(env_names: list[str]) -> bool:
    """Return True if any of the given env var names is set and non-empty."""
    return any(os.environ.get(name) for name in env_names)


def _assert_results_valid(results: list[SearchResult], *, source: str) -> None:
    """Shared assertions for any backend's results."""
    assert len(results) > 0, f"{source}: expected at least one result"

    for r in results:
        assert r.title, f"{source}: result missing title: {r}"
        assert r.url, f"{source}: result missing url: {r}"
        assert r.url.startswith("http"), f"{source}: url not HTTP(S): {r.url}"

    # At least one result should mention "python" in title or snippet.
    relevant = [r for r in results if "python" in r.title.lower() or "python" in r.snippet.lower()]
    assert len(relevant) > 0, (
        f"{source}: no result mentions 'python' in title or snippet. "
        f"Top titles: {[r.title for r in results[:5]]}"
    )


# ── SerpAPI ──────────────────────────────────────────────────────────────────


@pytest.mark.live
class TestSerpAPILive:
    """SerpAPI (Google results) — requires SERPAPI_API_KEY."""

    @pytest.fixture(autouse=True)
    def _require_key(self) -> None:
        if not _has_key(["KAOS_WEB_SERPAPI_API_KEY", "SERPAPI_API_KEY"]):
            pytest.skip("SERPAPI_API_KEY not set — skipping SerpAPI live tests")

    async def test_basic_search(self) -> None:
        """SerpAPI returns relevant organic results for a simple query."""
        results = await search_web(QUERY, backend="serpapi", max_results=10)
        _assert_results_valid(results, source="serpapi")

    async def test_result_fields(self) -> None:
        """Each SerpAPI result carries the expected source tag and position."""
        results = await search_web(QUERY, backend="serpapi", max_results=5)
        assert len(results) > 0
        for r in results:
            # Answer-box results have source "serpapi:answer_box"; organic have "serpapi"
            assert r.source.startswith("serpapi"), f"unexpected source: {r.source}"

    async def test_max_results_respected(self) -> None:
        """SerpAPI honours the max_results cap."""
        results = await search_web(QUERY, backend="serpapi", max_results=3)
        assert len(results) <= 3


# ── Brave ────────────────────────────────────────────────────────────────────


@pytest.mark.live
class TestBraveLive:
    """Brave Search — requires BRAVE_API_KEY."""

    @pytest.fixture(autouse=True)
    def _require_key(self) -> None:
        if not _has_key(["KAOS_WEB_BRAVE_API_KEY", "BRAVE_API_KEY"]):
            pytest.skip("BRAVE_API_KEY not set — skipping Brave live tests")

    async def test_basic_search(self) -> None:
        """Brave returns relevant results for a simple query."""
        results = await search_web(QUERY, backend="brave", max_results=10)
        _assert_results_valid(results, source="brave")

    async def test_result_fields(self) -> None:
        """Each Brave result carries the expected source tag."""
        results = await search_web(QUERY, backend="brave", max_results=5)
        assert len(results) > 0
        for r in results:
            assert r.source == "brave"

    async def test_max_results_respected(self) -> None:
        """Brave honours the max_results cap."""
        results = await search_web(QUERY, backend="brave", max_results=3)
        assert len(results) <= 3


# ── Exa ──────────────────────────────────────────────────────────────────────


@pytest.mark.live
class TestExaLive:
    """Exa neural/semantic search — requires EXA_API_KEY."""

    @pytest.fixture(autouse=True)
    def _require_key(self) -> None:
        if not _has_key(["KAOS_WEB_EXA_API_KEY", "EXA_API_KEY"]):
            pytest.skip("EXA_API_KEY not set — skipping Exa live tests")

    async def test_basic_search(self) -> None:
        """Exa returns relevant results for a simple query."""
        results = await search_web(QUERY, backend="exa", max_results=10)
        _assert_results_valid(results, source="exa")

    async def test_result_fields(self) -> None:
        """Each Exa result carries the expected source tag."""
        results = await search_web(QUERY, backend="exa", max_results=5)
        assert len(results) > 0
        for r in results:
            assert r.source == "exa"

    async def test_max_results_respected(self) -> None:
        """Exa honours the max_results cap."""
        results = await search_web(QUERY, backend="exa", max_results=3)
        assert len(results) <= 3


# ── DuckDuckGo ───────────────────────────────────────────────────────────────


@pytest.mark.live
class TestDuckDuckGoLive:
    """DuckDuckGo HTML scraping — no API key needed."""

    async def test_basic_search(self) -> None:
        """DuckDuckGo returns relevant results for a simple query."""
        results = await search_web(QUERY, backend="duckduckgo", max_results=10)
        _assert_results_valid(results, source="duckduckgo")

    async def test_result_fields(self) -> None:
        """Each DDG result carries the expected source tag."""
        results = await search_web(QUERY, backend="duckduckgo", max_results=5)
        assert len(results) > 0
        for r in results:
            assert r.source == "duckduckgo"

    async def test_max_results_respected(self) -> None:
        """DuckDuckGo honours the max_results cap."""
        results = await search_web(QUERY, backend="duckduckgo", max_results=3)
        assert len(results) <= 3
