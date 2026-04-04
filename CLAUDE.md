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

## Configuration — KaosWebSettings

All config is centralised in `kaos_web.settings.KaosWebSettings` (a `ModuleSettings` subclass).

### Environment variables
| Variable | Legacy alias | Default | Description |
|----------|-------------|---------|-------------|
| `KAOS_WEB_BROWSER_TYPE` | `KAOS_BROWSER_TYPE` | `chromium` | Playwright engine: `chromium`, `firefox`, `webkit` |
| `KAOS_WEB_BROWSER_HEADLESS` | `KAOS_BROWSER_HEADLESS` | `true` | Set `false` for visible browser |
| `KAOS_WEB_BROWSER_CHANNEL` | `KAOS_BROWSER_CHANNEL` | auto-detect | Browser channel: `chrome`, `firefox`, `webkit`, `auto` |
| `KAOS_WEB_SEARCH_BACKEND` | `KAOS_SEARCH_BACKEND` | auto-detect | Search backend: `serpapi`, `duckduckgo`, `exa`, `brave` |
| `KAOS_WEB_SERPAPI_API_KEY` | `SERPAPI_API_KEY` | — | SerpAPI API key (SecretStr) |
| `KAOS_WEB_EXA_API_KEY` | `EXA_API_KEY` | — | Exa API key (SecretStr) |
| `KAOS_WEB_BRAVE_API_KEY` | `BRAVE_API_KEY` | — | Brave Search API key (SecretStr) |

New `KAOS_WEB_*` prefix takes priority. Legacy env vars are supported for backward compatibility.

### Search backend tuning
| Variable | Default | Description |
|----------|---------|-------------|
| `KAOS_WEB_SEARCH_TIMEOUT` | `30.0` | Timeout (s) for search backend API calls |
| `KAOS_WEB_SEARCH_DDG_TIMEOUT` | `15.0` | Timeout (s) for DuckDuckGo HTML scraping |
| `KAOS_WEB_SEARCH_DDG_USER_AGENT` | Chrome UA | User-Agent for DDG scraping |

### Discovery / sitemap / crawl
| Variable | Default | Description |
|----------|---------|-------------|
| `KAOS_WEB_DISCOVERY_ROBOTS_TIMEOUT` | `10.0` | Timeout for robots.txt during discovery |
| `KAOS_WEB_DISCOVERY_PAGE_TIMEOUT` | `15.0` | Timeout for start page link extraction |
| `KAOS_WEB_SITEMAP_MAX_DEPTH` | `3` | Max recursion for sitemap indexes |
| `KAOS_WEB_SITEMAP_FETCH_TIMEOUT` | `15.0` | Timeout per sitemap fetch |
| `KAOS_WEB_SITEMAP_ROBOTS_TIMEOUT` | `10.0` | Timeout for robots.txt in sitemap discovery |
| `KAOS_WEB_SITEMAP_FALLBACK_TIMEOUT` | `10.0` | Timeout for well-known sitemap probes |
| `KAOS_WEB_CRAWL_MAX_DEPTH` | `2` | Default max crawl depth |
| `KAOS_WEB_CRAWL_MAX_PAGES` | `50` | Default max pages per crawl |
| `KAOS_WEB_CRAWL_CONCURRENCY` | `5` | Concurrent requests during crawl |
| `KAOS_WEB_CRAWL_PAGE_TIMEOUT` | `30.0` | Timeout per page during crawl |
| `KAOS_WEB_CRAWL_ENABLE_CACHE` | `true` | HTTP cache during crawl |
| `KAOS_WEB_CRAWL_OVER_DISCOVER_FACTOR` | `3` | Over-discover multiplier |

### Middleware defaults
| Variable | Default | Description |
|----------|---------|-------------|
| `KAOS_WEB_MIDDLEWARE_RETRY_MAX_RETRIES` | `3` | Max retry attempts |
| `KAOS_WEB_MIDDLEWARE_RETRY_INITIAL_DELAY` | `1.0` | Initial backoff delay (s) |
| `KAOS_WEB_MIDDLEWARE_RETRY_MAX_DELAY` | `60.0` | Max backoff delay (s) |
| `KAOS_WEB_MIDDLEWARE_RATE_LIMIT_RPS` | `10.0` | Requests per second |
| `KAOS_WEB_MIDDLEWARE_ROBOTS_USER_AGENT` | `KAOS-Web` | User agent for robots.txt |
| `KAOS_WEB_MIDDLEWARE_ROBOTS_CACHE_TTL` | `3600` | Robots.txt cache TTL (s) |
| `KAOS_WEB_MIDDLEWARE_ROBOTS_FETCH_TIMEOUT` | `10.0` | Timeout for robots.txt fetch |

KaosWebSettings provides `to_retry_config()`, `to_rate_limit_config()`, `to_robots_config()` builder methods for middleware config objects.

### Auto-detection logic
Browser channel: `_detect_browser_channel()` in `kaos_web.settings` auto-detects system Chrome on Linux. Search backend: auto-detects from configured API keys (serpapi → exa → brave → duckduckgo).

### Python API
```python
from kaos_web.settings import KaosWebSettings

settings = KaosWebSettings()              # from env + defaults
config = settings.to_browser_config()     # -> BrowserClientConfig

# Override before first tool call
from kaos_web.browser_tools import configure_browser
configure_browser(config)
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
- **Readability-first with semantic fallback**: Raw HTML goes through readability extraction before AST conversion. When readability returns < 50 words but the page has content, falls back to `<main>` → `<article>` parent → `[role=main]` → `<body>`.
- **Noise filtering**: `_SKIP_CLASSES` filters Wikipedia [edit] links (`mw-editsection`), screen-reader-only text, and noprint elements. `_ACTION_LINK_RE` filters vote/hide/flag action links.
- **`raw` mode**: `html_to_document(html, extract_content=False)` skips readability. Exposed via `raw=true` parameter on FetchPage, GetText, GetMarkdown tools.
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

## MCP Serve

`kaos-web-serve [--browser] [--crawl] [--http] [--host HOST] [--port PORT] [--debug]`

- Core 7 extraction tools are always registered
- `--browser` — Also register 18 browser interaction tools
- `--crawl` — Also register 3 crawl/discovery tools
- CLI: `kaos-web serve` delegates to `kaos_web.serve:main()`
- Also available via: `kaos-mcp serve --module web`

## MCP Tools

### Extraction tools (7) — `tools.py`

All with `openWorldHint=True`, `readOnlyHint=True`, `idempotentHint=True`.
FetchPage, GetText, GetMarkdown support `raw=true` to skip readability and return full page.

| Tool | Name | Purpose |
|------|------|---------|
| FetchPageTool | `kaos-web-fetch-page` | Fetch URL -> ContentDocument artifact with outline and sections |
| GetPageTextTool | `kaos-web-get-text` | Fetch URL -> plain text |
| GetPageMarkdownTool | `kaos-web-get-markdown` | Fetch URL -> markdown (context-free) |
| GetPageMetadataTool | `kaos-web-get-metadata` | Extract JSON-LD, OpenGraph, meta tags |
| SearchPageTool | `kaos-web-search-page` | Fetch URL -> BM25 search within content |
| GetPageLinksTool | `kaos-web-get-links` | Extract all links with classification (nav/content/social/download) |
| GetPageImagesTool | `kaos-web-get-images` | Extract all images with classification (content/decorative/icon/tracking) |

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
