# Code Quality Standards

These standards define the minimum quality bar for `kaos-web` changes.

## Baseline Tools

`kaos-web` uses:

- `uv` for environments, dependency resolution, builds, and publishing
  commands.
- `ruff format` for formatting.
- `ruff check` for linting.
- `ty check` for type checking.
- `pytest` for tests.
- `twine check --strict` for built distribution metadata checks.

`kaos-web` is pure Python. Rust, PyO3, `maturin`, and Cargo checks are
not part of this package's active quality gate.

## Formatting

- Formatting is automated and non-negotiable.
- Do not mix style-only rewrites with behavior changes.
- Keep generated files out of hand-edited diffs unless the generation
  step is part of the change.
- Avoid broad reformatting of files unrelated to the PR.

Local format check:

```bash
uv run ruff format --check kaos_web tests
```

## Linting

- Lint cleanly before review.
- Do not silence a lint rule without a local reason.
- Prefer targeted ignores over file-wide ignores.
- Delete unused code instead of hiding it.
- Keep imports ordered and explicit.

Local lint check:

```bash
uv run ruff check kaos_web tests
```

## Typing

- Public functions and methods must be typed.
- Complex internal functions should be typed.
- Avoid `Any` unless the boundary is genuinely dynamic (e.g. arbitrary
  JSON-LD payloads from third-party pages).
- Use `typing.Protocol` for structural extension points
  (`WebClientProtocol`, `Middleware`).
- Use `Literal`, `TypedDict`, dataclasses, or Pydantic models where they
  make external contracts clearer.
- Use `# ty: ignore[...]` only with the narrowest possible rule and a
  reason when the reason is not obvious.

Local type check:

```bash
uv run ty check kaos_web tests
```

## Tests

- Bug fixes require regression tests.
- New public behavior requires tests at the right tier.
- Test names should describe behavior, not implementation.
- Prefer semantic assertions over "not empty" assertions — for
  extraction tests, assert specific content text or block types are
  present, not just `len(blocks) > 0`.
- Avoid brittle snapshots for large payloads unless they are golden
  fixtures with a review process.
- Do not use network or live credentials in unit tests. The autouse
  `_block_real_playwright_launch` fixture in `tests/unit/conftest.py`
  enforces the no-real-browser rule for the unit tier.

Local unit and integration gate:

```bash
uv run pytest -m "not live and not network and not slow"
```

## Public API Discipline

- Public API changes need changelog entries.
- Avoid broad re-exports that make internals public accidentally.
- Deprecate before removal when the stability policy requires it.
- Keep CLI, MCP tool, JSON, schema, and `KAOS_WEB_*` env-var contracts
  stable once released.
- Do not rename public objects for aesthetics in patch releases.

## Security Standards

- Never commit secrets, tokens, private keys, credentials, or `.env`
  files.
- Use `pydantic.SecretStr` for API keys.
- Redact secrets in logs, CLI output, JSON output, and errors.
- Add limits for untrusted input: response size caps, crawl depth/page
  caps, sitemap recursion caps, fetch timeouts.
- Preserve robots.txt enforcement, URL safety, size-cap, and
  rate-limit checks unless the change explicitly relaxes them with a
  documented threat-model justification.
- The domain-intelligence tools that ship with TLS verification
  disabled by default (`kaos-web-http-headers`,
  `kaos-web-extract-org`) target hosts whose cert configurations are
  the *subject* of inspection — preserve the
  `KAOS_WEB_DOMAIN_VERIFY_TLS=true` opt-in path.
- Do not add GPL, AGPL, unknown-license, non-commercial, or
  no-derivatives dependencies.
- Run secret scanning before release.

## Dependency Hygiene

- Keep base dependencies minimal (kaos-content, kaos-core,
  httpx[http2], lxml).
- Do not promote optional integrations into the base install when they
  belong behind an extra.
- Pin lower bounds intentionally and test them with the `min-deps` CI
  job.
- Do not rely on undeclared transitive dependencies.
- Prefer well-maintained packages with compatible licenses.
- Document risky or unusual dependencies.

## Documentation Quality

- README examples must run.
- Public functions with non-obvious behavior need docstrings.
- CLI flags and JSON output must be documented.
- Error messages should be useful without reading source.
- Keep docs current with code in the same PR.

## Performance Quality

- Do not optimize without a measurement for non-trivial changes.
- Add or update benchmarks for performance-sensitive APIs (the
  `tests/benchmarks/` tier).
- Watch memory growth on large pages and crawls.
- Bound expensive operations (depth, pages, request count, response
  bytes).
- Preserve streaming behavior where it is part of the design.

## Definition Of Done

A change is done when:

- The implementation is complete and scoped to the stated problem.
- Tests cover the new or changed behavior.
- Formatting, linting, typing, and tests pass.
- Built distributions pass strict metadata checks when packaging is
  affected.
- Security and dependency checks pass when relevant.
- README, docs, and CHANGELOG are updated when public behavior changes.
- The PR explains what changed, why, and how it was verified.
