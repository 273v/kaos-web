# Changelog

All notable changes to `kaos-web` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
