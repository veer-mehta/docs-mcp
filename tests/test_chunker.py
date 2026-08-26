from docs_mcp.processing.chunker import chunk_markdown

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
