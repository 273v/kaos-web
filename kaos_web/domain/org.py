"""Organization entity extraction from websites.

Extracts legal entity information from HTML using:
1. JSON-LD structured data (Schema.org Organization, LegalService, etc.)
2. OpenGraph metadata
3. HTML meta tags
4. Footer text patterns (registration numbers, jurisdiction statements)

No LLM required — pure structural extraction.
"""

from __future__ import annotations

import json
import re
from typing import Any

from lxml import html as lxml_html
from pydantic import BaseModel, ConfigDict, Field

# ── Models ──────────────────────────────────────────────────────────


class OrgAddress(BaseModel):
    """Extracted address."""

    model_config = ConfigDict(extra="forbid")

    street: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = None
    raw: str | None = Field(None, description="Unstructured address text")


class OrgEntity(BaseModel):
    """Structured organization entity extracted from a website."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    legal_name: str | None = Field(None, description="Formal legal entity name")
    org_type: str | None = Field(
        None, description="Schema.org @type (Organization, LegalService, etc.)"
    )
    description: str | None = None
    url: str | None = None
    logo: str | None = None
    email: str | None = None
    phone: str | None = None
    address: OrgAddress | None = None
    founding_date: str | None = None
    social_links: dict[str, str] = Field(default_factory=dict)
    # Legal entity details (from footer/about page patterns)
    jurisdiction: str | None = Field(None, description="Incorporation/registration jurisdiction")
    registration_number: str | None = Field(None, description="Company/entity registration number")
    entity_form: str | None = Field(None, description="LLC, LLP, Inc., Ltd., etc.")
    # Source tracking
    sources: list[str] = Field(default_factory=list, description="Where data was extracted from")


# ── Schema.org type mapping ─────────────────────────────────────────

_ORG_TYPES = frozenset(
    {
        "Organization",
        "Corporation",
        "LocalBusiness",
        "LegalService",
        "ProfessionalService",
        "FinancialService",
        "GovernmentOrganization",
        "NGO",
        "EducationalOrganization",
        "MedicalOrganization",
        "SportsOrganization",
        "PerformingGroup",
        "Airline",
        "LawFirm",
        "AccountingService",
        "Attorney",
    }
)

_SOCIAL_DOMAINS: dict[str, str] = {
    "linkedin.com": "linkedin",
    "twitter.com": "twitter",
    "x.com": "twitter",
    "facebook.com": "facebook",
    "instagram.com": "instagram",
    "github.com": "github",
    "youtube.com": "youtube",
}


# ── Jurisdiction patterns ───────────────────────────────────────────

_US_STATE_PATTERN = (
    "Delaware|Nevada|California|New York|Texas|Florida|Wyoming|Illinois|"
    "Massachusetts|Georgia|Washington|Colorado|Ohio|Virginia|Maryland|"
    "New Jersey|Pennsylvania|Connecticut|North Carolina"
)

_JURISDICTION_PATTERNS: list[tuple[re.Pattern[str], str, str | None]] = [
    # US state entities
    (
        re.compile(
            rf"(?:a|an)\s+({_US_STATE_PATTERN})\s+"
            r"(?:limited liability company|LLC|corporation|Inc\.|incorporated|"
            r"L\.?L\.?C\.?|L\.?L\.?P\.?|limited partnership)",
            re.IGNORECASE,
        ),
        "jurisdiction",
        "entity_form",
    ),
    (
        re.compile(
            rf"(?:incorporated|organized|formed|registered)\s+"
            r"(?:in|under the laws of)\s+(?:the\s+)?(?:State\s+of\s+)?"
            rf"({_US_STATE_PATTERN})",
            re.IGNORECASE,
        ),
        "jurisdiction",
        None,
    ),
    # UK entities
    (
        re.compile(
            r"(?:registered|incorporated)\s+in\s+"
            r"(England(?:\s+and\s+Wales)?|Scotland|Northern Ireland)"
            r"(?:,?\s+(?:Company|Registration)\s+"
            r"(?:No\.?|Number:?)\s*(\w+))?",
            re.IGNORECASE,
        ),
        "jurisdiction",
        "registration_number",
    ),
    # Generic registration number
    (
        re.compile(
            r"(?:Company|Registration|Entity)\s+(?:No\.?|Number|#):?\s*(\d[\d\-]+\d)", re.IGNORECASE
        ),
        "registration_number",
        None,
    ),
    # ABN (Australia)
    (
        re.compile(r"ABN:?\s*(\d{2}\s*\d{3}\s*\d{3}\s*\d{3})", re.IGNORECASE),
        "registration_number",
        None,
    ),
]

_ENTITY_FORM_PATTERNS = re.compile(
    r"\b(LLC|L\.L\.C\.|LLP|L\.L\.P\.|Inc\.|Incorporated|Ltd\.|Limited|Corp\.|"
    r"Corporation|PLC|P\.L\.C\.|GmbH|AG|S\.A\.|S\.r\.l\.|PLLC|P\.A\.|"
    r"Professional Association)(?![A-Za-z])",
    re.IGNORECASE,
)


# ── Extraction ──────────────────────────────────────────────────────


def _extract_from_jsonld(structured_data: list[dict[str, Any]]) -> OrgEntity | None:
    """Extract org entity from JSON-LD structured data."""
    for item in structured_data:
        item_type = item.get("@type", "")
        # Handle list types like ["Organization", "LocalBusiness"]
        if isinstance(item_type, list):
            matching = [t for t in item_type if t in _ORG_TYPES]
            item_type = matching[0] if matching else item_type[0] if item_type else ""
        if item_type not in _ORG_TYPES:
            # Check @graph for nested entities
            graph = item.get("@graph", [])
            for node in graph:
                node_type = node.get("@type", "")
                if isinstance(node_type, list):
                    matching = [t for t in node_type if t in _ORG_TYPES]
                    node_type = matching[0] if matching else ""
                if node_type in _ORG_TYPES:
                    item = node
                    item_type = node_type
                    break
            else:
                continue

        # Parse address
        address = None
        addr_data = item.get("address", {})
        if isinstance(addr_data, dict):
            address = OrgAddress(
                street=addr_data.get("streetAddress"),
                city=addr_data.get("addressLocality"),
                state=addr_data.get("addressRegion"),
                postal_code=addr_data.get("postalCode"),
                country=addr_data.get("addressCountry"),
            )

        # Parse social links
        social: dict[str, str] = {}
        same_as = item.get("sameAs", [])
        if isinstance(same_as, str):
            same_as = [same_as]
        for url in same_as:
            if isinstance(url, str):
                for domain, platform in _SOCIAL_DOMAINS.items():
                    if domain in url.lower():
                        social[platform] = url
                        break

        # Logo
        logo = item.get("logo")
        if isinstance(logo, dict):
            logo = logo.get("url") or logo.get("contentUrl")

        return OrgEntity(
            name=item.get("name"),
            legal_name=item.get("legalName"),
            org_type=item_type,
            description=item.get("description"),
            url=item.get("url"),
            logo=logo if isinstance(logo, str) else None,
            email=item.get("email"),
            phone=item.get("telephone"),
            address=address,
            founding_date=item.get("foundingDate"),
            social_links=social,
            sources=["json-ld"],
        )

    return None


def _extract_from_opengraph(doc: Any) -> dict[str, str]:
    """Extract OpenGraph properties."""
    og: dict[str, str] = {}
    for meta in doc.iter("meta"):
        prop = meta.get("property", "")
        content = meta.get("content", "")
        if prop.startswith("og:") and content:
            og[prop[3:]] = content
    return og


def _extract_from_footer(text: str) -> dict[str, str | None]:
    """Extract legal entity info from footer/page text via regex."""
    results: dict[str, str | None] = {}

    for pattern, field1, field2 in _JURISDICTION_PATTERNS:
        match = pattern.search(text)
        if match:
            groups = match.groups()
            if groups and field1 and field1 not in results:
                results[field1] = groups[0].strip()
            if len(groups) > 1 and field2 and groups[1] and field2 not in results:
                results[field2] = groups[1].strip()

    # Entity form (LLC, Inc., etc.)
    if "entity_form" not in results:
        form_match = _ENTITY_FORM_PATTERNS.search(text)
        if form_match:
            results["entity_form"] = form_match.group(1).strip()

    return results


def extract_org_entity(
    html: str,
    *,
    url: str = "",
) -> OrgEntity:
    """Extract organization entity data from HTML.

    Combines JSON-LD structured data, OpenGraph metadata, HTML meta tags,
    and footer text patterns into a single OrgEntity.

    Args:
        html: Raw HTML string.
        url: Source URL.

    Returns:
        OrgEntity with all extracted fields (may be partially populated).
    """
    if not html or not html.strip():
        return OrgEntity()

    try:
        doc = lxml_html.document_fromstring(html)
    except Exception:
        return OrgEntity()

    # 1. JSON-LD extraction (highest priority)
    structured_data: list[dict[str, Any]] = []
    for script in doc.iter("script"):
        if script.get("type", "").lower() == "application/ld+json":
            raw = script.text_content()
            if raw:
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        structured_data.append(parsed)
                    elif isinstance(parsed, list):
                        structured_data.extend(d for d in parsed if isinstance(d, dict))
                except (json.JSONDecodeError, ValueError):
                    continue

    entity = _extract_from_jsonld(structured_data)
    if entity is None:
        entity = OrgEntity()

    # 2. OpenGraph fallback
    og = _extract_from_opengraph(doc)
    if not entity.name and og.get("site_name"):
        entity = entity.model_copy(
            update={"name": og["site_name"], "sources": [*entity.sources, "opengraph"]}
        )
    if not entity.description and og.get("description"):
        entity = entity.model_copy(update={"description": og["description"]})
    if not entity.url and og.get("url"):
        entity = entity.model_copy(update={"url": og["url"]})
    if not entity.logo and og.get("image"):
        entity = entity.model_copy(update={"logo": og["image"]})

    # 3. Meta tag fallback
    for meta in doc.iter("meta"):
        name_attr = meta.get("name", "").lower()
        content = meta.get("content", "")
        if not content:
            continue
        if not entity.description and name_attr == "description":
            entity = entity.model_copy(update={"description": content})

    # 4. Title fallback for name
    if not entity.name:
        title_el = doc.find(".//title")
        if title_el is not None and title_el.text:
            # Take first segment before separators
            title = title_el.text.strip()
            for sep in (" | ", " - ", " — ", " :: ", " » "):
                if sep in title:
                    title = title.split(sep)[0].strip()
                    break
            entity = entity.model_copy(
                update={"name": title, "sources": [*entity.sources, "html-title"]}
            )

    # 5. Footer/legal text extraction
    full_text = doc.text_content()
    footer_data = _extract_from_footer(full_text)
    updates: dict[str, Any] = {}
    if footer_data.get("jurisdiction") and not entity.jurisdiction:
        updates["jurisdiction"] = footer_data["jurisdiction"]
    if footer_data.get("registration_number") and not entity.registration_number:
        updates["registration_number"] = footer_data["registration_number"]
    if footer_data.get("entity_form") and not entity.entity_form:
        updates["entity_form"] = footer_data["entity_form"]
    if updates:
        updates["sources"] = [*entity.sources, "footer-text"]
        entity = entity.model_copy(update=updates)

    # 6. URL fallback
    if not entity.url and url:
        entity = entity.model_copy(update={"url": url})

    return entity
