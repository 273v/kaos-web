# kaos-web Product Requirements Document

**Date**: 2026-04-01
**Version**: 0.2
**Status**: Phase 1 complete, Phases 2-4 in progress

## Overview

kaos-web is the web content extraction module for KAOS. It fetches HTML from URLs
(via HTTP or browser rendering), extracts main content, and produces `ContentDocument`
AST with provenance — the same model kaos-pdf produces. This enables DocumentView,
BM25 search, MCP resource templates, and all downstream KAOS tooling to work with
web content automatically.

### Prior Art

- **kelvin-web** (`../kelvin-modules/kelvin_web/`): Dual-client (httpx + Playwright),
  middleware chain, 17 extractors (200K+ lines), trafilatura-based. Over-engineered
  extractors but solid HTTP/browser infrastructure.
- **kelvin-source HttpSource/HttpBrowserSource**: Sync/async HTTP with user-agent
  rotation, retry hooks, proxy support, Playwright with device emulation and
  network throttling.

### Key Differences from Kelvin

| Aspect | kelvin-web | kaos-web |
|--------|-----------|---------|
| Output | Custom `ExtractedContent` dict | `ContentDocument` AST with provenance |
| Extraction | trafilatura (15 transitive deps) | In-house readability + HTML-to-AST (0 extra deps) |
| Domain extractors | 17 specialized (200K lines) | Core only — defer domain-specific |
| Architecture | Standalone | Integrated with kaos-content, kaos-core, kaos-mcp |
| Performance | Not benchmarked | 1.7-2.2x faster than alternatives (benchmarked) |

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     MCP Tools (5)                        │
│  FetchPage · GetText · GetMarkdown · Metadata · Search   │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│                  Extraction Pipeline                     │
│  readability.py → html_to_ast.py → ContentDocument AST   │
│  metadata.py → PageMetadata                              │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│              Middleware Chain                             │
│  Retry → RateLimit → Robots → Cache → Handler            │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│                    Clients                                │
│  HttpClient (httpx)  ·  BrowserClient (Playwright)       │
└─────────────────────────────────────────────────────────┘
```

---

## Dependencies

### Required

| Package | License | Purpose |
|---------|---------|---------|
| `kaos-core` | Proprietary | Runtime, tools, artifacts |
| `kaos-content` | Proprietary | ContentDocument AST, serializers |
| `httpx[http2]` | BSD-3 | HTTP client |
| `lxml` | BSD | HTML parsing (transitive via kaos-content) |

### Optional Extras

| Extra | Package | License | Purpose |
|-------|---------|---------|---------|
| `[browser]` | `playwright` | Apache 2.0 | JS rendering, screenshots |
| `[mcp]` | `kaos-mcp` | Proprietary | MCP server integration |
| `[nlp]` | `kaos-nlp-core` | Proprietary | BM25 search in extracted content |

### Explicitly Excluded

| Package | Reason |
|---------|--------|
| trafilatura | 15 transitive deps — extraction reimplemented in-house |
| readability-lxml | Reimplemented (~350 lines) |
| html2text | GPLv3 |
| beautifulsoup4 | lxml sufficient |
| extruct | 20 transitive deps — JSON-LD/OG extraction reimplemented (~50 lines) |

---

## Component Specifications

### 1. HTTP Client

The `HttpClient` wraps `httpx.AsyncClient` for all non-browser web fetching.

#### Configuration

```python
class HttpClientConfig(BaseModel):
    """HTTP client configuration."""

    # Connection management
    max_connections: int = 100
    max_keepalive_connections: int = 20
    keepalive_expiry: float = 30.0

    # Timeouts (seconds)
    connect_timeout: float = 10.0
    read_timeout: float = 30.0
    write_timeout: float = 10.0
    pool_timeout: float = 5.0

    # TLS
    verify_ssl: bool = True
    ca_bundle: str | None = None          # Path to CA bundle or SSLContext
    client_cert: str | None = None        # Path to client certificate
    client_key: str | None = None         # Path to client key

    # Proxy
    proxy: str | None = None              # http://proxy:8080 or socks5://proxy:1080
    proxy_auth: tuple[str, str] | None = None

    # Behavior
    follow_redirects: bool = True
    max_redirects: int = 10
    user_agent: str = "KAOS-Web/0.1 (+https://273ventures.com/kaos-web)"

    # Authentication
    auth: tuple[str, str] | None = None   # (username, password) for Basic auth
    bearer_token: str | None = None
    api_key: str | None = None
    api_key_header: str = "X-API-Key"
```

#### Interface

```python
class HttpClient:
    def __init__(self, config: HttpClientConfig | None = None) -> None: ...
    async def fetch(self, request: WebRequest) -> WebResponse: ...
    async def close(self) -> None: ...

    # Context manager
    async def __aenter__(self) -> Self: ...
    async def __aexit__(self, ...) -> None: ...
```

#### Error Handling

Map httpx exceptions to KAOS types:

| httpx Exception | KAOS Error | Retry? |
|----------------|-----------|--------|
| `ConnectTimeout` | `WebTimeoutError("connect")` | Yes |
| `ReadTimeout` | `WebTimeoutError("read")` | Yes |
| `PoolTimeout` | `WebTimeoutError("pool")` | Yes |
| `ConnectError` | `WebNetworkError` | Yes |
| `HTTPStatusError(429)` | `WebRateLimitError` | Yes (with Retry-After) |
| `HTTPStatusError(5xx)` | `WebServerError` | Yes |
| `HTTPStatusError(4xx)` | `WebClientError` | No |
| `ProxyError` | `WebProxyError` | No |
| `TooManyRedirects` | `WebRedirectError` | No |

All errors subclass `WebError(KaosCoreError)` with structured details:
```python
class WebError(Exception):
    url: str
    status_code: int | None
    message: str
    retryable: bool
```

#### Implementation Notes

- **One long-lived client**: Create in `__init__`, reuse for all requests.
  Never create per-request.
- **Connection pooling**: Use `httpx.Limits(max_connections, max_keepalive_connections,
  keepalive_expiry)`. Defaults handle 100 concurrent connections.
- **HTTP/2**: Enabled via `httpx[http2]`. Automatic protocol negotiation.
- **Cookie jar**: httpx automatically persists cookies across requests on the
  same client instance. No separate cookie management needed.
- **Streaming**: Not exposed in Phase 1. Add `stream=True` support in Phase 4
  for large downloads.

---

### 2. Browser Client

The `BrowserClient` wraps Playwright's async API for JavaScript-rendered pages.
Optional — requires `pip install kaos-web[browser]`.

#### Configuration

```python
class BrowserClientConfig(BaseModel):
    """Browser client configuration."""

    # Browser
    browser_type: Literal["chromium", "firefox", "webkit"] = "chromium"
    headless: bool = True
    channel: str | None = None             # "chrome" to use installed Chrome

    # Viewport
    viewport_width: int = 1280
    viewport_height: int = 720
    device_scale_factor: float = 1.0
    is_mobile: bool = False

    # Navigation
    default_wait_until: Literal["load", "domcontentloaded", "networkidle", "commit"] = "load"
    navigation_timeout: int = 30000        # ms
    default_timeout: int = 30000           # ms

    # Network
    block_resources: list[str] = []        # Resource types to block: "image", "stylesheet", "font"
    proxy: str | None = None
    ignore_https_errors: bool = False
    extra_headers: dict[str, str] = {}

    # Auth
    storage_state: str | None = None       # Path to saved auth state JSON
    http_credentials: tuple[str, str] | None = None

    # Context
    user_agent: str | None = None
    locale: str | None = None
    timezone: str | None = None
    color_scheme: Literal["light", "dark"] | None = None
```

#### Interface

```python
class BrowserClient:
    def __init__(self, config: BrowserClientConfig | None = None) -> None: ...

    async def fetch(self, request: WebRequest) -> WebResponse: ...
    async def screenshot(
        self, url: str, *, full_page: bool = True, format: str = "png",
    ) -> bytes: ...
    async def evaluate(self, url: str, expression: str) -> Any: ...
    async def close(self) -> None: ...

    # Context manager
    async def __aenter__(self) -> Self: ...
    async def __aexit__(self, ...) -> None: ...
```

#### Implementation Notes

- **One browser per client**: Launch in first `fetch()` (lazy), reuse across requests.
  Browser launch is ~200-500ms; context creation is <10ms.
- **Context per request**: Each `fetch()` creates a new `BrowserContext` for isolation
  (separate cookies, storage). Close context after extraction.
- **Resource blocking**: Block images/fonts/CSS by default for faster extraction.
  Configurable via `block_resources`.
- **Wait strategy**: Default `wait_until="load"`. For SPAs, caller can set
  `wait_until="networkidle"` or provide `wait_for_selector`.
- **Only GET/HEAD**: Browser client doesn't support POST/PUT/etc.
  Use HttpClient for API calls.
- **Screenshots**: Return `bytes` (PNG/JPEG). Caller wraps in `KaosImage` if
  needed for artifact storage.
- **Error handling**: Playwright has minimal exception hierarchy. Classify by
  error message: "timeout" → `WebTimeoutError`, "net::" → `WebNetworkError`,
  else → `WebBrowserError`.

---

### 3. Middleware Chain

Composable request/response processing pipeline. Each middleware wraps the next
handler, enabling pre/post processing.

#### Protocol

```python
class Middleware(Protocol):
    async def process(
        self, request: WebRequest, next_handler: Callable
    ) -> WebResponse: ...

class MiddlewareChain:
    def __init__(self, handler: Callable) -> None: ...
    def add(self, middleware: Middleware) -> MiddlewareChain: ...
    async def execute(self, request: WebRequest) -> WebResponse: ...
```

#### Built-in Middleware

##### RetryMiddleware

```python
class RetryConfig(BaseModel):
    max_retries: int = 3
    initial_delay: float = 1.0            # seconds
    max_delay: float = 60.0               # seconds
    exponential_base: float = 2.0
    jitter: bool = True                   # ±50% randomization
    retry_on_status: set[int] = {429, 500, 502, 503, 504}
    respect_retry_after: bool = True      # Honor Retry-After header
```

Backoff: `delay = min(initial * base^attempt, max) * random(0.5, 1.0)`

##### RateLimitMiddleware

```python
class RateLimitConfig(BaseModel):
    requests_per_second: float = 10.0
    burst_size: int | None = None         # Defaults to int(requests_per_second)
    per_host: bool = True                 # Per-domain limiting
```

Token bucket algorithm. Per-host tracking by default (different domains get separate
buckets). Blocks until token available.

##### RobotsMiddleware

```python
class RobotsConfig(BaseModel):
    enabled: bool = True
    user_agent: str = "KAOS-Web"
    cache_ttl: int = 3600                 # Cache robots.txt for 1 hour
```

Uses `urllib.robotparser.RobotFileParser` from stdlib. Caches parsed robots.txt
per domain. Respects `Crawl-delay` directive as minimum delay.

##### CacheMiddleware

```python
class CacheConfig(BaseModel):
    enabled: bool = True
    backend: Literal["memory", "disk"] = "memory"
    max_entries: int = 1000               # Memory backend
    max_bytes: int = 104_857_600          # 100 MB
    default_ttl: int = 300                # 5 minutes
    respect_cache_control: bool = True    # Honor HTTP Cache-Control
    cache_dir: str | None = None          # Disk backend path
```

RFC 7231 compliant: respects `Cache-Control`, `max-age`, `no-store`, `no-cache`.
Only caches GET/HEAD with cacheable status codes (200, 301, 404, etc.).
Cache key: blake2b hash of (method, url, params).

---

### 4. Request/Response Models

#### WebRequest

```python
class WebRequest(BaseModel):
    url: str
    method: str = "GET"
    headers: dict[str, str] = {}
    timeout: float = 30.0
    follow_redirects: bool = True

    # Browser-specific (ignored by HttpClient)
    use_browser: bool = False
    wait_until: str | None = None         # "load", "domcontentloaded", "networkidle"
    wait_for_selector: str | None = None
    screenshot: bool = False

    # Extra options for client-specific features
    extra: dict[str, Any] = {}
```

#### WebResponse

```python
class WebResponse(BaseModel):
    url: str                              # Final URL after redirects
    status_code: int
    content_type: str = ""
    html: str = ""
    headers: dict[str, str] = {}
    elapsed_ms: float = 0.0

    # Browser-specific (None for HttpClient)
    title: str | None = None
    screenshot: bytes | None = None

    # Cookie jar snapshot
    cookies: dict[str, str] = {}

    # Error info
    error: str | None = None

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 400
```

#### PageMetadata

```python
class PageMetadata(BaseModel):
    title: str | None = None
    author: str | None = None
    description: str | None = None
    date_published: str | None = None
    date_modified: str | None = None
    url: str | None = None
    language: str | None = None
    site_name: str | None = None
    image: str | None = None
    structured_data: list[dict] = []      # JSON-LD
    opengraph: dict[str, str] = {}
```

---

### 5. MCP Tools

Five tools following `docs/TOOL_DESIGN_GUIDE.md`.

| Tool | Name | Input | Output | Annotations |
|------|------|-------|--------|-------------|
| FetchPage | `kaos-web-fetch-page` | url, use_browser | ContentDocument artifact | readOnly, idempotent, openWorld |
| GetPageText | `kaos-web-get-text` | url | Plain text string | readOnly, idempotent, openWorld |
| GetPageMarkdown | `kaos-web-get-markdown` | url | Markdown string | readOnly, idempotent, openWorld |
| GetPageMetadata | `kaos-web-get-metadata` | url | PageMetadata dict | readOnly, idempotent, openWorld |
| SearchPage | `kaos-web-search-page` | url, query, level, top_k | SearchResults | readOnly, idempotent, openWorld |

All tools: `openWorldHint=True` (makes HTTP requests), `destructiveHint=False`.

**KAOS conventions** (from top-level CLAUDE.md):
- Tool names match `^[a-z0-9]+(?:-[a-z0-9]+){2,}$` — all 5 names comply.
- Error messages follow the three-part rule: what went wrong + how to fix it +
  alternative approach.
- `FetchPage` uses `ArtifactManifest.to_tool_result()` for automatic inline/summary/link
  tiering (16 KB / 256 KB thresholds).
- `SearchPage` searches per-paragraph via DocumentView (AST-grounded), not on raw
  serialized text. Results carry `block_ref` from the AST.
- Tools pass `KaosContext` through for `session_id`/`trace_id` logging.
- Flat inputs with `ParameterSchema` and `constraints` for enums.

---

### 6. CLI

```
kaos-web fetch URL [--browser] [--output FILE] [--json]
kaos-web extract URL [--format markdown|text|json] [--no-readability] [--output FILE] [--json]
kaos-web search URL QUERY [--top-k 10] [--level paragraph|sentence] [--json]
kaos-web metadata URL [--json]
```

Follows `docs/CLI_STANDARD.md`:
- `--json` envelope: `{"command": "...", "url": "...", ...}`
- Human-readable output by default, JSON for piping/agents
- Errors to stderr with non-zero exit, output to stdout
- `main(argv: list[str] | None = None)` signature for testability
- Lazy-import heavy deps inside command handlers (fast `--help`)

---

## Implementation Phases

### Phase 1: Core Extraction (COMPLETE)

- [x] Readability algorithm (349 lines)
- [x] HTML-to-AST conversion (1219 lines, 20+ element types)
- [x] Metadata extraction (JSON-LD, OpenGraph, meta tags)
- [x] Minimal HttpClient (httpx wrapper)
- [x] CLI: extract, metadata commands
- [x] 110 tests (95 quality + 15 benchmarks)
- [x] Performance: 1.7-2.2x faster than alternatives
- [x] Quality: Grade A across 17 categories

### Phase 2: HTTP Hardening + MCP Tools

- [ ] HttpClient with full configuration (connection pooling, timeouts, auth, SSL, proxy)
- [ ] Error types (WebError hierarchy)
- [ ] RetryMiddleware with exponential backoff
- [ ] RateLimitMiddleware with per-domain token bucket
- [ ] RobotsMiddleware with stdlib robotparser
- [ ] Middleware chain composition
- [ ] 5 MCP tools (FetchPage, GetText, GetMarkdown, Metadata, Search)
- [ ] CLI: fetch, search commands
- [ ] HTTP client tests (mocked with pytest-httpx)
- [ ] MCP integration tests

### Phase 3: Browser Client

- [ ] BrowserClient with Playwright (chromium, firefox, webkit)
- [ ] Lazy browser launch, context-per-request isolation
- [ ] Resource blocking (images, fonts, CSS)
- [ ] Wait strategies (load, domcontentloaded, networkidle, selector)
- [ ] Screenshot support → KaosImage
- [ ] Authentication state persistence (storage_state)
- [ ] Browser client tests
- [ ] CLI: `--browser` flag integration

### Phase 4: Caching + Polish

- [ ] CacheMiddleware (memory + disk backends, RFC 7231 compliant)
- [ ] Cache key generation (blake2b of method + url + params)
- [ ] LRU eviction with configurable size limits
- [ ] Background pruning of expired entries
- [ ] Streaming response support for large downloads
- [ ] Link extraction with context (standalone extractor)
- [ ] kaos-source connector integration (HttpConnector, BrowserConnector)

---

## Performance Requirements

| Metric | Target | Current |
|--------|--------|---------|
| Article extraction latency | < 2 ms | 0.95 ms |
| Medium page extraction | < 10 ms | 5.9 ms |
| Large page extraction | < 100 ms | 64.9 ms |
| Throughput | > 2 MB/s | 2.1-3.5 MB/s |
| vs markdownify | >= 1x | 1.2-2.2x faster |
| vs trafilatura | >= 1x | 1.2-1.9x faster |
| Memory per extraction | < 10 MB | ~2-5 MB (estimated) |
| Browser launch | < 1s | Not measured (Phase 3) |
| Browser extraction | < 5s typical | Not measured (Phase 3) |

---

## Testing Requirements

| Category | Count | Status |
|----------|-------|--------|
| Readability | 6 | Complete |
| HTML-to-AST | 29 | Complete |
| Metadata | 13 | Complete |
| Edge cases | 48 | Complete |
| Benchmarks | 15 | Complete |
| HTTP client | 0 | Phase 2 |
| Middleware | 0 | Phase 2 |
| MCP tools | 0 | Phase 2 |
| Browser client | 0 | Phase 3 |
| CLI | 0 | Phase 2 |
| Integration (E2E) | 0 | Phase 2 |
| **Total** | **111** | **Phase 1 complete** |

Target: 200+ tests by Phase 4 completion.

---

## References

### Upstream Libraries
- [httpx documentation](https://www.python-httpx.org/) — AsyncClient, Limits, Timeout, auth, proxy
- [Playwright Python](https://playwright.dev/python/) — Browser, Context, Page, screenshots
- [Chrome DevTools MCP](https://github.com/anthropics/anthropic-quickstarts) — CDP tools reference

### Internal
- `docs/DESIGN.md` — Original architecture design
- `docs/HTML_TO_AST_REFERENCE.md` — Edge case patterns from 9 reference libraries
- `docs/QUALITY.md` — Quality report with benchmarks
- `docs/TOOL_DESIGN_GUIDE.md` — MCP tool design patterns (top-level)
- `docs/AGENTIC_MCP_ASSESSMENT.md` — MCP best practices scorecard (top-level)

### Prior Art
- `../kelvin-modules/kelvin_web/` — kelvin-web HTTP/browser/middleware/cache
- `../kelvin-modules/kelvin_source/` — kelvin-source HttpSource/HttpBrowserSource
