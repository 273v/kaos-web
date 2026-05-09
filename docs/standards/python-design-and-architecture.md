# Python Design And Architecture Standards

These standards apply to Python code in `kaos-web`.

`kaos-web` is a pure-Python package. It publishes the `kaos_web`
import package and the `kaos-web` and `kaos-web-serve` CLI entry
points.

## Package Shape

- Keep the import package name aligned with the distribution name:
  `kaos-web` publishes import package `kaos_web`.
- Declare the public API in `kaos_web.__all__`. Tool registration
  entry points (`register_web_tools`, `register_browser_tools`,
  `register_crawl_tools`, `register_domain_tools`) and the
  HTML-to-AST helpers (`html_to_document`, `extract_content`,
  `extract_metadata`) are part of that surface.
- Keep `kaos_web/py.typed` in the wheel.
- Keep import-time work minimal: no network calls, filesystem scans,
  Playwright launches, runtime initialization, logging setup, or
  expensive model loads at import time. The level-3 readability model
  is bundled but loaded lazily.
- Use absolute imports for package code.
- Keep base dependencies small: `kaos-content`, `kaos-core`,
  `httpx[http2]`, `lxml`. Capabilities that drag heavy or specialised
  deps belong behind extras (`[browser]`, `[dns]`, `[mcp]`, `[nlp]`).
- Prefer a small top-level package surface that re-exports stable,
  documented names only.

## Public API

Treat all of these as public API once released:

- Names exported from `kaos_web.__all__`.
- The four `register_*_tools()` entry points and the MCP tool surface
  they install (45 tools across extraction, browser interaction,
  multi-page workflows, and domain intelligence).
- `kaos-web` and `kaos-web-serve` CLI commands, flags, `--json`
  output, and exit behavior.
- `WebRequest` / `WebResponse` / `PageMetadata` shape, `HttpClient` /
  `BrowserClient` constructor + `fetch()` contracts, the
  `MiddlewareChain` + `Middleware` protocol, and the readability /
  metadata extractor APIs.
- `KaosWebSettings`, the `KAOS_WEB_*` environment-variable namespace,
  and the legacy fallback aliases.
- JSON Schema, MCP-compatible shapes, and `ToolResult` content
  contracts produced by every shipped tool.

Changing or removing public API requires a changelog entry and a version
bump consistent with the package's pre-1.0 stability policy.

## Dependency Boundaries

- Keep runtime dependencies minimal and justified.
- Do not make `kaos-web` depend on optional providers or heavy
  capabilities at import time. `playwright`, `dnspython`,
  `kaos-mcp`, and `kaos-nlp-core` must be lazy-imported behind their
  extras.
- Do not make tests pass by relying on undeclared transitive
  dependencies.
- Do not import between sibling extraction packages (no
  `kaos_web` ↔ `kaos_pdf`, `kaos_office`, etc.). Shared functions
  belong in `kaos-content`.
- Do not use private APIs from dependencies unless the risk is
  recorded and covered by tests.

## Data Modeling

- Use Pydantic for external boundaries: configuration
  (`KaosWebSettings`), request/response shapes (`WebRequest`,
  `WebResponse`, `PageMetadata`), CLI `--json` output, MCP tool
  payloads, and serialized results.
- Use dataclasses or small typed objects for simple internal value
  records when Pydantic validation is not needed.
- Keep parsing and validation at boundaries. Internal functions should
  receive typed, normalized values.
- Prefer explicit result types over loosely shaped dictionaries.
- Avoid returning ambiguous tuples from public APIs.

## Functions And Classes

- Prefer functions for stateless transformations (HTML → AST, banner
  fingerprinting, metadata parsing).
- Use classes when there is persistent state, lifecycle management
  (`HttpClient`, `BrowserClient`), shared configuration, registries,
  middleware, or an explicit protocol (`Middleware`,
  `WebClientProtocol`).
- Keep constructors cheap. Use `async with` and explicit `start()` /
  `close()` for expensive lifecycle (browser launch, HTTP/2 pool).
- Avoid inheritance unless the abstraction is stable and tested through
  multiple implementations.
- Prefer protocols or small composition points (`MiddlewareChain.add`)
  over deep class hierarchies.

## Configuration

- Use `KaosWebSettings` (a `kaos_core.ModuleSettings` subclass) for
  package configuration.
- Read environment variables at the edge (CLI, tool registration,
  client construction), not deep in algorithmic code.
- Keep the `KAOS_WEB_*` resolution order documented and covered by
  tests when it changes. Legacy fallbacks (`SERPAPI_API_KEY`,
  `EXA_API_KEY`, `BRAVE_API_KEY`, `KAOS_BROWSER_*`) must keep
  working.
- Represent secrets with `pydantic.SecretStr`.
- Do not print, log, serialize, or include secrets in exception strings.
- Preserve redaction behavior in CLI and structured logging output.

## Error Handling

- Use the package-specific exception hierarchy in `kaos_web.errors`
  for user-facing failure modes.
- Tool error messages must include (1) what went wrong, (2) how to fix
  it, (3) an alternative tool when applicable. Agent-facing prompts.
- Do not expose stack traces, credentials, internal paths, or provider
  payloads in user-facing errors.
- Preserve original exceptions with exception chaining when debugging
  context matters.
- Validate untrusted inputs (URLs, selectors, JSON paths) early and
  fail with bounded, predictable errors.

## Async And Concurrency

- HTTP and browser clients expose `async` APIs and integrate with the
  KAOS runtime's event loop.
- Use synchronous APIs for CPU-bound transformations (HTML parsing,
  readability scoring) — these run inside `asyncio.to_thread` only
  when explicitly offloaded.
- Bound concurrency with `asyncio.Semaphore` (see
  `batch.py`/`crawl.py`) when concurrent execution is introduced.
- Apply timeouts to every external call. Defaults live on
  `KaosWebSettings`.
- Make cancellation safe: close browser contexts, HTTP pools, response
  streams, and middleware caches on shutdown.

## Files, Paths, And Inputs

- Accept `str` and `PathLike` inputs where file paths are part of the
  public API (CLI fixtures, save-auth, screenshot output).
- Normalize paths at boundaries.
- Do not follow symlinks, traverse directories, or read arbitrary files
  unless the API explicitly permits it.
- Put size, page-count, depth, recursion, and time limits on untrusted
  inputs. Crawl depth/page caps and response body size caps live on
  `KaosWebSettings`.
- Prefer streaming for large response bodies and crawl artifacts.

## CLI Design

- Every `kaos-web` and `kaos-web-serve` command must support `--help`.
- Commands that produce machine-consumable output must support `--json`.
- JSON output must remain stable once released.
- CLI errors should be concise and actionable.
- CLI examples in README and docs must be tested or manually verified
  before release.

## Documentation Expectations

- README quick starts must be runnable from a fresh environment.
- Examples should use public APIs only (`html_to_document`,
  `register_*_tools`, `KaosWebSettings`).
- Advanced docs belong under `docs/`.
- Any advertised runtime behavior, CLI command, MCP tool, schema
  output, configuration convention, security control, or
  middleware-policy behavior must have at least one test at the
  appropriate tier.
