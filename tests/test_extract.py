from pathlib import Path

from docs_mcp.processing.extract import html_to_markdown, file_to_markdown

HTML = """
<html>
<head><title>Docs</title><script>var tracking = 1;</script></head>
<body>
<nav><a href="/home">Home</a></nav>
<article>
<h1>Hello</h1>
<p>World with a <a href="https://example.com/x">link</a>.</p>
<table><tr><th>A</th><td>1</td></tr></table>
</article>
<footer>copyright junk</footer>
</body>
</html>
"""


def test_extracts_main_content_as_markdown():
    markdown = html_to_markdown(HTML, "https://example.com/docs")
    assert markdown is not None
    assert "Hello" in markdown
    assert "World" in markdown


def test_output_is_markdown():
    markdown = html_to_markdown(HTML, "https://example.com/docs")
    assert markdown is not None
    assert "#" in markdown


def test_fallback_strips_scripts_and_junk():
    raw = "<html><body><script>alert(1)</script><nav>menu</nav><p>kept content</p></body></html>"
    markdown = html_to_markdown(raw, "https://example.com/page")
    assert markdown is not None
    assert "alert" not in markdown
    assert "kept content" in markdown


def test_empty_html_returns_none():
    assert html_to_markdown("", "https://example.com") is None


def test_file_to_markdown_md(tmp_path):
    f = tmp_path / "readme.md"
    f.write_text("# Hello\n\nSome content here.")
    result = file_to_markdown(f, "readme.md")
    assert result is not None
    assert "Hello" in result
    assert "Some content here" in result


def test_file_to_markdown_txt(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("Plain text content.")
    result = file_to_markdown(f, "notes.txt")
    assert result is not None
    assert "Plain text content" in result


def test_file_to_markdown_html(tmp_path):
    f = tmp_path / "page.html"
    f.write_text("<html><body><h1>Title</h1><p>Body text.</p></body></html>")
    result = file_to_markdown(f, "page.html")
    assert result is not None
    assert "Body text" in result


def test_file_to_markdown_htm(tmp_path):
    f = tmp_path / "page.htm"
    f.write_text("<html><body><p>Content.</p></body></html>")
    result = file_to_markdown(f, "page.htm")
    assert result is not None
    assert "Content" in result


def test_file_to_markdown_binary_returns_text_or_none(tmp_path):
    f = tmp_path / "image.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
    result = file_to_markdown(f, "image.png")
    assert result is None


def test_file_to_markdown_unknown_ext_reads_as_text(tmp_path):
    f = tmp_path / "data.xyz"
    f.write_text("some data")
    result = file_to_markdown(f, "data.xyz")
    assert result is not None
    assert "some data" in result
