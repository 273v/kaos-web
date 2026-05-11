# Changelog

All notable changes to `kaos-web` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Documentation

- **CI: 3.15 lane blocked on upstream rpds-py / PyO3.** Expanded the
  inline comment on the Python 3.15 matrix entry in ``ci.yml`` to
  explain the upstream block in detail: ``rpds-py==0.30.0`` (pulled
  transitively via ``jsonschema`` → ``referencing``) source-builds
  with ``pyo3-ffi 0.27.2`` which caps at CPython 3.14, so
  ``maturin pep517 build-wheel`` fails with ``the configured Python
  version (3.15) is newer than PyO3's``. No local fix is possible:
  we cannot drop ``jsonschema`` and ``rpds-py`` cannot be pinned to
  an older release on 3.15 because no older release has 3.15
  wheels either. Resolution is gated on rpds-py cutting a release
  with PyO3 0.28+. Tracking pointer added so the comment is
  actionable on each ecosystem refresh. The leg remains
  ``experimental: true`` / ``continue-on-error: true`` so the
  workflow signal stays green on PRs. No code change.

### Security

- **Sitemap parser no longer falls back to vulnerable
  `xml.etree.ElementTree`.** ``kaos_web/discover/sitemap.py``
  previously tried lxml first (safe — ``resolve_entities=False`` on a
  recovering parser) but fell back to stdlib
  ``xml.etree.ElementTree.fromstring`` if lxml raised. The fallback
  was unreachable in practice (lxml's recovering parser doesn't raise
  on syntactic chaos — it returns a partial tree) and stdlib
  ``etree.fromstring`` is itself vulnerable to XML attacks (XXE,
  entity expansion, billion-laughs). Bandit B314 flagged it; dropped
  the fallback entirely. If lxml's recovering parser raises
  ``ValueError`` / ``XMLSyntaxError``, the sitemap is now treated as
  unparseable and returns ``([], [])`` — same shape as the existing
  ``except ParseError`` path. The runtime ``xml.etree`` import is now
  TYPE_CHECKING-only (kept for the ``_find_text`` type annotation).
  Files: ``kaos_web/discover/sitemap.py``.

### Security

- **bandit + vulture now run in both pre-commit and CI.** Two new
  hooks in ``.pre-commit-config.yaml`` (bandit + vulture), mirrored
  by two new jobs in ``security.yml`` (``bandit (static security)``
  + ``vulture (dead-code scan)``). Pre-commit gives contributors fast
  feedback before push; CI makes the scan publicly visible on every
  PR. Bandit skip list (``B101,B404,B603,B607``) justified inline
  per audit; vulture runs at ``--min-confidence 100`` with the shared
  ``--ignore-names`` family list. Both pass clean — see PR for the
  prerequisite B314 sitemap fix that this PR depends on. Mirrors
  the rollout from kaos-core.
### Changed

- **uv.lock bumped to the current PyPI-latest of two kaos-* siblings:**
  ``kaos-content`` 0.1.0a2 → 0.1.0a4 and ``kaos-core`` 0.1.0a4 →
  0.1.0a5. Both bumps are no-op for the kaos-web public API but pull
  in upstream bug fixes / performance work. All 1337 unit tests
  continue to pass.

### Security

- **SSRF gate at every outbound URL/host site** (WEB5-001). Wires
  ``kaos_core.security.validate_outbound_url`` (and the host-only
  ``is_loopback`` / ``is_private_ip`` / ``is_metadata_service``
  primitives) into every kaos-web fetch site so a misconfigured
  caller — especially the HTTP-mode MCP server fronting multiple
  agents — cannot reach link-local cloud-metadata services
  (``169.254.169.254``), loopback, RFC1918 private networks, or
  block-listed schemes (``file://``, ``javascript:``, ``data:``,
  ``vbscript:``). New ``kaos_web.security`` module exposes
  ``validate_url(url)`` and ``validate_host(host)`` thin wrappers
  that translate ``UnsafeURLError`` into a new
  ``UrlPolicyError(WebError)`` whose message includes the specific
  policy field that fired plus the env var the operator can flip to
  relax it. **Strict by default**: blocks private/loopback/metadata
  and limits schemes to ``http``/``https``. Operators relax via
  ``KAOS_SECURITY_BLOCK_PRIVATE_NETWORKS=0`` /
  ``KAOS_SECURITY_BLOCK_LOOPBACK=0`` /
  ``KAOS_SECURITY_BLOCK_METADATA_SERVICES=0`` /
  ``KAOS_SECURITY_ALLOWED_HOSTS=["host","10.0.0.0/24",".example.com"]``.
  Sites wired (4 URL gates + 12 host gates):
  - URL: ``HttpClient._raw_fetch``, ``BrowserClient.fetch`` /
    ``screenshot`` / ``evaluate``, ``analyze_headers``,
    ``ExtractOrgTool.execute``.
  - Host: ``probe_port`` / ``probe_ports`` / ``probe_banner`` /
    ``probe_banners``, ``inspect_tls``, ``probe_dns`` / ``probe_ntp``
    / ``probe_snmp`` / ``probe_syslog``, ``whois_lookup``, ``lookup``
    / ``lookup_many`` / ``enumerate_dns`` / ``attempt_zone_transfer``
    / ``reverse_ptr``.
  Known gap: ``follow_redirects=True`` on httpx only validates the
  original URL — the redirect target is not re-validated. Closing
  this requires a connect-time hook on the HTTP client (kaos-core
  follow-up). Hostname-only inputs (most DNS / WHOIS use cases)
  pass through; the gate fires on IP literals where the policy
  classification is unambiguous.

- **Browser contexts are now session-scoped** (WEB5-002). Every entry
  in ``BrowserClient._contexts`` / ``_pages`` / ``_request_logs`` /
  ``_response_bodies`` / ``_logging_config`` is keyed by the tuple
  ``(KaosContext.session_id, context_id)``. Previously, the shared
  process-global client keyed by raw ``context_id`` strings — any
  caller who knew or guessed a context_id could click/fill/screenshot
  another caller's pages, read their cookies, or download captured
  fetch/XHR bodies. With the MCP HTTP server fronting multiple
  agents, that is a cross-tenant browser session takeover. Cross-
  session lookups now miss uniformly with the same "No active page" /
  "No context '<id>'" error a missing context returns — never
  disclosing existence in another session. ``close_context`` from a
  different session is a silent no-op. ``BrowserClient.active_contexts``
  changed from a property to a ``method(session_id) -> list[str]``
  that returns only the calling session's context IDs. Library
  callers that omit ``session_id`` fall back to a module-level
  ``ANONYMOUS_SESSION_ID`` sentinel so the original single-user stdio
  surface keeps working without churn.

- **`SaveAuthStateTool` no longer accepts a caller-supplied filesystem
  path** (WEB5-004). The previous implementation passed an MCP-input
  ``path`` straight to Playwright's ``context.storage_state(path=...)``
  — path-traversal / arbitrary-write to anywhere the server process
  could write, plus a credentials-leak persistence path. Rewritten to:
  capture the storage state in-memory via new
  ``BrowserClient.get_storage_state(context_id)``, write to a
  session-scoped VFS path, and persist as a kaos-core artifact via
  ``KaosContext.runtime.artifacts.create_from_path`` (auto-bound to
  the caller's ``session_id``). Returns an ``ArtifactManifest`` the
  agent retrieves via standard artifact MCP tools. **Breaking change
  for the MCP tool input schema**: the ``path`` parameter is removed
  and replaced with an optional ``name`` parameter (artifact name).
  Library users with their own filesystem authority can still call
  ``BrowserClient.save_storage_state(path)`` directly.

- **Observed third-party traffic redacts sensitive headers by default**
  (WEB5-003). When ``kaos-web-browser-log-requests`` captures network
  traffic, the recorded request and response headers now mask values
  for ``Authorization``, ``Proxy-Authorization``, ``Cookie``,
  ``Set-Cookie``, ``X-API-Key``, ``X-Auth-Token``, ``X-CSRF-Token``,
  plus any header whose name matches the catch-all
  ``(?i).*(?:secret|token|api[_-]?key|password|auth).*``. Mask format
  ``<redacted: N bytes>`` preserves length information without leaking
  the value. New ``KAOS_WEB_REDACT_OBSERVED_TRAFFIC`` env var
  (default ``true``); set ``false`` for explicit security-research
  workflows. The agent's OWN session cookies (returned by
  ``kaos-web-browser-cookies`` / ``GetCookiesTool``) are NOT
  redacted — they're the agent's own state.

- **Response-body size cap enforced at every fetch site** (WEB5-007).
  New `KaosWebSettings.max_body_bytes` (env: `KAOS_WEB_MAX_BODY_BYTES`,
  default 50 MB) bounds memory usage on hostile or misconfigured
  endpoints. Enforced at three sites:
  - `HttpClient._raw_fetch` switched to `client.stream() +
    aiter_bytes()` with a pre-check on the declared `Content-Length`
    header and a running tally over the streamed bytes. Aborts with
    `BodyTooLargeError` before materialization.
  - `BrowserClient.fetch` post-checks `len(page.content())` (Playwright
    has no streaming variant) — protects downstream parsers and
    artifact storage from operating on absurd strings.
  - `kaos_web.discover.sitemap._decompress_gzip` switched to
    `gzip.GzipFile.read(max_bytes + 1)` (gzip-bomb protection — a
    small gzipped payload can decompress to gigabytes; bounded read
    is the only memory-safe pattern).
  New `BodyTooLargeError(WebError)` carries `size_bytes`,
  `max_bytes`, and an agent-friendly recovery hint pointing at the
  env var.

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
