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


def _header_text(v: Any) -> str:
    if v is None:
        return ""
    if hasattr(v, "strftime") and not isinstance(v, str):
        try:  # date headers (e.g. a month typed as a date) -> "2026-01-01"
            return v.strftime("%Y-%m-%d") if getattr(v, "hour", 0) == 0 and getattr(v, "minute", 0) == 0 else v.isoformat(sep=" ")
        except (ValueError, TypeError):
            return str(v)
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    t = " ".join(str(v).split())
    return "" if t in {"nan", "None", "NaT"} else t


def clean_header(values: list[Any]) -> list[str]:
    """Turn a raw header row into unique, non-empty column names."""
    names: list[str] = []
    seen: dict[str, int] = {}
    for i, v in enumerate(values):
        name = _header_text(v) or f"Column {i + 1}"
        base = name
        if base in seen:
            seen[base] += 1
            name = f"{base} ({seen[base]})"
        else:
            seen[base] = 1
        names.append(name)
    return names


def _filled(c: Any) -> bool:
    return c is not None and str(c).strip() != ""


def detect_header_row(rows: list[list[Any]], max_scan: int = 30) -> int:
    """Pick the row that most looks like a header: mostly non-empty text, followed by data, and whose values do
    not repeat further down the same column (a data row's values do, e.g. 'North' every 4th row)."""
    best, best_score = 0, -1.0
    width = max((len(r) for r in rows[:max_scan]), default=0)
    if width == 0:
        return 0
    for i, row in enumerate(rows[:max_scan]):
        cells = [c for c in row if _filled(c)]
        if not cells:
            continue
        text_cells = sum(1 for c in cells if isinstance(c, str) and not _looks_numeric(c))
        fill = len(cells) / width
        score = fill * 0.6 + (text_cells / max(len(cells), 1)) * 0.4
        if i + 1 < len(rows) and any(_filled(c) for c in rows[i + 1]):
            score += 0.05
        if len(cells) == 1 and width > 2:  # a title line, not a header
            score -= 0.5
        key = [str(c).strip() if _filled(c) else "" for c in row]
        below = [r for r in rows[i + 1:i + 16] if [str(c).strip() if _filled(c) else "" for c in r] != key]  # repeated headers
        repeats = 0
        for j, c in enumerate(row):
            if _filled(c) and any(j < len(r) and _filled(r[j]) and str(r[j]).strip() == str(c).strip() for r in below):
                repeats += 1
        score -= 0.5 * repeats / max(len(cells), 1)
        if score > best_score + 0.05:
            best, best_score = i, score
    return best


def _looks_numeric(s: str) -> bool:
    t = s.strip().replace(",", "").replace("%", "").replace("$", "").replace("£", "").replace("€", "").replace("₹", "")
    try:
        float(t)
        return True
    except ValueError:
        return False


def _combine_two_row_header(raw: list[list[Any]], h: int) -> tuple[list[Any], int]:
    """'2025 | | 2026 |' above 'Units | Revenue | Units | Revenue' -> '2025 Units', '2025 Revenue', …
    Used when the header row has repeated names that the (sparser, merged-looking) row above tells apart."""
    if h == 0:
        return raw[h], h
    top, head = raw[h - 1], raw[h]
    names = [_header_text(v) for v in head]
    dup = len([n for n in names if n]) != len({n for n in names if n})
    top_cells = [i for i, v in enumerate(top) if _filled(v)]
    if not dup or len(top_cells) < 2 or len(top_cells) >= sum(1 for n in names if n):
        return raw[h], h
    filled_top, cur = [], ""
    for i, v in enumerate(top):
        if _filled(v):
            cur = _header_text(v)
        elif i < len(names) and not names[i]:
            cur = ""
        filled_top.append(cur if i >= top_cells[0] else "")
    combined = []
    for i in range(len(head)):
        a, b = filled_top[i] if i < len(filled_top) else "", names[i]
        combined.append(f"{a} {b}".strip() if a and b and a != b else (b or a or None))
    return combined, h - 1


def rows_to_table(rows: list[list[Any]], name: str, origin: str, locator: dict, first_row_number: int = 1,
                  approximate: bool = False, header_row: int | None = None) -> ExtractedTable | None:
    """Convert raw rows (lists) into an ExtractedTable with header detection and row numbers."""
    rows = [list(r) for r in rows if r is not None]
    # drop fully empty rows but keep numbering
    numbered = [(first_row_number + i, r) for i, r in enumerate(rows)]
    numbered = [(n, r) for n, r in numbered if any(_filled(c) for c in r)]
    if len(numbered) < 2:
        return None
    raw = [r for _, r in numbered]
    width = max(len(r) for r in raw)
    raw = [r + [None] * (width - len(r)) for r in raw]
    h = detect_header_row(raw) if header_row is None else header_row
    header_vals, top = _combine_two_row_header(raw, h)
    header = clean_header(header_vals)
    body = raw[h + 1:]
    numbers = [n for n, _ in numbered][h + 1:]
    # the header repeated further down (page breaks in exported reports)
    hkey = [_header_text(v) for v in raw[h]]
    keep_i = [i for i, r in enumerate(body) if [_header_text(v) for v in r] != hkey]
    if len(keep_i) < len(body):
        body, numbers = [body[i] for i in keep_i], [numbers[i] for i in keep_i]
        locator = {**locator, "repeated_headers_removed": True}
    # section label rows ("North" alone on a row, then that region's rows) -> a "Section" column
    if width >= 3 and body:
        def label(r):
            cells = [(j, c) for j, c in enumerate(r) if _filled(c)]
            return cells[0][1] if len(cells) == 1 and cells[0][0] == 0 and isinstance(cells[0][1], str) \
                and not _looks_numeric(cells[0][1]) and len(cells[0][1]) <= 60 else None
        labels = [label(r) for r in body]
        n_labels = sum(1 for x in labels if x)
        if n_labels >= 2 and labels[0] and n_labels <= len(body) / 2:
            sec, nb, nn, secs = None, [], [], []
            for r, n, lab in zip(body, numbers, labels):
                if lab:
                    sec = lab.strip()
                    continue
                nb.append(r)
                nn.append(n)
                secs.append(sec)
            body, numbers = [[s_] + r for s_, r in zip(secs, nb)], nn
            header = clean_header(["Section"] + list(header_vals))
            width += 1
    # notes stuck to the bottom of the table ("Source: …", "Prepared by …"): one text cell in a wide table
    while body and width >= 3:
        cells = [c for c in body[-1] if _filled(c)]
        if len(body) > 1 and len(cells) == 1 and isinstance(cells[0], str) and not _looks_numeric(cells[0]):
            body, numbers = body[:-1], numbers[:-1]
        else:
            break
    if not body:
        return None
    df = pd.DataFrame(body, columns=header)
    # drop columns that are entirely empty
    keep = [c for c in df.columns if df[c].notna().any() and (df[c].astype(str).str.strip() != "").any()]
    df = df[keep]
    if df.shape[1] == 0:
        return None
    loc = dict(locator)
    loc["header_row"] = [n for n, _ in numbered][top]
    return ExtractedTable(name=name, df=df.reset_index(drop=True), origin=origin, locator=loc,
                          row_numbers=numbers, approximate=approximate)
