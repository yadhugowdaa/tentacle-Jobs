"""Tests for critic numeric coercion and the Markdown→PDF text cleaner."""

from tentacle_apply.tailor.critic import _num, _str_list
from tentacle_apply.tailor.render import _clean_md, markdown_to_pdf


def test_num_clamps_to_0_100():
    assert _num("85") == 85.0
    assert _num(150) == 100.0
    assert _num("not a number") == 0.0
    # Documented quirk: _num strips every non-digit/dot, so signs and separators are lost.
    # The LLM is instructed to return a plain 0-100 integer, so this is low-risk in practice,
    # but the behavior is intentionally pinned here (see ROADMAP: harden critic parsing).
    assert _num(-5) == 5.0           # minus sign stripped
    assert _num("72/100") == 100.0   # becomes "72100" then clamped


def test_str_list_from_various():
    assert _str_list(["a", " b ", ""]) == ["a", "b"]
    assert _str_list("x, y; z") == ["x", "y", "z"]
    assert _str_list(None) == []


def test_clean_md_strips_markdown_syntax():
    md = "# Heading\n**bold** and *italic* and `code`\n- bullet one\n- bullet two"
    out = _clean_md(md)
    assert "HEADING" in out
    assert "**" not in out
    assert "`" not in out
    assert "\u2022 bullet one" in out  # bullet converted to • glyph


def test_markdown_to_pdf_writes_a_real_pdf(tmp_path):
    out = markdown_to_pdf("# Ada Lovelace\n\nExperienced engineer.\n- Python\n- Rust", tmp_path / "r.pdf")
    assert out.exists()
    assert out.read_bytes()[:4] == b"%PDF"
