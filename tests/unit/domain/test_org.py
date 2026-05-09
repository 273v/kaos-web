"""Tests for ``kaos_web.domain.org`` — Organization entity extraction.

Real Schema.org JSON-LD blobs (modeled on actual public business sites)
exercise the full extraction pipeline. No network — pure HTML parsing.
"""

from __future__ import annotations

import pytest

from kaos_web.domain.org import (
    _ENTITY_FORM_PATTERNS,
    OrgAddress,
    OrgEntity,
    _extract_from_footer,
    _extract_from_jsonld,
    _extract_from_opengraph,
    extract_org_entity,
)

# ── Models ──────────────────────────────────────────────────────────


class TestModels:
    def test_org_entity_default(self) -> None:
        e = OrgEntity()
        assert e.name is None
        assert e.social_links == {}
        assert e.sources == []

    def test_org_address_default(self) -> None:
        a = OrgAddress()
        assert a.street is None

    def test_org_entity_with_address(self) -> None:
        e = OrgEntity(
            name="ACME",
            address=OrgAddress(street="123 Main St", city="Wilmington", state="DE"),
        )
        assert e.address is not None
        assert e.address.city == "Wilmington"


# ── _extract_from_footer ────────────────────────────────────────────


class TestExtractFromFooter:
    def test_us_state_llc(self) -> None:
        text = "Acme Holdings LLC is a Delaware limited liability company. All rights reserved."
        result = _extract_from_footer(text)
        assert result["jurisdiction"] == "Delaware"
        # The first jurisdiction pattern captures the entity form as
        # group(2) — for a Delaware LLC that's "LLC" (alternation winner).
        assert result["entity_form"] is not None
        assert result["entity_form"].lower() in ("llc", "limited liability company")

    def test_us_state_inc(self) -> None:
        text = "Foo Bar Inc., a California corporation, is headquartered in San Francisco."
        result = _extract_from_footer(text)
        assert result["jurisdiction"] == "California"
        # _JURISDICTION_PATTERNS[0] declares ``field2 = "entity_form"`` but
        # the entity-form alternative inside the regex is a non-capturing
        # group, so the jurisdiction pattern itself does not set
        # entity_form. Fallback _ENTITY_FORM_PATTERNS catches "Inc." from
        # the company name. (WEB2-002 fix made this fallback actually work
        # for dotted forms.)
        assert result.get("entity_form") == "Inc."

    def test_organized_under_state_law(self) -> None:
        text = "We are organized under the laws of the State of Nevada."
        result = _extract_from_footer(text)
        assert result["jurisdiction"] == "Nevada"

    def test_uk_company(self) -> None:
        text = "Acme Limited is registered in England and Wales, Company Number: 12345678."
        result = _extract_from_footer(text)
        assert result["jurisdiction"] == "England and Wales"
        # the second capture group captures the registration number
        assert result.get("registration_number") == "12345678"

    def test_generic_company_number(self) -> None:
        text = "Company No. 09-876543"
        result = _extract_from_footer(text)
        assert result.get("registration_number") == "09-876543"

    def test_abn_australia(self) -> None:
        text = "ABN: 12 345 678 901"
        result = _extract_from_footer(text)
        # The pattern strips no whitespace, so the captured value matches the raw
        regnum = result.get("registration_number")
        assert regnum is not None
        assert "12" in regnum

    def test_entity_form_only(self) -> None:
        # The entity-form regex matches LLC/Ltd/Corp/etc. with word boundaries
        # — so "ACME Limited operates worldwide." matches "Limited".
        text = "ACME Limited operates worldwide."
        result = _extract_from_footer(text)
        form = result.get("entity_form")
        assert form is not None
        assert "Limited" in form

    def test_no_match(self) -> None:
        result = _extract_from_footer("Just some random page text without legal info.")
        assert result.get("jurisdiction") is None
        assert result.get("registration_number") is None

    def test_entity_form_fallback_inc(self) -> None:
        # No jurisdiction pattern matches → fallback to _ENTITY_FORM_PATTERNS.
        # WEB2-002: prior to the regex fix, "Acme Inc." returned None here
        # because the trailing \b couldn't match between "." and " " (both
        # non-word). This test guards the fix.
        text = "Acme Inc. is a worldwide leader."
        result = _extract_from_footer(text)
        assert result.get("entity_form") == "Inc."

    def test_entity_form_fallback_corp(self) -> None:
        text = "Beta Corp. ships worldwide."
        result = _extract_from_footer(text)
        assert result.get("entity_form") == "Corp."

    def test_entity_form_fallback_ltd(self) -> None:
        text = "Gamma Ltd. (the company) operates from London."
        result = _extract_from_footer(text)
        assert result.get("entity_form") == "Ltd."

    def test_entity_form_fallback_pa(self) -> None:
        text = "Smith & Jones P.A. is a professional association."
        result = _extract_from_footer(text)
        assert result.get("entity_form") == "P.A."

    def test_entity_form_fallback_sa(self) -> None:
        text = "Société Générale S.A. operates in many countries."
        result = _extract_from_footer(text)
        assert result.get("entity_form") == "S.A."


# ── _ENTITY_FORM_PATTERNS (WEB2-002) ────────────────────────────────


class TestEntityFormPatterns:
    """Direct coverage of the entity-form regex.

    WEB2-002 (audit-02 follow-up): the original pattern used a trailing
    ``\\b`` after every dotted alternative (``Inc.``, ``Corp.``, ``Ltd.``,
    ``P.A.``, ``S.A.``, ``L.L.C.``, ``S.r.l.``, ``PLLC``…). ``\\b`` cannot
    match between ``.`` (non-word) and a following space, comma, or
    end-of-string (also non-word), so 8 of 14 alternatives were silently
    broken. The fix replaces ``\\b`` with a ``(?![A-Za-z])`` lookahead.
    """

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            # Dotted forms — these were ALL broken before the fix.
            ("Acme Inc.", "Inc."),
            ("Acme Inc. and friends", "Inc."),
            ("Beta Corp.", "Corp."),
            ("Beta Corp. is great", "Corp."),
            ("Gamma Ltd.", "Ltd."),
            ("Gamma Ltd. operates", "Ltd."),
            ("Tech L.L.C.", "L.L.C."),
            ("Tech L.L.C., LLC", "L.L.C."),
            ("Smith P.A.", "P.A."),
            ("Smith P.A. associates", "P.A."),
            ("ACME P.L.C.", "P.L.C."),
            ("Foo S.A.", "S.A."),
            ("Bar S.r.l.", "S.r.l."),
            ("Quux L.L.P.", "L.L.P."),
            # Non-dotted forms — were already working, retain as guards.
            ("Foo LLC", "LLC"),
            ("Bar LLP", "LLP"),
            ("Baz Corporation", "Corporation"),
            ("Qux Limited", "Limited"),
            ("Plc PLC operates", "PLC"),
            ("Tech GmbH", "GmbH"),
            ("Group AG", "AG"),
            ("Pro PLLC", "PLLC"),
            ("Acme Incorporated", "Incorporated"),
            ("Smith Professional Association", "Professional Association"),
        ],
    )
    def test_matches(self, text: str, expected: str) -> None:
        m = _ENTITY_FORM_PATTERNS.search(text)
        assert m is not None, f"{text!r} should match {expected!r}"
        # Case-insensitive compare — pattern uses re.IGNORECASE.
        assert m.group(1).lower() == expected.lower()

    @pytest.mark.parametrize(
        "text",
        [
            # Substring-of-word should NOT match — the (?![A-Za-z]) trailing
            # lookahead and the leading \b together exclude these.
            "IncorporatedX",  # 'Incorporated' followed by letter
            "LLCfoo",  # 'LLC' followed by letter
            "Corporationsomething",
            "CorpX",  # 'Corp' (no dot) is not in the alternation; CorpX has no match
            "Justrandomtext",
            "ltd",  # bare 'ltd' (no dot) — pattern requires "Ltd\." or "Limited"
        ],
    )
    def test_no_match(self, text: str) -> None:
        m = _ENTITY_FORM_PATTERNS.search(text)
        assert m is None, f"{text!r} should NOT match (got {m.group(0) if m else None!r})"

    def test_alternation_picks_first_winner(self) -> None:
        # `LLC|L.L.C.` ordering: for input "L.L.C." the first alternative
        # ``LLC`` cannot match (next char is "."), so the regex falls
        # through to ``L.L.C.``.
        m = _ENTITY_FORM_PATTERNS.search("Tech L.L.C., Inc.")
        assert m is not None
        assert m.group(1) == "L.L.C."


# ── _extract_from_jsonld ────────────────────────────────────────────


class TestExtractFromJsonld:
    def test_basic_organization(self) -> None:
        data = [
            {
                "@type": "Organization",
                "name": "Acme Corp",
                "legalName": "Acme Corporation, Inc.",
                "url": "https://acme.example.com",
                "description": "A worldwide leader in coyote-related products.",
                "logo": "https://acme.example.com/logo.png",
                "email": "info@acme.example.com",
                "telephone": "+1-555-555-5555",
                "foundingDate": "1949-01-01",
            }
        ]
        e = _extract_from_jsonld(data)
        assert e is not None
        assert e.name == "Acme Corp"
        assert e.legal_name == "Acme Corporation, Inc."
        assert e.org_type == "Organization"
        assert e.url == "https://acme.example.com"
        assert e.email == "info@acme.example.com"
        assert e.phone == "+1-555-555-5555"
        assert e.logo == "https://acme.example.com/logo.png"
        assert e.founding_date == "1949-01-01"
        assert "json-ld" in e.sources

    def test_with_address(self) -> None:
        data = [
            {
                "@type": "LocalBusiness",
                "name": "Local Cafe",
                "address": {
                    "@type": "PostalAddress",
                    "streetAddress": "123 Main St",
                    "addressLocality": "Springfield",
                    "addressRegion": "IL",
                    "postalCode": "62701",
                    "addressCountry": "US",
                },
            }
        ]
        e = _extract_from_jsonld(data)
        assert e is not None
        assert e.address is not None
        assert e.address.street == "123 Main St"
        assert e.address.city == "Springfield"
        assert e.address.state == "IL"
        assert e.address.country == "US"

    def test_with_social_links(self) -> None:
        data = [
            {
                "@type": "Organization",
                "name": "Tech Co",
                "sameAs": [
                    "https://www.linkedin.com/company/techco",
                    "https://twitter.com/techco",
                    "https://x.com/techco_official",
                    "https://www.facebook.com/techco",
                    "https://github.com/techco",
                    "https://example.com/blog",
                ],
            }
        ]
        e = _extract_from_jsonld(data)
        assert e is not None
        assert e.social_links["linkedin"].endswith("techco")
        # twitter.com and x.com both map to twitter — last write wins
        assert "twitter" in e.social_links
        assert "facebook" in e.social_links
        assert "github" in e.social_links

    def test_same_as_string(self) -> None:
        data = [
            {
                "@type": "Organization",
                "name": "SoloSocial",
                "sameAs": "https://linkedin.com/company/solo",
            }
        ]
        e = _extract_from_jsonld(data)
        assert e is not None
        assert "linkedin" in e.social_links

    def test_logo_object(self) -> None:
        # logo can be {"@type": "ImageObject", "url": "..."}
        data = [
            {
                "@type": "Organization",
                "name": "ImgCo",
                "logo": {"@type": "ImageObject", "url": "https://imgco.example/logo.png"},
            }
        ]
        e = _extract_from_jsonld(data)
        assert e is not None
        assert e.logo == "https://imgco.example/logo.png"

    def test_logo_object_content_url(self) -> None:
        data = [
            {
                "@type": "Organization",
                "name": "ImgCo",
                "logo": {"contentUrl": "https://imgco.example/logo2.png"},
            }
        ]
        e = _extract_from_jsonld(data)
        assert e is not None and e.logo == "https://imgco.example/logo2.png"

    def test_logo_invalid_type(self) -> None:
        data = [{"@type": "Organization", "name": "ImgCo", "logo": 12345}]
        e = _extract_from_jsonld(data)
        assert e is not None and e.logo is None

    def test_type_list(self) -> None:
        # @type can be a list of types
        data = [
            {
                "@type": ["LegalService", "ProfessionalService"],
                "name": "Smith & Jones LLP",
            }
        ]
        e = _extract_from_jsonld(data)
        assert e is not None
        assert e.org_type in ("LegalService", "ProfessionalService")

    def test_type_list_no_match(self) -> None:
        # @type is a list but contains nothing matching _ORG_TYPES → fallback
        # to the first element. If that first element isn't a known type
        # → entity not picked up.
        data = [{"@type": ["WebPage", "Article"], "name": "A page"}]
        e = _extract_from_jsonld(data)
        assert e is None

    def test_graph_nesting(self) -> None:
        # Schema.org @graph wrapper (very common in WordPress sites)
        data = [
            {
                "@context": "https://schema.org",
                "@graph": [
                    {"@type": "WebPage", "name": "Home"},
                    {
                        "@type": "Organization",
                        "name": "Nested Co",
                        "url": "https://nested.example",
                    },
                ],
            }
        ]
        e = _extract_from_jsonld(data)
        assert e is not None
        assert e.name == "Nested Co"

    def test_no_org_type(self) -> None:
        data = [{"@type": "WebPage", "name": "A page"}]
        e = _extract_from_jsonld(data)
        assert e is None

    def test_empty_data(self) -> None:
        assert _extract_from_jsonld([]) is None


# ── _extract_from_opengraph ─────────────────────────────────────────


class TestExtractFromOpengraph:
    def test_basic(self) -> None:
        from lxml import html as lxml_html

        doc = lxml_html.document_fromstring(
            """
            <html>
              <head>
                <meta property="og:site_name" content="Acme" />
                <meta property="og:description" content="Leader in stuff" />
                <meta property="og:url" content="https://acme.example/" />
                <meta property="og:image" content="https://acme.example/og.png" />
                <meta property="og:title" content="Home" />
                <meta name="description" content="non-og description" />
              </head>
            </html>
            """
        )
        og = _extract_from_opengraph(doc)
        assert og["site_name"] == "Acme"
        assert og["description"] == "Leader in stuff"
        assert og["url"] == "https://acme.example/"
        assert og["image"] == "https://acme.example/og.png"
        assert og["title"] == "Home"


# ── extract_org_entity (full pipeline) ──────────────────────────────


_HTML_FULL = """\
<!DOCTYPE html>
<html>
  <head>
    <title>Acme Corp | Coyote Solutions</title>
    <meta name="description" content="Meta description fallback" />
    <meta property="og:site_name" content="Acme via OG" />
    <meta property="og:description" content="OG description" />
    <meta property="og:image" content="https://acme.example/og.png" />
    <script type="application/ld+json">
      {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": "Acme Corp",
        "legalName": "Acme Corporation, Inc.",
        "url": "https://acme.example",
        "logo": "https://acme.example/logo.png",
        "sameAs": ["https://linkedin.com/company/acme"]
      }
    </script>
    <script type="application/ld+json">{ "invalid json broken </script>
  </head>
  <body>
    <main><p>Hello world</p></main>
    <footer>
      Acme Corporation, Inc. is a Delaware corporation. Company Number: 1234567
    </footer>
  </body>
</html>
"""

_HTML_OG_ONLY = """\
<!DOCTYPE html>
<html>
  <head>
    <title>Page | Brand</title>
    <meta property="og:site_name" content="OG Only Brand" />
    <meta property="og:description" content="OG only description" />
    <meta property="og:image" content="https://example/og.png" />
  </head>
  <body><p>content</p></body>
</html>
"""

_HTML_TITLE_ONLY = """\
<!DOCTYPE html>
<html>
  <head>
    <title>Brandname — Tagline</title>
    <meta name="description" content="meta desc" />
  </head>
  <body><p>content</p></body>
</html>
"""

_HTML_LAW_FIRM = """\
<!DOCTYPE html>
<html>
  <head>
    <title>Smith & Jones</title>
    <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": "LegalService",
      "name": "Smith & Jones LLP",
      "address": {
        "@type": "PostalAddress",
        "streetAddress": "456 Court St",
        "addressLocality": "Wilmington",
        "addressRegion": "DE",
        "postalCode": "19801",
        "addressCountry": "US"
      }
    }
    </script>
  </head>
  <body>
    <footer>
      Smith & Jones LLP is a Delaware limited liability partnership.
      Registered in England and Wales, Company Number: 87654321.
    </footer>
  </body>
</html>
"""


class TestExtractOrgEntity:
    def test_empty_html(self) -> None:
        e = extract_org_entity("")
        assert e == OrgEntity()

    def test_whitespace_only(self) -> None:
        e = extract_org_entity("   \n\t  ")
        assert e == OrgEntity()

    def test_jsonld_priority(self) -> None:
        # JSON-LD beats OpenGraph and title for the name
        e = extract_org_entity(_HTML_FULL, url="https://acme.example/")
        assert e.name == "Acme Corp"
        assert e.legal_name == "Acme Corporation, Inc."
        assert e.url == "https://acme.example"
        assert e.logo == "https://acme.example/logo.png"
        # Footer-derived fields layered on top
        assert e.jurisdiction == "Delaware"
        assert e.registration_number == "1234567"
        assert "json-ld" in e.sources
        assert "footer-text" in e.sources
        assert "linkedin" in e.social_links

    def test_og_fallback(self) -> None:
        e = extract_org_entity(_HTML_OG_ONLY)
        assert e.name == "OG Only Brand"
        assert e.description == "OG only description"
        assert "opengraph" in e.sources
        assert e.logo == "https://example/og.png"

    def test_title_fallback(self) -> None:
        # No JSON-LD, no OG site_name → title parsed
        e = extract_org_entity(_HTML_TITLE_ONLY)
        # Em-dash separator splits the title
        assert e.name == "Brandname"
        assert e.description == "meta desc"
        assert "html-title" in e.sources

    def test_url_fallback(self) -> None:
        # No URL in JSON-LD/OG, but provided as kwarg
        html = "<html><head><title>X</title></head><body></body></html>"
        e = extract_org_entity(html, url="https://x.example/")
        assert e.url == "https://x.example/"

    def test_law_firm_full_extraction(self) -> None:
        e = extract_org_entity(_HTML_LAW_FIRM)
        assert e.name == "Smith & Jones LLP"
        assert e.org_type == "LegalService"
        assert e.address is not None
        assert e.address.street == "456 Court St"
        assert e.address.country == "US"
        # Footer adds jurisdiction. The Delaware LLP pattern matches first;
        # in either case, jurisdiction should not be empty.
        assert e.jurisdiction is not None

    def test_invalid_html(self) -> None:
        # lxml is very permissive; truly malformed input still works,
        # but we exercise the bare-bones except path with a binary input that
        # cannot be parsed as a document.
        e = extract_org_entity("\x00\x01\x02")
        # Either succeeds with empty entity or returns empty after parse failure
        assert isinstance(e, OrgEntity)


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Brand | Tagline", "Brand"),
        ("Brand - Tagline", "Brand"),
        ("Brand — Tagline", "Brand"),
        ("Brand :: Section", "Brand"),
        ("Brand » Section", "Brand"),
        ("Just a Title", "Just a Title"),
    ],
)
def test_title_separators(title: str, expected: str) -> None:
    html = f"<html><head><title>{title}</title></head><body></body></html>"
    e = extract_org_entity(html)
    assert e.name == expected
