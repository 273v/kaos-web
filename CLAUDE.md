# kaos-web Development Notes

## Purpose

Web content extraction for KAOS. Fetches HTML from URLs via HTTP or headless browser and produces kaos-content `ContentDocument` AST with provenance. Dual-client architecture (httpx for fast HTTP, Playwright for JS-rendered pages).

## Architecture

```
URL -> Client (HTTP or Browser) -> Raw HTML
    -> Readability (main content extraction)
    -> HTML-to-AST (lxml tree -> ContentDocument blocks/inlines)
    -> ContentDocument with provenance
    -> Serializers (markdown, text, search)
```

Key modules:
- `clients/http.py` — httpx-based async client with connection pooling, auth, SSL, proxy, structured error mapping
- `clients/browser.py` — Playwright-based browser client with lazy launch, context-per-request isolation, resource blocking
- `extract/readability.py` — Readability algorithm (Mozilla port) for main content extraction
- `extract/html_to_ast.py` — lxml HTML element tree to kaos-content Block/Inline AST conversion
- `extract/metadata.py` — JSON-LD, OpenGraph, and meta tag extraction
- `middleware/` — Composable chain: retry, rate_limit, robots, cache
- `tools.py` — 5 MCP tools registered with KaosRuntime
- `cli.py` — CLI with fetch, search, metadata commands

## Dependencies

- **httpx[http2]** — Async HTTP client with HTTP/2, connection pooling
- **lxml** — Fast HTML parsing and tree walking
- **playwright** (optional `[browser]` extra) — Headless browser rendering
- **kaos-content** — Document AST model (Block/Inline/Provenance)
- **kaos-core** — Runtime, tool framework, artifact helpers
- **kaos-mcp** (optional `[mcp]` extra) — MCP server bridge
- **kaos-nlp-core** (optional `[nlp]` extra) — BM25 search within extracted content

## Browser Setup

Playwright's bundled Chromium does **not** work on Ubuntu 26.04. You **must** use system Chrome via the `channel` parameter:

```python
from kaos_web.clients.browser import BrowserClient
from kaos_web.clients.config import BrowserClientConfig

config = BrowserClientConfig(channel="chrome")  # Uses /usr/bin/google-chrome
async with BrowserClient(config) as client:
    resp = await client.fetch(WebRequest(url="https://example.com"))
```

System Chrome must be installed at `/usr/bin/google-chrome`. Without `channel="chrome"`, Playwright will attempt to use its bundled browser and fail.

## Key Patterns

- **`model_construct` for AST nodes**: Bypass Pydantic validation for performance when building AST from trusted lxml data. Uses `uuid4()` for fast IDs.
- **Provenance on every node**: `SourceRef(source=url)` + `Provenance(source_ref=...)` attached to every block via `Attr`.
- **Provenance cache**: Single `SourceRef` and `Provenance` created per document, reused across all nodes to avoid allocation overhead.
- **Readability-first**: Raw HTML goes through readability extraction before AST conversion to strip navigation, ads, sidebars.
- **Lazy imports**: Heavy dependencies (playwright, kaos-content serializers) are imported inside handlers, not at module level, keeping `--help` fast.

## HTML-to-AST

The `html_to_document()` function walks an lxml element tree and produces `ContentDocument` with:
- Headings (h1-h6), Paragraphs, Lists (ordered/unordered), BlockQuote, CodeBlock, Table, Figure, DefinitionList, ThematicBreak
- Inline nodes: Text, Strong, Emphasis, Code, Link, Image, Strikethrough, Subscript, Superscript, LineBreak
- All nodes grounded to kaos-content model with provenance

## MCP Tools

5 tools, all with `openWorldHint=True` (make HTTP requests), `readOnlyHint=True`, `idempotentHint=True`:

| Tool | Name | Purpose |
|------|------|---------|
| FetchPageTool | `kaos-web-fetch-page` | Fetch URL -> ContentDocument artifact with outline and sections |
| GetPageTextTool | `kaos-web-get-text` | Fetch URL -> plain text |
| GetPageMarkdownTool | `kaos-web-get-markdown` | Fetch URL -> markdown (context-free) |
| GetPageMetadataTool | `kaos-web-get-metadata` | Extract JSON-LD, OpenGraph, meta tags |
| SearchPageTool | `kaos-web-search-page` | Fetch URL -> BM25 search within content |

## Middleware

Composable middleware chain with protocol-based design:

```python
chain = MiddlewareChain(client.fetch)
    .add(RetryMiddleware(RetryConfig(max_retries=3)))
    .add(RateLimitMiddleware(RateLimitConfig(requests_per_second=10)))
    .add(RobotsMiddleware(RobotsConfig(user_agent="KAOS-Web")))
    .add(CacheMiddleware(CacheConfig(default_ttl=300)))
```

Execution order: first added = outermost (retry wraps rate_limit wraps robots wraps cache wraps handler).

- **RetryMiddleware**: Exponential backoff with jitter, respects Retry-After header
- **RateLimitMiddleware**: Per-domain token bucket algorithm
- **RobotsMiddleware**: stdlib `robotparser`, cached per domain
- **CacheMiddleware**: In-memory LRU cache, RFC 7231 compliant (Cache-Control: no-store, no-cache, max-age)

## Performance

HTML extraction benchmarks (from `tests/unit/test_benchmarks.py`):
- Small articles (~1 KB HTML): ~1ms
- Medium pages (~10 KB HTML): ~6ms
- Large pages (~100 KB HTML): ~15ms
- 1.7-2.2x faster than trafilatura for equivalent extraction quality

## QA Process

```bash
ruff format kaos_web/ tests/
ruff check --fix kaos_web/ tests/
ty check kaos_web/ tests/
pytest tests/ -v
```

Integration tests (require network): `pytest tests/integration/ -v`
Skip in CI: `pytest -m "not integration"`

## Rules

- **Never add AGPL/GPL dependencies.** This is a proprietary codebase.
- Follow kaos-core `KaosTool` ABC for all MCP tools. Set `ToolAnnotations` on every tool.
- Tool error messages must include recovery guidance (what went wrong + how to fix + alternative).
- Use `async with` for both `HttpClient` and `BrowserClient` (context manager pattern).
- 1-based page numbers in CLI, 0-based internally.
- Errors to stderr with non-zero exit, output to stdout.
