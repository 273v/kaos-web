# kaos-web Roadmap & TODO

**Updated**: 2026-04-02
**Status**: Phase 5 complete (18 browser tools, 23 total). Phase 6 next.

---

## Completed

### Phase 1: Core Extraction (DONE)
- [x] Readability algorithm (349 lines, heading bug fixed)
- [x] HTML-to-AST conversion (1,219 lines, 20+ element types, model_construct optimized)
- [x] Metadata extraction (JSON-LD, OpenGraph, meta tags — 155 lines, zero deps)
- [x] Performance: 0.95ms articles, 5.9ms medium, 1.7-2.2x faster than alternatives

### Phase 2: HTTP Hardening + MCP Tools (DONE)
- [x] HttpClient with full config (pooling, timeouts, auth, SSL, proxy, cookies)
- [x] WebError hierarchy (8 error types with retryable flag)
- [x] Middleware chain wired into HttpClient.fetch() (retry → rate_limit → robots → cache)
- [x] RetryMiddleware (exponential backoff + jitter + Retry-After)
- [x] RateLimitMiddleware (per-domain token bucket + error mode)
- [x] RobotsMiddleware (stdlib robotparser + per-domain cache)
- [x] 5 MCP tools (FetchPage, GetText, GetMarkdown, Metadata, SearchPage)
- [x] CLI: fetch, extract, search, metadata commands
- [x] User-agent randomization (100 real UAs from microlinkhq, updatable via script)

### Phase 3: Browser Client (DONE)
- [x] BrowserClient with Playwright (chromium/firefox/webkit, auto-detected channel)
- [x] Lazy browser launch, context-per-request isolation
- [x] Named context pooling (session persistence via context_id)
- [x] Resource blocking (images, fonts, CSS, media)
- [x] Wait strategies (load, domcontentloaded, networkidle, selector)
- [x] Screenshot capture
- [x] Auth state persistence via storage_state

### Phase 4: Cache + Extraction + Quality (DONE)
- [x] CacheMiddleware (memory + disk backends, RFC 7231, LRU eviction, blake2b verification)
- [x] Link extraction with classification (nav/content/social/download/pagination)
- [x] Image extraction with classification (content/decorative/icon/tracking/social_card)
- [x] Middleware E2E tests proving retry/cache/robots/rate-limit work through client
- [x] Fuzz/invariant tests across 4 HTML fixtures (60 tests)
- [x] Real-site integration tests (17 tests, 8 diverse sites)
- [x] MCP E2E through kaos-mcp adapter (10 tests)
- [x] Error message consistency (three-part rule on all tools)
- [x] Canonical search moved to kaos_content.search (shared by kaos-pdf + kaos-web)

### Architecture
- [x] search_document() in kaos-content (not duplicated across extraction modules)
- [x] No cross-imports between sibling modules (kaos-pdf ↛ kaos-web)
- [x] CLAUDE.md, QUALITY.md, PRD.md, DESIGN.md, HTML_TO_AST_REFERENCE.md

---

## Phase 5 — Browser Interaction

### 5.1 Browser Interaction MCP Tools (DONE)

12 tools (10 interaction + 2 context management) in `browser_tools.py`:

| Tool | Name | Annotations | Description |
|------|------|-------------|-------------|
| BrowserNavigate | `kaos-web-browser-navigate` | write, openWorld | Navigate to URL, create persistent page |
| ClickElement | `kaos-web-browser-click` | write, openWorld | Click element by CSS selector |
| FillInput | `kaos-web-browser-fill` | write, openWorld | Fill input field (clears first) |
| TypeText | `kaos-web-browser-type` | write, openWorld | Type character-by-character (autocomplete) |
| PressKey | `kaos-web-browser-press` | write, openWorld | Press keyboard key (Enter, Tab, etc.) |
| SelectOption | `kaos-web-browser-select` | write, openWorld | Select dropdown option |
| Screenshot | `kaos-web-browser-screenshot` | readOnly, openWorld | Take screenshot (context or URL) |
| EvaluateJS | `kaos-web-browser-evaluate` | write, openWorld | Execute JS expression |
| GetSnapshot | `kaos-web-browser-snapshot` | readOnly, openWorld | Accessibility tree |
| GetContent | `kaos-web-browser-content` | readOnly, openWorld | Extract updated page content |
| ListContexts | `kaos-web-browser-list-contexts` | readOnly, openWorld | List active browser contexts |
| CloseContext | `kaos-web-browser-close-context` | write, openWorld | Close context and free resources |

Architecture:
- [x] `BrowserClient` enhanced with page tracking (`_pages: dict[str, Page]`)
- [x] Named contexts keep pages alive for multi-step interaction workflows
- [x] Unnamed contexts (no context_id) retain original fetch-and-cleanup behavior
- [x] Shared `_browser_client` singleton with configurable browser channel
- [x] Auto-detection: system Chrome on Linux, bundled Chromium elsewhere
- [x] Env vars: `KAOS_BROWSER_CHANNEL`, `KAOS_BROWSER_HEADLESS`, `KAOS_BROWSER_TYPE`
- [x] `configure_browser(config)` Python API for programmatic override
- [x] `_require_page()` with agent-friendly error messages listing active contexts
- [x] `_raise_browser_error(exc, url, operation)` with per-operation error messages
- [x] Context management: ListContexts + CloseContext tools for agent resource cleanup
- [x] 70 unit tests (page tracking, interaction, tool metadata, error paths, config detection)

### 5.2 Cookie / Storage MCP Tools (DONE)

| Tool | Name | Annotations | Description |
|------|------|-------------|-------------|
| GetCookies | `kaos-web-browser-cookies` | readOnly, openWorld | List cookies for context |
| SetCookie | `kaos-web-browser-set-cookie` | write, openWorld | Set a cookie (name, value, domain/url) |
| SaveAuthState | `kaos-web-browser-save-auth` | write, local | Save context state to JSON file |

Architecture:
- [x] `BrowserClient.get_cookies()`, `set_cookies()`, `save_storage_state()` methods
- [x] Cookie CRUD operates on context (not page) — persists across page navigations
- [x] `SaveAuthState` has `openWorldHint=False` (writes to local disk only)
- [x] LoadAuthState deferred — use `BrowserClientConfig(storage_state="path.json")` directly

### 5.3 Network Monitoring Tools (DONE)

| Tool | Name | Annotations | Description |
|------|------|-------------|-------------|
| EnableRequestLogging | `kaos-web-browser-log-requests` | write, openWorld | Start recording network requests |
| ListRequests | `kaos-web-browser-requests` | readOnly, openWorld | List recorded requests with filter |
| GetRequestDetail | `kaos-web-browser-get-request` | readOnly, openWorld | Full request/response detail by ID |

Architecture:
- [x] `BrowserClient.enable_request_logging()` attaches `page.on("request")`/`page.on("response")`
- [x] `_request_logs: dict[str, list[dict]]` stores per-context request logs
- [x] Response matching by URL (reversed scan for latest matching request)
- [x] Logs cleaned up on `close_context()` and `close()`
- [x] `resource_type` filter on ListRequests (document, xhr, fetch, script, etc.)

### Phase 5 Integration Tests (DONE)

29 integration tests against real browsers and real sites:
- [x] Navigate + page tracking (3 tests)
- [x] Click elements on example.com + books.toscrape.com (2 tests)
- [x] Fill forms on httpbin.org (3 tests: fill, type, submit)
- [x] Press keys (1 test)
- [x] Screenshots: PNG + JPEG from named contexts (2 tests)
- [x] Accessibility snapshots via Playwright aria_snapshot() (2 tests)
- [x] JavaScript evaluation: title, complex expressions, DOM queries (3 tests)
- [x] Content extraction after interaction (2 tests)
- [x] Cookies: set via httpbin, programmatic set + read (2 tests)
- [x] Network monitoring: log, list, detail, filter (3 tests)
- [x] Multi-step workflows: fill+submit+extract, click+navigate (2 tests)
- [x] MCP tool E2E: navigate, snapshot, click+content, screenshot (4 tests)

---

## Next: Phase 6 — Multi-Page Operations (P2)

Inspired by Firecrawl's Map/Crawl separation: discover URLs first (fast, cheap),
then extract selectively. Agents review the URL list, filter, then crawl a subset.
Zero new dependencies — uses lxml (already have) and stdlib.

### 6.1 Sitemap Parser (`sitemap.py`)

~100-150 lines using lxml. No third-party sitemap library (only good one is GPL).

```python
@dataclass(frozen=True, slots=True)
class SitemapEntry:
    """A URL entry from a sitemap."""
    url: str
    lastmod: datetime | None = None
    changefreq: str | None = None   # daily, weekly, monthly, etc.
    priority: float | None = None   # 0.0 - 1.0

@dataclass(frozen=True, slots=True)
class SitemapResult:
    """Result of parsing one or more sitemaps."""
    entries: list[SitemapEntry]
    sitemap_urls: list[str]         # Source sitemap URLs that were parsed
    errors: list[str]               # Non-fatal parse errors

async def parse_sitemap(url: str, client: HttpClient) -> SitemapResult:
    """Fetch and parse a sitemap URL (XML, text, or gzip)."""

async def discover_sitemaps(domain: str, client: HttpClient) -> list[str]:
    """Discover sitemap URLs for a domain via robots.txt + well-known paths."""
```

Features:
- XML `<urlset>` and `<sitemapindex>` parsing (with/without namespace)
- Plain text sitemaps (one URL per line)
- Gzip decompression (`.xml.gz`)
- Sitemap index recursion (depth-limited, cycle detection)
- robots.txt discovery via stdlib `RobotFileParser.site_maps()`
- Fallback to `/sitemap.xml`, `/sitemap_index.xml`
- `lastmod` parsing via `datetime.fromisoformat()`
- Tolerant parsing: malformed XML, missing fields, encoding issues

### 6.2 URL Discovery (`discovery.py`)

Combines all URL sources into a single discovery pipeline. Firecrawl-style
`sitemap` enum: `include` (default), `skip`, `only`.

```python
@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    """URLs discovered from a domain."""
    urls: list[DiscoveredUrl]
    sitemap_count: int              # How many sitemaps were found
    page_link_count: int            # How many links from page crawling
    total: int

@dataclass(frozen=True, slots=True)
class DiscoveredUrl:
    """A URL with discovery metadata."""
    url: str
    source: str                     # "sitemap", "page_link", "robots"
    lastmod: datetime | None = None
    link_type: str | None = None    # navigation, content, pagination, etc.
    depth: int = 0

async def discover_urls(
    url: str,
    *,
    sitemap: Literal["include", "skip", "only"] = "include",
    include_patterns: list[str] | None = None,   # regex on URL path
    exclude_patterns: list[str] | None = None,
    max_urls: int = 1000,
    respect_robots: bool = True,
) -> DiscoveryResult:
    """Discover all URLs from a domain.

    1. Fetch robots.txt → extract Sitemap: directives
    2. Parse sitemaps (if sitemap != "skip")
    3. Fetch start page → extract_links() (if sitemap != "only")
    4. Deduplicate, filter, sort by lastmod (newest first)
    """
```

### 6.3 Batch Fetch (`batch.py`)

Concurrent URL fetching with semaphore and per-domain rate limiting.
Reuses existing HttpClient middleware chain.

```python
@dataclass
class BatchResult:
    """Results from a batch fetch operation."""
    responses: list[WebResponse]
    errors: list[BatchError]
    total: int
    succeeded: int
    failed: int

async def batch_fetch(
    urls: list[str],
    *,
    concurrency: int = 5,
    use_browser: bool = False,
    extract_content: bool = False,  # Also run HTML-to-AST extraction
) -> BatchResult:
    """Fetch multiple URLs concurrently with rate limiting."""
```

Implementation: `asyncio.Semaphore(concurrency)` + existing `HttpClient`
middleware chain (retry, rate limit, cache). Per-URL error isolation — one
failure doesn't abort the batch.

### 6.4 Site Crawl (`crawl.py`)

Orchestrates discovery + batch fetch + extraction. BFS crawl with depth limit.

```python
@dataclass
class CrawlResult:
    """Results from a site crawl."""
    pages: list[CrawlPage]
    total_discovered: int           # URLs found via discovery
    total_crawled: int              # Pages actually fetched
    total_extracted: int            # Pages successfully extracted
    errors: list[CrawlError]
    sitemap_entries: int            # URLs from sitemaps

@dataclass
class CrawlPage:
    """A single crawled and extracted page."""
    url: str
    depth: int
    title: str | None
    content_text: str               # Extracted plain text
    content_markdown: str           # Extracted markdown
    links: list[str]                # Outbound links discovered
    metadata: dict                  # JSON-LD, OG, etc.

async def crawl_site(
    start_url: str,
    *,
    max_depth: int = 2,
    max_pages: int = 50,
    concurrency: int = 5,
    sitemap: Literal["include", "skip", "only"] = "include",
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
    respect_robots: bool = True,
    use_browser: bool = False,
) -> CrawlResult:
    """Crawl a site with sitemap-first discovery.

    Strategy:
    1. discover_urls() — sitemaps + start page links
    2. BFS: dequeue URL → fetch → extract → enqueue new links
    3. Stop at max_depth or max_pages
    4. Deduplicate throughout via seen-set
    """
```

### 6.5 MCP Tools (3 tools)

| Tool | Name | Annotations | Description |
|------|------|-------------|-------------|
| DiscoverUrls | `kaos-web-discover-urls` | readOnly, openWorld | Fast URL discovery (sitemaps + page links) |
| BatchFetch | `kaos-web-batch-fetch` | readOnly, openWorld | Concurrent multi-URL fetch with extraction |
| CrawlSite | `kaos-web-crawl-site` | readOnly, openWorld | Full site crawl with sitemap-first discovery |

`kaos-web-discover-urls` parameters:
- `url` (required) — domain or page URL
- `sitemap` — `include` / `skip` / `only` (default: include)
- `include_patterns` — comma-separated regex patterns for URL paths
- `exclude_patterns` — comma-separated regex patterns to exclude
- `max_urls` — max URLs to return (default: 1000)

`kaos-web-batch-fetch` parameters:
- `urls` (required) — comma-separated URLs to fetch
- `concurrency` — max concurrent requests (default: 5)
- `output_format` — `text` / `markdown` / `metadata` (default: markdown)

`kaos-web-crawl-site` parameters:
- `url` (required) — starting URL
- `max_depth` — max link-following depth (default: 2)
- `max_pages` — max pages to extract (default: 50)
- `concurrency` — max concurrent requests (default: 5)
- `sitemap` — `include` / `skip` / `only` (default: include)
- `include_patterns` — comma-separated regex patterns
- `exclude_patterns` — comma-separated regex patterns
- `output_format` — `text` / `markdown` / `summary` (default: summary)

### Design Decisions

**Firecrawl pattern: Map/Crawl separation.** `discover-urls` is the "map" step —
fast, returns just URLs with metadata. Agents review, filter, then call `batch-fetch`
or `crawl-site` on a subset. This is more useful for agents than a monolithic crawl.

**Sitemap-first discovery.** Sitemaps are authoritative — site owners declare what
pages exist. Link-following misses orphan pages and is slow. Sitemaps give you the
full URL inventory in seconds. The `sitemap` enum (`include`/`skip`/`only`) gives
agents control over the strategy.

**Zero new dependencies.** Sitemap parsing uses lxml (already in tree) + stdlib
gzip + stdlib `RobotFileParser.site_maps()`. No sitemap library needed — the only
good one (ultimate-sitemap-parser) is GPL.

**BFS not DFS.** Breadth-first crawling gets the most important pages first (homepage,
top-level sections) before diving deep. Priority-queue crawling (Crawl4AI pattern)
deferred — BFS is sufficient for the common case.

**Per-URL error isolation.** One broken URL doesn't abort the batch or crawl.
Errors are collected and returned alongside successful results.

**robots.txt respected throughout.** Discovery filters URLs through robots.txt
Disallow rules. Sitemap URLs are not exempt — appearance in a sitemap does NOT
override Disallow.

### Implementation Order

1. `sitemap.py` — parser + robots.txt discovery (~150 lines)
2. `discovery.py` — combine sitemaps + page links (~100 lines)
3. `batch.py` — concurrent fetch with semaphore (~80 lines)
4. `crawl.py` — BFS orchestrator (~150 lines)
5. MCP tools — 3 tools in `crawl_tools.py` (~300 lines)
6. Tests — unit (mocked sitemaps) + integration (real sites)

### Test Plan

Unit tests (~40):
- Sitemap XML parsing (with/without namespace, malformed)
- Sitemap index recursion (depth limit, cycle detection)
- Text sitemap parsing
- Gzip decompression
- robots.txt sitemap discovery
- URL deduplication and filtering
- Batch fetch with concurrency limits
- BFS crawl depth enforcement

Integration tests (~15):
- Real sitemap parsing (Wikipedia, GitHub, books.toscrape.com)
- Real robots.txt sitemap discovery
- Batch fetch of 5-10 real URLs
- Crawl books.toscrape.com (well-structured, paginated)
- Crawl with sitemap-only mode
- Crawl with include/exclude patterns

---

## Phase 7 — Polish & Integration (P3)

### 7.1 Remaining from prior phases
- [ ] Streaming response support for large downloads
- [ ] kaos-source connectors (HttpConnector, BrowserConnector)
- [ ] Wire search_sentences into DocumentView sentence interface

### 7.2 Quality improvements
- [ ] HTML fuzz tests with more fixtures (10+ real pages)
- [ ] Readability quality improvements (usa.gov, W3C extract too aggressively)
- [ ] Table extraction: detect layout tables vs data tables
- [ ] Extended metadata: RSS/Atom feeds, hreflang, robots directives

### 7.3 Documentation
- [ ] QUICKSTART.md with usage examples
- [ ] Update PRD with Phase 5-7 specs
- [ ] API reference documentation

---

## Competitive Position

| Capability | kaos-web | Playwright MCP | Chrome DevTools | Firecrawl | Browserbase |
|-----------|----------|---------------|-----------------|-----------|-------------|
| Content extraction | **A** (AST + provenance) | C (raw text) | C (raw text) | B (markdown) | B (LLM) |
| In-page search | **A** (BM25) | None | None | None | None |
| Structured metadata | **A** (JSON-LD/OG) | None | None | None | None |
| HTTP middleware | **A** (retry/cache/robots) | None | None | N/A (SaaS) | N/A |
| Browser interaction | **A-** (18 tools) | **A** (50+ tools) | **A** (29 tools) | B (interact) | A (act/observe) |
| Auth/cookies | **B+** (get/set/save) | **A** (full CRUD) | B | B | A |
| Network monitoring | **B** (log/list/detail) | **A** | **A** | None | None |
| JS execution | **B+** (evaluate) | **A** | **A** | None | A (via Stagehand) |
| Multi-page crawl | **F** | None | None | **A** | None |
| Self-hosted | **A** | **A** | **A** | F (SaaS) | F (SaaS) |
| License clean | **A** | A | A | F (AGPL) | N/A |

**Strategy**: Phase 5 closes the browser interaction gap (F → A-). Combined with
our content extraction advantage (A vs C), kaos-web is now the most complete
self-hosted MCP server for web content. No other tool combines structured AST
extraction with full browser interaction.

---

## Test Targets

| Milestone | Tests | Current |
|-----------|-------|---------|
| Phase 4 complete | 250+ | **293** (exceeded) |
| Phase 5.1 complete | 330+ | **335** (exceeded) |
| Phase 5 complete | 350+ | **393** (exceeded) |
| Phase 6 complete | 400+ | — |
| Phase 7 complete | 450+ | — |
