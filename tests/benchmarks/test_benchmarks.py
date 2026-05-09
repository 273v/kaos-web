"""Performance benchmarks for HTML extraction pipeline.

Run with: uv run pytest tests/benchmarks/ -v --benchmark-only
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kaos_web.extract.html_to_ast import html_to_document
from kaos_web.extract.metadata import extract_metadata
from kaos_web.extract.readability import extract_content

pytestmark = pytest.mark.benchmark

FIXTURES = Path(__file__).parent.parent / "fixtures"
ARTICLE_HTML = (FIXTURES / "article.html").read_text()


def _make_large_html(n_paragraphs: int) -> str:
    """Generate HTML with N paragraphs of content."""
    paras = []
    for i in range(n_paragraphs):
        paras.append(
            f"<p>Paragraph {i} with some <strong>bold</strong> text, "
            f'a <a href="/page-{i}">link</a>, and <code>inline code</code>. '
            f"More content to make this a realistic paragraph with enough text "
            f"to properly exercise the extraction pipeline.</p>"
        )
    sections = []
    for i in range(0, n_paragraphs, 10):
        section_paras = "\n".join(paras[i : i + 10])
        sections.append(f"<h2>Section {i // 10 + 1}</h2>\n{section_paras}")

    body = "\n".join(sections)
    return f"""<!DOCTYPE html>
<html lang="en">
<head><title>Test Document with {n_paragraphs} paragraphs</title></head>
<body>
<nav><a href="/">Home</a><a href="/about">About</a></nav>
<article>
<h1>Large Test Document</h1>
{body}
</article>
<footer>Copyright 2026</footer>
</body>
</html>"""


SMALL_HTML = _make_large_html(10)  # ~2 KB
MEDIUM_HTML = _make_large_html(100)  # ~20 KB
LARGE_HTML = _make_large_html(1000)  # ~200 KB


# ─── Readability benchmarks ─────────────────────────────────────────────────


class TestReadabilityBenchmarks:
    def test_bench_readability_article(self, benchmark):
        benchmark(extract_content, ARTICLE_HTML)

    def test_bench_readability_small(self, benchmark):
        benchmark(extract_content, SMALL_HTML)

    def test_bench_readability_medium(self, benchmark):
        benchmark(extract_content, MEDIUM_HTML)

    def test_bench_readability_large(self, benchmark):
        benchmark(extract_content, LARGE_HTML)


# ─── HTML-to-AST benchmarks ─────────────────────────────────────────────────


class TestHtmlToAstBenchmarks:
    def test_bench_ast_article(self, benchmark):
        benchmark(html_to_document, ARTICLE_HTML, url="https://example.com")

    def test_bench_ast_article_no_readability(self, benchmark):
        benchmark(
            html_to_document,
            ARTICLE_HTML,
            url="https://example.com",
            extract_content=False,
        )

    def test_bench_ast_small(self, benchmark):
        benchmark(html_to_document, SMALL_HTML, url="https://example.com")

    def test_bench_ast_medium(self, benchmark):
        benchmark(html_to_document, MEDIUM_HTML, url="https://example.com")

    def test_bench_ast_large(self, benchmark):
        benchmark(html_to_document, LARGE_HTML, url="https://example.com")


# ─── Metadata benchmarks ────────────────────────────────────────────────────


class TestMetadataBenchmarks:
    def test_bench_metadata_article(self, benchmark):
        benchmark(extract_metadata, ARTICLE_HTML)

    def test_bench_metadata_large(self, benchmark):
        benchmark(extract_metadata, LARGE_HTML)


# ─── Full pipeline benchmarks ───────────────────────────────────────────────


class TestFullPipelineBenchmarks:
    def test_bench_full_article(self, benchmark):
        """Full pipeline: readability + AST + markdown serialization."""
        from kaos_content.serializers.markdown import serialize_markdown

        def _pipeline():
            doc = html_to_document(ARTICLE_HTML, url="https://example.com")
            return serialize_markdown(doc)

        benchmark(_pipeline)

    def test_bench_full_medium(self, benchmark):
        from kaos_content.serializers.markdown import serialize_markdown

        def _pipeline():
            doc = html_to_document(MEDIUM_HTML, url="https://example.com")
            return serialize_markdown(doc)

        benchmark(_pipeline)

    def test_bench_full_large(self, benchmark):
        from kaos_content.serializers.markdown import serialize_markdown

        def _pipeline():
            doc = html_to_document(LARGE_HTML, url="https://example.com")
            return serialize_markdown(doc)

        benchmark(_pipeline)


# ─── Throughput calculation ──────────────────────────────────────────────────


class TestThroughput:
    def test_throughput_report(self):
        """Report throughput in KB/s for different HTML sizes."""
        import time

        sizes = [
            ("small (~2KB)", SMALL_HTML),
            ("medium (~20KB)", MEDIUM_HTML),
            ("large (~200KB)", LARGE_HTML),
        ]

        for name, html in sizes:
            html_bytes = len(html.encode("utf-8"))
            start = time.perf_counter()
            iterations = 0
            while time.perf_counter() - start < 1.0:
                html_to_document(html, url="https://example.com")
                iterations += 1
            elapsed = time.perf_counter() - start
            throughput_kbs = (html_bytes * iterations) / (elapsed * 1024)
            docs_per_sec = iterations / elapsed
            print(
                f"  {name}: {throughput_kbs:.0f} KB/s, "
                f"{docs_per_sec:.1f} docs/s ({iterations} iterations)"
            )
