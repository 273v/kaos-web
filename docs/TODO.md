# kaos-web Roadmap & TODO

**Updated**: 2026-04-02
**Status**: Phases 1-4 complete. Browser interaction (Phase 5) is the critical gap.

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
- [x] BrowserClient with Playwright (chromium/firefox/webkit, channel="chrome")
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

## Next: Phase 5 — Browser Interaction (P0)

The critical competitive gap. Every serious MCP server (Playwright MCP, Chrome DevTools,
Browserbase, Firecrawl) exposes browser interaction tools. Without these, agents cannot
get past cookie banners, log in, click "load more", or fill forms.

### 5.1 Browser Interaction MCP Tools

New tools to add (following existing tool design patterns):

| Tool | Name | Input | Description |
|------|------|-------|-------------|
| ClickElement | `kaos-web-browser-click` | url_or_context, selector | Click element by CSS selector |
| FillInput | `kaos-web-browser-fill` | url_or_context, selector, value | Type into input field |
| Screenshot | `kaos-web-browser-screenshot` | url, full_page, format | Take screenshot, return as artifact |
| EvaluateJS | `kaos-web-browser-evaluate` | url_or_context, expression | Execute JS, return result |
| GetSnapshot | `kaos-web-browser-snapshot` | url_or_context | Accessibility tree (text representation of page) |

Design decisions:
- All browser tools use `openWorldHint=True`, `readOnlyHint=False` (click/fill modify state)
- `ClickElement` and `FillInput` are `destructiveHint=False` (additive, not destructive)
- Tools that need persistence across calls use named contexts via `context_id`
- `Screenshot` returns `KaosImage` artifact (via kaos-content images)
- `GetSnapshot` returns accessibility tree as text (like Playwright MCP's `browser_snapshot`)

### 5.2 Cookie / Storage MCP Tools

| Tool | Name | Description |
|------|------|-------------|
| GetCookies | `kaos-web-browser-cookies` | List cookies for a domain |
| SetCookie | `kaos-web-browser-set-cookie` | Set a cookie |
| SaveAuthState | `kaos-web-browser-save-auth` | Save browser context state to file |
| LoadAuthState | `kaos-web-browser-load-auth` | Load saved auth state |

### 5.3 Network Monitoring Tools

| Tool | Name | Description |
|------|------|-------------|
| ListRequests | `kaos-web-browser-requests` | List network requests made by a page |
| GetRequest | `kaos-web-browser-get-request` | Get full request/response detail by ID |

### Implementation approach:
- All browser tools delegate to `BrowserClient` methods
- Named contexts enable multi-step workflows (login → navigate → extract)
- Network monitoring via Playwright's `page.on("request")` / `page.on("response")`
- Accessibility snapshot via `page.accessibility.snapshot()`

### Tests needed:
- Unit tests with mocked Playwright
- Integration tests: login flow (httpbin basic auth), form fill, screenshot
- E2E through kaos-mcp adapter

---

## Phase 6 — Multi-Page Operations (P2)

### 6.1 Batch Fetch

```python
async def batch_fetch(urls: list[str], *, concurrency: int = 5) -> list[WebResponse]:
    """Fetch multiple URLs concurrently with rate limiting."""
```

MCP tool: `kaos-web-batch-fetch` (urls, concurrency, use_browser)

### 6.2 Site Crawl

```python
async def crawl(
    start_url: str, *, max_depth: int = 2, max_pages: int = 50,
    url_filter: str | None = None,
) -> list[ContentDocument]:
    """Crawl a site starting from a URL."""
```

MCP tool: `kaos-web-crawl-site` (url, max_depth, max_pages)

### 6.3 URL Discovery

```python
async def discover_urls(url: str) -> list[ExtractedLink]:
    """Discover all linked URLs from a page, classify by type."""
```

This already exists as `extract_links()` but needs an MCP tool wrapper.

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
| Browser interaction | **F** (fetch only) | **A** (50+ tools) | **A** (29 tools) | B (interact) | A (act/observe) |
| Auth/cookies | C (storage_state) | **A** (full CRUD) | B | B | A |
| Network monitoring | **F** | **A** | **A** | None | None |
| JS execution | **F** | **A** | **A** | None | A (via Stagehand) |
| Multi-page crawl | **F** | None | None | **A** | None |
| Self-hosted | **A** | **A** | **A** | F (SaaS) | F (SaaS) |
| License clean | **A** | A | A | F (AGPL) | N/A |

**Strategy**: Phase 5 closes the browser interaction gap (F → B+). Combined with
our content extraction advantage (A vs C), kaos-web becomes the most complete
self-hosted MCP server for web content.

---

## Test Targets

| Milestone | Tests | Current |
|-----------|-------|---------|
| Phase 4 complete | 250+ | **293** (exceeded) |
| Phase 5 complete | 350+ | — |
| Phase 6 complete | 400+ | — |
| Phase 7 complete | 450+ | — |
