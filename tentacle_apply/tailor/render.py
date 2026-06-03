"""Render the tailored Markdown resume to a clean, ATS-friendly PDF (selectable text).

Uses PyMuPDF (already a dependency) so there are no extra system deps. We lay text out line by
line with manual wrapping/pagination because `insert_textbox` can't return overflow text.
"""

from __future__ import annotations

import re
from pathlib import Path

import fitz  # PyMuPDF

_FONT = "helv"
_SIZE = 10.5
_LEADING = 14.0
_MARGIN = 54.0


def _clean_md(md: str) -> str:
    lines: list[str] = []
    for raw in md.splitlines():
        line = raw.rstrip()
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        line = re.sub(r"\*(.+?)\*", r"\1", line)
        line = re.sub(r"`(.+?)`", r"\1", line)
        m = re.match(r"^(#{1,6})\s*(.*)$", line)
        if m:
            line = m.group(2).upper()
        line = re.sub(r"^\s*[-*]\s+", "\u2022 ", line)
        lines.append(line)
    return "\n".join(lines)


def _wrap(text: str, max_width: float) -> list[str]:
    words = text.split(" ")
    out: list[str] = []
    cur = ""
    for w in words:
        trial = w if not cur else f"{cur} {w}"
        if fitz.get_text_length(trial, fontname=_FONT, fontsize=_SIZE) <= max_width:
            cur = trial
        else:
            if cur:
                out.append(cur)
            cur = w
    if cur:
        out.append(cur)
    return out or [""]


def markdown_to_pdf(md: str, out_path: str | Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    text = _clean_md(md)
    doc = fitz.open()
    page = doc.new_page()
    max_width = page.rect.width - 2 * _MARGIN
    bottom = page.rect.height - _MARGIN
    y = _MARGIN + _SIZE

    for para in text.split("\n"):
        if para.strip() == "":
            y += _LEADING * 0.6
            continue
        for line in _wrap(para, max_width):
            if y > bottom:
                page = doc.new_page()
                y = _MARGIN + _SIZE
            page.insert_text((_MARGIN, y), line, fontsize=_SIZE, fontname=_FONT)
            y += _LEADING

    doc.save(str(out_path))
    doc.close()
    return out_path
