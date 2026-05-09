# kaos-web

> **Part of [Kelvin Agentic OS](https://kelvin.legal) (KAOS)** — open agentic
> infrastructure for legal work, built by
> [273 Ventures](https://273ventures.com).
> See the [full KAOS package map](https://github.com/273v) for the rest of the stack.

[![PyPI - Version](https://img.shields.io/pypi/v/kaos-web)](https://pypi.org/project/kaos-web/)
[![Python](https://img.shields.io/pypi/pyversions/kaos-web)](https://pypi.org/project/kaos-web/)
[![License](https://img.shields.io/pypi/l/kaos-web)](https://github.com/273v/kaos-web/blob/main/LICENSE)
[![CI](https://github.com/273v/kaos-web/actions/workflows/ci.yml/badge.svg)](https://github.com/273v/kaos-web/actions/workflows/ci.yml)

`kaos-web` is the web extraction, browser automation, and domain-intelligence
module for KAOS. It fetches pages over HTTP or a headless browser, converts
HTML into a `kaos-content` `ContentDocument` AST with provenance on every
block, and exposes the surface as 45 MCP tools across 4 servers.

The base install is small (`kaos-content`, `kaos-core`, `httpx[http2]`,
`lxml`). Capabilities that drag heavy or specialised dependencies are gated
behind extras: `[browser]` adds Playwright for JS-heavy pages, `[dns]` adds
`dnspython` for the domain-intelligence DNS tools, `[mcp]` adds `kaos-mcp`
for serving tools as a FastMCP bridge, and `[nlp]` adds `kaos-nlp-core` for
BM25 search inside extracted documents. Everything beyond the base is opt-in.

## Install

```bash
uv add kaos-web
# or
pip install kaos-web
```

`kaos-web` requires Python **3.13** or newer. Pure Python; no native build.
Install extras with `uv add 'kaos-web[browser,dns,mcp,nlp]'` (or the
equivalent `pip install 'kaos-web[browser,dns,mcp,nlp]'`).

## Quick start

Convert an HTML page into a `ContentDocument` and serialize it to markdown:

```python
from kaos_web import html_to_document
from kaos_content.serializers import serialize_markdown

html = """
<html>
  <head><title>Demo</title></head>
  <body>
    <article>
      <h1>Hello</h1>
      <p>World <a href="https://example.com">link</a>.</p>
    </article>
  </body>
</html>
"""

doc = html_to_document(html, url="https://example.com/demo")
print(f"blocks: {len(doc.body)}")
print(serialize_markdown(doc).strip())
# blocks: 2
# # Hello
#
# World [link](https://example.com).
```

To fetch a live page over HTTP, swap `html_to_document(html, ...)` for
`HttpClient().fetch(WebRequest(url=...))` and feed the response body into
`html_to_document`.

## Concepts

The package is built around a small, composable set of primitives.

| Concept | What it is |
|---|---|
| **`WebRequest` / `WebResponse`** | Typed request/response models. `WebRequest.url`, `headers`, `extra` (e.g. `context_id` for browser context pooling); `WebResponse.body`, `status_code`, `headers`, `final_url`. |
| **`HttpClient`** | Async httpx-based client with HTTP/2, connection pooling, auth, SSL, proxy, structured error mapping. Routes through `MiddlewareChain` automatically. |
| **`BrowserClient`** | Playwright-backed client with lazy launch, named-context page tracking, cookie-banner dismissal for 8 known consent-management platforms. Use `async with` for cleanup. |
| **`MiddlewareChain`** | Composable chain wrapping `client.fetch`. `RetryMiddleware` (exponential backoff with jitter, honors `Retry-After`), `RateLimitMiddleware` (per-domain token bucket), `RobotsMiddleware` (stdlib `robotparser`), `CacheMiddleware` (in-memory LRU, RFC 7231). |
| **`html_to_document`** | The HTML-to-AST entry point. Walks an lxml element tree and produces `ContentDocument` with Block/Inline grammar and `SourceRef` + `Provenance` on every node. `content_scope` (0.0–1.0) tunes precision/recall on the level-3 learned readability extractor. |
| **`KaosWebSettings`** | Typed `ModuleSettings` with the `KAOS_WEB_*` env prefix and legacy fallbacks (`SERPAPI_API_KEY`, `EXA_API_KEY`, `BRAVE_API_KEY`, `KAOS_BROWSER_*`, `KAOS_SEARCH_*`). API keys use `pydantic.SecretStr` so they are redacted in logs. Builders: `to_browser_config()`, `to_retry_config()`, `to_rate_limit_config()`, `to_robots_config()`. |
| **`register_*_tools()`** | Four registration entry points expose the MCP tool surface: `register_web_tools` (7 extraction), `register_browser_tools` (19 browser), `register_crawl_tools` (3 multi-page), `register_domain_tools` (14 domain intelligence). Each takes a `KaosRuntime`. |

## CLI

`kaos-web` ships two entry points. Every structured command supports `--json`
for machine-readable output:

```bash
kaos-web extract https://example.com                  # HTML → ContentDocument
kaos-web extract page.html --format text              # local file → plain text
kaos-web metadata https://example.com --json          # JSON-LD / OpenGraph / meta
kaos-web search "kaos web extraction" --backend exa   # delegate to a search backend
kaos-web serve                                        # MCP server (stdio)
```

`kaos-web-serve` is the dedicated MCP server entry point with feature flags
for the larger tool surfaces:

```bash
kaos-web-serve                                        # 7 core extraction tools (stdio)
kaos-web-serve --http --port 8000                     # streamable HTTP transport
kaos-web-serve --browser                              # +19 browser interaction tools
kaos-web-serve --crawl                                # +3 multi-page tools
kaos-web-serve --domain                               # +14 domain-intelligence tools
kaos-web-serve --browser --crawl --domain --debug     # all 45 tools + verbose logs
```

The `--browser` flag requires the `[browser]` extra (Playwright); `--domain`
DNS tools require the `[dns]` extra (`dnspython`).

## Compatibility & status

| Aspect | |
|---|---|
| **Python** | 3.13, 3.14 (informational matrix entries for 3.14t free-threaded and 3.15-dev) |
| **OS** | Linux, macOS, Windows (pure-Python wheel; no native code) |
| **Maturity** | Alpha. The public API is documented in `kaos_web.__all__` (8 symbols) and the four `register_*_tools()` entry points. |
| **Stability policy** | Pre-1.0: minor bumps may change behaviour. Every change is documented in [`CHANGELOG.md`](CHANGELOG.md). The MCP tool surface and the `KAOS_WEB_*` environment-variable namespace are public API and follow the same policy. |
| **Test coverage** | 1235 unit tests, 90% line coverage on 5609 statements. |
| **Type checker** | Validated with [`ty`](https://docs.astral.sh/ty/), Astral's Python type checker. |

## Companion packages

`kaos-web` is one of the packages in the
[Kelvin Agentic OS](https://kelvin.legal). The broader stack:

| Package | Layer | What it does |
|---|---|---|
| [`kaos-core`](https://github.com/273v/kaos-core) | Core | Foundational runtime, MCP-native types, registries, execution engine, VFS |
| [`kaos-content`](https://github.com/273v/kaos-content) | Core | Typed document AST: Block/Inline, provenance, views |
| [`kaos-mcp`](https://github.com/273v/kaos-mcp) | Bridge | FastMCP server, `kaos` management CLI, MCP resource templates |
| [`kaos-pdf`](https://github.com/273v/kaos-pdf) | Extraction | PDF → AST with provenance |
| [`kaos-web`](https://github.com/273v/kaos-web) | Extraction | Web extraction, browser automation, search, domain intelligence |
| [`kaos-office`](https://github.com/273v/kaos-office) | Extraction | DOCX / PPTX / XLSX readers + writers to AST |
| [`kaos-tabular`](https://github.com/273v/kaos-tabular) | Extraction | DuckDB-powered SQL analytics |
| [`kaos-source`](https://github.com/273v/kaos-source) | Data | Government + financial data connectors (Federal Register, eCFR, EDGAR, GovInfo, PACER, GLEIF) |
| [`kaos-llm-client`](https://github.com/273v/kaos-llm-client) | LLM | Multi-provider LLM transport |
| [`kaos-llm-core`](https://github.com/273v/kaos-llm-core) | LLM | Typed LLM programming (Signatures, Programs, Optimizers) |
| [`kaos-nlp-core`](https://github.com/273v/kaos-nlp-core) | Primitives (Rust) | High-performance NLP primitives |
| [`kaos-nlp-transformers`](https://github.com/273v/kaos-nlp-transformers) | ML | Dense embeddings + retrieval |
| [`kaos-graph`](https://github.com/273v/kaos-graph) | Primitives (Rust) | Graph algorithms + RDF/SPARQL |
| [`kaos-ml-core`](https://github.com/273v/kaos-ml-core) | Primitives (Rust) | Classical ML on the document AST |
| [`kaos-citations`](https://github.com/273v/kaos-citations) | Legal | Legal citation extraction, resolution, verification |
| [`kaos-agents`](https://github.com/273v/kaos-agents) | Agentic | Agent runtime, memory, recipes |
| [`kaos-reference`](https://github.com/273v/kaos-reference) | Sample | Reference module for module authors |

Packages depend on `kaos-core`; everything else is opt-in. Mix and match the
ones you need.

## Development

```bash
git clone https://github.com/273v/kaos-web
cd kaos-web
uv sync --group dev
```

Install pre-commit hooks (recommended — they run the same checks as CI on
every commit, scoped to staged files):

```bash
uvx pre-commit install
uvx pre-commit run --all-files     # one-time full sweep
```

Manual QA commands (the same set CI runs):

```bash
uv run ruff format --check kaos_web tests
uv run ruff check kaos_web tests
uv run ty check kaos_web tests
uv run pytest -m "not live and not network and not slow"
```

## Build from source

```bash
uv build
uv pip install dist/*.whl
```

## Contributing

Issues and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md)
for setup, quality gates, pull request expectations, and engineering
standards. By contributing you certify the
[Developer Certificate of Origin v1.1](https://developercertificate.org/) —
sign every commit with `git commit -s`. Please open an issue before starting
on a non-trivial change so we can align on scope.

## Security

For security issues, **please do not file a public issue**. Report privately
via [GitHub Private Vulnerability Reporting](https://github.com/273v/kaos-web/security/advisories/new)
or email **security@273ventures.com**. See [SECURITY.md](SECURITY.md) for the
full disclosure policy.

### Security-relevant settings

| Env var | Default | Effect |
|---|---|---|
| `KAOS_WEB_DOMAIN_VERIFY_TLS` | `true` | Verify TLS certs on the two domain-intelligence HTTP probes (`kaos-web-http-headers`, `kaos-web-extract-org`). Set `false` to inspect hosts whose cert is the *subject* of inspection (self-signed, expired, mismatched SAN). Content-extraction tools (`HttpClient` / `BrowserClient`) keep TLS verification on independently of this flag. |
| `KAOS_WEB_MAX_BODY_BYTES` | `50000000` | Maximum response body size accepted from any fetch site. Enforced via streaming with `Content-Length` pre-check + running tally in `HttpClient`, post-check in `BrowserClient`, and bounded gzip-decompress in the sitemap parser (`_decompress_gzip`). Raises `BodyTooLargeError` on overflow. Raise this when working with bulk data (legal corpus pages, large data exports, archival snapshots). |
| `KAOS_WEB_REDACT_OBSERVED_TRAFFIC` | `true` | Mask `Authorization`, `Proxy-Authorization`, `Cookie`, `Set-Cookie`, `X-API-Key`, `X-Auth-Token`, `X-CSRF-Token`, and any token/secret/auth-shaped header in CAPTURED request/response logs (`kaos-web-browser-log-requests`). Mask format `<redacted: N bytes>` preserves length without leaking value bytes. The agent's OWN session cookies (`kaos-web-browser-cookies`) are NOT redacted. Set `false` for security-research workflows that need raw bytes. |

The `CacheMiddleware` automatically bypasses any request that carries
`Authorization`, `Proxy-Authorization`, `Cookie`, `X-API-Key`,
`X-Auth-Token`, or `X-CSRF-Token` headers, so authenticated responses
cannot leak across callers via the shared cache.

URL filter regexes used by `kaos-web-discover-urls` /
`kaos-web-crawl-site` route through the `kaos-nlp-core` Rust regex
engine (linear-time, no catastrophic backtracking) when the `[nlp]`
optional extra is installed. Without it, kaos-web falls back to stdlib
`re` and logs a one-shot warning — install `kaos-web[nlp]` if you
plan to accept caller-supplied regex patterns.

## License

Apache License 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).

Copyright 2026 [273 Ventures LLC](https://273ventures.com).
Built for [kelvin.legal](https://kelvin.legal).
