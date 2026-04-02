# kaos-web Roadmap & TODO

**Updated**: 2026-04-02
**Status**: Phase 6.5 complete (28 MCP tools, 558 tests). Phase 7 next.

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

## Honest Competitive Assessment (2026-04-02)

Tested head-to-head against live sites (273ventures.com, Wikipedia, HN, react.dev,
GitHub). Compared output against Jina Reader (r.jina.ai) on same URLs.

### Extraction Quality (what actually matters)

| Scenario | kaos-web | Jina Reader | Firecrawl | Notes |
|----------|----------|-------------|-----------|-------|
| Blog article (273v) | **A** (1723w, clean) | B+ (nav leaks) | B+ (nav leaks) | Our readability strips nav; Jina/Firecrawl include full nav menu |
| Blog listing (273v) | **F** (9w, copyright only) | **A** (874w, all posts) | A | **Critical failure**: readability discards article cards as boilerplate |
| Products page (273v) | B+ (150w, clean) | B (nav leaks) | B | Short pages work but low word count |
| Wikipedia article | B (16K words but [edit] links, cleanup boxes) | B- (full nav sidebar leaks) | B+ | Both leak junk; Wikipedia is hard for everyone |
| Hacker News | C (58 vote links in markdown) | C (same problem) | B | HN's flat structure defeats readability |
| react.dev (SPA) | D (65w, only footer text) | B (JS rendering) | A (headless) | httpx can't render JS; Playwright gets same bad result |
| GitHub README | B+ (478w, clean) | B (nav leaks) | A | We extract README well; Jina includes all nav chrome |

### Academic Context

Bevendorff et al. 2023 compared 14 extractors across combined datasets:
- **Trafilatura**: F1 0.883 mean (best overall), Precision 0.978, Recall 0.920
- **Readability**: F1 0.861 mean, but **0.970 median** (more consistent)
- **Key finding**: "Heuristic extractors perform the best and are most robust;
  the performance of large neural models is surprisingly bad."
- **Ensemble wins**: Weighted ensemble of Readability + Trafilatura + Goose = 0.974 median F1

This validates our Readability-based approach over LLM extraction (Jina's ReaderLM,
Crawl4AI's LLM strategy). The gap is not in the algorithm — it's in our fallback
logic when Readability's scoring discards non-article content.

### Where We Actually Win

- **Nav stripping**: On article pages, our readability correctly removes nav/header/footer.
  Jina Reader dumps the full navigation menu into every page's markdown output.
  Firecrawl's `onlyMainContent` occasionally leaks nav too (their docs recommend
  `excludeTags`/`includeTags` overrides, which is an implicit admission).
- **AST with provenance**: No competitor produces a typed document model. Everyone else
  outputs flat markdown strings. This matters for downstream search, references, and MCP.
- **Structured metadata**: JSON-LD, OpenGraph extraction is solid. Jina returns bare
  metadata headers. Trafilatura has good metadata too but produces flat text, not AST.
- **Self-hosted, license-clean**: Firecrawl is AGPL. Jina is SaaS-only for the API
  (model can be self-hosted but lacks the API). Crawl4AI is Apache 2.0 (closest competitor
  on licensing). We're proprietary.
- **In-page BM25 search**: Unique feature. No competitor offers structured AST-grounded search.
- **Speed**: 1.7-2.2x faster than Trafilatura on article extraction, validated by benchmarks.

### Where We Actually Lose

- **Readability fails on listing/card pages**: Blog index, product grids, search results —
  any page where the "main content" is a list of cards/excerpts rather than a single article.
  Readability's scoring algorithm treats these as boilerplate. **This is the #1 gap.**
  Firecrawl handles these (95.3% success rate on 1000-URL benchmark). Jina returns content
  but with full nav leakage.
- **Wikipedia [edit] links**: We include 63 `[edit]` section links in markdown.
  Firecrawl strips these. We should too.
- **JS-rendered SPAs**: httpx gets nothing from react.dev, Next.js apps.
  Firecrawl handles these (96.6% success on JS-heavy SPAs via managed browser fleet).
  Our Playwright path helps but readability can still discard SPA content shells.
- **Anti-bot sites**: We have no anti-bot evasion. Firecrawl: 88.4% success.
  Crawl4AI: 72.0%. Not a priority for our use case (legal data, not scraping) but
  worth noting.
- **Crawl sophistication**: No priority queue, no incremental/resumable state, no
  politeness beyond rate limiting. Firecrawl's managed crawl is more mature.

### Honest Grades

| Capability | kaos-web | Jina Reader | Firecrawl | Crawl4AI | Trafilatura |
|-----------|----------|-------------|-----------|----------|-------------|
| Article extraction | **A** | B+ | B+ | B | **A** |
| Listing/card pages | **B+** (semantic fallback) | B+ | A | B | C |
| Nav/chrome stripping | **A** (articles + class filter) | D (nav leaks) | B | C (11.3% noise) | **A** (highest precision) |
| SPA/JS pages | D | B+ | **A** | B+ | F (no JS) |
| Wikipedia/complex | B | B- | B+ | B | B+ |
| Structured metadata | **A** | C | B | C | **A** |
| In-page search | **A** (unique) | — | — | — | — |
| Multi-page crawl | B | — | **A** | B+ | — |
| Browser interaction | B+ (18 tools) | — | C | B | — |
| Tables | A- (AST) | B+ (GFM) | B+ | B | C (historically buggy) |
| Images | B+ | C (broken URLs) | B+ | B | D (basically broken) |
| Self-hosted + clean | **A** (proprietary) | F (SaaS) | F (AGPL) | **A** (Apache 2.0) | **A** (Apache 2.0) |

### Real Competitor Weaknesses (from GitHub issues + benchmarks)

- **Firecrawl**: Self-hosted markdown randomly empty while HTML works (issue #1297).
  H1/H2 headings lose `#` marks in self-hosted (issue #1360). Credit multiplier trap:
  JSON+Enhanced = 9x cost per page. Anti-bot looping on self-hosted (issue #2350).
- **Jina Reader**: Incomplete content on SPAs (issue #1196). Image URLs return SVG
  placeholders (issue #36). Unannounced table format changes breaking pipelines (#1100).
  No developer responses on many issues.
- **Crawl4AI**: LLM extraction hallucinates prices/data (issue #712). Scroll extraction
  loses content (issue #731). Memory leaks in Docker (issue #1256). Rate limiter broken
  (issue #1095). 11.3% noise ratio (vs Firecrawl's 6.8%).
- **Trafilatura**: Images basically broken (issue #610). Table handling historically buggy
  (#136). No JS rendering. Slower than alternatives.

### Priority Fixes

1. ~~**P0: Readability fallback for listing pages**~~ — **DONE**. Semantic container
   fallback: `<main>` → `<article>` parent → `[role=main]` → `<body>`. Triggers when
   readability returns < 50 words but body has > 200. 273v /blog: 9 → 400+ words.
2. ~~**P1: Strip Wikipedia [edit] links**~~ — **DONE**. `_SKIP_CLASSES` filter in
   `_process_element()` and `_process_inlines()`. 63 → 0 [edit] links on Wikipedia.
3. ~~**P1: Strip vote/action links**~~ — **DONE**. `_ACTION_LINK_RE` filter in
   `_element_to_inline()`. 58 → 0 vote links on HN.
4. **P2: Better SPA handling** — When httpx extraction yields < 50 words, auto-suggest
   or auto-fallback to Playwright in tool error messages.
5. **P3: Consider Readability+Trafilatura ensemble** — Academic research shows weighted
   ensemble hits 0.974 median F1, beating any single extractor. Worth evaluating for
   cases where Readability alone fails. (But Trafilatura is Apache 2.0 — license-safe.)

---

## Test Targets

| Milestone | Tests | Current |
|-----------|-------|---------|
| Phase 4 complete | 250+ | **293** (exceeded) |
| Phase 5.1 complete | 330+ | **335** (exceeded) |
| Phase 5 complete | 350+ | **393** (exceeded) |
| Phase 6 complete | 400+ | **545** (exceeded) |
| Phase 7 complete | 550+ | — |
