"""Live E2E tests for organization entity extraction.

Tests the kaos-web-extract-org tool against real websites.

Run with: pytest tests/integration/test_entity_live.py -v
"""

from __future__ import annotations

import pytest

from kaos_core import KaosRuntime

pytestmark = pytest.mark.integration


# ── Helpers ─────────────────────────────────────────────────────────


def _build_tools() -> dict:
    from kaos_web.domain_tools import register_domain_tools

    runtime = KaosRuntime()
    register_domain_tools(runtime)
    return {tool.metadata.name: tool for tool in runtime.tools.list_tool_objects()}


TOOLS = _build_tools()


# ── Org extraction tests ───────────────────────────────────────────


@pytest.mark.asyncio
class TestExtractOrgLive:
    async def test_273v_org(self) -> None:
        """273ventures.com should extract structured org data from JSON-LD."""
        tool = TOOLS["kaos-web-extract-org"]
        result = await tool.execute({"url": "https://273ventures.com"})
        assert not result.isError
        data = result.require_structured()

        # Name should be extracted (from JSON-LD or title)
        assert data.get("name") is not None
        assert "273" in data["name"] or "Ventures" in data["name"]

        # Should detect LLC from footer text
        if data.get("entity_form"):
            assert data["entity_form"] == "LLC"

        # Should have social links from JSON-LD sameAs
        social = data.get("social_links", {})
        assert "linkedin" in social or "github" in social

        # Should have sources tracking
        assert len(data.get("sources", [])) >= 1

    async def test_google_org(self) -> None:
        """Google homepage — less structured but should get name."""
        tool = TOOLS["kaos-web-extract-org"]
        result = await tool.execute({"url": "https://google.com"})
        assert not result.isError
        data = result.require_structured()
        assert data.get("name") is not None

    async def test_invalid_url_error(self) -> None:
        tool = TOOLS["kaos-web-extract-org"]
        result = await tool.execute({"url": "https://thisdoesnotexist12345.com"})
        assert result.isError

    async def test_tool_count_updated(self) -> None:
        """Domain tools should now include the org extraction tool."""
        assert "kaos-web-extract-org" in TOOLS
        assert len(TOOLS) == 11  # 10 original + 1 new
