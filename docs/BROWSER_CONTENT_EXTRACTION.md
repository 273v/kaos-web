# Browser Content Extraction: Cookie Banners, Content Loading, and Readability

Design document covering three interrelated problems encountered during browser-based web extraction, the solutions implemented, and a roadmap for improving content extraction quality via parametric readability.

**Origin:** 2026-04-04 session extracting the Miller Canfield law firm directory via MCP tools. The session exposed three gaps in kaos-web's browser extraction pipeline: cookie consent banners blocking content, JS-rendered content not loading, and readability extracting the wrong page section.

## Table of Contents

- [Problem Statement](#problem-statement)
- [A. Cookie Consent Banner Dismissal](#a-cookie-consent-banner-dismissal)
- [B. Content Loading Detection](#b-content-loading-detection)
- [C. Parametric Readability](#c-parametric-readability)
- [D. Response Body Capture](#d-response-body-capture)
- [Field Testing Results](#field-testing-results)
- [Public Datasets for Training](#public-datasets-for-training)
- [Implementation Status](#implementation-status)

---

## Problem Statement

When an agent calls `kaos-web-get-markdown` with `use_browser=true` on a typical law firm or enterprise website, three things can go wrong:

1. **Cookie consent banners** are extracted as the page content instead of the actual page. The readability algorithm picks the prominent, text-heavy consent dialog over the (sometimes empty or hidden) main content area.

2. **JS-rendered content** hasn't loaded when the HTML is captured. The browser waits for `load` or `networkidle`, but SPAs and Firmseek-style directories populate their content containers via JavaScript after those events fire. The `#mainContent` div is empty at extraction time.

3. **Readability selects the wrong content** even when the full HTML is available. On directory/listing pages, the search form or navigation gets higher readability scores than the actual results because the algorithm is tuned for article-shaped content, not structured listings.

### Original Session: Miller Canfield

The triggering session attempted to count personnel at Miller Canfield (millercanfield.com) via their firm directory. The workflow required ~15 MCP tool calls and manual JavaScript evaluation to complete a task that should have been 2-3 calls:

1. `kaos-web-get-markdown` with `use_browser=true` returned only the cookie banner text
2. `kaos-web-browser-navigate` + `kaos-web-browser-snapshot` revealed a custom `<dialog>` cookie banner and an empty `#mainContent` div
3. Manual `kaos-web-browser-evaluate` was needed to dismiss the cookie dialog, discover the alphabetical pagination pattern (`?do_item_search=1&letter=A`), and extract structured records via DOM parsing

---

## A. Cookie Consent Banner Dismissal

### Approach

Target only well-known Consent Management Platforms (CMPs) with stable, documented CSS selectors. No heuristic or generic popup detection — better to miss an unknown banner than break a page by clicking the wrong element.

### Implemented: `kaos_web/browser_page_prep.py`

Detection uses a **single `page.evaluate()` call** that checks all CMP selectors synchronously in the browser (~5ms). Only if a banner is found does a second round-trip happen to click the dismiss button. No-banner pages pay negligible cost.

#### Known CMPs

| CMP | Detect Selector | Dismiss Selector | Notes |
|-----|----------------|------------------|-------|
| **OneTrust** | `#onetrust-banner-sdk` | `#onetrust-accept-btn-handler` | Largest CMP globally. Stable IDs. |
| **CookieBot** | `#CybotCookiebotDialog` | `#CybotCookiebotDialogBodyButtonAccept` | Second most popular. Cybot prefix. |
| **TrustArc** | `#truste-consent-track` | `#truste-consent-button` | Enterprise/legal sites. truste- prefix. |
| **Quantcast Choice** | `.qc-cmp2-summary-buttons` | `.qc-cmp2-summary-buttons button[mode='primary']` | Media sites. qc-cmp2 class prefix. |
| **Complianz** | `.cmplz-cookiebanner` | `.cmplz-btn.cmplz-accept` | WordPress plugin. |
| **Osano** | `.osano-cm-dialog` | `.osano-cm-accept-all` | US-focused CMP. |
| **Didomi** | `#didomi-notice` | `#didomi-notice-agree-button` | European CMP. didomi- prefix. |
| **Termly** | `[data-tid='banner-accept']` | `[data-tid='banner-accept']` | Small/medium business CMP. |

#### Research: Existing Projects

| Project | Stars | License | Approach | Notes |
|---------|-------|---------|----------|-------|
| [DuckDuckGo autoconsent](https://github.com/duckduckgo/autoconsent) | 109 | MPL-2.0 | 285 coded rules + 1000 auto-generated + heuristic fallback | Gold standard but far more than we need |
| [Consent-O-Matic](https://github.com/cavi-au/Consent-O-Matic) | 4,011 | Custom | 204 CMP rules via JSON DSL | Academic origin (Aarhus University) |
| [I Still Don't Care About Cookies](https://github.com/OhMyGuus/I-Still-Dont-Care-About-Cookies) | 4,139 | GPL-3.0 | CSS hiding + click handlers | GPL — incompatible with our codebase |
| [Brave CookieCrumbler](https://github.com/brave/cookiecrumbler) | 166 | MPL-2.0 | LLM + keyword detection | Detection only, not dismissal |
| [searchmcp/autoconsent-playwright](https://github.com/searchmcp/autoconsent-playwright) | 0 | MIT | Bundles autoconsent for Playwright `addInitScript()` | Shows the integration pattern |

We chose a focused, hand-curated approach over adopting autoconsent because: (a) we only need ~8 CMPs, not 1000+ rules; (b) the heuristic fallback in autoconsent clicks arbitrary buttons which violates our "known CMPs only" constraint; (c) MPL-2.0 adds source-disclosure obligations for modifications.

#### Performance Design

**Initial (flawed) approach:** Sequential `is_visible(timeout=500)` per CMP. Cost: 8 CMPs x 500ms = ~4 seconds on no-banner pages.

**Current approach:** Single `page.evaluate()` with inline JavaScript checking all detect selectors synchronously. Cost: ~5ms regardless of CMP count. The JS checks `document.querySelector()` + `getComputedStyle()` for each selector in a loop and returns the index of the first visible match, or -1.

#### API

All tools that support `use_browser` now expose two new parameters:

- `dismiss_overlays: bool = True` — auto-dismiss known cookie consent banners before extraction
- `wait_for_selector: str | None` — CSS selector to wait for before extracting (for JS-rendered content)

These are available on: `kaos-web-get-markdown`, `kaos-web-get-text`, `kaos-web-fetch-page`, `kaos-web-search-page`, `kaos-web-get-tables`, and `kaos-web-browser-navigate`.

For `kaos-web-browser-navigate`, `dismiss_overlays` defaults to `False` (interactive sessions are opt-in since the agent may want to inspect the banner).

#### Page Lifecycle

`BrowserClient.fetch()` uses a centralized `page_stored` flag with an inner `finally` block for page cleanup. This prevents page leaks on any failure path (navigation, overlay dismissal, selector wait, content extraction):

```
page = await context.new_page()
page_stored = False
try:
    navigate → dismiss overlays → wait_for_selector → extract
    if context_id:
        self._pages[context_id] = page
        page_stored = True
    return WebResponse(...)
finally:
    if not page_stored:
        page.close()  # suppresses errors
```

---

## B. Content Loading Detection

### Implemented: `wait_for_selector` parameter

Threads through `_fetch_html()` to `BrowserClient.fetch()`. The agent specifies a CSS selector; Playwright waits for it to appear before extracting HTML.

Order of operations in `BrowserClient.fetch()`:
1. Navigate with `wait_until` (load/domcontentloaded/networkidle)
2. Dismiss overlays (if enabled) — overlays can block content visibility
3. Wait for selector (if specified) — now content underneath should be rendering
4. Extract `page.content()`

### Research: Approaches Beyond `wait_for_selector`

| Approach | Maturity | Generic? | Reliability |
|----------|----------|----------|-------------|
| `waitForSelector` | Production (Playwright native) | No (needs known selector) | High |
| `waitForFunction` with content heuristics | Production API | Yes | Medium |
| MutationObserver "DOM settled" | Community gists | Yes | Medium (tuning-sensitive) |
| CDP lifecycle events (firstMeaningfulPaint) | Experimental | Yes | Low (deprecated) |
| LCP via PerformanceObserver | Experimental | Yes | Medium (Chromium-only) |
| Framework hydration checks (React/Vue/Angular) | Ad-hoc | No | High when applicable |

Notable implementations:
- [Crawl4AI](https://github.com/unclecode/crawl4ai) (14k stars): Multi-stage pipeline with `wait_for` supporting CSS and JS conditions
- [Crawlee](https://crawlee.dev/): `AdaptivePlaywrightCrawler` that learns which URLs need browser rendering
- [rinogo's `waitForMutationToStop`](https://gist.github.com/rinogo/7370cfd10f0290a01c773221b26994ad): MutationObserver timer-reset pattern

### Not Yet Implemented: Content Settling Heuristic

A `wait_for_settled: bool` parameter using the MutationObserver timer-reset pattern:
1. Set up a MutationObserver on `document.body`
2. Reset a timer on each mutation
3. Resolve when no mutations for N ms (quiet period)
4. Hard timeout as safety net

This would help with SPA-style pages where content loads progressively. Not implemented yet because it adds latency (minimum = quiet period) and can hang on animated pages. The explicit `wait_for_selector` covers the most common case.

---

## C. Parametric Readability

### Problem

The current readability algorithm (`kaos_web/extract/readability.py`) makes hard binary decisions at multiple stages:

- `_strip_unlikely`: removes elements if negative class AND text < **200** chars
- `_score_candidates`: minimum text length of **25** chars per scored paragraph
- `_collect_siblings`: threshold = `max(10.0, best_score * 0.2)`
- `_select_best`: link density penalty applied uniformly

These hardcoded thresholds are tuned for news articles. They fail on:
- **Directory listings** (many short entries, high link density)
- **Search result pages** (the search form often scores higher than results)
- **Multi-section pages** (only one section extracted, rest discarded)

The agent's only escape hatch is `raw=True`, which disables readability entirely and returns everything — too much for most use cases.

### Proposed: `content_scope` Parameter (0.0 to 1.0)

A single parameter controlling the precision/recall tradeoff of content extraction:

- **0.0 (strict)** — only the highest-confidence content. Good for extracting a single article from a cluttered page.
- **0.5 (default)** — current behavior.
- **1.0 (permissive)** — include anything that might be content. Good for directories, listings, complex multi-section pages.

The agent adjusts this without understanding readability internals. "I got too little content" → increase scope. "I got junk/nav" → decrease scope.

### Design Levels

#### Level 1: Parametric Heuristic (Threshold Scaling)

Map `content_scope` to the existing hardcoded thresholds:

| Internal Parameter | At scope=0.0 | At scope=0.5 | At scope=1.0 |
|-------------------|-------------|-------------|-------------|
| strip_text_threshold | 50 | 200 | 500 |
| sibling_threshold_factor | 0.4 | 0.2 | 0.05 |
| min_paragraph_length | 50 | 25 | 10 |

**Pros:** Zero dependencies, fast, ships immediately.
**Cons:** Arbitrary mapping. "scope=0.7 means strip_threshold=350" has no principled basis. Tuning magic numbers with another magic number.

#### Level 2: Continuous Scoring with Threshold (Recommended Near-Term)

Refactor the existing readability to expose **continuous scores** per DOM node instead of making hard binary decisions. The `content_scope` parameter becomes a **classification threshold** on the score.

The scoring pipeline already computes text density, link density, class/id weight, tag weight, comma count, and length bonus per candidate. Currently these are combined into a score and then subjected to hard cutoffs. Instead:

1. Normalize scores to [0, 1] range (sigmoid or min-max)
2. Remove hard cutoffs from `_strip_unlikely` — instead, let low-scoring nodes compete on score
3. `content_scope` controls the inclusion threshold: `include if P(content) >= 1.0 - content_scope`

```
P(content | node) ~ sigmoid(combined_score)
threshold = 1.0 - content_scope
include node if P >= threshold
```

**Pros:** Same speed, same features, principled threshold interpretation. No training data needed. Compatible upgrade path to Level 3.
**Cons:** Still using hand-tuned feature weights.

#### Level 3: Feature-Based ML Model (Recommended Medium-Term)

Extract a feature vector per DOM node and train a lightweight classifier:

| Feature | Type | Source |
|---------|------|--------|
| text_density (chars / descendant tags) | float | structural |
| link_density (% text in `<a>`) | float | structural |
| class_positive_match | bool | regex |
| class_negative_match | bool | regex |
| tag_type | categorical | structural |
| dom_depth | int | structural |
| relative_position (0.0 = top, 1.0 = bottom) | float | structural |
| sibling_mean_score | float | contextual |
| comma_density | float | content signal |
| sentence_count | int | content signal |
| has_block_children | bool | structural |

Train a logistic regression or small gradient-boosted tree (scikit-learn). Ship the model weights as a constant array in the module — **no ML runtime dependency** at inference. A logistic regression is a dot product; a 50-tree GBT is ~50 comparisons. Sub-microsecond inference.

`content_scope` maps to the classification threshold on model output probability. This is the standard precision/recall tradeoff with a real statistical interpretation.

**Training data sources:** See [Public Datasets](#public-datasets-for-training) below.

**Pros:** Learned weights outperform hand-tuned on novel layouts. Weights are a ~100-float array. Standard ML evaluation (precision, recall, F1 at various thresholds). Same API as Level 2.
**Cons:** Requires labeled training data and a one-time training step. Model may need periodic retraining as web conventions evolve.

#### Level 4: DOM-Structure-Aware Neural Model

Graph neural network on the DOM tree, or transformer on linearized DOM (e.g., MarkupLM). Captures long-range dependencies ("this sidebar is boilerplate because it appears on every page of the site").

**Pros:** State-of-the-art accuracy on complex layouts. Can learn cross-page patterns.
**Cons:** Heavy inference (GPU or large model), requires substantial training data, deployment complexity. Likely overkill for our use case unless we're building a general-purpose web extraction service.

### Recommendation

**Ship Level 2 now, build toward Level 3.**

Level 2 is a clean refactor of the existing code — keep the scoring pipeline, remove hard cutoffs, expose the threshold. Zero new dependencies, zero training data, ships immediately.

Level 3 is the right medium-term move. The key insight: **Levels 2 and 3 share the same API**. The agent doesn't care whether the score comes from a heuristic or a model. So we can:

1. Ship Level 2 with `content_scope` parameter
2. Gather implicit training signal from agent behavior:
   - Retries with `raw=True` = implicit signal that extraction failed
   - `content_scope` adjustments = implicit signal about extraction quality
   - Manual corrections = explicit labels
3. Train a Level 3 model when we have enough signal
4. Swap in the model behind the same `content_scope` API — no tool schema change

---

## Field Testing Results

Tested 2026-04-04 from US-based server. CMP banners are often geo-dependent (GDPR applies to EU visitors), so detection rates vary by location.

### Cookie Banner Detection

| Site | CMP Script Present | Banner Visible | Detected | Dismissed | Notes |
|------|-------------------|---------------|----------|-----------|-------|
| **Freshfields** (freshfields.com) | OneTrust | Yes (first run) | Yes | Yes | Log confirmed: `Cookie banner detected: OneTrust` / `Cookie banner dismissed: OneTrust` |
| **Freshfields** (subsequent runs) | OneTrust | No | No | - | Banner suppressed after first accept (cookies persisted in browser instance) or geo-dependent |
| **Baker McKenzie** (bakermckenzie.com) | OneTrust (script loaded) | No (banner not rendered) | No | - | OneTrust script present but banner suppressed — likely US IP doesn't trigger GDPR banner |
| **Miller Canfield** (millercanfield.com) | None (custom `<dialog>`) | Yes | No | - | Custom cookie dialog, not a known CMP. Correctly ignored. |
| **DLA Piper** (dlapiper.com) | Unknown | No | No | - | JS-rendered SPA, directory content requires interaction |
| **ICO UK** (ico.org.uk) | None detected | No | No | - | UK data protection authority — may serve banner only to EU IPs |
| **BBC** (bbc.co.uk) | None detected | No | No | - | Content extracted successfully without dismissal needed |

### Sites Blocked by WAF

| Site | Blocker | Error |
|------|---------|-------|
| **Clifford Chance** (cliffordchance.com) | Cloudflare WAF | 403 on repeat requests |
| **Hogan Lovells** (hoganlovells.com) | Cloudflare WAF | "Why have I been blocked?" |
| **Dentons** (dentons.com) | Unknown | Empty content returned |
| **Adobe** (adobe.com) | HTTP/2 protocol error | `net::ERR_HTTP2_PROTOCOL_ERROR` |

### Content Extraction Quality

| Site | `dismiss_overlays=False` | `dismiss_overlays=True` | Difference |
|------|------------------------|------------------------|------------|
| Miller Canfield `/people.html?letter=A` | 13 attorneys in HTML, readability extracts search form | 13 attorneys in HTML, readability extracts search form | Cookie dismissal works (both have content); readability quality is the separate problem |
| Freshfields `/find-a-lawyer/` | 16 lines (nav links) | 16 lines (nav links) | SPA — directory requires JS interaction beyond initial load |
| Linklaters `/find-a-lawyer` | 2 lines (heading only) | 3 lines (heading + empty search result) | Slight improvement — dismiss revealed "No results" content |
| BBC `/` | 233 lines (full homepage) | 183 lines (full homepage) | Slight variation due to page load timing |

### Key Observations

1. **Cookie banner dismissal works** when the CMP is present and visible (confirmed on Freshfields/OneTrust).
2. **CMP banners are geo-dependent** — most don't appear from US IPs, making systematic testing from US servers difficult.
3. **The bigger problem is readability quality**, not banner dismissal. On Miller Canfield, the content IS in the HTML after browser rendering, but readability picks the search form over the directory results. This motivates the parametric readability work (Level 2/3).
4. **Many enterprise law firm sites use aggressive WAFs** (Cloudflare, Akamai) that block headless browsers entirely. This is orthogonal to cookie banners.

---

## Public Datasets for Training

Datasets suitable for training a Level 3 content extraction model:

| Dataset | Size | Source | Format | Notes |
|---------|------|--------|--------|-------|
| **CleanEval** | 800 pages | 2007 shared task | HTML + gold-standard text | Classic benchmark. English web pages with human-annotated content boundaries. |
| **L3S-GN1** (Google News) | 621 pages | L3S Research Center | HTML + main text annotations | News articles from Google News. Good for article-style content. |
| **Dragnet** corpus | 1,381 pages | [dragnet GitHub](https://github.com/dragnet-org/dragnet) | HTML + Readability + gold content | Used to train the Dragnet extractor. Includes diverse page types. |
| **CETD** (Content Extraction via Tag Decomposition) | 400 pages | Microsoft Research | HTML + content labels per tag | Block-level labels, directly suitable for per-node classification. |
| **Web2Text** | 2,000+ pages | ETH Zurich | HTML + text annotations | DOM-level labels with multiple annotators. [Paper](https://dl.acm.org/doi/10.1145/3340531.3412003). |

### Bootstrap Approach

For initial training without manual labeling:

1. Run current readability on 1,000 diverse URLs (news, directories, corporate, government)
2. Use readability output as **silver labels** (imperfect but cheap)
3. Manually review and correct the worst 50-100 cases (pages where readability clearly failed)
4. Train on silver + corrected labels
5. Iterate: deploy model, log agent retries with `raw=True` as implicit failure signal, add those pages to correction set

---

## D. Response Body Capture

### Problem

SPA-style directories (DLA Piper, Linklaters) load a page shell and then populate content via `fetch`/`xhr` calls to backend APIs (Sitecore Discover, Sitecore Edge Search, internal REST endpoints). The existing request logging pipeline captured request/response **metadata** (URL, method, headers, status) but not **response bodies** — the actual structured data.

Agents needed to use `browser-evaluate` with custom DOM-parsing JavaScript to extract records, a fragile and non-composable approach.

### Implemented: Response Body Capture Pipeline

**Files:** `kaos_web/clients/browser.py`, `kaos_web/browser_tools.py`

#### Architecture

`enable_request_logging(context_id, capture_bodies=True)` installs an **async** `page.on("response")` handler that:

1. **Phase 1 (sync):** Matches response to request entry, populates status/headers
2. **Phase 2 (sync filters):** Skips 3xx redirects, checks `resource_type` against whitelist (`fetch`, `xhr` by default), checks `content-type` against whitelist (JSON, HTML, XML, text, CSV), checks `Content-Length` against size limit (1MB default)
3. **Phase 3 (async):** Calls `await response.body()` wrapped in try/except, truncates if over limit, stores in `_response_bodies[context_id][request_id]`

Playwright's `pyee.asyncio.AsyncIOEventEmitter` handles async handlers natively via `asyncio.ensure_future` — fire-and-forget, exceptions must be caught internally.

#### Hook Re-attachment on Page Replacement

Logging config is stored in `_logging_config[context_id]`. When `BrowserClient.fetch()` replaces a page in a named context, `_attach_logging_handlers()` re-attaches handlers to the new page. Logs accumulate across navigations within the same context.

#### MCP Tools

| Tool | Purpose |
|------|---------|
| `kaos-web-browser-log-requests` | Enable logging with `capture_bodies=true`, configurable `resource_types` and `max_body_size` |
| `kaos-web-browser-requests` | List all requests with `has_body` indicator and `body_size` |
| `kaos-web-browser-get-request` | Get full request/response detail with decoded body (JSON as string, binary as base64) |
| `kaos-web-browser-captured-responses` | List responses with bodies, filter by type, optionally `store_artifacts=true` to persist as session artifacts |

#### Agent Workflow

```
1. browser-navigate       url="https://firm.com"  context_id="s1"
2. browser-log-requests   context_id="s1"  capture_bodies=true
3. browser-navigate       url="https://firm.com/people"  context_id="s1"
   → hooks auto-reattach, page load triggers API calls
4. browser-requests       context_id="s1"  resource_type="fetch"
   → shows API endpoints with has_body=true
5. browser-get-request    context_id="s1"  request_id=95
   → returns decoded JSON body (e.g., 305KB people data)
6. browser-captured-responses  context_id="s1"  store_artifacts=true
   → persists all JSON responses as session artifacts
```

### Field Testing Results

| Site | Framework | People API | Records | Body Captured |
|------|-----------|-----------|---------|---------------|
| **DLA Piper** | Next.js + Sitecore Discover | `discover-euc1.sitecorecloud.io/discover/v2` | 1,826 people, 25/page | 305KB JSON |
| **Linklaters** | Next.js + Sitecore Edge Search | `edge-platform.sitecorecloud.io/v1/search` | 2,813 lawyers, 24/page | 74KB JSON |
| **Freshfields** | Next.js + server-side search | N/A (SSR, not client-side API) | Server-rendered HTML | Not applicable |

Key observations:
- Both DLA Piper and Linklaters use **Sitecore** backends, but different APIs (Discover vs Edge Search)
- Freshfields uses server-side rendering — the search navigates to `?searchText=...` rather than calling a client-side API
- Hook re-attachment works correctly — logging survives page replacement in all cases
- The 1MB default body size limit is sufficient for typical API responses (largest was 305KB)

---

## Implementation Status

| Component | Status | Files |
|-----------|--------|-------|
| Cookie banner dismissal (`browser_page_prep.py`) | **Implemented** | `kaos_web/browser_page_prep.py` |
| `dismiss_overlays` parameter on tools | **Implemented** | `kaos_web/tools.py`, `kaos_web/browser_tools.py` |
| `wait_for_selector` parameter on tools | **Implemented** | `kaos_web/tools.py` |
| Page lifecycle cleanup (`page_stored` pattern) | **Implemented** | `kaos_web/clients/browser.py` |
| `dismiss_overlays` on `browser-navigate` | **Implemented** | `kaos_web/browser_tools.py` |
| Unit tests (24 tests, 100% coverage on banner code) | **Implemented** | `tests/unit/test_browser_page_prep.py` |
| Level 3 learned readability (`content_scope`) | **Implemented** | `kaos_web/extract/readability_l3.py` |
| `content_scope` parameter on tools | **Implemented** | `kaos_web/tools.py` (get-markdown, get-text, fetch-page, search-page) |
| L3 integrated into `html_to_document` | **Implemented** | `kaos_web/extract/html_to_ast.py` |
| Experiment harness and 10-page corpus | **Implemented** | `kaos_web/extract/readability_experiments.py`, `tests/fixtures/readability/corpus.json` |
| Level 2 parametric readability (skipped) | **Superseded by L3** | — |
| Content settling (`wait_for_settled`) | **Implemented** | `kaos_web/browser_page_prep.py`, `kaos_web/clients/browser.py` |
| Response body capture (`capture_bodies`) | **Implemented** | `kaos_web/clients/browser.py` |
| Logging hook re-attachment on page replacement | **Implemented** | `kaos_web/clients/browser.py` |
| `kaos-web-browser-captured-responses` tool | **Implemented** | `kaos_web/browser_tools.py` |
| Artifact storage for captured responses | **Implemented** | `kaos_web/browser_tools.py` |
| Response capture tests (36 tests) | **Implemented** | `tests/unit/test_response_capture.py` |
| Structured record extraction (`extract-records` tool) | **Deferred** | Building blocks sufficient; agents compose the 4-step workflow |
