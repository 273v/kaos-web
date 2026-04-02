# kaos-web Roadmap & TODO

**Updated**: 2026-04-02
**Status**: Phase 6 complete (26 MCP tools, 502 tests). Phase 7 next.

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

## Phase 6 — Multi-Page Operations (DONE)

Firecrawl-style Map/Crawl separation: discover URLs first (fast, cheap),
then extract selectively. Zero new dependencies — lxml + stdlib only.

### 6.1 Sitemap Parser (`sitemap.py`) — DONE

~200 lines using lxml. XML/text/gzip parsing, sitemap index recursion,
cycle detection, robots.txt discovery.

- [x] XML `<urlset>` and `<sitemapindex>` (with/without namespace)
- [x] Plain text sitemaps (one URL per line)
- [x] Gzip decompression
- [x] Sitemap index recursion (depth-limited to 3, cycle detection)
- [x] robots.txt discovery via stdlib `RobotFileParser.site_maps()`
- [x] Fallback to `/sitemap.xml`, `/sitemap_index.xml`
- [x] Tolerant parsing (malformed XML recovery, missing fields)

### 6.2 URL Discovery (`discovery.py`) — DONE

~150 lines. Combines sitemaps + page links with Firecrawl-style `sitemap` enum.

- [x] `sitemap` enum: `include` (default), `skip`, `only`
- [x] `include_patterns` / `exclude_patterns` (regex on URL path)
- [x] `max_urls` limit
- [x] robots.txt Disallow filtering
- [x] Deduplication across sources
- [x] Sort by lastmod (newest first)

### 6.3 Batch Fetch (`batch.py`) — DONE

~80 lines. Concurrent URL fetching with `asyncio.Semaphore`.

- [x] Per-URL error isolation
- [x] Reuses HttpClient middleware chain (retry, rate limit, cache)
- [x] Configurable concurrency
- [x] Elapsed time tracking

### 6.4 Site Crawl (`crawl.py`) — DONE

~200 lines. BFS orchestrator with sitemap-first discovery.

- [x] BFS queue with depth/page limits
- [x] Content extraction (text + markdown + metadata)
- [x] Internal link extraction and enqueuing
- [x] URL normalization (fragment removal, trailing slash)
- [x] External link filtering

### 6.5 MCP Tools (3 tools) — DONE

| Tool | Name | Annotations | Description |
|------|------|-------------|-------------|
| DiscoverUrls | `kaos-web-discover-urls` | readOnly, openWorld | Fast URL inventory (sitemaps + page links) |
| BatchFetch | `kaos-web-batch-fetch` | readOnly, openWorld | Concurrent multi-URL fetch with extraction |
| CrawlSite | `kaos-web-crawl-site` | readOnly, openWorld | Full site crawl with sitemap-first discovery |

### 6.6 Tests — DONE

- 93 unit tests: sitemap, discovery, batch, crawl, tool metadata/errors
- 16 integration tests: real sitemaps, batch fetch, crawl, MCP tool E2E
- Total: 109 new tests (502 total, up from 393)

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
| Multi-page crawl | **A-** (sitemap+BFS) | None | None | **A** | None |
| Self-hosted | **A** | **A** | **A** | F (SaaS) | F (SaaS) |
| License clean | **A** | A | A | F (AGPL) | N/A |

**Strategy**: Phase 6 closes the multi-page crawl gap (F → A-). Combined with
our content extraction advantage (A vs C) and browser interaction (A-), kaos-web
is now the most complete self-hosted MCP server for web content. Only Firecrawl
matches on multi-page crawl, but it's AGPL/SaaS-only.

---

## Test Targets

| Milestone | Tests | Current |
|-----------|-------|---------|
| Phase 4 complete | 250+ | **293** (exceeded) |
| Phase 5.1 complete | 330+ | **335** (exceeded) |
| Phase 5 complete | 350+ | **393** (exceeded) |
| Phase 6 complete | 400+ | **502** (exceeded) |
| Phase 7 complete | 550+ | — |
