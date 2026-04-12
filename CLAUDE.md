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
- `browser_page_prep.py` — Cookie consent banner dismissal for 8 known CMPs (OneTrust, CookieBot, TrustArc, Quantcast, Complianz, Osano, Didomi, Termly)
- `extract/readability.py` — Heuristic readability algorithm (Mozilla port), used as fallback
- `extract/readability_l3.py` — **Level 3 learned readability**: pre-trained logistic regression (35 DOM-node features, 10-page corpus). Default extractor. `content_scope` parameter (0.0-1.0) controls precision/recall tradeoff
- `scripts/readability_experiments.py` — Experiment harness for evaluating L1/L2/L3 on labeled corpus (research script, not importable production code)
- `extract/html_to_ast.py` — lxml HTML element tree to kaos-content Block/Inline AST conversion
- `extract/metadata.py` — JSON-LD, OpenGraph, and meta tag extraction
- `middleware/` — Composable chain: retry, rate_limit, robots, cache
- `tools.py` — 5 extraction MCP tools registered with KaosRuntime
- `browser_tools.py` — 19 browser interaction MCP tools (navigate, click, fill, type, press, select, screenshot, evaluate, snapshot, content, cookies, set-cookie, save-auth, log-requests, requests, get-request, captured-responses, list-contexts, close-context)
- `sitemap.py` — Sitemap parser (XML/text/gzip, index recursion, robots.txt discovery)
- `discovery.py` — URL discovery pipeline (sitemaps + page links, pattern filtering)
- `batch.py` — Concurrent URL fetching with asyncio.Semaphore
- `crawl.py` — BFS site crawl orchestrator with depth/page limits
- `crawl_tools.py` — 3 crawl MCP tools (discover-urls, batch-fetch, crawl-site)
- `domain/` — Domain intelligence package: `tcp.py` (async port probing), `tls.py` (stdlib SSL cert inspection with validated→fallback pattern), `http.py` (header + security scoring + CDN detection), `service.py` (composite), `dns.py` (dnspython-based queries + reverse PTR + DNSSEC + zone transfers), `security.py` (SPF/DKIM/DMARC parsing, 12 DKIM selectors), `whois.py` (own stdlib WHOIS client, 55-TLD server map, referral following), `profile.py` (composite domain profile), `org.py` (Schema.org Organization/LegalService/Attorney entity extraction from JSON-LD + OpenGraph + footer patterns), `models.py` (Pydantic models)
- `domain_tools.py` — 11 domain intelligence MCP tools (tcp-probe, tls-inspect, http-headers, service-detect, dns-lookup, dns-enumerate, dns-zone-transfer, dns-security, whois-lookup, domain-profile, extract-org). Registered via `register_domain_tools()` and enabled with `kaos-web-serve --domain`.
- `cli.py` — CLI with fetch, search, metadata commands

## Dependencies

- **httpx[http2]** — Async HTTP client with HTTP/2, connection pooling
- **lxml** — Fast HTML parsing and tree walking
- **playwright** (optional `[browser]` extra) — Headless browser rendering
- **dnspython** (optional `[dns]` extra) — DNS queries, zone transfers, reverse PTR (used by domain intelligence tools)
- **kaos-content** — Document AST model (Block/Inline/Provenance)
- **kaos-core** — Runtime, tool framework, artifact helpers
- **kaos-mcp** (optional `[mcp]` extra) — MCP server bridge
- **kaos-nlp-core** (optional `[nlp]` extra) — BM25 search within extracted content

**Pure stdlib for domain tools**: TCP probing (`asyncio.open_connection`), TLS inspection (`ssl` + `socket`), WHOIS (`asyncio.open_connection` on port 43 + regex parsing — no `python-whois` dep).

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
- **L3-first with readability fallback**: `html_to_document()` tries the Level 3 learned model first, falls back to heuristic readability, then to semantic container detection (`<main>` → `<article>` → `[role=main]` → `<body>`). When extraction returns < 50 words, the fallback chain activates.
- **`content_scope` parameter**: `html_to_document(html, content_scope=0.7)` controls extraction breadth (0.0 = strict article-only, 0.5 = balanced default, 1.0 = permissive). Exposed on get-markdown, get-text, fetch-page, search-page tools.
- **Cookie banner dismissal**: `dismiss_overlays=true` (default for extraction tools) auto-dismisses known CMP banners before content extraction in browser mode. Uses single `page.evaluate()` for detection (~5ms). Exposed on all browser-capable tools and `browser-navigate`.
- **Noise filtering**: `_SKIP_CLASSES` filters Wikipedia [edit] links (`mw-editsection`), screen-reader-only text, and noprint elements. `_ACTION_LINK_RE` filters vote/hide/flag action links.
- **`raw` mode**: `html_to_document(html, extract_content=False)` skips all extraction. Exposed via `raw=true` parameter on FetchPage, GetText, GetMarkdown tools.
- **Lazy imports**: Heavy dependencies (playwright, kaos-content serializers) are imported inside handlers, not at module level, keeping `--help` fast.
- **Search lives in kaos-content**: `kaos_content.search.search_document()` is the canonical search. Never import search from kaos-pdf or duplicate it. All extraction modules share the same search.
- **Middleware wired in HttpClient**: `HttpClient.fetch()` routes through `MiddlewareChain` (retry → rate_limit → robots → cache → raw httpx). Config flags control which middleware are active. Unit tests use `_NO_MIDDLEWARE` config to avoid mock interference.
- **Response body capture**: `enable_request_logging(context_id, capture_bodies=True)` captures response bodies for fetch/xhr requests with text-like content types (JSON, HTML, XML, text, CSV). Bodies stored in `_response_bodies[context_id][request_id]`. 1MB default size limit. Logging config stored in `_logging_config` — handlers are automatically re-attached when `fetch()` replaces a page in a named context. Logs accumulate across navigations.
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
- `--browser` — Also register 19 browser interaction tools
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

### Browser interaction tools (19) — `browser_tools.py`

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
| EnableRequestLoggingTool | `kaos-web-browser-log-requests` | Start recording network requests, optionally capture response bodies |
| ListRequestsTool | `kaos-web-browser-requests` | List recorded network requests with filter, shows `has_body` indicator |
| GetRequestDetailTool | `kaos-web-browser-get-request` | Full request/response detail by ID, includes decoded body if captured |
| ListCapturedResponsesTool | `kaos-web-browser-captured-responses` | List responses with captured bodies, optionally store as session artifacts |
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

## Agentic Workflow Patterns

### Quick page extraction (simple, fast)
1. `kaos-web-get-markdown` or `kaos-web-get-text` — fetch and extract content
2. If 403 → auto-retries with Playwright browser. No action needed.

### Browser interaction (forms, login, JS-heavy sites)
1. `kaos-web-browser-navigate` — open page in persistent context (set `context_id`)
2. `kaos-web-browser-snapshot` — get accessibility tree to find interactive elements
3. `kaos-web-browser-click` / `kaos-web-browser-fill` / `kaos-web-browser-type` — interact
4. `kaos-web-browser-content` — extract updated page content after interaction
5. `kaos-web-browser-close-context` — clean up when done

### API endpoint discovery (find backend JSON APIs behind a web app)
1. `kaos-web-browser-navigate` — open the page in a persistent context
2. `kaos-web-browser-log-requests` with `capture_bodies: true` — enable request logging with body capture
3. `kaos-web-browser-navigate` — navigate to the target page (logging survives page replacement)
4. Interact with the page (click, fill) to trigger additional API calls
5. `kaos-web-browser-requests` with `resource_type: "fetch"` — list API calls (`has_body` shows which have bodies)
6. `kaos-web-browser-get-request` — get full detail with decoded JSON body
7. `kaos-web-browser-captured-responses` with `store_artifacts: true` — persist JSON responses as session artifacts

### Site crawling (discover and extract all pages)
1. `kaos-web-discover-urls` — fast URL inventory via sitemaps + page links
2. `kaos-web-batch-fetch` — concurrent extraction of selected URLs
3. Or: `kaos-web-crawl-site` — full BFS crawl with depth/page limits

### Data extraction (tables, links, images, metadata)
- `kaos-web-get-tables` — extract HTML `<table>` elements as structured TSV
- `kaos-web-get-links` — extract all links classified by type (nav/content/social)
- `kaos-web-get-images` — extract all images classified (content/decorative/icon)
- `kaos-web-get-metadata` — JSON-LD, OpenGraph, meta tags
- `kaos-web-search-page` — BM25 search within extracted page content

## Rules

- **Never add AGPL/GPL dependencies.** This is a proprietary codebase.
- Follow kaos-core `KaosTool` ABC for all MCP tools. Set `ToolAnnotations` on every tool.
- Tool error messages must include recovery guidance (what went wrong + how to fix + alternative).
- Use `async with` for both `HttpClient` and `BrowserClient` (context manager pattern).
- 1-based page numbers in CLI, 0-based internally.
- Errors to stderr with non-zero exit, output to stdout.
