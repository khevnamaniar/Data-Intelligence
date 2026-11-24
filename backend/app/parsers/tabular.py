"""CSV and Excel parsers."""
from __future__ import annotations

import csv
import io
import zipfile

from .base import ExtractionResult, ParserError, rows_to_table


def _decode(data: bytes) -> tuple[str, str]:
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            pass
    try:
        from charset_normalizer import from_bytes

        best = from_bytes(data).best()
        if best is not None:
            return str(best), best.encoding
    except Exception:
        pass
    return data.decode("latin-1"), "latin-1"


def sniff_delimiter(sample: str) -> str:
    """Pick the delimiter that gives the most lines with the same (>1) number of fields."""
    from collections import Counter

    lines = [ln for ln in sample.splitlines() if ln.strip()][:60]
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


def parse_csv(data: bytes, filename: str) -> ExtractionResult:
    res = ExtractionResult("csv")
    text, encoding = _decode(data)
    delimiter = sniff_delimiter(text[:50000])
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        rows = [row for row in reader]
    except csv.Error as exc:
        raise ParserError("The CSV file could not be read.", str(exc), "Check that the file is a valid CSV export.")
    rows = [[(c if c.strip() != "" else None) for c in r] for r in rows]
    table = rows_to_table(rows, filename, "csv", {"file": filename}, first_row_number=1)
    if table is None:
        res.error("No data rows were found.", "The CSV file is empty or only contains a header.",
                  next_step="Upload a CSV that has a header row and at least one data row.")
    else:
        # line numbers in a CSV include the header line, which matches what spreadsheet apps show
        res.tables.append(table)
    res.metadata.update({"encoding": encoding, "delimiter": delimiter, "rows": len(rows)})
    return res


def parse_xlsx(data: bytes, filename: str) -> ExtractionResult:
    res = ExtractionResult("xlsx")
    try:
        import openpyxl

        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except (zipfile.BadZipFile, KeyError, ValueError, OSError) as exc:
        raise ParserError("The Excel file could not be opened.", f"It may be corrupt or password-protected ({exc.__class__.__name__}).",
                          "Open it in Excel, save a fresh copy as .xlsx and upload again.")
    sheets = []
    for ws in wb.worksheets:
        try:
            rows = [list(r) for r in ws.iter_rows(values_only=True)]
        except Exception as exc:  # pragma: no cover - unusual workbook structures
            res.error(f"Sheet '{ws.title}' could not be read.", str(exc), "Other sheets were still analysed.")
            continue
        sheets.append(ws.title)
        if not rows:
            res.warnings.append(f"Sheet '{ws.title}' is empty.")
            continue
        table = rows_to_table(rows, f"{filename} › {ws.title}", "sheet", {"file": filename, "sheet": ws.title}, first_row_number=1)
        if table is None:
            res.warnings.append(f"Sheet '{ws.title}' has no table-shaped data.")
            continue
        res.tables.append(table)
    wb.close()
    res.metadata["sheets"] = sheets
    if not res.tables and not res.errors:
        res.error("No tables were found in the workbook.", "All sheets are empty or contain only text.",
                  next_step="Check the workbook contains rows of data with a header row.")
    return res
