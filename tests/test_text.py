from pathlib import Path

from docs_mcp.text import chunk_markdown, file_to_markdown, html_to_markdown


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


SAMPLE = """# Guide

Intro paragraph for the guide.

## Setup

Install the package with npm.

### Requirements

Node 18+ is required along with a supported package manager.
You should also verify that your build tools match the versions
documented in the compatibility matrix before proceeding further,
and confirm that your continuous integration pipeline installs the
same toolchain so local and CI builds behave identically.

## API

### useRouter

Returns the router object.
"""


def test_breadcrumbs_follow_heading_hierarchy():
    chunks = chunk_markdown(SAMPLE)
    paths = {" > ".join(chunk.heading_path) for chunk in chunks}
    assert "Guide > Setup > Requirements" in paths
    assert "Guide > API > useRouter" in paths
    assert "Guide" in paths


def test_small_child_section_absorbs_into_parent_chunk():
    chunks = chunk_markdown(SAMPLE)
    by_path = {" > ".join(chunk.heading_path): chunk.content for chunk in chunks}
    assert "Install the package with npm." in by_path["Guide"]
    assert "Intro paragraph" in by_path["Guide"]

    unrelated_leaf_keeps_own_chunk = by_path["Guide > API > useRouter"]
    assert "router object" in unrelated_leaf_keeps_own_chunk


def test_hash_comments_inside_code_fences_are_not_headings():
    markdown = "# Install\n\nRun this:\n\n```bash\n# install deps\npip install x\n```\n\n~~~\n## not a heading\n~~~\n\nThen continue."
    chunks = chunk_markdown(markdown)
    assert [chunk.heading_path for chunk in chunks] == [["Install"]]
    assert "# install deps\npip install x\n```" in chunks[0].content
    assert "## not a heading" in chunks[0].content


def test_no_headings_produces_single_stream():
    text = "plain paragraph. " * 40
    chunks = chunk_markdown(text)
    assert len(chunks) >= 1
    assert all(chunk.heading_path == [] for chunk in chunks)


def test_long_section_is_split_with_overlap():
    paragraph = "Sentence one. " * 80
    markdown = f"# Doc\n\n{paragraph}\n\n{paragraph}"
    chunks = chunk_markdown(markdown, max_chars=800, overlap=100)
    assert len(chunks) > 1
    assert all(len(chunk.content) <= 800 + 100 + 2 for chunk in chunks)
    assert all(chunk.heading_path == ["Doc"] for chunk in chunks)


def test_huge_paragraph_hard_split():
    text = "# T\n\n" + ("word " * 3000)
    chunks = chunk_markdown(text, max_chars=1000, overlap=0)
    assert len(chunks) >= 10
    assert all(len(chunk.content) <= 1000 + 100 for chunk in chunks)


def test_empty_input():
    assert chunk_markdown("") == []
