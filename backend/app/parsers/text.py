"""Plain text and Markdown parser."""
from __future__ import annotations

import re

from .base import ExtractedChunk, ExtractionResult, rows_to_table
from .tabular import _decode

MD_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
MD_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")
LIST_RE = re.compile(r"^\s*([-*•+]|\d+[.)])\s+(.*)$")


def parse_text(data: bytes, filename: str, markdown: bool = False) -> ExtractionResult:
    res = ExtractionResult("md" if markdown else "txt")
    text, enc = _decode(data)
    lines = text.splitlines()
    heading, para, para_start = "", [], 0
    tables = 0
    i = 0

    def flush(end_line: int):
        nonlocal para
        if para:
            res.chunks.append(ExtractedChunk("paragraph", " ".join(" ".join(para).split()), heading,
                                             {"file": filename, "lines": f"{para_start}-{end_line}"}))
            para = []

    while i < len(lines):
        line = lines[i]
        ln = i + 1
        stripped = line.strip()
        # markdown table
        if stripped.startswith("|") and i + 1 < len(lines) and MD_TABLE_SEP.match(lines[i + 1]):
            flush(ln - 1)
            rows = [[c.strip() or None for c in stripped.strip("|").split("|")]]
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                rows.append([c.strip() or None for c in lines[j].strip().strip("|").split("|")])
                j += 1
            tables += 1
            # header + separator lines: data starts two lines after the header
            t = rows_to_table([rows[0]] + rows[1:], f"{filename} › table {tables}", "md_table",
                              {"file": filename, "table": tables, "section": heading})
            if t is not None:
                t.row_numbers = [ln + 2 + k for k in range(len(t.df))]
                res.tables.append(t)
            i = j
            continue
        m = MD_HEADING.match(stripped) if markdown else None
        is_plain_heading = (not markdown and stripped and len(stripped) < 80 and stripped == stripped.upper()
                            and any(ch.isalpha() for ch in stripped) and not stripped.endswith("."))
        if m or is_plain_heading:
            flush(ln - 1)
            heading = m.group(2).strip() if m else stripped.title()
            res.chunks.append(ExtractedChunk("heading", heading, heading, {"file": filename, "line": ln}))
        elif LIST_RE.match(line):
            flush(ln - 1)
            res.chunks.append(ExtractedChunk("list_item", LIST_RE.match(line).group(2).strip(), heading,
                                             {"file": filename, "line": ln}))
        elif not stripped:
            flush(ln - 1)
        else:
            if not para:
                para_start = ln
            para.append(stripped)
        i += 1
    flush(len(lines))
    res.metadata.update({"encoding": enc, "lines": len(lines)})
    if not res.chunks and not res.tables:
        res.error("The file contains no text.", "It is empty.")
    return res
