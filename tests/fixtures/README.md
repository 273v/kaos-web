# kaos-web test fixtures

Provenance manifest per
[`docs/oss/50-data-and-fixtures/provenance-policy.md`](../../../docs/oss/50-data-and-fixtures/provenance-policy.md).

These fixtures back the `kaos-web` HTML-to-AST, readability, and
crawl/extract test suites. Three of the four top-level HTML files are
captures of public-internet pages used as stable, network-free
substitutes for real fetches. `article.html` and everything under
`readability/` are hand-crafted in-house for the express purpose of
exercising the readability extractor under realistic-but-synthetic
layouts (firm directory pages, regulator portals, PACER-style docket
reports). None of these fixtures came from a customer engagement,
internal share, or pseudonymized client document.

## Top-level fixtures

| File | Source URL | License | Retrieved | SHA-256 |
|---|---|---|---|---|
| `cornell_law.html` | https://www.law.cornell.edu/uscode/text/17/107 (17 U.S. Code § 107 — Fair use, Cornell LII page; canonical URL declared in the file's `<link rel="canonical">` / `og:url` / `twitter:url` metadata) | Cornell LII page wrapper around U.S. Code text. The underlying U.S. Code is public-domain (17 USC §105); the Cornell wrapper is reproduced here as a fixture for HTML-extraction regression. No transformative redistribution. | 2026-04-01 (first git commit `17dece7`) | `e674a81e2f86cb8b38e6920a5d484b25ee01fc9b9863893c74d4324dcc88784b` |
| `httpbin.html` | https://httpbin.org/html (Kenneth Reitz / Postman httpbin reference service — Moby-Dick excerpt by Herman Melville) | httpbin: ISC / MIT (https://github.com/postmanlabs/httpbin); Moby-Dick text: public-domain (pre-1924) | 2026-04-01 (first git commit `17dece7`) | `3f324f9914742e62cf082861ba03b207282dba781c3349bee9d7c1b5ef8e0bfe` |
| `books_toscrape.html` | https://books.toscrape.com/ — "A Light in the Attic" product page (scrapinghub.com training sandbox, explicitly published for scraping practice) | Public training sandbox by Scrapinghub/Zyte; book metadata is fictional/test content | 2026-04-01 (first git commit `17dece7`) | `a6e572bec156bf80ff3149b89b6d218cdcf8866ccc26ccf69a5431db8e142c6a` |
| `article.html` | hand-crafted by 273V for kaos-web readability regression (synthetic `example.com` URLs, fictional "Jane Doe" author, fictional "Test Article" body — no real-world source) | hand-crafted, 273V | 2026-04-01 (first git commit `b5945f6`) | `a034f2b59eca39b463e4476803760f3b9f4b9f317a6256fe82db95e19a5bb086` |

## Notes

- `cornell_law.html` is referenced by `tests/fixtures/readability/corpus.json`
  as a "noisy" extraction target (heavy navigation chrome around the
  statutory body).
- `httpbin.html` is referenced by `tests/integration/test_crawl.py` and
  `tests/integration/test_real_http.py` against the live httpbin.org
  endpoint, and used in offline form for fuzz tests.
- `books_toscrape.html` is referenced by
  `tests/unit/test_readability_l3.py` and `tests/integration/test_crawl.py`.
- `article.html` is the canonical "clean article" fixture for L3
  readability scope tuning tests.

## Confirmations (per provenance-policy backfill template)

- **No customer / client / privileged content** in this directory.
- **No real PII**. The author byline in `article.html` is a fictional
  "Jane Doe"; the Cornell LII capture is a statute page with no
  personal data; httpbin's body is a public-domain literary excerpt;
  books.toscrape.com is a published scraping sandbox.
- **No dep-license-policy denylisted licenses** apply
  (no CC-BY-NC, no AGPL, no SSPL).
