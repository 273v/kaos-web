# Changelog

All notable changes to `kaos-web` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).


## [0.1.7] — 2026-05-23

### Added

- **RSS/Atom feed parser + `kaos-web-fetch-feed` MCP tool** (PR #33).
  Pure-stdlib feed extractor returns a typed `FeedDocument` with
  entries, normalized to `ContentDocument`. New MCP tool fetches a
  feed URL through the URL policy gate, parses it, and ships the
  AST as an artifact.


## [0.1.6] — 2026-05-23

### Security

- **HttpClient now revalidates redirect targets through the URL
  policy gate.** `HttpClient` previously delegated redirect handling
  to httpx (`follow_redirects=True`), which validates only the
  original request URL. A 3xx response with a `Location` pointing at
  loopback, RFC1918, link-local metadata
  (e.g. `http://169.254.169.254/latest/meta-data/`), or a non-(http|
  https) scheme bypassed `kaos_web.security.validate_url` —
  contradicting README:206's "enforced at every kaos-web fetch site"
  promise and reintroducing the classic SSRF-via-redirect class.

  `_streamed_request` now drives redirects manually: it always passes
  `follow_redirects=False` to httpx and walks the chain itself,
  re-entering `validate_url` on each `Location` target before any
  socket I/O for the next hop. httpx's `response.next_request`
  preserves the method-rewriting + cross-origin header stripping
  behavior; only the policy check is added. Hop count is enforced
  against `self._config.max_redirects` (raises
  `httpx.TooManyRedirects` → `WebRedirectError`).

  `tests/unit/test_redirect_revalidation.py` pins three contracts via
  `httpx.MockTransport`: redirect to `127.0.0.1` is rejected, redirect
  to `169.254.169.254` is rejected, and an allowed
  public→public→public chain walks each hop. Closes
  audit-04/kaos-web.md F-001.

  This is the second security-class High in the audit-04 set
  (kaos-mcp #30 was the first). Per the §10 stakeholder decision,
  coordinate disclosure with kaos-mcp's GHSA window if both
  advisories ship within 7 days of each other.

  **Note**: `BrowserClient` (Playwright) is NOT covered by this fix —
  Playwright's `page.goto()` follows redirects inside the browser
  process and the kaos-web policy gate cannot intercept them. Per
  the audit recommendation, this limitation should be documented
  next to the `KAOS_SECURITY_*` table; high-risk callers needing
  redirect-validated fetches should use `HttpClient` directly.

### Changed

- `pyproject.toml` classifier bumped from `Development Status :: 3 - Alpha`
  to `Development Status :: 5 - Production/Stable` to reflect the
  0.1.0 GA release (WU-L #543) that froze the public API for the
  0.1.x line. Closes audit-04/kaos-web.md Family D (classifier drift).

### Documentation

- **audit-04 README count drift.** README Maturity row updated from
  "8 symbols" to the correct "10 symbols" and from "four
  `register_*_tools()` entry points" to five (including
  `register_web_all_tools`). Pre-fix counts dated from before the
  `register_web_all_tools` addition and a third extraction helper
  came online. The new wording enumerates each name so future drift
  is immediately visible in diff review. No public API or behavior
  change; doc-only fix closing audit-04/kaos-web.md README count
  drift.


## [0.1.5] — 2026-05-22

### Fixed

- **`BrowserClient` consults `KaosWebSettings.browser_channel` +
  auto-detect** when ``BrowserClientConfig.channel`` is None. Previously
  the env var (``KAOS_WEB_BROWSER_CHANNEL`` / ``KAOS_BROWSER_CHANNEL``)
  and `_detect_browser_channel()` (which selects system `chrome` when
  Playwright's bundled Chromium isn't available, e.g. Ubuntu 26.04+)
  were dead code unless callers built a custom `BrowserClientConfig`.
  Default `BrowserClient()` now picks them up automatically. Verified
  live against Cloudflare's home page (1.4MB rendered HTML) using
  system `google-chrome` on Ubuntu 26.04.


## [0.1.4] — 2026-05-22

Re-tag of 0.1.3 — the 0.1.3 publish failed pre-publish QA on three
unit tests that pinned the old viewport / `_fetch_html` defaults.
0.1.4 ships the same code with those tests updated to match the new
anti-bot defaults. See 0.1.3 entry below for full content.

## [0.1.3] — 2026-05-22 (failed publish — superseded by 0.1.4)

### Changed

- **`_fetch_html` is Playwright-first.** When Playwright is available
  (i.e. ``kaos-web[browser]`` extra is installed), the realistic-
  browser path is now the default — not a 403/406 fallback. This is
  the canonical pattern: realistic Chrome fingerprint, 1365x768
  viewport, en-US locale, America/New_York timezone, rotated desktop
  UA across :data:`DEFAULT_DESKTOP_UAS`, and the full
  ``sec-ch-ua`` / ``sec-fetch-*`` / ``accept-language`` /
  ``cache-control`` header set. Bare httpx is the explicit opt-out
  (``use_browser=False`` for known-API JSON endpoints).
- **BrowserClient defaults overhauled.** ``BrowserClientConfig`` now
  ships with the production-validated anti-bot defaults ported from
  ``kelvin-legal-intelligence/kelvin_firm_db/services/browser/
  collector.py``: ``randomize_user_agent=True``,
  ``use_default_anti_bot_headers=True``, viewport 1365x768,
  locale ``en-US``, timezone ``America/New_York``,
  ``default_wait_until="networkidle"``. Callers that want the old
  bare-Playwright behavior can disable these explicitly.

### Added

- :data:`kaos_web.clients.user_agents.DEFAULT_DESKTOP_UAS` — curated,
  market-share-weighted realistic desktop UA list (Chrome Win/Mac/
  Linux, Safari, Edge, Firefox).
- :data:`DEFAULT_EXTRA_HEADERS` — full anti-bot Chrome header set
  (``sec-ch-ua``, ``sec-ch-ua-mobile``, ``sec-ch-ua-platform``,
  ``sec-fetch-dest``, ``sec-fetch-mode``, ``sec-fetch-site``,
  ``sec-fetch-user``, ``upgrade-insecure-requests``,
  ``accept-language``, ``cache-control``, ``pragma``, ``accept``).
- :func:`next_default_desktop_ua` — process-wide round-robin UA
  rotation safe under asyncio concurrency.
- Live regression tests in
  ``tests/integration/test_playwright_anti_bot.py`` — SEC.gov press
  releases, EDGAR CGI route, Cloudflare home page, UA rotation
  invariant, sec-ch-ua header invariant, and explicit httpx fallback.
  All 6 pass against real targets.


## [0.1.2] — 2026-05-22

### Fixed

- **SEC.gov 403 cascade** (closes kaos-modules #444). The 2026-05-22
  237-session production audit found 21 `kaos-web-fetch-page` failures
  against `sec.gov` press releases + EDGAR routes, driving 30+ cascading
  `max_iterations` and `wall_clock_exceeded` aborts. Root cause: the
  randomized-Chrome desktop UA strategy that protects against
  consumer-site bot detection is *inverted* on government domains —
  SEC.gov specifically rejects Chrome UAs but accepts honest bot
  identifiers like `KAOS-Web/0.1 (+https://273ventures.com/kaos-web)`.

  `_raw_fetch` now overrides the client's baked-in UA per request when
  the host matches `BOT_FRIENDLY_HOSTS` (sec.gov, govinfo.gov,
  federalregister.gov, ecfr.gov, congress.gov, uscourts.gov,
  courtlistener.com, irs.gov, fcc.gov, ftc.gov, doj.gov,
  treasury.gov, data.gov, europa.eu). Verified live: SEC.gov press
  releases + EDGAR company filings both return 200 instead of 403.
  Caller-supplied `User-Agent` headers always win — explicit
  overrides are never clobbered. (`tests/integration/test_sec_gov_anti_bot.py`,
  `tests/unit/test_user_agents.py`)


## [0.1.1] — 2026-05-21

### Fixed

- **Domain-profile error unwraps `BaseExceptionGroup`** (P2-A,
  WU-K v2 Case E6 follow-up). When `profile_domain` raised an
  `ExceptionGroup` from its `asyncio.TaskGroup`, the agent saw a
  useless "unhandled errors in a TaskGroup (1 sub-exception)" with
  no signal as to what actually went wrong. The dispatcher in
  `kaos_web/domain_tools.py:678-695` now unwraps the first 3 sub-
  exceptions and renders each as `Type: message`, restoring the
  what/how/alternative-tool contract required by kaos-mcp tool design.
  Bare `Exception` paths also now include `type(exc).__name__:` so the
  failure shape is visible regardless of the exception family.

- **Search dispatcher rejects the literal string `"auto"`** (#545,
  WU-K v2 Case C2 + cluster). LLMs (gpt-5.4-mini, Haiku 4.5) routinely
  pass `backend="auto"` to `kaos-web-search` because the public MCP
  tool description on 0.1.0 says "Default: auto-detect from env vars"
  — even though `"auto"` was never a real enum value in `_BACKENDS`.
  The dispatcher at `kaos_web/search/backends.py:89` now normalizes
  the literal `"auto"` (case-insensitive) to the auto-detect path,
  the same as omitting the parameter. Three new regression tests in
  `tests/unit/test_search_backends.py` pin the new behavior:
  `test_auto_string_falls_through_to_detect`,
  `test_auto_synonym_with_no_keys_uses_duckduckgo`, and
  `test_auto_uppercase_also_accepted`.

### Changed

- **`kaos-web-search` MCP tool description rewritten** to discourage
  the literal-string-passing pattern: "Optional search backend. Omit
  this parameter to auto-detect ... Do NOT pass the literal string
  'auto' — use one of the enum values below to force a specific
  backend. The string 'auto' is also accepted as a synonym for
  omission (0.1.1, #545) but the canonical pattern is to omit the
  parameter." Fixing the description is necessary but not sufficient
  (training-cutoff propagation, copy-paste from docs); the
  dispatcher-side fix is the load-bearing change.


## [0.1.0] — 2026-05-20

### Released

- 0.1.0 GA — WU-L of GA plan. First stable release. Public API frozen.
- Pin floor raised to `>=0.1.0,<0.2` across all kaos-* runtime and
  optional dependencies. Refreshed `uv.lock` to pick up the 0.1.0
  line of every upstream.

### Internal

- WU-L of the 0.1.0 GA plan
  (`kaos-modules/docs/plans/2026-05-20-0.1.0-ga-plan.md`).


## [0.1.0rc1] — 2026-05-20

### Changed

- Pin floor raised to `>=0.1.0rc1,<0.2` across kaos-* runtime and
  optional dependencies (`kaos-core`, `kaos-content`, `kaos-mcp`,
  `kaos-nlp-core`). Refreshed `uv.lock` to pick up the rc1 line of
  every upstream.

### Internal

- WU-J of the 0.1.0 GA plan
  (`kaos-modules/docs/plans/2026-05-20-0.1.0-ga-plan.md`).
  Release candidate; freezes the public API for `kaos-web`
  ahead of 0.1.0 GA.


## [0.1.0a6] — 2026-05-20

### Changed

- Bumped minimum `kaos-core` to `0.1.0a12` (post-URI-redesign +
  Capability type). kaos-web does not use the URI redesign directly —
  the bump aligns the supported floor with the rest of the kaos-*
  DAG ahead of 0.1.0 GA.
- Refreshed `uv.lock` to pick up `kaos-core 0.1.0a12`,
  `kaos-content 0.1.0a12`, `kaos-mcp 0.1.0a4`, and
  `kaos-nlp-core 0.1.0a8`.

### Internal

- WU-F.6 of the 0.1.0 GA plan
  (`kaos-modules/docs/plans/2026-05-20-0.1.0-ga-plan.md`):
  catch-up to kaos-core 0.1.0a12.

## [0.1.0a5] — 2026-05-17

### Changed (intentional break — alpha train)

- **`kaos-web-crawl-site`** and **`kaos-web-batch-fetch`** no longer
  silently truncate page content at 5000 characters in the
  no-runtime-context fallback path. The four `[:5000]` truncations
  and the `truncated: bool` flag are **deleted** from
  `crawl_tools.py`:
  - `CrawlSiteTool.execute` (text + markdown fallback branches)
  - `_extract_response` helper used by `BatchFetchTool` (text +
    markdown branches)

  The artifact-tier happy path (`_store_response_artifact` /
  `_store_crawl_page_artifact`) — which already activates when a
  `KaosContext` with a `KaosRuntime` is supplied — is the canonical
  flow for large pages. The fallback path now returns full content
  unbounded; downstream callers should supply a runtime context to
  get the tiered (inline / summary+link / link-only) experience
  driven by the `KaosCoreArtifactSettings` thresholds shipped in
  kaos-core 0.1.0a8.

  **Output-shape break:** the `truncated` key is gone from both
  fallback responses. Callers that read it must remove the read.

### Why

Stage B3 of the cross-package
`no-hardcoded-caps-and-artifact-first-tool-results` plan in the
kaos-modules monorepo. The 5000-char silent truncation hid information
from downstream agents — long pages came back claiming
`"truncated": true` but the full text was discarded. With the artifact
path already wired for the runtime-context case, the surgical fix is
to delete the fallback truncation and trust the artifact tier system
to handle size.

### Constants audit

```bash
$ git grep '\[:5000\]\|max_chars\s*=\|content_max_chars' kaos_web/
# (no hits in production code)
```

### Dependencies

No version pin changes. `kaos-web` continues to declare
`kaos-core>=0.1.0a4,<0.2`; the artifact helpers already used by
`_store_response_artifact` / `_store_crawl_page_artifact` predate
0.1.0a8 and don't require the new API surface.

## [0.1.0a4] — 2026-05-15

### Added — `tags=["browser"]` / `tags=["netinfra"]` on Playwright + DNS/WHOIS tools (PRD PR 2 Stage A.5)

kaos-agents 0.1.0a3's `derive_group()` reads recognized tags as
narrowing signals: `["browser"]` routes a tool to the SessionToolSet
`browser` group; `["netinfra"]` routes to `netinfra`. Without these
tags, both surfaces would land in the broader `web` group and
accidentally surface on the default research preset, even for
sessions that haven't opted into Playwright or netinfra
introspection.

Affected tools:

- **Browser** (19 tools — every tool in `browser_tools.py`):
  `browser-navigate`, `-click`, `-fill`, `-type`, `-press`,
  `-select`, `-screenshot`, `-evaluate`, `-snapshot`, `-content`,
  `-cookies`, `-set-cookie`, `-save-auth`, `-log-requests`,
  `-requests`, `-get-request`, `-captured-responses`,
  `-list-contexts`, `-close-context`.
- **Netinfra** (14 tools — every tool in `domain_tools.py`):
  `tcp-probe`, `tls-inspect`, `http-headers`, `service-detect`,
  `dns-lookup`, `dns-enumerate`, `dns-zone-transfer`, `dns-security`,
  `whois-lookup`, `domain-profile`, `extract-org`, `tcp-banner`,
  `fingerprint-service`, `udp-probe`.

HTTP fetch + search tools in `tools.py` (9) and crawl tools in
`crawl_tools.py` (3) deliberately stay untagged — they're pure
`web` group and the derivation reaches them via
`openWorldHint=True` + `readOnlyHint=True` without needing a tag.

Tests:
  - 3 new tests pin the tag coverage: every browser tool carries
    `tags=["browser"]`; every netinfra tool carries `tags=["netinfra"]`;
    web + crawl tools carry NEITHER.

Motivated by `kaos-modules/docs/internal/dynamic-tool-planning-completion-plan.md`
§4 Stage A.5. Purely additive: the `tags` field was empty before.

## [0.1.0a3] — 2026-05-15

### Added — `register_web_all_tools` convenience union (PRD PR 1)

- **`register_web_all_tools(runtime)`** — registers every kaos-web
  MCP tool with one call. Composes the existing 4 group entry
  points:
  - `register_web_tools(runtime)` → 9 HTTP fetch + search tools
    (SessionToolSet `web` group)
  - `register_browser_tools(runtime)` → 19 Playwright tools
    (`browser` group; `[browser]` extra needed at *runtime*, not
    registration)
  - `register_domain_tools(runtime)` → 14 DNS / WHOIS / TLS / TCP
    banner / UDP probe / HTTP header / org-extract tools
    (`netinfra` group; `[dns]` extra at runtime)
  - `register_crawl_tools(runtime)` → 3 URL discovery / batch
    fetch / full-site crawl tools (`web` group)

  Total: **45 tools** registered.

The four group-specific registration functions retain their
existing names and behavior — no breaking changes. The new union
is purely additive for callers (single-user-chat backend,
power-user sessions) that want the full 45-tool surface in one
call instead of four.

Pins the SessionToolSet `web` / `browser` / `netinfra` group entry
points so kaos-agents (PR 2) can wire ceiling membership without
a new public surface bump.

Motivated by `kaos-modules/docs/internal/dynamic-tool-planning-prd.md`
§4 ("PR 1 — catalog expansion"; round-1 decision #3 — bump from 9
to 45 registered tools).

## [0.1.0a2] — 2026-05-11

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

[Unreleased]: https://github.com/273v/kaos-web/compare/v0.1.0a2...HEAD
[0.1.0a2]: https://github.com/273v/kaos-web/compare/v0.1.0a1...v0.1.0a2
[0.1.0a1]: https://github.com/273v/kaos-web/releases/tag/v0.1.0a1
