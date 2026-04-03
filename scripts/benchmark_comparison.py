"""Head-to-head benchmark: kaos-web vs markdownify vs trafilatura.

Run with:
    uv run --with markdownify --with trafilatura python scripts/benchmark_comparison.py
"""

from __future__ import annotations

import time
from pathlib import Path


def make_html(n: int) -> str:
    """Generate HTML with N paragraphs."""
    paras = []
    for i in range(n):
        paras.append(
            f"<p>Paragraph {i} with some <strong>bold</strong> text, "
            f'a <a href="/page-{i}">link</a>, and <code>inline code</code>. '
            f"More content to make this a realistic paragraph.</p>"
        )
    sections = []
    for i in range(0, n, 10):
        sections.append(f"<h2>Section {i // 10 + 1}</h2>\n" + "\n".join(paras[i : i + 10]))
    body = "\n".join(sections)
    return (
        f"<html><head><title>Test</title></head><body>"
        f'<nav><a href="/">Home</a></nav>'
        f"<article><h1>Title</h1>{body}</article>"
        f"<footer>Copyright</footer></body></html>"
    )


def bench(
    name: str,
    fn: object,
    html: str,
    *,
    warmup: int = 3,
    duration: float = 3.0,
) -> None:
    """Benchmark a function and print results."""
    for _ in range(warmup):
        fn(html)  # type: ignore[operator]
    count = 0
    start = time.perf_counter()
    while time.perf_counter() - start < duration:
        fn(html)  # type: ignore[operator]
        count += 1
    elapsed = time.perf_counter() - start
    size_kb = len(html.encode("utf-8")) / 1024
    throughput = (size_kb * count) / elapsed
    latency_ms = (elapsed / count) * 1000
    print(
        f"  {name:30s} | {latency_ms:8.2f} ms"
        f" | {throughput:8.0f} KB/s | {count / elapsed:8.1f} docs/s"
    )


def main() -> None:
    fixtures = Path(__file__).parent.parent / "tests" / "fixtures"
    article_html = (fixtures / "article.html").read_text()
    small_html = make_html(10)
    medium_html = make_html(100)

    # kaos-web
    from kaos_content.serializers.markdown import serialize_markdown
    from kaos_web import html_to_document

    def kaos_web_fn(html: str) -> str:
        doc = html_to_document(html, url="https://example.com")
        return serialize_markdown(doc)

    # markdownify
    from markdownify import markdownify as md

    def markdownify_fn(html: str) -> str:
        return md(html)

    # trafilatura
    import trafilatura

    def trafilatura_fn(html: str) -> str | None:
        return trafilatura.extract(html, output_format="markdown", url="https://example.com")

    for label, html in [
        (f"ARTICLE (~{len(article_html) // 1024} KB)", article_html),
        (f"SMALL (~{len(small_html) // 1024} KB)", small_html),
        (f"MEDIUM (~{len(medium_html) // 1024} KB)", medium_html),
    ]:
        print(f"=== {label} ===")
        header = f"  {'Library':30s} | {'Latency':>8s} | {'Throughput':>8s} | {'Rate':>8s}"
        print(header)
        print("  " + "-" * 75)
        bench("kaos-web (AST + markdown)", kaos_web_fn, html)
        bench("markdownify", markdownify_fn, html)
        bench("trafilatura", trafilatura_fn, html)
        print()


if __name__ == "__main__":
    main()
