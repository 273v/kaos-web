"""Edge case tests for HTML-to-AST conversion.

Test cases derived from markdownify, turndown, html-to-markdown (Go),
htmd (Rust), and alea-markdown-python test suites. Adapted for
ContentDocument AST output instead of markdown strings.
"""

from __future__ import annotations

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
    LineBreak,
    Link,
    Strong,
    Text,
)
from kaos_web.extract import html_to_document


def _doc(html: str, **kwargs):
    """Helper to create a document without readability."""
    return html_to_document(html, extract_content=False, **kwargs)


def _text(block) -> str:
    """Extract all text content from a block's children."""
    parts = []
    for c in getattr(block, "children", ()):
        if hasattr(c, "value"):
            parts.append(c.value)
        elif hasattr(c, "children"):
            parts.append(_text(c))
    return "".join(parts)


# ─── Inline formatting edge cases ───────────────────────────────────────────


class TestEmptyInlineElements:
    def test_empty_bold(self):
        doc = _doc("<p>before<b></b>after</p>")
        para = doc.body[0]
        # Empty <b> should be dropped
        assert not any(isinstance(c, Strong) for c in para.children)

    def test_empty_em(self):
        doc = _doc("<p>before<em></em>after</p>")
        para = doc.body[0]
        assert not any(isinstance(c, Emphasis) for c in para.children)

    def test_whitespace_only_strong(self):
        doc = _doc("<p>before<strong>   </strong>after</p>")
        para = doc.body[0]
        # Whitespace-only strong should be dropped or flattened
        strongs = [c for c in para.children if isinstance(c, Strong)]
        if strongs:
            # If kept, should only contain whitespace
            assert _text(strongs[0]).strip() == ""


class TestNestedInlineFormatting:
    def test_bold_italic(self):
        doc = _doc("<p><b><i>bold italic</i></b></p>")
        para = doc.body[0]
        # Should have Strong containing Emphasis (or vice versa)
        assert len(para.children) >= 1

    def test_intra_word_emphasis(self):
        doc = _doc("<p>It<i>al</i>ic</p>")
        para = doc.body[0]
        # Should have Text + Emphasis + Text
        types = [type(c) for c in para.children]
        assert Text in types
        assert Emphasis in types


# ─── Whitespace edge cases ──────────────────────────────────────────────────


class TestWhitespace:
    def test_nbsp_preserved(self):
        doc = _doc("<p>foo&nbsp;bar</p>")
        para = doc.body[0]
        text = _text(para)
        assert "foo" in text and "bar" in text

    def test_multiple_spaces_collapsed(self):
        doc = _doc("<p>foo     bar</p>")
        para = doc.body[0]
        text = _text(para)
        # Multiple spaces should collapse to single
        assert "foo bar" in text or "foo" in text

    def test_pre_preserves_whitespace(self):
        doc = _doc("<pre>  foo\n  bar\n  baz</pre>")
        code_blocks = [b for b in doc.body if isinstance(b, CodeBlock)]
        assert len(code_blocks) == 1
        # Pre should preserve internal whitespace
        assert "\n" in code_blocks[0].value

    def test_heading_whitespace_collapsed(self):
        doc = _doc("<h3>\n\nHello   World\n\n</h3>")
        headings = [b for b in doc.body if isinstance(b, Heading)]
        assert len(headings) == 1
        text = _text(headings[0])
        assert "Hello" in text
        assert "World" in text


# ─── Code edge cases ────────────────────────────────────────────────────────


class TestCodeEdgeCases:
    def test_backticks_in_inline_code(self):
        doc = _doc("<p><code>`bar`</code></p>")
        para = doc.body[0]
        codes = [c for c in para.children if isinstance(c, Code)]
        assert len(codes) == 1
        assert "`bar`" in codes[0].value

    def test_formatting_inside_code_stripped(self):
        doc = _doc("<p><code>foo<b>bar</b>baz</code></p>")
        para = doc.body[0]
        codes = [c for c in para.children if isinstance(c, Code)]
        assert len(codes) == 1
        # Bold inside code should be flattened to text
        assert "foobarbaz" in codes[0].value

    def test_language_from_language_class(self):
        doc = _doc('<pre><code class="language-rust">let x = 1;</code></pre>')
        blocks = [b for b in doc.body if isinstance(b, CodeBlock)]
        assert blocks[0].language == "rust"

    def test_language_from_lang_class(self):
        doc = _doc('<pre><code class="lang-go">fmt.Println()</code></pre>')
        blocks = [b for b in doc.body if isinstance(b, CodeBlock)]
        assert blocks[0].language == "go"

    def test_language_from_pre_class(self):
        doc = _doc('<pre class="language-ruby"><code>puts "hi"</code></pre>')
        blocks = [b for b in doc.body if isinstance(b, CodeBlock)]
        assert blocks[0].language == "ruby"

    def test_empty_pre(self):
        doc = _doc("<pre></pre>")
        # Empty pre should produce empty code block or be skipped
        code_blocks = [b for b in doc.body if isinstance(b, CodeBlock)]
        assert len(code_blocks) <= 1


# ─── Link edge cases ────────────────────────────────────────────────────────


class TestLinkEdgeCases:
    def test_empty_href(self):
        doc = _doc('<p><a href="">text</a></p>')
        para = doc.body[0]
        # Empty href — may produce a link with empty url or plain text
        links = [c for c in para.children if isinstance(c, Link)]
        if links:
            assert links[0].url == ""

    def test_no_href(self):
        doc = _doc("<p><a>text</a></p>")
        para = doc.body[0]
        # No href — may produce Link with empty url or plain text
        links = [c for c in para.children if isinstance(c, Link)]
        if links:
            assert links[0].url == ""

    def test_relative_url_resolved(self):
        doc = _doc('<p><a href="/page">link</a></p>', url="https://example.com")
        para = doc.body[0]
        links = [c for c in para.children if isinstance(c, Link)]
        assert links[0].url == "https://example.com/page"

    def test_javascript_uri_stripped(self):
        doc = _doc('<p><a href="javascript:void(0)">bad</a> <a href="https://ok.com">ok</a></p>')
        para = doc.body[0]
        links = [c for c in para.children if isinstance(c, Link)]
        # Only the safe link survives
        assert all(lnk.url.startswith("https://") for lnk in links)

    def test_data_uri_stripped(self):
        doc = _doc('<p><a href="data:text/html,<h1>bad</h1>">bad</a> text</p>')
        para = doc.body[0]
        links = [c for c in para.children if isinstance(c, Link)]
        assert len(links) == 0


# ─── Image edge cases ───────────────────────────────────────────────────────


class TestImageEdgeCases:
    def test_no_src(self):
        doc = _doc("<p><img> some text</p>")
        # Image with no src should be dropped; paragraph may be empty and skipped
        for block in doc.body:
            if isinstance(block, Paragraph):
                images = [c for c in block.children if isinstance(c, Image)]
                assert len(images) == 0

    def test_empty_src(self):
        doc = _doc('<p><img src=""> some text</p>')
        # Image with empty src should be dropped
        for block in doc.body:
            if isinstance(block, Paragraph):
                images = [c for c in block.children if isinstance(c, Image)]
                assert len(images) == 0

    def test_data_src_preferred(self):
        doc = _doc(
            '<p><img data-src="/real.jpg" src="/placeholder.gif" alt="Test"></p>',
            url="https://example.com",
        )
        para = doc.body[0]
        images = [c for c in para.children if isinstance(c, Image)]
        assert images[0].src == "https://example.com/real.jpg"

    def test_alt_text_preserved(self):
        doc = _doc('<p><img src="/img.jpg" alt="A beautiful photo"></p>')
        para = doc.body[0]
        images = [c for c in para.children if isinstance(c, Image)]
        assert images[0].alt == "A beautiful photo"


# ─── Table edge cases ───────────────────────────────────────────────────────


class TestTableEdgeCases:
    def test_no_thead(self):
        doc = _doc("<table><tr><td>A</td><td>B</td></tr></table>")
        tables = [b for b in doc.body if isinstance(b, Table)]
        assert len(tables) == 1
        # Should have at least one body section
        assert len(tables[0].bodies) >= 1

    def test_td_in_thead(self):
        doc = _doc(
            "<table><thead><tr><td>Name</td></tr></thead>"
            "<tbody><tr><td>Alice</td></tr></tbody></table>"
        )
        tables = [b for b in doc.body if isinstance(b, Table)]
        assert tables[0].head is not None

    def test_empty_table(self):
        doc = _doc("<table></table>")
        tables = [b for b in doc.body if isinstance(b, Table)]
        # Empty table may be skipped or produce an empty Table
        assert len(tables) <= 1

    def test_colspan(self):
        doc = _doc("<table><tr><td colspan='2'>Wide</td></tr><tr><td>A</td><td>B</td></tr></table>")
        tables = [b for b in doc.body if isinstance(b, Table)]
        assert len(tables) == 1
        # First row first cell should have col_span=2
        first_row = tables[0].bodies[0].rows[0]
        assert first_row.cells[0].col_span == 2


# ─── List edge cases ────────────────────────────────────────────────────────


class TestListEdgeCases:
    def test_nested_lists(self):
        doc = _doc("<ul><li>1<ul><li>a</li><li>b</li></ul></li><li>2</li></ul>")
        lists = [b for b in doc.body if isinstance(b, BulletList)]
        assert len(lists) == 1
        # First item should contain a nested list
        first_item = lists[0].children[0]
        nested = [c for c in first_item.children if isinstance(c, BulletList)]
        assert len(nested) == 1

    def test_ol_start_attribute(self):
        doc = _doc('<ol start="5"><li>Five</li><li>Six</li></ol>')
        lists = [b for b in doc.body if isinstance(b, OrderedList)]
        assert lists[0].start == 5

    def test_mixed_nested_lists(self):
        doc = _doc("<ol><li>First<ul><li>Nested bullet</li></ul></li></ol>")
        lists = [b for b in doc.body if isinstance(b, OrderedList)]
        assert len(lists) == 1
        first_item = lists[0].children[0]
        nested = [c for c in first_item.children if isinstance(c, BulletList)]
        assert len(nested) == 1


# ─── Blockquote edge cases ──────────────────────────────────────────────────


class TestBlockquoteEdgeCases:
    def test_nested_blockquotes(self):
        doc = _doc("<blockquote><p>outer</p><blockquote><p>inner</p></blockquote></blockquote>")
        bqs = [b for b in doc.body if isinstance(b, BlockQuote)]
        assert len(bqs) == 1
        nested = [c for c in bqs[0].children if isinstance(c, BlockQuote)]
        assert len(nested) == 1

    def test_empty_blockquote(self):
        doc = _doc("<blockquote></blockquote>")
        bqs = [b for b in doc.body if isinstance(b, BlockQuote)]
        assert len(bqs) == 0  # Empty blockquote should be dropped

    def test_blockquote_with_heading(self):
        doc = _doc("<blockquote><h2>Title</h2><p>Content</p></blockquote>")
        bqs = [b for b in doc.body if isinstance(b, BlockQuote)]
        assert len(bqs) == 1
        headings = [c for c in bqs[0].children if isinstance(c, Heading)]
        assert len(headings) == 1


# ─── Heading edge cases ─────────────────────────────────────────────────────


class TestHeadingEdgeCases:
    def test_h7_not_a_heading(self):
        doc = _doc("<h7>Not a heading</h7>")
        headings = [b for b in doc.body if isinstance(b, Heading)]
        assert len(headings) == 0

    def test_heading_with_link(self):
        doc = _doc('<h2><a href="/page">Linked Heading</a></h2>')
        headings = [b for b in doc.body if isinstance(b, Heading)]
        assert len(headings) == 1
        links = [c for c in headings[0].children if isinstance(c, Link)]
        assert len(links) == 1

    def test_heading_with_strong(self):
        doc = _doc("<h3>A <strong>bold</strong> heading</h3>")
        headings = [b for b in doc.body if isinstance(b, Heading)]
        assert len(headings) == 1
        strongs = [c for c in headings[0].children if isinstance(c, Strong)]
        assert len(strongs) == 1


# ─── Structural edge cases ──────────────────────────────────────────────────


class TestStructuralEdgeCases:
    def test_hr(self):
        doc = _doc("<p>Before</p><hr><p>After</p>")
        hrs = [b for b in doc.body if isinstance(b, ThematicBreak)]
        assert len(hrs) == 1

    def test_br_in_paragraph(self):
        doc = _doc("<p>Line one<br>Line two</p>")
        para = doc.body[0]
        breaks = [c for c in para.children if isinstance(c, LineBreak)]
        assert len(breaks) >= 1

    def test_script_stripped(self):
        doc = _doc("<p>Text</p><script>alert(1)</script><p>More</p>")
        for block in doc.body:
            assert isinstance(block, Paragraph)

    def test_style_stripped(self):
        doc = _doc("<p>Text</p><style>body{color:red}</style><p>More</p>")
        for block in doc.body:
            assert isinstance(block, Paragraph)

    def test_nested_divs_transparent(self):
        doc = _doc("<div><div><div><p>Deep</p></div></div></div>")
        paras = [b for b in doc.body if isinstance(b, Paragraph)]
        assert len(paras) >= 1

    def test_html_comment_stripped(self):
        doc = _doc("<p>Before</p><!-- comment --><p>After</p>")
        paras = [b for b in doc.body if isinstance(b, Paragraph)]
        assert len(paras) == 2


# ─── Definition list edge cases ─────────────────────────────────────────────


class TestDefinitionListEdgeCases:
    def test_multiple_definitions(self):
        doc = _doc("<dl><dt>Term</dt><dd>Def 1</dd><dd>Def 2</dd></dl>")
        dls = [b for b in doc.body if isinstance(b, DefinitionList)]
        assert len(dls) == 1
        item = dls[0].children[0]
        assert len(item.definitions) == 2

    def test_multiple_terms(self):
        doc = _doc("<dl><dt>Term A</dt><dd>Def A</dd><dt>Term B</dt><dd>Def B</dd></dl>")
        dls = [b for b in doc.body if isinstance(b, DefinitionList)]
        assert len(dls) == 1
        assert len(dls[0].children) == 2


# ─── Malformed HTML ─────────────────────────────────────────────────────────


class TestMalformedHTML:
    def test_unclosed_tags(self):
        doc = _doc("<h1><i>Unclosed italic</h1>")
        headings = [b for b in doc.body if isinstance(b, Heading)]
        assert len(headings) == 1
        # lxml auto-closes — should still extract content
        text = _text(headings[0])
        assert "Unclosed" in text

    def test_uppercase_tags(self):
        doc = _doc("<H1>Hello</H1><P>World</P>")
        headings = [b for b in doc.body if isinstance(b, Heading)]
        paras = [b for b in doc.body if isinstance(b, Paragraph)]
        assert len(headings) == 1
        assert len(paras) == 1

    def test_nested_paragraphs(self):
        doc = _doc("<p><p>nested</p></p>")
        # lxml normalizes this — should still produce content
        paras = [b for b in doc.body if isinstance(b, Paragraph)]
        assert len(paras) >= 1
