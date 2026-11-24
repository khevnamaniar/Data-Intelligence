"""Common types for file parsers."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass
class ExtractedTable:
    name: str
    df: pd.DataFrame  # header already applied; values as read from the source
    origin: str  # sheet | csv | pdf_table | docx_table | pptx_table | pptx_chart | md_table | image_chart
    locator: dict = field(default_factory=dict)
    row_numbers: list[int] | None = None  # source row/line number for each df row (traceability)
    approximate: bool = False
    note: str = ""


@dataclass
class ExtractedChunk:
    kind: str  # heading | paragraph | list_item | caption | footnote | note | slide_title | ocr | table_text
    text: str
    heading: str = ""
    locator: dict = field(default_factory=dict)
    approximate: bool = False


@dataclass
class ExtractionResult:
    file_type: str
    tables: list[ExtractedTable] = field(default_factory=list)
    chunks: list[ExtractedChunk] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> str:
        has_content = bool(self.tables or self.chunks)
        if self.errors and not has_content:
            return "failed"
        if self.errors or (self.warnings and not has_content):
            return "partial"
        return "processed" if has_content else "partial"

    def error(self, what: str, why: str, can_still: str = "", next_step: str = "") -> None:
        self.errors.append({"what": what, "why": why, "can_still": can_still, "next": next_step})


class ParserError(Exception):
    def __init__(self, what: str, why: str, next_step: str = ""):
        super().__init__(what)
        self.what, self.why, self.next_step = what, why, next_step


def clean_header(values: list[Any]) -> list[str]:
    """Turn a raw header row into unique, non-empty column names."""
    names: list[str] = []
    seen: dict[str, int] = {}
    for i, v in enumerate(values):
        name = " ".join(str(v).split()) if v is not None and str(v).strip() not in {"", "nan", "None"} else f"Column {i + 1}"
        base = name
        if base in seen:
            seen[base] += 1
            name = f"{base} ({seen[base]})"
        else:
            seen[base] = 1
        names.append(name)
    return names


def detect_header_row(rows: list[list[Any]], max_scan: int = 15) -> int:
    """Pick the row that most looks like a header (mostly non-empty text, followed by data)."""
    best, best_score = 0, -1.0
    width = max((len(r) for r in rows[:max_scan]), default=0)
    if width == 0:
        return 0
    for i, row in enumerate(rows[:max_scan]):
        cells = [c for c in row if c is not None and str(c).strip() != ""]
        if not cells:
            continue
        text_cells = sum(1 for c in cells if isinstance(c, str) and not _looks_numeric(c))
        fill = len(cells) / width
        score = fill * 0.6 + (text_cells / max(len(cells), 1)) * 0.4
        # A header must be followed by at least one non-empty row.
        if i + 1 < len(rows) and any(c not in (None, "") for c in rows[i + 1]):
            score += 0.05
        if len(cells) == 1 and width > 2:  # a title line, not a header
            score -= 0.5
        if score > best_score + 0.05:
            best, best_score = i, score
    return best


def _looks_numeric(s: str) -> bool:
    t = s.strip().replace(",", "").replace("%", "").replace("$", "").replace("£", "").replace("€", "")
    try:
        float(t)
        return True
    except ValueError:
        return False


def rows_to_table(rows: list[list[Any]], name: str, origin: str, locator: dict, first_row_number: int = 1,
                  approximate: bool = False) -> ExtractedTable | None:
    """Convert raw rows (lists) into an ExtractedTable with header detection and row numbers."""
    rows = [list(r) for r in rows if r is not None]
    # drop fully empty rows but keep numbering
    numbered = [(first_row_number + i, r) for i, r in enumerate(rows)]
    numbered = [(n, r) for n, r in numbered if any(c is not None and str(c).strip() != "" for c in r)]
    if len(numbered) < 2:
        return None
    raw = [r for _, r in numbered]
    width = max(len(r) for r in raw)
    raw = [r + [None] * (width - len(r)) for r in raw]
    h = detect_header_row(raw)
    header = clean_header(raw[h])
    body = raw[h + 1:]
    numbers = [n for n, _ in numbered][h + 1:]
    if not body:
        return None
    df = pd.DataFrame(body, columns=header)
    # drop columns that are entirely empty
    keep = [c for c in df.columns if df[c].notna().any() and (df[c].astype(str).str.strip() != "").any()]
    df = df[keep]
    if df.shape[1] == 0:
        return None
    loc = dict(locator)
    loc["header_row"] = [n for n, _ in numbered][h]
    return ExtractedTable(name=name, df=df.reset_index(drop=True), origin=origin, locator=loc,
                          row_numbers=numbers, approximate=approximate)
