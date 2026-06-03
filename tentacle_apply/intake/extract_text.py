"""Extract raw text from a resume file (PDF / DOCX / TXT / MD)."""

from __future__ import annotations

from pathlib import Path


def extract_text(path: str | Path) -> str:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Resume not found: {p}")
    suffix = p.suffix.lower()
    if suffix == ".pdf":
        return _from_pdf(p)
    if suffix == ".docx":
        return _from_docx(p)
    if suffix in {".txt", ".md"}:
        return p.read_text(encoding="utf-8", errors="ignore")
    raise ValueError(f"Unsupported resume type '{suffix}'. Use PDF, DOCX, TXT or MD.")


def _from_pdf(p: Path) -> str:
    try:
        import pymupdf as fitz  # PyMuPDF >= 1.24 exposes the `pymupdf` name
    except ImportError:  # pragma: no cover - older installs
        import fitz  # type: ignore

    with fitz.open(p) as doc:
        return "\n".join(page.get_text() for page in doc).strip()


def _from_docx(p: Path) -> str:
    import docx

    document = docx.Document(str(p))
    return "\n".join(par.text for par in document.paragraphs).strip()
