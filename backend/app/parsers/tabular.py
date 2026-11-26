"""Spreadsheet and delimited-text parsers.

Handles: CSV/TSV/TXT exports in any common delimiter and encoding (UTF-8/16, Windows-1252, …, the Excel
"sep=;" hint line), Excel .xlsx/.xlsm (macros are ignored, never run), .xlsb, legacy .xls, OpenDocument .ods
and JSON.  Every sheet is split into its separate tables (blocks of cells separated by empty rows/columns);
title lines and notes become text, hidden sheets are skipped.
"""
from __future__ import annotations

import csv
import io
import json
import re
import zipfile

from .base import ExtractedChunk, ExtractionResult, ParserError, rows_to_table

def _max_cells() -> int:
    """Cells read per file. 1.5 million (~150,000 rows x 10 columns) keeps a 512 MB server safe; raise MAX_CELLS
    on a larger server."""
    import os

    try:
        return max(10_000, int(os.environ.get("MAX_CELLS", "1500000")))
    except ValueError:
        return 1_500_000


MAX_CELLS = _max_cells()


def _too_big_error(res: ExtractionResult, read_rows: int, total_rows: int | None = None) -> None:
    total = f" of about {total_rows:,}" if total_rows else ""
    res.error(f"This file is larger than this server can analyse at once: the first {read_rows:,}{total} rows were read.",
              f"The limit is {MAX_CELLS:,} cells per file, to stay within the server's memory.",
              "The rows that were read are analysed normally.",
              "To analyse everything, split the file (for example one file per year or region), or summarise it "
              "(for example totals per day) before uploading.")


# ------------------------------------------------------------------ text decoding
def _decode(data: bytes) -> tuple[str, str]:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16"), "utf-16"
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace"), "utf-8-sig"
    # UTF-16 without a BOM: every other byte is zero
    head = data[:2000]
    if len(head) > 20 and head[1::2].count(0) > 0.4 * len(head[1::2]) and head[0::2].count(0) < 0.1 * len(head[0::2]):
        try:
            return data.decode("utf-16-le"), "utf-16-le"
        except UnicodeDecodeError:
            pass
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        from charset_normalizer import from_bytes

        best = from_bytes(data[:200_000]).best()
        if best is not None:
            enc = best.encoding.lower().replace("-", "_")
            multi_byte = re.match(r"(utf|gb|big5|shift_jis|sjis|euc|iso2022|cp93[26]|cp949|cp950|hz|johab)", enc)
            other_script = re.match(r"(cp1251|cp1253|cp1255|cp1256|cp1257|koi8|iso8859_[5678]|mac_(cyrillic|greek)|cp866|cp874|tis)", enc)
            if not multi_byte and not other_script:
                # single-byte Western guesses (cp775, cp850, latin-1, …) are almost always Windows-1252 exports from Excel
                return data.decode("cp1252", errors="replace"), "cp1252"
            return data.decode(best.encoding, errors="replace"), best.encoding
    except Exception:
        pass
    return data.decode("cp1252", errors="replace"), "cp1252"


def sniff_delimiter(sample: str) -> str:
    """Pick the delimiter that gives the most lines with the same (>1) number of fields."""
    from collections import Counter

    lines = [ln for ln in sample.splitlines() if ln.strip()][:80]
    best, best_score = ",", (-1, -1)
    for d in (",", ";", "\t", "|"):
        try:
            counts = [len(r) for r in csv.reader(lines, delimiter=d)]
        except csv.Error:
            continue
        common = Counter(c for c in counts if c > 1).most_common(1)
        if not common:
            continue
        width, n = common[0]
        score = (n, width)
        if score > best_score:
            best, best_score = d, score
    return best


def looks_delimited(text: str) -> bool:
    """Is a .txt file really a table export (several lines with the same number of fields)?"""
    lines = [ln for ln in text.splitlines()[:60] if ln.strip()]
    if len(lines) < 3:
        return False
    d = sniff_delimiter("\n".join(lines))
    counts = [len(r) for r in csv.reader(lines, delimiter=d)]
    width = max(set(counts), key=counts.count)
    return width >= 2 and counts.count(width) >= 0.7 * len(counts)


def parse_csv(data: bytes, filename: str) -> ExtractionResult:
    res = ExtractionResult("csv")
    if not data.strip():
        res.error("The file is empty.", "It contains no rows.", next_step="Export the data again and upload the new file.")
        return res
    text, encoding = _decode(data)
    text = text.lstrip("﻿")
    delimiter = None
    first = text.split("\n", 1)[0].strip().strip('"')
    m = re.match(r"^sep=(.)$", first, re.I)  # Excel's "sep=;" hint line
    if m:
        delimiter = m.group(1)
        text = text.split("\n", 1)[1] if "\n" in text else ""
    delimiter = delimiter or sniff_delimiter(text[:100_000])
    csv.field_size_limit(10 * 1024 * 1024)
    truncated = False
    try:
        rows, cells = [], 0
        for r in csv.reader(io.StringIO(text), delimiter=delimiter):
            rows.append(r)
            cells += len(r)
            if cells > MAX_CELLS:
                truncated = True
                break
    except csv.Error:
        # unbalanced quotes: read line by line without quote handling
        rows = [ln.split(delimiter) for ln in text.splitlines()]
        res.warnings.append("Some quotes in the file were unbalanced, so quotes were read as plain characters.")
    if truncated:
        _too_big_error(res, len(rows) - 1, max(text.count("\n") - 1, 0))  # minus the header line
    del text
    rows = [[(c if c.strip() != "" else None) for c in r] for r in rows]
    tables = _tables_from_grid(rows, filename, "csv", {"file": filename}, res)
    if not tables:
        res.chunks.clear()
        res.error("No data rows were found.", "The file is empty or only contains a header.",
                  next_step="Upload a CSV that has a header row and at least one data row.")
    res.metadata.update({"encoding": encoding, "delimiter": {"\t": "tab"}.get(delimiter, delimiter), "rows": len(rows)})
    return res


# ------------------------------------------------------------------ grids -> tables
def _filled(v) -> bool:
    return v is not None and not (isinstance(v, str) and v.strip() == "")


def split_blocks(rows: list[list]) -> list[tuple[int, int, int, int]]:
    """Find separate tables in a sheet: (first_row, last_row, first_col, last_col), 0-based inclusive.

    Columns that are empty over the whole sheet split side-by-side tables (unless both sides cover the same rows,
    which is just a spacer column). Within a column band, a run of 2+ empty rows - or 1 empty row followed by a new
    header line - starts a new table."""
    if not rows:
        return []
    width = max((len(r) for r in rows), default=0)
    col_used = [any(_filled(r[c]) if c < len(r) else False for r in rows) for c in range(width)]
    bands, start = [], None
    for c in range(width + 1):
        if c < width and col_used[c]:
            start = c if start is None else start
        elif start is not None:
            bands.append([start, c - 1])
            start = None
    # merge bands that cover the same rows (spacer columns inside one table)
    def extent(b):
        rs = [i for i, r in enumerate(rows) if any(_filled(r[c]) for c in range(b[0], min(b[1] + 1, len(r))))]
        return (rs[0], rs[-1]) if rs else (0, -1)
    merged: list[list[int]] = []
    for b in bands:
        if merged:
            e1, e2 = extent(merged[-1]), extent(b)
            if abs(e1[0] - e2[0]) <= 1 and abs(e1[1] - e2[1]) <= 1:
                merged[-1][1] = b[1]
                continue
        merged.append(b)
    blocks = []
    for c0, c1 in merged:
        filled = [sum(1 for c in range(c0, min(c1 + 1, len(r))) if _filled(r[c])) for r in rows]
        seg_start, empties = None, 0
        for i, n in enumerate(filled + [0, 0]):
            if n:
                if seg_start is None:
                    seg_start = i
                elif empties:
                    new_header = empties >= 2 or (_header_like(rows[i][c0:c1 + 1]) and i + 1 < len(rows)
                                                  and filled[i + 1 if i + 1 < len(filled) else i] > 0
                                                  and _differs(rows, seg_start, i - empties - 1, i, c0, c1))
                    if new_header:
                        blocks.append((seg_start, i - empties - 1, c0, c1))
                        seg_start = i
                empties = 0
            else:
                if seg_start is not None:
                    empties += 1
                    if empties >= 2 and i >= len(filled):
                        break
        if seg_start is not None:
            last = max(i for i, n in enumerate(filled) if n)
            blocks.append((seg_start, last, c0, c1))
    return blocks


def _header_like(cells) -> bool:
    vals = [c for c in cells if _filled(c)]
    return len(vals) >= 1 and all(isinstance(c, str) and not re.fullmatch(r"[-+()$€£₹.,%\d\s]+", c) for c in vals)


def _differs(rows, s0, s1, n0, c0, c1) -> bool:
    """Does the block starting at n0 use different columns from the block s0..s1 (a new table, not a gap)?"""
    def cols(a, b):
        used = set()
        for r in rows[a:b + 1]:
            used |= {c for c in range(c0, min(c1 + 1, len(r))) if _filled(r[c])}
        return used
    a, b = cols(s0, s1), cols(n0, min(n0 + 5, len(rows) - 1))
    return not b or len(a & b) / max(len(a | b), 1) < 0.8


def _tables_from_grid(rows: list[list], name: str, origin: str, locator: dict, res: ExtractionResult) -> list:
    """Split a sheet/CSV grid into tables; single-cell lines (titles, notes, 'Source: …') become text."""
    width = max((len(r) for r in rows), default=0)
    rows = [list(r) + [None] * (width - len(r)) for r in rows]
    tables = []
    blocks = split_blocks(rows)
    real = []
    for b in blocks:
        r0, r1, c0, c1 = b
        sub = [r[c0:c1 + 1] for r in rows[r0:r1 + 1]]
        multi = [r for r in sub if sum(1 for v in r if _filled(v)) >= 2]
        vals = [str(v).strip() for r in sub for v in r if _filled(v)]
        is_list = (c1 == c0 or len(multi) < 2) and len(vals) >= 4 and sorted(len(v) for v in vals)[len(vals) // 2] <= 40 \
            and len(vals) == len(sub)
        if is_list:  # a one-column list (e.g. customer names) is a table with one column
            real.append((r0, r1, c0, c1))
            continue
        if len(multi) < 2:  # titles, notes, footers: keep as text
            for k, r in enumerate(sub):
                txt = " ".join(str(v).strip() for v in r if _filled(v))
                if txt:
                    res.chunks.append(ExtractedChunk("note", txt[:2000], "", {**locator, "row": r0 + k + 1}))
            continue
        real.append(b)
    for k, (r0, r1, c0, c1) in enumerate(real):
        sub = [r[c0:c1 + 1] for r in rows[r0:r1 + 1]]
        title = ""
        # a single-cell line directly above the table is its title
        lead = [v for v in sub[0] if _filled(v)]
        if len(lead) == 1 and isinstance(lead[0], str) and len(sub) > 2:
            title = lead[0].strip()[:80]
        tname = name if len(real) == 1 else f"{name} › {title or f'table {k + 1}'}"
        loc = {**locator}
        if len(real) > 1:
            loc.update({"table": k + 1, "cells": f"{_col(c0)}{r0 + 1}:{_col(c1)}{r1 + 1}"})
        if title:
            loc["title"] = title
        t = rows_to_table(sub, tname, origin, loc, first_row_number=r0 + 1)
        if t is not None:
            tables.append(t)
            res.tables.append(t)
    return tables


def _col(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _cell(v):
    if isinstance(v, str):
        v = v.lstrip("'") if v.startswith("'") and len(v) > 1 else v
        if re.fullmatch(r"#(DIV/0!|N/A|REF!|VALUE!|NAME\?|NUM!|NULL!|SPILL!|CALC!|GETTING_DATA)", v.strip()):
            return None  # Excel error values are missing values
    return v


# ------------------------------------------------------------------ Excel / OpenDocument
def _password_protected(data: bytes) -> bool:
    return data.startswith(b"\xd0\xcf\x11\xe0") and ("EncryptedPackage".encode("utf-16-le") in data[:200_000]
                                                     or b"EncryptedPackage" in data[:200_000])


def _protected_error():
    return ParserError("The spreadsheet is password-protected.",
                       "Its contents are encrypted, so they cannot be read without the password.",
                       "Open it in Excel, remove the password (File › Info › Protect Workbook), save and upload again.")


def parse_spreadsheet(data: bytes, filename: str, kind: str = "xlsx") -> ExtractionResult:
    """kind: xlsx | xlsm | xlsb | xls | ods (detected from the content, not just the name)."""
    if _password_protected(data):
        raise _protected_error()
    res = ExtractionResult("xlsx")
    if kind in ("xlsx", "xlsm"):
        try:
            _read_openpyxl(data, filename, res)
            if kind == "xlsm":
                res.warnings.append("This is a macro-enabled workbook. Only its data was read; macros were ignored and never run.")
            return _finish(res)
        except ParserError:
            raise
        except Exception:
            res = ExtractionResult("xlsx")  # fall back to the second reader below
    _read_calamine(data, filename, res, kind)
    return _finish(res)


def _finish(res: ExtractionResult) -> ExtractionResult:
    if not res.tables and not res.errors:
        if res.chunks:
            res.warnings.append("No table-shaped data was found; the text in the workbook was kept for questions and search.")
        else:
            res.error("No tables were found in the workbook.", "All sheets are empty or contain only text.",
                      next_step="Check the workbook contains rows of data with a header row.")
    return res


def _read_openpyxl(data: bytes, filename: str, res: ExtractionResult) -> None:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)  # failures fall back to the second reader
    sheets, cells, formulas_missing = [], 0, []
    for ws in wb.worksheets:
        hidden = getattr(ws, "sheet_state", "visible") != "visible"
        sheets.append(ws.title)
        try:
            rows = []
            for r in ws.iter_rows(values_only=True):
                rows.append([_cell(v) for v in r])
                cells += len(r)
                if cells > MAX_CELLS:
                    break
        except Exception as exc:  # pragma: no cover - unusual workbook structures
            res.error(f"Sheet '{ws.title}' could not be read.", str(exc)[:200], "Other sheets were still analysed.")
            continue
        _sheet_to_tables(rows, filename, ws.title, res, hidden)
        if cells > MAX_CELLS:
            _too_big_error(res, len(rows))
            break
    wb.close()
    # formulas saved without results (e.g. files written by scripts) read as empty cells
    try:
        if not res.tables or any(t.df.isna().all().any() for t in res.tables):
            wbf = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=False)
            for ws in wbf.worksheets[:20]:
                for r in ws.iter_rows(max_row=50, values_only=True):
                    if any(isinstance(v, str) and v.startswith("=") for v in r):
                        formulas_missing.append(ws.title)
                        break
            wbf.close()
    except Exception:
        pass
    if formulas_missing:
        res.warnings.append(f"Some formula cells in {', '.join(sorted(set(formulas_missing)))} have no saved results, so they "
                            "read as empty. Open the file in Excel, save it, and upload again to include them.")
    res.metadata["sheets"] = sheets


def _read_calamine(data: bytes, filename: str, res: ExtractionResult, kind: str) -> None:
    try:
        import python_calamine as pc

        wb = pc.CalamineWorkbook.from_filelike(io.BytesIO(data))
    except Exception as exc:
        name = exc.__class__.__name__
        if "Password" in name:
            raise _protected_error()
        raise ParserError("The spreadsheet could not be opened.", f"It may be corrupt or in an unusual format ({name}).",
                          "Open it in Excel or LibreOffice, save a fresh copy as .xlsx and upload again.")
    sheets, cells = [], 0
    meta = {m.name: m for m in getattr(wb, "sheets_metadata", [])}
    for sname in wb.sheet_names:
        m = meta.get(sname)
        hidden = m is not None and "Visible" not in str(getattr(m, "visible", "Visible"))
        if m is not None and "WorkSheet" not in str(getattr(m, "typ", "WorkSheet")):
            continue  # chart sheets etc.
        try:
            rows = []
            for r in wb.get_sheet_by_name(sname).iter_rows():
                rows.append([_cell(v if v != "" else None) for v in r])
                if cells + len(rows) * max(len(r), 1) > MAX_CELLS:
                    break
        except Exception as exc:
            res.error(f"Sheet '{sname}' could not be read.", str(exc)[:200], "Other sheets were still analysed.")
            continue
        sheets.append(sname)
        cells += sum(len(r) for r in rows)
        _sheet_to_tables(rows, filename, sname, res, hidden)
        if cells > MAX_CELLS:
            _too_big_error(res, len(rows))
            break
    res.metadata.update({"sheets": sheets, "format": kind})


def _sheet_to_tables(rows, filename, sheet, res, hidden: bool = False):
    while rows and not any(_filled(v) for v in rows[-1]):
        rows.pop()
    if not rows:
        if not hidden:
            res.warnings.append(f"Sheet '{sheet}' is empty.")
        return
    loc = {"file": filename, "sheet": sheet, **({"hidden": True} if hidden else {})}
    n_before, c_before = len(res.tables), len(res.chunks)
    tables = _tables_from_grid(rows, f"{filename} › {sheet}", "sheet", loc, res)
    if hidden:
        # hidden sheets often hold real data (rate cards, schedules) but also drop-down lists: skip those
        for t in [t for t in res.tables[n_before:] if t.df.shape[1] < 2]:
            res.tables.remove(t)
        del res.chunks[c_before:]
        kept = len(res.tables) - n_before
        if kept:
            res.warnings.append(f"Hidden sheet '{sheet}' was included ({kept} table(s)).")
        return
    if not tables:
        if not any(c.locator.get("sheet") == sheet for c in res.chunks):
            res.warnings.append(f"Sheet '{sheet}' has no table-shaped data.")


def parse_xlsx(data: bytes, filename: str) -> ExtractionResult:  # backwards-compatible name
    return parse_spreadsheet(data, filename, "xlsx")


# ------------------------------------------------------------------ JSON
def parse_json(data: bytes, filename: str) -> ExtractionResult:
    import pandas as pd

    res = ExtractionResult("json")
    text, _ = _decode(data)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        # JSON Lines: one object per line
        try:
            obj = [json.loads(ln) for ln in text.splitlines() if ln.strip()]
        except json.JSONDecodeError as exc:
            raise ParserError("The JSON file could not be read.", f"It is not valid JSON ({exc.msg} at line {exc.lineno}).",
                              "Export the data again, or save it as CSV.")
    found: list[tuple[str, list]] = []

    def walk(o, path):
        if isinstance(o, list) and o and sum(isinstance(x, dict) for x in o) >= 0.8 * len(o):
            found.append((path or "records", o))
            return
        if isinstance(o, dict):
            if o and all(isinstance(v, list) for v in o.values()) and len({len(v) for v in o.values()}) == 1 and len(next(iter(o.values()))) > 1:
                found.append((path or "data", [dict(zip(o, vals)) for vals in zip(*o.values())]))  # column-oriented
                return
            for k, v in o.items():
                walk(v, f"{path}.{k}" if path else str(k))
    walk(obj, "")
    for path, recs in found[:20]:
        df = pd.json_normalize([r for r in recs if isinstance(r, dict)], sep=".", max_level=3)
        df = df.map(lambda v: json.dumps(v, ensure_ascii=False)[:500] if isinstance(v, (list, dict)) else v)
        rows = [list(df.columns)] + df.astype(object).where(df.notna(), None).values.tolist()
        t = rows_to_table(rows, f"{filename} › {path}" if len(found) > 1 else filename, "json", {"file": filename, "path": path},
                          first_row_number=1, header_row=0)
        if t is not None:
            res.tables.append(t)
    if not res.tables:
        if isinstance(obj, dict):
            flat = pd.json_normalize(obj, sep=".")
            rows = [["Field", "Value"]] + [[k, v] for k, v in flat.iloc[0].items()]
            t = rows_to_table(rows, filename, "json", {"file": filename}, header_row=0)
            if t is not None:
                res.tables.append(t)
        if not res.tables:
            res.error("No records were found in the JSON file.", "It does not contain a list of records.",
                      next_step="Export the data as a list of records or as CSV.")
    return res
