# kaos-web Design Document

**Date**: 2026-04-01
**Status**: Draft
**Depends on**: kaos-core, kaos-content

## Overview

kaos-web provides web content extraction for the KAOS platform. It fetches HTML from URLs
(via HTTP or browser rendering), extracts main content, and produces `ContentDocument` AST
with provenance — the same model kaos-pdf produces. This means DocumentView, BM25 search,
MCP resource templates, and all downstream tooling work automatically.

## Design Principles

- **Minimal dependencies**: 3 required deps (httpx, lxml, html-to-markdown). No trafilatura,
  no readability-lxml, no extruct. Core extraction algorithms are implemented in-house for
  full control and a lean dependency tree.
- **ContentDocument output**: All extraction paths produce `kaos-content` AST. Agents interact
  with web content through the same DocumentView/block_ref/search APIs as PDF content.
- **Dual client**: HTTP (httpx) for static pages, Playwright (optional) for JS-rendered pages.
  Both behind a common protocol.
- **Async-first**: All client operations are async. Sync wrappers for CLI.
- **AST-grounded**: Operations reference the content model, not raw text offsets.

## Architecture

```
kaos_web/
├── __init__.py
├── __main__.py
├── cli.py                    # CLI: fetch, extract, search, metadata
├── _version.py
│
├── clients/                  # Web client layer
│   ├── __init__.py
│   ├── protocol.py           # WebClientProtocol (abstract)
│   ├── http.py               # HttpClient (httpx-based)
│   └── browser.py            # BrowserClient (playwright, optional)
│
├── models/                   # Request/response/metadata models
│   ├── __init__.py
│   ├── request.py            # WebRequest
│   ├── response.py           # WebResponse
│   └── metadata.py           # PageMetadata (OG, JSON-LD, etc.)
│
├── extract/                  # HTML → ContentDocument
│   ├── __init__.py
│   ├── readability.py        # Main content extraction (readability algorithm)
│   ├── html_to_ast.py        # HTML elements → Block/Inline AST nodes
│   ├── metadata.py           # JSON-LD + OpenGraph + meta tag extraction
│   └── links.py              # Link extraction with context
│
├── middleware/                # Composable request/response processing
│   ├── __init__.py
│   ├── base.py               # Middleware protocol
│   ├── cache.py              # HTTP caching (disk + memory)
│   ├── retry.py              # Retry with exponential backoff
│   ├── rate_limit.py         # Per-domain rate limiting
│   └── robots.py             # robots.txt checking (stdlib-based)
│
└── tools.py                  # MCP tools (registered with KaosRuntime)
```

## Dependencies

### Required

| Package | Version | License | Purpose |
|---------|---------|---------|---------|
| `kaos-core` | >=0.1.0 | Proprietary | Runtime, tools, artifacts |
| `kaos-content` | >=0.1.0 | Proprietary | ContentDocument AST |
| `httpx[http2]` | >=0.28 | BSD-3 | HTTP client with HTTP/2, connection pooling |
| `lxml` | >=5.0 | BSD | HTML parsing (already in kaos-content) |
| `html-to-markdown` | >=2.28 | MIT | HTML→Markdown conversion |

### Optional

| Package | Extra | License | Purpose |
|---------|-------|---------|---------|
| `playwright` | `[browser]` | Apache 2.0 | JS rendering, browser automation |

### Explicitly NOT Used

| Library | Reason |
|---------|--------|
| `trafilatura` | 15 transitive deps, 12K lines. We implement readability-style extraction in ~400 lines |
| `readability-lxml` | Core algorithm is ~300 lines, easy to reimplement. Drops chardet + cssselect deps |
| `extruct` | 20 transitive deps (rdflib, pyrdfa3, beautifulsoup4). JSON-LD + OpenGraph extraction is ~50 lines with lxml |
| `beautifulsoup4` | lxml handles all parsing needs. BS4 adds complexity without benefit |
| `html2text` | GPLv3 — license incompatible |
| `protego` | stdlib `urllib.robotparser` sufficient. Add wildcard matching later if needed |

---

## Core Components

### 1. Web Clients

#### WebClientProtocol

```python
class WebClientProtocol(Protocol):
    async def fetch(self, request: WebRequest) -> WebResponse: ...
    async def close(self) -> None: ...
```

Both `HttpClient` and `BrowserClient` implement this. Tools and extractors work with either
client through the protocol.

#### HttpClient

Wraps `httpx.AsyncClient` with:
- HTTP/2 support via `httpx[http2]`
- Connection pooling (reuse one client across requests)
- Configurable timeouts, redirects, headers
- User-agent: `KAOS-Web/0.1 (+https://273ventures.com/kaos-web)`

#### BrowserClient (optional, requires `[browser]` extra)

Wraps Playwright's async API:
- Headless Chromium by default
- Waits for network idle / DOM ready before extraction
- Screenshot support (returns `KaosImage` with provenance)
- Cookie and authentication support
- Lazy-imports playwright (fast `--help`, fails only when browser features used)

### 2. Content Extraction

#### Readability Algorithm (`extract/readability.py`)

Reimplementation of Mozilla's Readability.js (~300-400 lines with lxml):

1. **Remove unlikely candidates**: Drop elements whose class/id matches
   `comment|sidebar|footer|nav|ad|sponsor` (unless also matching `article|body|content`)
2. **Transform misused divs**: Divs without block children → `<p>`
3. **Score paragraphs**: Base score + comma count + length bonus. Parent gets full score,
   grandparent gets half. Apply class/tag weight modifiers.
4. **Select best candidate**: Highest-scored element + qualifying siblings
5. **Sanitize**: Remove low-scoring elements, high link-density blocks

Output: cleaned HTML subtree of the main content.

#### HTML → ContentDocument AST (`extract/html_to_ast.py`)

Converts cleaned HTML into `kaos-content` Block/Inline nodes:

| HTML | AST Node | Provenance |
|------|----------|-----------|
| `<h1>`-`<h6>` | `Heading(depth=N)` | URL + CSS selector path |
| `<p>` | `Paragraph` | URL + selector |
| `<ul>/<ol>` | `BulletList`/`OrderedList` | URL + selector |
| `<blockquote>` | `BlockQuote` | URL + selector |
| `<pre><code>` | `CodeBlock` | URL + selector |
| `<table>` | `Table` | URL + selector |
| `<img>` | `Image` (inline) or `Figure` (block) | URL + src |
| `<a>` | `Link` | URL + href |
| `<strong>/<b>` | `Strong` | — |
| `<em>/<i>` | `Emphasis` | — |
| `<code>` | `Code` | — |

Provenance on every node includes:
- `source`: URL of the page
- `extractor`: `"kaos-web/readability"` or `"kaos-web/browser"`
- `confidence`: 1.0 for direct HTML mapping, lower for heuristic extraction

#### Metadata Extraction (`extract/metadata.py`)

~50 lines with lxml. No external dependencies.

**JSON-LD** (~15 lines):
```python
for script in tree.xpath('//script[@type="application/ld+json"]'):
    data = json.loads(script.text_content())
    # Normalize to list, extract @type, name, author, datePublished, etc.
```

**OpenGraph** (~15 lines):
```python
for meta in tree.xpath('//meta[starts-with(@property, "og:")]'):
    og[meta.get("property")] = meta.get("content")
```

**Standard meta tags** (~20 lines):
- `<title>`, `<meta name="description">`, `<meta name="author">`
- `<meta name="keywords">`, `<link rel="canonical">`
- `<html lang="...">`

Result: `PageMetadata` model with title, author, date, description, url, language,
site_name, image, structured_data (JSON-LD dict).

### 3. Middleware

Composable chain following kelvin-web's proven pattern:

```python
class Middleware(Protocol):
    async def process(self, request: WebRequest, next: Callable) -> WebResponse: ...
```

**RobotsMiddleware**: Check `urllib.robotparser` before fetching. Cache parsed robots.txt
per domain.

**RetryMiddleware**: Exponential backoff on 429/503/5xx. Configurable max retries.

**RateLimitMiddleware**: Per-domain token bucket. Default 1 req/sec. Respects
`Crawl-delay` from robots.txt.

**CacheMiddleware**: Disk-based HTTP cache. Respects Cache-Control headers.
Key = (method, url, params). Configurable TTL and max size.

### 4. MCP Tools

5 tools, following `docs/TOOL_DESIGN_GUIDE.md`:

| Tool | Name | Description |
|------|------|-------------|
| **FetchPage** | `kaos-web-fetch-page` | Fetch URL → ContentDocument artifact. Uses HTTP by default, browser if `use_browser=true`. Returns summary + artifact link. |
| **GetPageText** | `kaos-web-get-text` | Fetch URL → plain text (no artifact storage needed). Lightweight. |
| **GetPageMarkdown** | `kaos-web-get-markdown` | Fetch URL → markdown. Uses readability + html-to-markdown. |
| **GetPageMetadata** | `kaos-web-get-metadata` | Extract JSON-LD, OpenGraph, meta tags from URL. No content extraction. |
| **SearchPage** | `kaos-web-search-page` | Fetch + extract + BM25 search within the page. Returns matching sentences/paragraphs with block_refs. |

All tools:
- Set `ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=True)`
  (openWorld=True because they make HTTP requests)
- Return agent-friendly error messages with recovery guidance
- Use artifact tiering for large pages

### 5. CLI

```
kaos-web fetch URL [--browser] [--format markdown|text|json|html] [--output FILE]
kaos-web search URL QUERY [--top-k 10] [--level paragraph|sentence]
kaos-web metadata URL [--json]
kaos-web extract URL [--output FILE] [--json]
```

Follows `docs/CLI_STANDARD.md`: `--json` envelope, errors to stderr, `main(argv)` signature.

---

## Content Model Integration

The key architectural decision: web extraction produces the **same ContentDocument AST** as
PDF extraction. This means:

1. **DocumentView works**: pages (none for web), sections (from headings), paragraphs, sentences
2. **BM25 search works**: `search_document(doc, query, level="sentence")` — same API
3. **MCP resources work**: All 12 content resource templates apply
4. **Annotations work**: NLP entity detection, defined terms, citations
5. **Serializers work**: markdown, HTML, text output from the AST

Provenance differs from PDF (no page numbers, no bounding boxes) but the `source` field
carries the URL and the `extractor` field identifies `kaos-web`.

---

## Implementation Plan

### Phase 1: HTTP + Readability Extraction
- HttpClient with httpx
- Readability algorithm (~400 lines with lxml)
- HTML → ContentDocument AST conversion
- Metadata extraction (JSON-LD + OpenGraph + meta tags)
- Basic middleware (retry, robots.txt)
- Tests with saved HTML fixtures (no network in tests)

### Phase 2: MCP Tools + CLI
- 5 MCP tools with annotations and error messages
- CLI with 4 commands
- Integration tests with kaos-mcp

### Phase 3: Browser Support
- BrowserClient with playwright (optional extra)
- Screenshot → KaosImage
- JS-rendered content extraction
- Wait strategies (network idle, selector visible)

### Phase 4: Middleware + Polish
- Rate limiting (per-domain)
- Disk caching with TTL
- Link extraction with context
- Table extraction

---

## Prior Art

kelvin-web (`../kelvin-modules/kelvin_web/`) was the prior implementation. Key patterns
carried forward:
- Dual-client protocol (HTTP + Browser)
- Middleware composition chain
- Request/Response models with metadata

Key differences:
- Output is ContentDocument AST (not custom ExtractedContent)
- No trafilatura/readability-lxml dependency (in-house readability)
- No extruct dependency (in-house JSON-LD + OpenGraph)
- Fewer extractors (core types only, add domain-specific later)
- Integrates with kaos-source connectors for discovery

---

## References

- Mozilla Readability.js: https://github.com/mozilla/readability
- readability-lxml (algorithm reference): https://github.com/buriy/python-readability
- html-to-markdown: https://pypi.org/project/html-to-markdown/
- httpx: https://www.python-httpx.org/
- Playwright Python: https://playwright.dev/python/
- kelvin-web: `../kelvin-modules/kelvin_web/`
- `docs/TOOL_DESIGN_GUIDE.md`: MCP tool design patterns
- `docs/AGENTIC_MCP_ASSESSMENT.md`: MCP best practices scorecard
