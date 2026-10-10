"""Deterministic document extraction (handoff docs/03 s6, FR-004, AT-006).

Extraction is the first of two normalisation steps; the second (interpretation into
claims and requirements) is a scoped team task. This module never calls a model.

For every supported type it returns the normalised text, source locators (page or
paragraph/table cell), warnings, and a completeness assessment:

* ``complete``   every unit yielded text;
* ``partial``    some pages/paragraphs yielded nothing (scanned pages, images);
* ``unreadable`` nothing could be extracted (encrypted, scanned without OCR);
* ``blocked``    the extractor library is not installed (a capability, not an error
  in the document).

Text inside the document is evidence, never an instruction to the runtime.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from io import BytesIO
from typing import Any

MARKDOWN_TYPES = {"text/markdown", "text/plain"}
PDF_TYPE = "application/pdf"
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
HTML_TYPE = "text/html"
JSON_TYPE = "application/json"
CSV_TYPE = "text/csv"
SUPPORTED_TYPES = MARKDOWN_TYPES | {PDF_TYPE, DOCX_TYPE, HTML_TYPE, JSON_TYPE, CSV_TYPE}


@dataclass(frozen=True, slots=True)
class Locator:
    """Where a span of text came from: ``kind`` is page, paragraph, heading or table_cell."""

    kind: str
    index: int
    text: str
    detail: str | None = None  # e.g. "table 1, row 2, col 3" or heading level


@dataclass
class Extraction:
    media_type: str
    method: str
    text: str
    locators: list[Locator] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    completeness: str = "complete"
    units_total: int = 0
    units_empty: int = 0

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "media_type": self.media_type,
            "method": self.method,
            "completeness": self.completeness,
            "units_total": self.units_total,
            "units_empty": self.units_empty,
            "warnings": list(self.warnings),
            "locators": [asdict(l) for l in self.locators],
            "text": self.text,
        }


def extract(data: bytes, media_type: str, *, max_pdf_pages: int = 200) -> Extraction:
    if media_type in MARKDOWN_TYPES:
        return extract_markdown(data.decode("utf-8"), media_type)
    if media_type == PDF_TYPE:
        return extract_pdf(data, max_pages=max_pdf_pages)
    if media_type == DOCX_TYPE:
        return extract_docx(data)
    if media_type == HTML_TYPE:
        return extract_html(data.decode("utf-8", "replace"))
    if media_type == JSON_TYPE:
        return extract_json(data.decode("utf-8", "replace"))
    if media_type == CSV_TYPE:
        return extract_csv(data.decode("utf-8", "replace"))
    return Extraction(media_type=media_type, method="none", text="", completeness="unreadable", warnings=[f"unsupported media type {media_type}"])


# ----------------------------------------------------------------------------- html / json / csv (M8)


def extract_html(text: str) -> Extraction:
    """Readable text from HTML: headings become Markdown headings, scripts and styles are dropped."""
    import html as _html
    import re as _re

    body = _re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", text)
    body = _re.sub(r"(?is)<h([1-6])[^>]*>(.*?)</h\1>", lambda m: "\n" + "#" * int(m.group(1)) + " " + _re.sub(r"<[^>]+>", "", m.group(2)).strip() + "\n", body)
    body = _re.sub(r"(?i)<(br|/p|/div|/li|/tr)\s*/?>", "\n", body)
    body = _re.sub(r"<[^>]+>", "", body)
    lines = [_html.unescape(line).strip() for line in body.splitlines()]
    kept = [line for line in lines if line]
    cleaned = "\n".join(kept)
    locators = [Locator("heading", i, line.lstrip("#").strip(), detail=f"h{len(line) - len(line.lstrip('#'))}") for i, line in enumerate(kept) if line.startswith("#")]
    return Extraction(media_type=HTML_TYPE, method="html-strip", text=cleaned, locators=locators, units_total=len(lines), units_empty=len(lines) - len(kept),
                      completeness="complete" if cleaned.strip() else "empty", warnings=[] if cleaned.strip() else ["no readable text"])


def extract_json(text: str) -> Extraction:
    """A JSON document is kept verbatim (pretty-printed) so ids and structure survive."""
    import json as _json

    try:
        payload = _json.loads(text)
    except ValueError as exc:
        return Extraction(media_type=JSON_TYPE, method="json", text=text, completeness="unreadable", warnings=[f"invalid JSON: {exc}"])
    return Extraction(media_type=JSON_TYPE, method="json", text=_json.dumps(payload, indent=2, ensure_ascii=False), units_total=1, completeness="complete")


def extract_csv(text: str) -> Extraction:
    """A CSV table becomes a Markdown table; every non-empty cell gets a locator (row, col)."""
    import csv as _csv
    import io as _io

    rows = [r for r in _csv.reader(_io.StringIO(text)) if any(c.strip() for c in r)]
    if not rows:
        return Extraction(media_type=CSV_TYPE, method="csv", text="", completeness="empty", warnings=["no rows"])
    width = max(len(r) for r in rows)
    norm = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(c.strip() for c in norm[0]) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(c.strip() for c in r) + " |" for r in norm[1:]]
    locators = [Locator("table_cell", i, c, detail=f"table 1, row {i + 1}, col {j + 1}") for i, r in enumerate(norm) for j, c in enumerate(r) if c.strip()]
    return Extraction(media_type=CSV_TYPE, method="csv", text="\n".join(lines), locators=locators, units_total=len(norm), completeness="complete")


# ----------------------------------------------------------------------------- markdown


def extract_markdown(text: str, media_type: str = "text/markdown") -> Extraction:
    """Paragraphs separated by blank lines; headings get their own locator with level."""
    locators: list[Locator] = []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text.replace("\r\n", "\n")) if p.strip()]
    for index, paragraph in enumerate(paragraphs, start=1):
        heading = re.match(r"^(#{1,6})\s+(.*)$", paragraph)
        if heading and "\n" not in paragraph:
            locators.append(Locator("heading", index, heading.group(2).strip(), detail=f"level {len(heading.group(1))}"))
        else:
            locators.append(Locator("paragraph", index, paragraph))
    return Extraction(
        media_type=media_type,
        method="markdown-paragraphs",
        text="\n\n".join(paragraphs),
        locators=locators,
        completeness="complete" if paragraphs else "unreadable",
        units_total=len(paragraphs),
        units_empty=0,
        warnings=[] if paragraphs else ["document contains no text"],
    )


# ----------------------------------------------------------------------------- pdf


def extract_pdf(data: bytes, *, max_pages: int = 200) -> Extraction:
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError:
        return Extraction(
            media_type=PDF_TYPE,
            method="pypdf",
            text="",
            completeness="blocked",
            warnings=["pypdf is not installed: `pip install sdlc-agent-teams[pdf]`; the original is retained"],
        )
    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:  # pragma: no cover - depends on cipher support
                pass
            if reader.is_encrypted:
                return Extraction(media_type=PDF_TYPE, method="pypdf", text="", completeness="unreadable", warnings=["PDF is encrypted; supply the password-free version or the text"])
        pages = reader.pages
    except PdfReadError as exc:
        return Extraction(media_type=PDF_TYPE, method="pypdf", text="", completeness="unreadable", warnings=[f"PDF could not be parsed: {exc}"])
    warnings: list[str] = []
    if len(pages) > max_pages:
        warnings.append(f"PDF has {len(pages)} pages; only the first {max_pages} were extracted (imports.max_pdf_pages)")
    locators: list[Locator] = []
    empty = 0
    for number, page in enumerate(list(pages)[:max_pages], start=1):
        try:
            text = (page.extract_text() or "").strip()
        except Exception as exc:  # pragma: no cover - library-specific failures
            text = ""
            warnings.append(f"page {number}: extraction error {type(exc).__name__}")
        if text:
            locators.append(Locator("page", number, text))
        else:
            empty += 1
            warnings.append(f"page {number}: no extractable text (scanned image? OCR is an optional capability)")
    total = min(len(pages), max_pages)
    completeness = "complete" if empty == 0 and total > 0 else ("unreadable" if empty == total else "partial")
    return Extraction(
        media_type=PDF_TYPE,
        method="pypdf",
        text="\n\n".join(l.text for l in locators),
        locators=locators,
        warnings=warnings,
        completeness=completeness,
        units_total=total,
        units_empty=empty,
    )


# ----------------------------------------------------------------------------- docx


def extract_docx(data: bytes) -> Extraction:
    try:
        import docx  # python-docx
    except ImportError:
        return Extraction(
            media_type=DOCX_TYPE,
            method="python-docx",
            text="",
            completeness="blocked",
            warnings=["python-docx is not installed: `pip install sdlc-agent-teams[docx]`; the original is retained"],
        )
    try:
        document = docx.Document(BytesIO(data))
    except Exception as exc:
        return Extraction(media_type=DOCX_TYPE, method="python-docx", text="", completeness="unreadable", warnings=[f"DOCX could not be opened: {type(exc).__name__}: {exc}"])
    locators: list[Locator] = []
    empty = 0
    index = 0
    for paragraph in document.paragraphs:
        index += 1
        text = paragraph.text.strip()
        if not text:
            empty += 1
            continue
        style = (paragraph.style.name if paragraph.style is not None else "") or ""
        if style.lower().startswith("heading"):
            locators.append(Locator("heading", index, text, detail=style))
        else:
            locators.append(Locator("paragraph", index, text, detail=style or None))
    for t_index, table in enumerate(document.tables, start=1):
        for r_index, row in enumerate(table.rows, start=1):
            for c_index, cell in enumerate(row.cells, start=1):
                text = cell.text.strip()
                if text:
                    index += 1
                    locators.append(Locator("table_cell", index, text, detail=f"table {t_index}, row {r_index}, col {c_index}"))
    total = index
    warnings: list[str] = []
    if document.inline_shapes:
        warnings.append(f"{len(document.inline_shapes)} embedded image(s) were not extracted")
    completeness = "complete" if locators else "unreadable"
    return Extraction(
        media_type=DOCX_TYPE,
        method="python-docx",
        text="\n\n".join(l.text for l in locators),
        locators=locators,
        warnings=warnings or ([] if locators else ["document contains no text"]),
        completeness=completeness,
        units_total=total,
        units_empty=empty,
    )
