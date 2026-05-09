# Tests, Fixtures, And CI Standards

This document defines test tiers, fixture rules, and GitHub Actions
standards for `kaos-web`.

## Test Tiers

`kaos-web` ships four explicit test tiers. CI's PR gate runs only the
unit tier; the others run on demand or against scheduled live windows.

| Tier | Marker | Network | Credentials | Purpose |
|---|---|---|---|---|
| Unit | `unit` or none | No | No | Fast deterministic behavior; HTTP and browser clients are stubbed. The autouse `_block_real_playwright_launch` fixture in `tests/unit/conftest.py` blocks Playwright launches. Coverage gate ≥80%. |
| Integration | `integration` | Yes | No | Hits real HTTP servers (httpbin, public test pages) without credentials. |
| Live | `live` | Yes | Yes | Real provider APIs (`SERPAPI`, `EXA`, `BRAVE` search backends). |
| Benchmark | `benchmark` | No | No | `pytest-benchmark` performance checks under `tests/benchmarks/`. |

Unit-tier CI must not require network, credentials, local services, or
large downloads.

## Test Requirements

- New behavior needs tests at the appropriate tier.
- Bug fixes need regression tests.
- Security fixes need abuse-case tests where safe (URL allowlists,
  robots policy, response size caps, TLS verification toggles).
- README quick starts and CLI examples need smoke coverage or manual
  verification before release.
- Extraction, middleware, browser, crawl, domain-intelligence, search,
  and MCP tool behavior need tests at the appropriate tier when
  changed.
- Tests should assert semantics, not just non-empty output. For
  extraction, assert specific content (text, block types, link
  classification) is present.
- Tests should avoid wall-clock sleeps unless testing timeouts.

## Marker Discipline

- Integration tests must be marked `integration`.
- Benchmark tests must be marked `benchmark`.
- Live-credential tests must be marked `live`.
- New marker tiers must be registered in `pyproject.toml`
  (`[tool.pytest.ini_options].markers`).
- CI unit selection must be able to run:

```bash
uv run pytest -m "not live and not network and not slow and not integration" --no-cov
```

The command above must not collect tests that need credentials, local
services, or external network.

## Fixtures

`kaos-web` ships fixtures under `tests/fixtures/` for HTML pages,
sitemaps, robots.txt samples, and DNS/WHOIS reply payloads.

Fixtures must be:

- Small enough for normal repository use.
- Redistributable under compatible terms.
- Free of customer data, privileged content, secrets, and PII.
- Documented with source, license, and purpose.
- Stable enough to support deterministic tests.

Do not commit:

- Customer documents.
- Real credentials.
- Unknown-license data.
- Non-commercial or no-derivatives data for redistributed fixtures.
- Large binary corpora that should be downloaded and hash-verified.

## Fixture Provenance

Every fixture directory should include a README or manifest that records:

- File name.
- Source URL or generation method.
- License or public-domain status.
- Retrieval date when relevant.
- SHA256 for externally sourced files.
- Reason the fixture exists.
- Any transformations applied (anonymisation, header redaction,
  whitespace normalisation).

Generated fixtures should include the generator script or enough
description to recreate them.

## Golden Files

Golden files are allowed when output stability matters (markdown
serialisation, MCP tool JSON output).

Rules:

- Keep golden files small and reviewable.
- Include a command for regenerating them.
- Review diffs semantically.
- Do not bless broad golden changes without explaining the behavior
  change.
- Store comments in a companion README when the file format cannot
  carry comments.

## Fuzzing

`kaos-web` includes property and fuzz tests under
`tests/unit/test_fuzz.py` covering HTML parsing, URL normalisation, and
robots.txt parsing.

Python fuzz/property testing:

- Prefer Hypothesis for structured inputs.
- Keep failing examples as regression tests.
- Bound generated sizes so local runs stay practical.

Fuzz targets should check:

- No crashes.
- No infinite loops.
- No unbounded memory growth.
- Valid errors for invalid inputs.
- Round-trip or invariant properties where available.

## Coverage

- Coverage is a signal, not the goal.
- The unit gate enforces ≥80% line coverage via
  `[tool.coverage.report].fail_under`.
- New important branches should be covered.
- Public API, error paths, security limits, and serialization deserve
  explicit tests.
- Do not add trivial tests only to move a percentage.

## CI Workflows

Required PR checks:

- Formatting.
- Linting.
- Type checking.
- Unit tests (no network, no credentials, no local services).
- `min-deps` job (lowest-direct resolution).
- Build check (wheel + sdist + twine `--strict` + clean-venv smoke).
- Dependency CVE audit (`pip-audit` against the locked dep set).
- Secret scanning (`gitleaks`).

Recommended scheduled or manual checks:

- Weekly `pip-audit` re-scan.
- Live-tier sweep against real search backends when API keys are
  available.
- Benchmark regression check.

Release workflow checks:

- Clean checkout.
- Build pure-Python wheel and sdist.
- Strict metadata check.
- Fresh install smoke test.
- Publish through OIDC.
- Verify published install after release when practical.

## GitHub Actions Standards

- Use least-privilege `permissions`.
- Do not expose secrets to forked PRs.
- Pin third-party actions to trusted versions.
- Prefer OIDC over static credentials.
- Separate build, test, security, and publish jobs.
- Cache dependencies carefully; never cache secrets.
- Keep workflow logs free of credentials and private paths.
- Use environment protection for publishing.

## Local Verification Commands

Base development setup:

```bash
uv sync --group dev
```

Fast local quality gate:

```bash
uv run ruff format --check kaos_web tests
uv run ruff check kaos_web tests
uv run ty check kaos_web tests
uv run pytest -m "not live and not network and not slow and not integration" --no-cov
```

Packaging gate when packaging, metadata, README rendering, or release
behavior changes:

```bash
uv build
uvx --from twine twine check --strict dist/*
```

## Release Gate

Before release:

- Unit and integration CI are green.
- Security checks are green.
- Fixtures have provenance if fixtures were added.
- Fuzz/security regressions are included for parser or input-safety
  fixes where relevant.
- Build artifacts pass metadata checks.
- Fresh install smoke test passes.
