# kaos-web readability test fixtures

Provenance manifest per
[`docs/oss/50-data-and-fixtures/provenance-policy.md`](../../../../docs/oss/50-data-and-fixtures/provenance-policy.md).

Every file in this directory is **hand-crafted in-house by 273V** for
the express purpose of exercising the kaos-web L3 readability
extractor on realistic-but-synthetic page archetypes (PACER docket
report, law-firm directory listing, regulator portal landing page,
client-alert search results, team / advisory directory cards, generic
category listing). The synthetic layouts are designed to look like
genuine institutional websites so that the readability scorer's
positive/negative-region decisions get exercised against
plausible-but-novel structure — but the underlying entities, names,
and case numbers are fictional and have no real-world referent.

None of these files came from a customer engagement, internal share,
or pseudonymized client document.

## Per-file manifest

| File | Source URL | License | Retrieved | SHA-256 |
|---|---|---|---|---|
| `docket_report.html` | hand-crafted by 273V — fictional civil docket report (`CIVIL DOCKET FOR CASE #: 3:24-cv-08437-SI`) styled after a PACER docket page. No real case; party names and filings are synthetic. | hand-crafted, 273V | 2026-04-04 (first git commit `979a64b`) | `9b1fd14c6aa0aa1b025b0f4fcfd245a560fae00f0fa6b9552377f81dc314c677` |
| `directory_listing.html` | hand-crafted by 273V — fictional "Example Firm Directory" lawyer-finder page (office filter form + result cards). | hand-crafted, 273V | 2026-04-04 (first git commit `979a64b`) | `9fdd95bf08bca79b906dc40077d5d49e0e2a8862f42c860d6cbe9ef6c2b085a4` |
| `search_results_page.html` | hand-crafted by 273V — fictional "Client Alert Search" results page (search box + matching alert tiles). | hand-crafted, 273V | 2026-04-04 (first git commit `979a64b`) | `db4f4c80c27ea6d8b884d0848911b1d26a0ec2e817e3d88338caa7e4ce872fa8` |
| `team_directory_cards.html` | hand-crafted by 273V — fictional "Advisory Team Directory" card-grid bio page. | hand-crafted, 273V | 2026-04-04 (first git commit `979a64b`) | `8e5eef4ccdd197adfc03ad5ad87493110a6a239d9c069017cdffc9d3a4ca6a1d` |
| `multi_section_landing.html` | hand-crafted by 273V — fictional "Regulatory Insights Portal" multi-section landing page (overview + alerts + events). | hand-crafted, 273V | 2026-04-04 (first git commit `979a64b`) | `8b4cf2b8ba515672b04973e64f9f83d1d68e0b8d0bfb2d28013b84a78c53080e` |
| `category_listing.html` | hand-crafted by 273V — fictional "Books Category Listing" page with sidebar filters + product grid. | hand-crafted, 273V | 2026-04-04 (first git commit `979a64b`) | `95807655918170d9892e7f0a45e5e34c60c652bc72c2f5873554f07a0eeed455` |
| `corpus.json` | hand-crafted by 273V — test-harness configuration mapping each fixture above (plus the three captured pages in the parent directory) to its expected positive / negative XPath regions for the readability experiments harness. | hand-crafted, 273V | 2026-04-04 (first git commit `979a64b`) | `30ee205c011d4120720e0f97427d77b80f290ba0e3bed700d8601ab0c186af64` |

## Notes

- `corpus.json` cross-references the three captured pages in the parent
  directory (`../article.html`, `../books_toscrape.html`,
  `../cornell_law.html`, `../httpbin.html`); see `../README.md` for
  the provenance of those files.
- Consumers: `tests/unit/test_readability_l3.py`,
  `tests/unit/test_readability_experiments.py`, and the readability
  experiment scripts under `kaos-web/scripts/`.

## Confirmations (per provenance-policy backfill template)

- **No customer / client / privileged content** in this directory.
- **No real PII**. All party names, attorney names, case numbers,
  email addresses, and entity names are fictional placeholders
  authored for fixture purposes only.
- **No dep-license-policy denylisted licenses** apply (everything is
  273V-authored work product).
