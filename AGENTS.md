# AGENTS.md

Repository-local guidance for coding agents working on `kaos-web`.
This file is the canonical cross-tool instruction file for this
repository.

## Scope

- Follow this file for all automated coding-agent work in this
  repository.
- Keep changes focused and public-repository appropriate.
- Preserve user changes already present in the worktree.
- For contributor process, use [CONTRIBUTING.md](CONTRIBUTING.md).
- For detailed engineering rules, use:
  - [Python design and architecture](docs/standards/python-design-and-architecture.md)
  - [Code quality standards](docs/standards/code-quality-standards.md)
  - [Engineering process](docs/standards/engineering-process.md)
  - [Tests, fixtures, and CI](docs/standards/tests-fixtures-ci.md)

## Project Identity

- Distribution: `kaos-web`.
- Import package: `kaos_web`.
- Runtime: Python 3.13+.
- Package type: pure Python, typed, Apache-2.0 licensed.
- CLI entry points: `kaos-web` and `kaos-web-serve`.
- Core purpose: fetch web content (httpx for fast HTTP, Playwright for
  JS-rendered) and produce `kaos-content` `ContentDocument` AST with
  provenance, plus a domain-intelligence layer (DNS / WHOIS / TLS /
  TCP banner / UDP probe / HTTP header analysis / Schema.org entity
  extraction). 45 MCP tools across 4 register functions.
- Public contracts include `kaos_web.__all__`, CLI commands and
  `--json` output, MCP tool names and schemas, the `WebRequest` /
  `WebResponse` / `BrowserClient` / `HttpClient` / `MiddlewareChain`
  Pydantic shapes, documented errors, and `KAOS_WEB_*` /
  `KAOS_SECURITY_*` environment-variable names.

## Setup

Use `uv` for local environments, dependency resolution, builds, and
tool execution.

```bash
uv sync --group dev
uvx pre-commit install
```

Public extras are optional and must stay lazy:

- `browser` for Playwright-backed JS rendering and 19 browser
  interaction tools.
- `dns` for `dnspython`-backed DNS enumeration and zone-transfer
  probes.
- `mcp` for the FastMCP server bridge (consumed by `kaos-web-serve`).
- `nlp` for `kaos-nlp-core`'s linear-time Rust regex engine (used by
  the URL filter patterns in crawl/discovery — protects against
  ReDoS on caller-supplied regex).

## Local Checks

Run the focused quality gate before handing off code changes:

```bash
uv run ruff format --check kaos_web tests
uv run ruff check kaos_web tests
uv run ty check kaos_web tests
uv run pytest tests/unit -q --no-cov
```

Use `ty`, not mypy. Inline type suppressions use `# ty: ignore[...]`
with the narrowest practical rule.

When packaging, release metadata, README rendering, or build behavior
changes, also run:

```bash
uv build
uvx --from twine twine check --strict dist/*
```

For docs-only changes, run at least `git diff --check` and a practical
Markdown/link sanity check.

## Architecture Rules

- Keep `kaos_web` import-time work minimal: no filesystem scans,
  network calls, browser launches, provider setup, or logging setup
  at import time.
- Keep the top-level API small and explicit through `kaos_web.__all__`.
- Use typed Pydantic models for external shapes (`WebRequest`,
  `WebResponse`, `BrowserClientConfig`, `HttpClientConfig`,
  `KaosWebSettings`, the `kaos_web.domain` models) rather than
  loosely structured dictionaries.
- Keep optional dependencies behind extras and lazy imports.
  Playwright (`[browser]`), dnspython (`[dns]`), kaos-mcp (`[mcp]`),
  and kaos-nlp-core (`[nlp]`) imports must live inside function
  bodies, not at module top.
- Keep the four `*_tools.py` files (`tools.py`, `browser_tools.py`,
  `crawl_tools.py`, `domain_tools.py`) at the top level — this is
  the documented KAOS convention (see
  `docs/python/design/modules.md` in the monorepo). The
  `kaos_web/discover/` subpackage groups the BFS-discovery
  subsystem (`batch`, `crawl`, `discovery`, `sitemap`).
- Treat CLI, MCP, JSON/schema, error, and environment-variable
  behavior as stable public surfaces once released.

## Web And Browser Principles

- `httpx` is the HTTP engine; `playwright` is the optional browser
  engine. Both have permissive licenses.
- Do not add GPL, AGPL, unknown-license, non-commercial, or
  no-derivatives HTTP, browser, or HTML-parsing dependencies.
- Keep the base install small. Heavy browser, DNS, NLP, and MCP
  capabilities belong behind explicit extras.
- All outbound network calls must route through the
  `kaos_web.security` policy gate
  (`validate_url(url)` for URL-bearing fetches,
  `validate_host(host)` for TCP / UDP / DNS / WHOIS probes). The
  gate wraps `kaos_core.security.validate_outbound_url` and is
  strict by default — blocks loopback, private networks, link-local
  metadata services, and non-(http|https) schemes. Operators relax
  via `KAOS_SECURITY_*` env vars.
- Browser contexts MUST be session-scoped. `BrowserClient` keys
  every internal map by `(KaosContext.session_id, context_id)`.
  Cross-session lookups raise the same "No context" error a missing
  context returns — never disclose existence in another session.
- Mask sensitive headers (`Authorization`, `Cookie`, `Set-Cookie`,
  `X-API-Key`, `X-Auth-Token`, `X-CSRF-Token`, plus the
  `(?i).*(?:secret|token|api[_-]?key|password|auth).*` catch-all)
  in CAPTURED third-party traffic. The agent's OWN session cookies
  (returned by `GetCookiesTool`) are NOT redacted.
- Enforce `KAOS_WEB_MAX_BODY_BYTES` at every fetch site to prevent
  OOM on hostile / oversized responses. Streaming (`HttpClient`),
  post-check (`BrowserClient`), and bounded gzip-decompress
  (`sitemap`) — three enforcement points.
- Bound untrusted HTML / sitemap / DNS handling with practical
  size, depth, recursion, and wall-time limits.

## Testing

- Bug fixes need regression tests.
- New public behavior needs tests through the real public entry point.
- Security-sensitive behavior needs accepted and rejected cases.
- Unit tests must not require network, credentials, large downloads,
  or local services. Browser tests must seed `client._browser =
  MagicMock()` to short-circuit `_ensure_browser()`; the autouse
  `_block_real_playwright_launch` fixture in
  `tests/unit/conftest.py` is the safety net that fails any
  regression loudly.
- Use realistic HTML / sitemap / DNS-response fixtures for parser
  behavior; small synthetic fixtures only when they isolate a
  condition.
- MCP and CLI changes must cover stable names, JSON/schema shapes,
  exit behavior, and actionable error messages with the three-part
  what/how/alternative recovery format.

## Security

- Never commit secrets, tokens, API keys, credentials, `.env` files,
  customer pages, privileged HTML, or unknown-license fixtures.
- Use the existing gitleaks allowlist (`.gitleaks.toml`) for
  legitimate public-page strings (e.g. the Cornell LII fixture's
  embedded bitly key); narrow allowlist entries to the specific
  path + rule + value pattern.
- Validate untrusted URL / host inputs early via
  `kaos_web.security.validate_url` / `validate_host` before any
  socket I/O.
- Do not discuss suspected vulnerabilities in public issues; follow
  [SECURITY.md](SECURITY.md).
- Do not weaken dependency license posture to add web functionality.

## Commits, PRs, And Releases

- Use conventional commit style and sign commits with `git commit -s`.
- Keep docs-only, code, tests, packaging, and release changes
  separated when possible.
- PRs should state what changed, why, how it was tested, and whether
  public API, CLI, MCP schema, package metadata, fixtures, or
  release artifacts changed.
- User-visible behavior changes need a `CHANGELOG.md` entry.
- Releases require green formatting, linting, typing, tests, build,
  strict metadata check, and a fresh install smoke test as described
  in the standards.
- Do not move public tags or force-push shared branches.
