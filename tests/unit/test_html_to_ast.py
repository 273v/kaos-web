"""Tests for HTML-to-ContentDocument AST conversion."""

from __future__ import annotations

from pathlib import Path

from kaos_content.model.blocks import (
    BlockQuote,
    BulletList,
    CodeBlock,
    DefinitionList,
    Heading,
    OrderedList,
    Paragraph,
    Table,
    ThematicBreak,
)
from kaos_content.model.inlines import (
    Code,
    Emphasis,
    Image,
    Link,
    Strong,
    Text,
)
from kaos_content.serializers.markdown import serialize_markdown
from kaos_web.extract import html_to_document

FIXTURES = Path(__file__).parent.parent / "fixtures"


class TestBasicExtraction:
    def test_empty_html(self):
        doc = html_to_document("")
        assert len(doc.body) == 0

    def test_simple_paragraph(self):
        doc = html_to_document("<p>Hello world</p>", extract_content=False)
        assert len(doc.body) >= 1
        para = doc.body[0]
        assert isinstance(para, Paragraph)

    def test_heading_depth(self):
        html = "<h1>One</h1><h2>Two</h2><h3>Three</h3>"
        doc = html_to_document(html, extract_content=False)
        headings = [b for b in doc.body if isinstance(b, Heading)]
        assert len(headings) == 3
        assert headings[0].depth == 1
        assert headings[1].depth == 2
        assert headings[2].depth == 3

    def test_inline_formatting(self):
        html = "<p><strong>bold</strong> and <em>italic</em> and <code>code</code></p>"
        doc = html_to_document(html, extract_content=False)
        para = doc.body[0]
        assert isinstance(para, Paragraph)
        types = [type(c) for c in para.children]
        assert Strong in types
        assert Emphasis in types
        assert Code in types

    def test_link_extraction(self):
        html = '<p><a href="https://example.com">Click here</a></p>'
        doc = html_to_document(html, extract_content=False)
        para = doc.body[0]
        assert isinstance(para, Paragraph)
        links = [c for c in para.children if isinstance(c, Link)]
        assert len(links) == 1
        assert links[0].url == "https://example.com"

    def test_image_extraction(self):
        html = '<p><img src="/photo.jpg" alt="A photo"></p>'
        doc = html_to_document(html, url="https://example.com", extract_content=False)
        para = doc.body[0]
        assert isinstance(para, Paragraph)
        images = [c for c in para.children if isinstance(c, Image)]
        assert len(images) == 1
        assert images[0].src == "https://example.com/photo.jpg"
        assert images[0].alt == "A photo"


class TestCodeBlocks:
    def test_fenced_code_with_language(self):
        html = '<pre><code class="language-python">print("hello")</code></pre>'
        doc = html_to_document(html, extract_content=False)
        code_blocks = [b for b in doc.body if isinstance(b, CodeBlock)]
        assert len(code_blocks) == 1
        assert code_blocks[0].language == "python"
        assert 'print("hello")' in code_blocks[0].value

    def test_code_block_no_language(self):
        html = "<pre><code>plain code</code></pre>"
        doc = html_to_document(html, extract_content=False)
        code_blocks = [b for b in doc.body if isinstance(b, CodeBlock)]
        assert len(code_blocks) == 1
        assert code_blocks[0].language is None

    def test_language_from_highlight_class(self):
        html = '<pre><code class="highlight-js">var x = 1;</code></pre>'
        doc = html_to_document(html, extract_content=False)
        code_blocks = [b for b in doc.body if isinstance(b, CodeBlock)]
        assert len(code_blocks) == 1
        assert code_blocks[0].language == "js"


class TestLists:
    def test_bullet_list(self):
        html = "<ul><li>One</li><li>Two</li><li>Three</li></ul>"
        doc = html_to_document(html, extract_content=False)
        lists = [b for b in doc.body if isinstance(b, BulletList)]
        assert len(lists) == 1
        assert len(lists[0].children) == 3

    def test_ordered_list(self):
        html = "<ol><li>First</li><li>Second</li></ol>"
        doc = html_to_document(html, extract_content=False)
        lists = [b for b in doc.body if isinstance(b, OrderedList)]
        assert len(lists) == 1
        assert len(lists[0].children) == 2

    def test_ordered_list_start(self):
        html = '<ol start="5"><li>Fifth</li><li>Sixth</li></ol>'
        doc = html_to_document(html, extract_content=False)
        lists = [b for b in doc.body if isinstance(b, OrderedList)]
        assert len(lists) == 1
        assert lists[0].start == 5


class TestTables:
    def test_simple_table(self):
        html = """
        <table>
            <thead><tr><th>Name</th><th>Value</th></tr></thead>
            <tbody><tr><td>A</td><td>1</td></tr></tbody>
        </table>
        """
        doc = html_to_document(html, extract_content=False)
        tables = [b for b in doc.body if isinstance(b, Table)]
        assert len(tables) == 1
        assert tables[0].head is not None
        assert len(tables[0].head.rows) == 1
        assert len(tables[0].bodies) >= 1


class TestBlockquotes:
    def test_blockquote(self):
        html = "<blockquote><p>A wise quote.</p></blockquote>"
        doc = html_to_document(html, extract_content=False)
        bqs = [b for b in doc.body if isinstance(b, BlockQuote)]
        assert len(bqs) == 1
        assert len(bqs[0].children) >= 1


class TestDefinitionLists:
    def test_definition_list(self):
        html = "<dl><dt>Term</dt><dd>Definition</dd></dl>"
        doc = html_to_document(html, extract_content=False)
        dls = [b for b in doc.body if isinstance(b, DefinitionList)]
        assert len(dls) == 1
        assert len(dls[0].children) == 1
        item = dls[0].children[0]
        assert len(item.term) >= 1
        assert len(item.definitions) >= 1


class TestThematicBreak:
    def test_hr(self):
        html = "<p>Before</p><hr><p>After</p>"
        doc = html_to_document(html, extract_content=False)
        hrs = [b for b in doc.body if isinstance(b, ThematicBreak)]
        assert len(hrs) == 1


class TestURLHandling:
    def test_relative_url_resolution(self):
        html = '<p><a href="/about">About</a></p>'
        doc = html_to_document(html, url="https://example.com/page", extract_content=False)
        para = doc.body[0]
        assert isinstance(para, Paragraph)
        links = [c for c in para.children if isinstance(c, Link)]
        assert links[0].url == "https://example.com/about"

    def test_dangerous_url_stripped(self):
        html = (
            '<p><a href="javascript:alert(1)">Bad</a> and <a href="https://safe.com">safe</a></p>'
        )
        doc = html_to_document(html, extract_content=False)
        para = doc.body[0]
        assert isinstance(para, Paragraph)
        links = [c for c in para.children if isinstance(c, Link)]
        # Only the safe link should survive
        assert len(links) == 1
        assert links[0].url == "https://safe.com"

    def test_lazy_image_data_src(self):
        html = '<p><img data-src="/real.jpg" src="/placeholder.gif" alt="Test"></p>'
        doc = html_to_document(html, url="https://example.com", extract_content=False)
        para = doc.body[0]
        assert isinstance(para, Paragraph)
        images = [c for c in para.children if isinstance(c, Image)]
        assert len(images) == 1
        assert images[0].src == "https://example.com/real.jpg"


class TestProvenance:
    def test_blocks_have_provenance(self):
        html = "<h1>Title</h1><p>Content</p>"
        doc = html_to_document(html, url="https://example.com", extract_content=False)
        for block in doc.body:
            assert block.provenance is not None
            assert block.provenance.source is not None
            assert block.provenance.source.uri == "https://example.com"
            assert block.provenance.extractor == "kaos-web"

    def test_provenance_mime_type(self):
        html = "<p>Text</p>"
        doc = html_to_document(html, url="https://x.com", extract_content=False)
        block = doc.body[0]
        assert block.provenance is not None
        assert block.provenance.source is not None
        assert block.provenance.source.mime_type == "text/html"


class TestEdgeCases:
    def test_empty_paragraph_skipped(self):
        html = "<p></p><p>Real content</p><p>   </p>"
        doc = html_to_document(html, extract_content=False)
        paras = [b for b in doc.body if isinstance(b, Paragraph)]
        assert len(paras) == 1

    def test_script_style_stripped(self):
        html = "<p>Text</p><script>alert(1)</script><style>body{}</style><p>More</p>"
        doc = html_to_document(html, extract_content=False)
        # Only paragraphs, no script/style content
        for block in doc.body:
            assert isinstance(block, Paragraph)

    def test_transparent_div(self):
        html = "<div><p>Inside div</p></div>"
        doc = html_to_document(html, extract_content=False)
        paras = [b for b in doc.body if isinstance(b, Paragraph)]
        assert len(paras) >= 1

    def test_metadata_title(self):
        html = "<html><head><title>My Title</title></head><body><p>Text</p></body></html>"
        doc = html_to_document(html, extract_content=False)
        assert doc.metadata.title == "My Title"


class TestReadabilityIntegration:
    def test_article_fixture(self):
        html = (FIXTURES / "article.html").read_text()
        doc = html_to_document(html, url="https://example.com/article")
        assert doc.metadata.title is not None
        assert len(doc.body) > 0
        # Should have headings from the article, not from nav/sidebar
        headings = [b for b in doc.body if isinstance(b, Heading)]
        heading_texts = [
            "".join(c.value for c in h.children if isinstance(c, Text)) for h in headings
        ]
        assert any("Main Article" in t for t in heading_texts)

    def test_article_has_code_block(self):
        html = (FIXTURES / "article.html").read_text()
        doc = html_to_document(html, url="https://example.com/article")
        code_blocks = [b for b in doc.body if isinstance(b, CodeBlock)]
        assert len(code_blocks) >= 1
        assert code_blocks[0].language == "python"

    def test_article_markdown_roundtrip(self):
        html = (FIXTURES / "article.html").read_text()
        doc = html_to_document(html, url="https://example.com/article")
        md = serialize_markdown(doc)
        assert len(md) > 100
        assert "Main Article Heading" in md

    def test_no_readability_includes_nav(self):
        html = (FIXTURES / "article.html").read_text()
        doc = html_to_document(html, url="https://example.com/article", extract_content=False)
        # Without readability, we get more blocks (including nav content)
        doc_with = html_to_document(html, url="https://example.com/article")
        assert len(doc.body) >= len(doc_with.body)


# ---------------------------------------------------------------------------
# Phase 6.5: Extraction quality fixes
# ---------------------------------------------------------------------------


def _text(doc):
    """Helper: serialize document to plain text."""
    from kaos_content.serializers.text import serialize_text

    return serialize_text(doc)


class TestSkipClasses:
    """Class-based noise filtering (Fix 2)."""

    def test_wikipedia_editsection_stripped_from_heading(self):
        html = '<h2>History <span class="mw-editsection">[<a href="/edit">edit</a>]</span></h2>'
        doc = html_to_document(html, extract_content=False)
        headings = [b for b in doc.body if isinstance(b, Heading)]
        assert len(headings) == 1
        text = "".join(c.value for c in headings[0].children if isinstance(c, Text))
        assert "edit" not in text
        assert "History" in text

    def test_mw_editsection_stripped_at_block_level(self):
        html = '<div class="mw-editsection"><a href="/edit">edit</a></div><p>Content</p>'
        doc = html_to_document(html, extract_content=False)
        text = _text(doc)
        assert "edit" not in text
        assert "Content" in text

    def test_mw_jump_link_stripped(self):
        html = '<div><span class="mw-jump-link">Jump to navigation</span><p>Content</p></div>'
        doc = html_to_document(html, extract_content=False)
        text = _text(doc)
        assert "Jump to" not in text
        assert "Content" in text

    def test_sr_only_stripped(self):
        html = '<p>Visible <span class="sr-only">screen reader only</span></p>'
        doc = html_to_document(html, extract_content=False)
        text = _text(doc)
        assert "screen reader only" not in text
        assert "Visible" in text

    def test_visually_hidden_stripped(self):
        html = '<p>Visible <span class="visually-hidden">hidden text</span></p>'
        doc = html_to_document(html, extract_content=False)
        text = _text(doc)
        assert "hidden text" not in text

    def test_noprint_stripped(self):
        html = '<div class="noprint">Print-only nav</div><p>Content</p>'
        doc = html_to_document(html, extract_content=False)
        text = _text(doc)
        assert "Print-only" not in text
        assert "Content" in text

    def test_normal_span_preserved(self):
        html = '<p>Text with <span class="highlight">highlighted</span> content</p>'
        doc = html_to_document(html, extract_content=False)
        text = _text(doc)
        assert "highlighted" in text

    def test_multiple_classes_one_skip(self):
        html = '<p>Title <span class="bracket mw-editsection">edit</span></p>'
        doc = html_to_document(html, extract_content=False)
        text = _text(doc)
        assert "edit" not in text
        assert "Title" in text

    def test_tail_text_preserved_after_skip(self):
        html = '<p>Before <span class="mw-editsection">edit</span> After</p>'
        doc = html_to_document(html, extract_content=False)
        text = _text(doc)
        assert "Before" in text
        assert "After" in text
        assert "edit" not in text


class TestActionLinks:
    """Action link filtering (Fix 3)."""

    def test_vote_link_stripped(self):
        html = '<p><a href="vote?id=123&how=up">▲</a> Content</p>'
        doc = html_to_document(html, extract_content=False)
        md = serialize_markdown(doc)
        assert "vote" not in md
        assert "Content" in md

    def test_hide_link_stripped(self):
        html = '<p><a href="hide?id=123">hide</a> Content</p>'
        doc = html_to_document(html, extract_content=False)
        md = serialize_markdown(doc)
        assert "hide?" not in md

    def test_flag_link_stripped(self):
        html = '<p><a href="/flag?id=123">flag</a> Content</p>'
        doc = html_to_document(html, extract_content=False)
        links = []
        for b in doc.body:
            if isinstance(b, Paragraph):
                for c in b.children:
                    if isinstance(c, Link):
                        links.append(c)
        assert not any("flag" in lnk.url for lnk in links)

    def test_normal_link_preserved(self):
        html = '<p><a href="https://example.com/article">Read more</a></p>'
        doc = html_to_document(html, extract_content=False)
        links = []
        for b in doc.body:
            if isinstance(b, Paragraph):
                for c in b.children:
                    if isinstance(c, Link):
                        links.append(c)
        assert len(links) == 1

    def test_vote_in_path_not_stripped(self):
        """A legitimate link like /articles/vote-counting should NOT be stripped."""
        html = '<p><a href="/articles/vote-counting">Vote Counting</a></p>'
        doc = html_to_document(html, extract_content=False)
        links = []
        for b in doc.body:
            if isinstance(b, Paragraph):
                for c in b.children:
                    if isinstance(c, Link):
                        links.append(c)
        assert len(links) == 1

    def test_upvote_link_stripped(self):
        html = '<p><a href="/upvote?item=42">upvote</a> Good post</p>'
        doc = html_to_document(html, extract_content=False)
        md = serialize_markdown(doc)
        assert "upvote" not in md
        assert "Good post" in md


class TestSemanticFallback:
    """Readability fallback for listing pages (Fix 1)."""

    def test_listing_uses_main_element(self):
        """When readability fails, <main> should provide the content."""
        words = " ".join(["word"] * 10)
        html = f"""
        <html><body>
        <nav><a href="/">Home</a><a href="/about">About</a></nav>
        <main>
            <h1>Blog</h1>
            <article><h2>Post One</h2><span>{words} alpha</span></article>
            <article><h2>Post Two</h2><span>{words} beta</span></article>
            <article><h2>Post Three</h2><span>{words} gamma</span></article>
        </main>
        <footer><p>Copyright 2026</p></footer>
        </body></html>
        """
        doc = html_to_document(html, url="https://example.com/blog")
        text = _text(doc)
        assert "Post One" in text
        assert "Post Two" in text
        assert "Post Three" in text
        assert len(text.split()) > 30

    def test_listing_with_multiple_articles(self):
        """Multiple <article> elements should trigger parent extraction."""
        words = " ".join(["content"] * 15)
        html = f"""
        <html><body>
        <div class="posts">
            <article><h2>First</h2><div>{words}</div></article>
            <article><h2>Second</h2><div>{words}</div></article>
            <article><h2>Third</h2><div>{words}</div></article>
        </div>
        <footer><p>Copyright</p></footer>
        </body></html>
        """
        doc = html_to_document(html, url="https://example.com/blog")
        text = _text(doc)
        assert "First" in text
        assert "Second" in text

    def test_article_extraction_unchanged(self):
        """Existing article pages must NOT be affected by the fallback."""
        html = (FIXTURES / "article.html").read_text()
        doc = html_to_document(html, url="https://example.com/article")
        md = serialize_markdown(doc)
        assert "Main Article Heading" in md
        assert len(md) > 100

    def test_small_page_no_fallback(self):
        """A genuinely small page should use readability's result as-is."""
        html = "<html><body><p>This is a short page with just one paragraph.</p></body></html>"
        doc = html_to_document(html)
        text = _text(doc)
        assert "short page" in text

    def test_fallback_skips_nav_footer(self):
        """Semantic fallback via <main> should NOT include nav/footer."""
        words = " ".join(["content"] * 20)
        html = f"""
        <html><body>
        <nav><a href="/">Navigation Link</a></nav>
        <main><h1>Title</h1><div>{words}</div></main>
        <footer><p>Footer Text Should Not Appear</p></footer>
        </body></html>
        """
        doc = html_to_document(html, url="https://example.com/test")
        text = _text(doc)
        assert "Title" in text
        # _SKIP_TAGS still filters nav/footer even within the main element
        assert "Navigation Link" not in text
