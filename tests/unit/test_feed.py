"""Unit tests for the RSS / Atom feed parser.

Lives in tests/unit/ so it runs on every PR. The live counterpart in
``tests/integration/test_web_live_extraction_matrix.py`` exercises
real publishers; this file is fixture-based and offline.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from kaos_web.extract import parse_feed
from kaos_web.extract.feed import FeedItem, FeedResult

# ─── RSS 2.0 fixtures ────────────────────────────────────────────────────────


SEC_LIKE_RSS = b"""<?xml version="1.0" encoding="utf-8"?>
<rss xmlns:dc="http://purl.org/dc/elements/1.1/" version="2.0" xml:base="https://www.sec.gov/">
  <channel>
    <title>Press Releases</title>
    <link>https://www.sec.gov/</link>
    <description>Official announcements highlighting recent actions taken by the SEC.</description>
    <language>en</language>
    <item>
      <title>SEC and NFA Announce MOU</title>
      <link>https://www.sec.gov/newsroom/press-releases/2026-47</link>
      <description>The SEC and NFA today announced an MOU.</description>
      <pubDate>Thu, 21 May 2026 08:51:10 -0400</pubDate>
      <dc:creator>Press Office</dc:creator>
      <guid isPermaLink="false">91a62b49-3f57-4072-9ca9-133ad58c78bd</guid>
      <category>enforcement</category>
    </item>
    <item>
      <title>SEC Proposes Reforms</title>
      <link>https://www.sec.gov/newsroom/press-releases/2026-46</link>
      <description>Today's proposal.</description>
      <pubDate>Tue, 19 May 2026 10:55:00 -0400</pubDate>
    </item>
  </channel>
</rss>
"""


# ─── Atom 1.0 fixtures ───────────────────────────────────────────────────────


GITHUB_LIKE_ATOM = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xml:lang="en-US">
  <id>tag:github.com,2008:https://github.com/python/cpython/releases</id>
  <link type="text/html" rel="alternate" href="https://github.com/python/cpython/releases"/>
  <link type="application/atom+xml" rel="self" href="https://github.com/python/cpython/releases.atom"/>
  <title>Release notes from cpython</title>
  <updated>2026-05-10T10:21:34Z</updated>
  <entry>
    <id>tag:github.com,2008:Repository/81598/v3.14.5</id>
    <updated>2026-05-10T10:21:34Z</updated>
    <link rel="alternate" type="text/html" href="https://github.com/python/cpython/releases/tag/v3.14.5"/>
    <title>v3.14.5</title>
    <content type="html">&lt;p&gt;Bugfix release&lt;/p&gt;</content>
    <author><name>release-tools</name></author>
    <category term="release"/>
  </entry>
</feed>
"""


# ─── RSS 2.0 ────────────────────────────────────────────────────────────────


class TestRSSParser:
    def test_parses_channel_metadata(self):
        result = parse_feed(SEC_LIKE_RSS)
        assert result.format == "rss"
        assert result.title == "Press Releases"
        assert result.link == "https://www.sec.gov/"
        assert "actions taken by the SEC" in (result.description or "")

    def test_parses_items_in_order(self):
        result = parse_feed(SEC_LIKE_RSS)
        assert len(result.items) == 2
        assert result.items[0].title == "SEC and NFA Announce MOU"
        assert result.items[1].title == "SEC Proposes Reforms"

    def test_parses_pub_date_rfc822(self):
        result = parse_feed(SEC_LIKE_RSS)
        first = result.items[0]
        assert first.pub_date is not None
        assert first.pub_date.year == 2026
        assert first.pub_date.month == 5
        assert first.pub_date.day == 21
        # Has tz info preserved
        assert first.pub_date.tzinfo is not None

    def test_pulls_dc_creator_as_author(self):
        """RSS 2.0 spec uses <author>; SEC uses <dc:creator>. Parser should
        accept either."""
        result = parse_feed(SEC_LIKE_RSS)
        assert result.items[0].author == "Press Office"
        # Item 2 has no creator; should be None
        assert result.items[1].author is None

    def test_pulls_guid(self):
        result = parse_feed(SEC_LIKE_RSS)
        assert result.items[0].guid == "91a62b49-3f57-4072-9ca9-133ad58c78bd"
        assert result.items[1].guid is None

    def test_pulls_categories(self):
        result = parse_feed(SEC_LIKE_RSS)
        assert result.items[0].categories == ("enforcement",)
        assert result.items[1].categories == ()


# ─── Atom 1.0 ───────────────────────────────────────────────────────────────


class TestAtomParser:
    def test_parses_feed_metadata(self):
        result = parse_feed(GITHUB_LIKE_ATOM)
        assert result.format == "atom"
        assert result.title == "Release notes from cpython"
        # Should pick the rel="alternate" link, NOT rel="self".
        assert result.link == "https://github.com/python/cpython/releases"

    def test_parses_entry(self):
        result = parse_feed(GITHUB_LIKE_ATOM)
        assert len(result.items) == 1
        entry = result.items[0]
        assert entry.title == "v3.14.5"
        assert entry.link == "https://github.com/python/cpython/releases/tag/v3.14.5"

    def test_parses_atom_updated_as_pub_date(self):
        result = parse_feed(GITHUB_LIKE_ATOM)
        assert result.items[0].pub_date == datetime(2026, 5, 10, 10, 21, 34, tzinfo=UTC)

    def test_atom_author_name_resolved(self):
        result = parse_feed(GITHUB_LIKE_ATOM)
        assert result.items[0].author == "release-tools"

    def test_atom_category_term(self):
        result = parse_feed(GITHUB_LIKE_ATOM)
        assert result.items[0].categories == ("release",)


# ─── Defensive parsing ──────────────────────────────────────────────────────


class TestDefensive:
    def test_empty_returns_unknown(self):
        assert parse_feed(b"").format == "unknown"

    def test_whitespace_returns_unknown(self):
        assert parse_feed(b"   \n\t  ").format == "unknown"

    def test_truncated_xml_recovers(self):
        # Truncated mid-item — lxml recover=True should still parse the channel
        truncated = SEC_LIKE_RSS.split(b"<pubDate>")[0]
        result = parse_feed(truncated)
        # Either format=rss with partial items, or format=unknown — never crash
        assert result.format in ("rss", "unknown")

    def test_html_404_page_unknown(self):
        result = parse_feed(b"<html><body>404 Not Found</body></html>")
        assert result.format == "unknown"
        assert result.items == ()

    def test_atom_self_link_ignored_when_alternate_present(self):
        result = parse_feed(GITHUB_LIKE_ATOM)
        # Must prefer rel="alternate" over rel="self"
        assert result.link is not None
        assert "alternate" not in result.link  # link is the bare URL
        assert not result.link.endswith(".atom")

    def test_str_input_accepted(self):
        """parse_feed should accept str OR bytes."""
        result = parse_feed(SEC_LIKE_RSS.decode("utf-8"))
        assert result.format == "rss"
        assert len(result.items) == 2

    def test_no_xxe_via_entity_substitution(self):
        """External entity references must not be resolved (XXE defense).
        The parser passes ``resolve_entities=False`` to lxml."""
        evil = b"""<?xml version="1.0"?>
<!DOCTYPE rss [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<rss version="2.0"><channel><title>x</title><item><title>&xxe;</title><link>https://example.com</link></item></channel></rss>"""
        result = parse_feed(evil)
        # Must parse without raising AND must not interpolate /etc/passwd
        assert result.format == "rss"
        # Item title should NOT contain "root:" or any file content
        assert "root:" not in (result.items[0].title if result.items else "")


# ─── Sanity: FeedItem / FeedResult dataclass shape ──────────────────────────


class TestShape:
    def test_feed_item_is_frozen(self):
        import dataclasses

        item = FeedItem(title="t", link="https://example.com")
        # FrozenInstanceError IS an AttributeError; dataclasses re-exports
        # it. Catch via the public symbol so ty doesn't complain about
        # writing to a read-only attribute.
        try:
            object.__setattr__(item, "title", "x")  # bypasses ``frozen=True``
            # Still validate via the public path — should raise
            dataclasses.replace(item, title="x")  # this is fine
            # Direct attribute set MUST raise on a frozen dataclass.
            with pytest.raises(dataclasses.FrozenInstanceError):
                item.title = "x"  # ty: ignore[invalid-assignment]
        except dataclasses.FrozenInstanceError:
            pass

    def test_feed_result_items_is_tuple(self):
        result = FeedResult(format="rss", title=None, link=None, description=None, items=())
        assert isinstance(result.items, tuple)
