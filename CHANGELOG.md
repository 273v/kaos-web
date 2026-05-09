# Changelog

All notable changes to `kaos-web` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security

- **URL filter regexes now use the Rust regex engine when available**
  (WEB5-008). `kaos_web.discover.discovery._compile_patterns` previously
  built `re.compile(...)` patterns from caller-supplied include /
  exclude regex strings and applied `pattern.search(...)` to every
  discovered URL path. Stdlib `re` is a backtracking engine —
  pathological patterns like `(a+)+b` against `"a" * N` run in
  exponential time and block the asyncio event loop (ReDoS).
  Compiled patterns now route through a `_SafePattern` shim that
  prefers `kaos_nlp_core.matching.RegexMatcher` (Rust regex, linear
  time, no backtracking) when the `[nlp]` optional extra is installed,
  with stdlib `re` as a fallback (one-shot warning logged so operators
  see the path). Install `kaos-web[nlp]` to get the protection by
  default.

- **CacheMiddleware bypasses any request bearing auth-shaped headers**
  (WEB5-009). Cache key is `method:url` only — without this gate, an
  authenticated request would either return another caller's cached
  anonymous response (read-leak) or poison the cache for subsequent
  anonymous callers (write-leak). The bypass is conservative: if the
  request includes any of `Authorization`, `Proxy-Authorization`,
  `Cookie`, `X-API-Key`, `X-Auth-Token`, `X-CSRF-Token`
  (case-insensitive), the cache is skipped entirely (no LOOKUP, no
  STORE) and the request always hits upstream. Anonymous requests
  still benefit from the cache normally.

- **TLS verification on domain-intelligence probes now defaults to ON**
  (WEB5-006). The two probes that explicitly disable verification
  (`kaos-web-http-headers`, `kaos-web-extract-org`) previously
  defaulted to `KAOS_WEB_DOMAIN_VERIFY_TLS=false` (audit-02 WEB2-001
  shipped the setting with that default; WEB5-006 flips it to `true`).
  Secure-by-default: the typical use case is observing healthy public
  sites where CA validation is correct. Set
  `KAOS_WEB_DOMAIN_VERIFY_TLS=false` to inspect hosts whose cert is
  itself the subject of inspection (self-signed, expired, mismatched
  SAN, staging environments). **Migration**: anyone scraping such
  hosts will see new TLS errors; explicitly opt out via the env var.
  Content-extraction tools (`HttpClient` / `BrowserClient`) keep TLS
  verification on independently of this flag.

- **Browser interaction tools now declare `destructiveHint=True`**
  (WEB5-005). Click / fill / type / press / select / evaluate run
  inside an authenticated browser session and CAN trigger real actions
  (form submit, settings change, JS-driven side effects). The prior
  shared `_BROWSER_WRITE_ANNOTATIONS` annotation said
  `destructiveHint=False` for all of them, which weakened any MCP
  client that gates auto-approval on the annotation. Split into
  `_BROWSER_INTERACT_ANNOTATIONS` (destructive=True for the 6
  interaction tools) and the existing `_BROWSER_WRITE_ANNOTATIONS`
  (destructive=False for local-state tools that do not trigger remote
  actions: set-cookie, save-auth-state, enable-request-logging,
  close-context, navigate). No behavior change — annotation
  correctness only.

### Changed

- **Refactored package layout** for better cohesion (per
  `docs/python/design/modules.md`):
  - New `kaos_web.discover` subpackage groups the BFS-discovery
    subsystem: `batch`, `crawl`, `discovery`, `sitemap` (formerly four
    top-level modules with mutual imports). Re-exports the canonical
    public API at the package level (e.g. `from kaos_web.discover
    import batch_fetch, crawl_site, discover_urls, parse_sitemap`).
  - `kaos_web.browser_page_prep` → `kaos_web.clients.page_prep`. Only
    consumer was `clients/browser.py`; the helper is logically a
    browser-client primitive.
  - The four `*_tools.py` files (`tools.py`, `browser_tools.py`,
    `crawl_tools.py`, `domain_tools.py`) **stay top-level** per the
    explicit KAOS convention documented in
    `docs/python/design/modules.md` ("split tool files by domain when
    they would otherwise exceed ~1500 lines"). No tools/ subpackage.

  Pre-0.1.0a1 — no published version pins these import paths, so no
  back-compat shims are shipped. If you imported from these paths in
  pre-release builds, update to the new locations.

## [0.1.0a1] — 2026-05-08

First public alpha release.

### Added

- Web content extraction for the KAOS (Kelvin Agentic Operating System)
  platform. Fetches HTML from URLs over HTTP or a headless browser and
  produces `kaos-content` `ContentDocument` AST with provenance on every
  block.
- **Dual-client architecture**: `HttpClient` (httpx, async, HTTP/2,
  connection pooling, auth, SSL, proxy, structured error mapping) and
  `BrowserClient` (Playwright, lazy launch, named-context page tracking,
  cookie-banner dismissal for 8 known consent-management platforms).
- **HTML-to-AST extraction** (`html_to_document`): lxml element tree to
  Block/Inline `ContentDocument` with `SourceRef` + `Provenance` on
  every node. Supports headings, paragraphs, lists, blockquotes, code
  blocks, tables, figures, definition lists, thematic breaks, and the
  full inline grammar (text, strong, emphasis, code, link, image,
  strikethrough, sub/superscript, line break).
- **Level-3 learned readability** (`extract.readability_l3`): pre-trained
  logistic regression over 35 DOM-node features. Default extractor with
  a `content_scope` parameter (0.0–1.0) controlling
  precision/recall tradeoff. Heuristic readability and semantic
  container detection (`<main>` → `<article>` → `[role=main]` → `<body>`)
  remain as fallbacks when the L3 extraction returns `< 50` words.
- **Composable middleware chain** (`middleware/`): `RetryMiddleware`
  (exponential backoff with jitter, honors `Retry-After`),
  `RateLimitMiddleware` (per-domain token bucket), `RobotsMiddleware`
  (stdlib `robotparser`, cached per domain), `CacheMiddleware`
  (in-memory LRU, RFC 7231 compliant). Wired into `HttpClient.fetch()`
  via `MiddlewareChain`; configurable per-client.
- **Search backends** (`search/`): SerpAPI, DuckDuckGo, Exa, Brave —
  unified async interface with auto-detection from configured API keys.
- **Domain intelligence** (`domain/`): TCP probing + banner grab,
  TLS cert inspection, HTTP header analysis with CDN detection and
  security scoring, DNS lookup/enumeration/zone-transfer/security
  posture, stdlib WHOIS client (55-TLD server map with referral
  following), UDP protocol-aware probes (DNS / NTP / SNMPv1 / syslog),
  pure banner→ServiceIdentity fingerprinting, and Schema.org
  organization-entity extraction.
- **Multi-page workflows**: `discovery` (sitemaps + page links with
  pattern filtering), `batch` (concurrent URL fetching with
  `asyncio.Semaphore`), `crawl` (BFS site crawl with depth/page limits
  and sitemap-first discovery).
- **45 MCP tools across 4 servers**:
  - `register_web_tools()` — 7 extraction tools (fetch-page, get-text,
    get-markdown, get-metadata, search-page, get-links, get-images).
  - `register_browser_tools()` — 19 browser interaction tools
    (navigate, click, fill, type, press, select, screenshot, evaluate,
    snapshot, content, cookies, set-cookie, save-auth, log-requests,
    requests, get-request, captured-responses, list-contexts,
    close-context).
  - `register_crawl_tools()` — 3 multi-page tools (discover-urls,
    batch-fetch, crawl-site).
  - `register_domain_tools()` — 14 domain-intelligence tools
    (tcp-probe, tcp-banner, tls-inspect, http-headers, service-detect,
    fingerprint-service, dns-lookup, dns-enumerate, dns-zone-transfer,
    dns-security, whois-lookup, domain-profile, extract-org,
    udp-probe). Enabled with `kaos-web-serve --domain`.
- **Typed module settings** (`KaosWebSettings`): `KAOS_WEB_*` env prefix
  with legacy fallbacks (`SERPAPI_API_KEY`, `EXA_API_KEY`,
  `BRAVE_API_KEY`, `KAOS_BROWSER_*`, `KAOS_SEARCH_*`). API keys use
  `pydantic.SecretStr`. Knobs cover browser (type/headless/channel),
  search (backend selection + per-backend timeouts + DDG user-agent),
  discovery, sitemap, crawl, and middleware behavior.
- **CLI** (`kaos-web`): `extract`, `metadata`, `serve` subcommands with
  `--json` envelope output for piping/agents.
- **Standalone MCP server** (`kaos-web-serve`): stdio (default) or
  streamable HTTP (`--http --port`); `--browser`, `--crawl`, `--domain`
  flags compose the registered tool surface; `--debug` enables verbose
  logging.
- **Optional extras**: `[browser]` adds Playwright for JS-heavy pages;
  `[dns]` adds dnspython for the domain-intelligence DNS tools; `[mcp]`
  adds `kaos-mcp` for serving tools as a FastMCP bridge; `[nlp]` adds
  `kaos-nlp-core` for BM25 search inside extracted documents.

### License

This release is the first to ship under the Apache License 2.0. Earlier
internal versions were proprietary.

[Unreleased]: https://github.com/273v/kaos-web/compare/v0.1.0a1...HEAD
[0.1.0a1]: https://github.com/273v/kaos-web/releases/tag/v0.1.0a1
