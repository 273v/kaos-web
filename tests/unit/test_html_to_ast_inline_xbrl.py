"""``html_to_document`` on Inline XBRL (iXBRL) filings.

iXBRL annual reports lay their text out as a flat ``<body>`` of sibling
paragraph ``<div>``s split into pages, with no article-level container.
Readability-style extraction scores containers, so it can only select a
table or a handful of page blocks and silently drops the rest of the
report. An iXBRL document carries no site chrome, so ``html_to_document``
converts the whole (cleaned) body instead.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kaos_content.model.blocks import Table
from kaos_content.parsers.html import parse_html
from kaos_content.serializers.text import serialize_text
from kaos_web.extract import html_to_document

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture(scope="module")
def filing() -> str:
    return (FIXTURES / "ixbrl_annual_report.htm").read_text(encoding="utf-8")


class TestInlineXbrlWholeDocument:
    def test_risk_factors_survive_extraction(self, filing: str) -> None:
        text = serialize_text(html_to_document(filing))
        assert "Item 1A." in text
        assert "Risk Factors" in text
        assert "A disruption at a single-source supplier" in text
        assert "Item 1." in text
        assert "Example Widget Corporation designs, manufactures and sells" in text
        assert "Inventories are stated at the lower of cost" in text

    def test_tables_and_numeric_facts_are_kept(self, filing: str) -> None:
        doc = html_to_document(filing)
        assert any(isinstance(b, Table) for b in doc.body)
        assert "Net sales for the year were $1,234 million" in serialize_text(doc)

    def test_hidden_facts_do_not_leak(self, filing: str) -> None:
        text = serialize_text(html_to_document(filing))
        for hidden in ("0009999999", "HIDDENFACTFY", "2025-01-01", "iso4217:USD"):
            assert hidden not in text

    def test_matches_raw_conversion(self, filing: str) -> None:
        extracted = serialize_text(html_to_document(filing, url="https://example.com/f.htm"))
        raw = serialize_text(parse_html(filing, url="https://example.com/f.htm"))
        assert extracted == raw

    @pytest.mark.parametrize("scope", [0.0, 0.5, 1.0])
    def test_every_content_scope_keeps_the_body(self, filing: str, scope: float) -> None:
        text = serialize_text(html_to_document(filing, content_scope=scope))
        assert "A disruption at a single-source supplier" in text

    def test_metadata_and_provenance(self, filing: str) -> None:
        doc = html_to_document(filing, url="https://example.com/f.htm")
        assert doc.metadata.title == "exco-20251231"
        assert doc.metadata.source is not None
        assert doc.metadata.source.uri == "https://example.com/f.htm"
        prov = doc.body[0].provenance
        assert prov is not None
        assert prov.extractor == "kaos-web"

    def test_forced_strip_on_fragment(self) -> None:
        html = '<div><p>Rev <ix:nonFraction name="a">5</ix:nonFraction>.</p></div>'
        text = serialize_text(html_to_document(html, strip_xbrl=True))
        assert "Rev 5." in text


class TestOrdinaryPagesStillUseExtraction:
    """Pages that merely look XBRL-ish keep readability extraction."""

    @pytest.mark.parametrize(
        "title",
        ["Linux: a field guide", "Fix: release notes", "What is XBRL? An inlineXBRL primer"],
    )
    def test_lookalike_text_does_not_bypass_extraction(self, title: str) -> None:
        html = (FIXTURES / "article.html").read_text()
        baseline = serialize_text(html_to_document(html))
        retitled = html.replace(
            "<title>Test Article — Example Site</title>", f"<title>{title}</title>"
        )
        assert retitled != html
        assert serialize_text(html_to_document(retitled)) == baseline
        assert "Related Posts" not in baseline
