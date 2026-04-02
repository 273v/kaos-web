# kaos-web Development Notes

## Purpose

Web content extraction for KAOS. Fetches HTML from URLs via HTTP or headless browser and produces kaos-content `ContentDocument` AST with provenance. Dual-client architecture (httpx for fast HTTP, Playwright for JS-rendered pages).

## Architecture

```
URL -> Client (HTTP or Browser) -> Middleware (retry/rate/robots/cache) -> Raw HTML
    -> Readability (main content extraction)
    -> HTML-to-AST (lxml tree -> ContentDocument blocks/inlines)
    -> ContentDocument with provenance
    -> kaos_content.search (BM25 via kaos-nlp-core)
    -> Serializers (markdown, text)
```

Key modules:
- `clients/http.py` — httpx-based async client with connection pooling, auth, SSL, proxy, structured error mapping
- `clients/browser.py` — Playwright-based browser client with lazy launch, page tracking, interaction methods, context pooling
- `extract/readability.py` — Readability algorithm (Mozilla port) for main content extraction
- `extract/html_to_ast.py` — lxml HTML element tree to kaos-content Block/Inline AST conversion
- `extract/metadata.py` — JSON-LD, OpenGraph, and meta tag extraction
- `middleware/` — Composable chain: retry, rate_limit, robots, cache
- `tools.py` — 5 extraction MCP tools registered with KaosRuntime
- `browser_tools.py` — 18 browser interaction MCP tools (navigate, click, fill, type, press, select, screenshot, evaluate, snapshot, content, cookies, set-cookie, save-auth, log-requests, requests, get-request, list-contexts, close-context)
- `sitemap.py` — Sitemap parser (XML/text/gzip, index recursion, robots.txt discovery)
- `discovery.py` — URL discovery pipeline (sitemaps + page links, pattern filtering)
- `batch.py` — Concurrent URL fetching with asyncio.Semaphore
- `crawl.py` — BFS site crawl orchestrator with depth/page limits
- `crawl_tools.py` — 3 crawl MCP tools (discover-urls, batch-fetch, crawl-site)
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

Browser channel is **auto-detected** — no hardcoded config needed.

### Auto-detection logic (`_detect_browser_channel()`)
1. `KAOS_BROWSER_CHANNEL` env var (explicit override, e.g. `chrome`, `firefox`, `auto`)
2. Linux + system Chrome available → `chrome` (bundled Chromium fails on Ubuntu 24.04+)
3. Otherwise → `None` (use Playwright's bundled Chromium)

### Environment variables
| Variable | Default | Description |
|----------|---------|-------------|
| `KAOS_BROWSER_CHANNEL` | auto-detect | Browser channel: `chrome`, `firefox`, `webkit`, `auto` |
| `KAOS_BROWSER_HEADLESS` | `true` | Set `false` for visible browser |
| `KAOS_BROWSER_TYPE` | `chromium` | Playwright engine: `chromium`, `firefox`, `webkit` |

### Python API
```python
from kaos_web.browser_tools import configure_browser
from kaos_web.clients.config import BrowserClientConfig

# Override before first tool call
configure_browser(BrowserClientConfig(channel="firefox", headless=False))
```

### Direct client usage
```python
from kaos_web.clients.browser import BrowserClient
from kaos_web.clients.config import BrowserClientConfig

async with BrowserClient(BrowserClientConfig(channel="chrome")) as client:
    resp = await client.fetch(WebRequest(url="https://example.com"))
```

## Key Patterns

- **`model_construct` for AST nodes**: Bypass Pydantic validation for performance when building AST from trusted lxml data. Uses `uuid4()` for fast IDs.
- **Provenance on every node**: `SourceRef(source=url)` + `Provenance(source_ref=...)` attached to every block via `Attr`.
- **Provenance cache**: Single `SourceRef` and `Provenance` created per document, reused across all nodes to avoid allocation overhead.
- **Readability-first**: Raw HTML goes through readability extraction before AST conversion to strip navigation, ads, sidebars.
- **Lazy imports**: Heavy dependencies (playwright, kaos-content serializers) are imported inside handlers, not at module level, keeping `--help` fast.
- **Search lives in kaos-content**: `kaos_content.search.search_document()` is the canonical search. Never import search from kaos-pdf or duplicate it. All extraction modules share the same search.
- **Middleware wired in HttpClient**: `HttpClient.fetch()` routes through `MiddlewareChain` (retry → rate_limit → robots → cache → raw httpx). Config flags control which middleware are active. Unit tests use `_NO_MIDDLEWARE` config to avoid mock interference.
- **Browser context pooling**: Named contexts via `request.extra["context_id"]` persist pages and cookies across requests. Unnamed requests get isolated context-per-request. `close_context(id)` to clean up explicitly.
- **Browser page tracking**: Named contexts store active pages in `_pages` dict, enabling multi-step interaction (navigate → click → fill → screenshot). `_require_page()` raises agent-friendly errors with active context list.
- **Operation-aware errors**: `_raise_browser_error(exc, url, operation)` includes the operation type (click, fill, type, etc.) in timeout messages so agents can self-correct.
- **Browser config auto-detection**: Shared singleton auto-detects system Chrome on Linux. Configurable via env vars (`KAOS_BROWSER_CHANNEL`) or `configure_browser()` API.

## HTML-to-AST

The `html_to_document()` function walks an lxml element tree and produces `ContentDocument` with:
- Headings (h1-h6), Paragraphs, Lists (ordered/unordered), BlockQuote, CodeBlock, Table, Figure, DefinitionList, ThematicBreak
- Inline nodes: Text, Strong, Emphasis, Code, Link, Image, Strikethrough, Subscript, Superscript, LineBreak
- All nodes grounded to kaos-content model with provenance

## MCP Tools

### Extraction tools (5) — `tools.py`

All with `openWorldHint=True`, `readOnlyHint=True`, `idempotentHint=True`:

| Tool | Name | Purpose |
|------|------|---------|
| FetchPageTool | `kaos-web-fetch-page` | Fetch URL -> ContentDocument artifact with outline and sections |
| GetPageTextTool | `kaos-web-get-text` | Fetch URL -> plain text |
| GetPageMarkdownTool | `kaos-web-get-markdown` | Fetch URL -> markdown (context-free) |
| GetPageMetadataTool | `kaos-web-get-metadata` | Extract JSON-LD, OpenGraph, meta tags |
| SearchPageTool | `kaos-web-search-page` | Fetch URL -> BM25 search within content |

### Browser interaction tools (18) — `browser_tools.py`

Write tools use `readOnlyHint=False`, `destructiveHint=False`, `openWorldHint=True`.
Read tools use `readOnlyHint=True`, `openWorldHint=True`.

| Tool | Name | Purpose |
|------|------|---------|
| BrowserNavigateTool | `kaos-web-browser-navigate` | Navigate to URL, create persistent page for interaction |
| ClickElementTool | `kaos-web-browser-click` | Click element by CSS selector |
| FillInputTool | `kaos-web-browser-fill` | Fill input field (clears existing content first) |
| TypeTextTool | `kaos-web-browser-type` | Type character-by-character (autocomplete, JS listeners) |
| PressKeyTool | `kaos-web-browser-press` | Press keyboard key (Enter, Tab, Escape, modifiers) |
| SelectOptionTool | `kaos-web-browser-select` | Select dropdown option |
| ScreenshotTool | `kaos-web-browser-screenshot` | Take screenshot (context page or one-shot URL) |
| EvaluateJSTool | `kaos-web-browser-evaluate` | Execute JavaScript expression |
| GetSnapshotTool | `kaos-web-browser-snapshot` | Accessibility tree (find interactive elements) |
| GetPageContentTool | `kaos-web-browser-content` | Extract updated page content after interaction |
| GetCookiesTool | `kaos-web-browser-cookies` | List cookies for context |
| SetCookieTool | `kaos-web-browser-set-cookie` | Set a cookie (name, value, domain/url) |
| SaveAuthStateTool | `kaos-web-browser-save-auth` | Save context state to JSON file |
| EnableRequestLoggingTool | `kaos-web-browser-log-requests` | Start recording network requests |
| ListRequestsTool | `kaos-web-browser-requests` | List recorded network requests with filter |
| GetRequestDetailTool | `kaos-web-browser-get-request` | Full request/response detail by ID |
| ListContextsTool | `kaos-web-browser-list-contexts` | List active browser contexts |
| CloseContextTool | `kaos-web-browser-close-context` | Close context and free resources |

Browser tools use a shared `_browser_client` singleton. Named contexts (via `context_id`) keep pages alive for multi-step workflows. Use `kaos-web-browser-navigate` first, then interact.

### Multi-page tools (3) — `crawl_tools.py`

All with `openWorldHint=True`, `readOnlyHint=True`, `idempotentHint=True`:

| Tool | Name | Purpose |
|------|------|---------|
| DiscoverUrlsTool | `kaos-web-discover-urls` | Fast URL inventory (sitemaps + page links) |
| BatchFetchTool | `kaos-web-batch-fetch` | Concurrent multi-URL fetch with extraction |
| CrawlSiteTool | `kaos-web-crawl-site` | Full site crawl with sitemap-first BFS discovery |

Firecrawl-style Map/Crawl: `discover-urls` first (fast, returns URL list), then `batch-fetch` or `crawl-site` on a subset. The `sitemap` parameter (`include`/`skip`/`only`) controls whether sitemaps are used for URL discovery.

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
