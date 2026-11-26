"""A corpus of awkward real-world spreadsheets, each with what a correct reading must find.

Used by test_messy_sheets.py. Every file is generated here, so no real data is stored in the repository.
expect keys:  tables=int, rows=int (of the main table), num={column: total}, cat=[columns that must be labels],
              status=str, time_col=str, error_contains=str, warn_contains=str
"""
from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime, timedelta

from openpyxl import Workbook
from openpyxl.comments import Comment

REGIONS = ["North", "South", "East", "West"]
PRODUCTS = ["Alpha", "Beta", "Gamma"]


def _base_rows(n=24):
    out = []
    d0 = date(2026, 1, 1)
    for i in range(n):
        out.append({"Month": date(d0.year + (d0.month - 1 + i // 4) // 12, (d0.month - 1 + i // 4) % 12 + 1, 1),
                    "Region": REGIONS[i % 4], "Product": PRODUCTS[i % 3], "Units": 10 + i, "Revenue": 1000.0 + 50 * i})
    return out


BASE = _base_rows()
UNITS = sum(r["Units"] for r in BASE)
REV = sum(r["Revenue"] for r in BASE)
HEAD = ["Month", "Region", "Product", "Units", "Revenue"]


def _xlsx(build) -> bytes:
    wb = Workbook()
    build(wb)
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def _csv(rows, delimiter=",", encoding="utf-8", preamble="", lineterminator="\r\n") -> bytes:
    s = io.StringIO()
    w = csv.writer(s, delimiter=delimiter, lineterminator=lineterminator)
    for r in rows:
        w.writerow(r)
    return (preamble + s.getvalue()).encode(encoding)


def _plain(r):
    return [r["Month"].isoformat(), r["Region"], r["Product"], r["Units"], r["Revenue"]]


def corpus() -> list[tuple[str, bytes, dict]]:
    C: list[tuple[str, bytes, dict]] = []
    std = {"num": {"Units": UNITS, "Revenue": REV}, "rows": len(BASE), "cat": ["Region", "Product"], "time_col": "Month"}

    # ------------------------------------------------------------------ CSV / text variations
    C.append(("plain.csv", _csv([HEAD] + [_plain(r) for r in BASE]), std))
    C.append(("semicolon_decimal_comma.csv", _csv([HEAD] + [[r["Month"].strftime("%d.%m.%Y"), r["Region"], r["Product"], r["Units"],
                                                            f"{r['Revenue']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")]
                                                           for r in BASE], delimiter=";"), std))
    C.append(("tab_separated.tsv", _csv([HEAD] + [_plain(r) for r in BASE], delimiter="\t"), std))
    C.append(("pipe.csv", _csv([HEAD] + [_plain(r) for r in BASE], delimiter="|"), std))
    C.append(("utf16_excel_unicode_text.csv", _csv([HEAD] + [_plain(r) for r in BASE], delimiter="\t", encoding="utf-16"), std))
    C.append(("windows1252_accents.csv", _csv([["Mois", "Région", "Produit", "Unités", "Chiffre d'affaires €"]] +
                                              [[r["Month"].strftime("%d/%m/%Y"), r["Region"] + "é", r["Product"], r["Units"], f"€{r['Revenue']:.2f}"]
                                               for r in BASE], encoding="cp1252"),
              {"num": {"Unités": UNITS, "Chiffre d'affaires €": REV}, "rows": len(BASE), "cat": ["Région"]}))
    C.append(("excel_sep_hint.csv", _csv([HEAD] + [_plain(r) for r in BASE], delimiter=";", preamble="sep=;\r\n"), std))
    C.append(("report_preamble.csv", _csv([["Sales report"], ["Generated 2026-11-26"], []] + [HEAD] + [_plain(r) for r in BASE] +
                                          [[], ["Source: internal system"]]), std))
    C.append(("quoted_multiline_and_ragged.csv",
              _csv([HEAD + ["Notes"]] + [_plain(r) + (["line one\nline two, with comma"] if i % 5 == 0 else []) for i, r in enumerate(BASE)]), std))
    C.append(("unix_newlines_trailing_delims.csv", _csv([HEAD + [""]] + [_plain(r) + [""] for r in BASE], lineterminator="\n"), std))
    C.append(("accounting_formats.csv", _csv([HEAD] + [[r["Month"].strftime("%b %Y"), r["Region"], r["Product"],
                                                      f"({r['Units']})" if False else f"{r['Units']:,}",
                                                      f"$ {r['Revenue']:,.2f} "] for r in BASE]), std))
    neg = [[r["Month"].isoformat(), r["Region"], r["Product"], r["Units"], f"({r['Revenue']:,.0f})" if i % 2 else f"{r['Revenue']:,.0f}"]
           for i, r in enumerate(BASE)]
    C.append(("negatives_in_brackets.csv", _csv([HEAD] + neg),
              {"num": {"Revenue": sum(-r["Revenue"] if i % 2 else r["Revenue"] for i, r in enumerate(BASE))}, "rows": len(BASE)}))
    C.append(("excel_errors_and_blanks.csv", _csv([HEAD] + [[r["Month"].isoformat(), r["Region"], r["Product"],
                                                            "#DIV/0!" if i == 3 else ("" if i == 4 else r["Units"]), "#N/A" if i == 5 else r["Revenue"]]
                                                           for i, r in enumerate(BASE)]),
              {"num": {"Units": UNITS - BASE[3]["Units"] - BASE[4]["Units"], "Revenue": REV - BASE[5]["Revenue"]}, "rows": len(BASE)}))
    C.append(("us_dates_mdy.csv", _csv([["Date", "Store", "Sales"]] + [[(date(2026, 1, 1) + timedelta(days=i * 9)).strftime("%m/%d/%Y"),
                                                                      f"S{i % 3}", 100 + i] for i in range(40)]),
              {"num": {"Sales": sum(100 + i for i in range(40))}, "time_col": "Date"}))
    C.append(("text_with_tabs.txt", _csv([HEAD] + [_plain(r) for r in BASE], delimiter="\t"), {**std, "cat": []}))
    C.append(("only_header.csv", _csv([HEAD]), {"status": "failed", "error_contains": "No data rows"}))
    C.append(("empty.csv", b"", {"status": "failed"}))
    C.append(("single_column_list.csv", _csv([["Customer"]] + [[f"Cust {i}"] for i in range(30)]), {"rows": 30}))
    C.append(("json_records.json", json.dumps([{**{k: (v.isoformat() if isinstance(v, date) else v) for k, v in r.items()}} for r in BASE]).encode(),
              std))
    C.append(("json_nested.json", json.dumps({"meta": {"source": "api"}, "data": [{"month": r["Month"].isoformat(), "geo": {"region": r["Region"]},
                                                                                  "metrics": {"units": r["Units"], "revenue": r["Revenue"]}} for r in BASE]}).encode(),
              {"num": {"metrics units": UNITS, "metrics revenue": REV}, "rows": len(BASE)}))

    # ------------------------------------------------------------------ Excel layout variations
    def std_sheet(ws, start_row=1, start_col=1, rows=BASE):
        for j, h in enumerate(HEAD):
            ws.cell(start_row, start_col + j, h)
        for i, r in enumerate(rows, start=1):
            for j, h in enumerate(HEAD):
                ws.cell(start_row + i, start_col + j, r[h])

    C.append(("plain.xlsx", _xlsx(lambda wb: std_sheet(wb.active)), std))

    def titled(wb):
        ws = wb.active
        ws["A1"] = "ACME Pty Ltd"
        ws["A2"] = "Sales by region – FY2026"
        ws["A3"] = "All figures in AUD"
        std_sheet(ws, start_row=6, start_col=3)
        ws.cell(6 + len(BASE) + 2, 3, "Prepared by Finance. Confidential.")
    C.append(("title_rows_offset_and_footer.xlsx", _xlsx(titled), std))

    def totals(wb):
        ws = wb.active
        std_sheet(ws)
        r0 = len(BASE) + 2
        ws.cell(r0, 2, "Total")
        ws.cell(r0, 4, UNITS)
        ws.cell(r0, 5, REV)
        ws.cell(r0 + 1, 1, "Grand Total")
        ws.cell(r0 + 1, 4, UNITS)
        ws.cell(r0 + 1, 5, REV)
    C.append(("with_total_rows.xlsx", _xlsx(totals), {**std, "warn_contains": "total"}))

    def subtotals(wb):
        ws = wb.active
        ws.append(["Region", "Product", "Units", "Revenue"])
        for reg in REGIONS:
            rs = [r for r in BASE if r["Region"] == reg]
            for r in rs:
                ws.append([reg, r["Product"], r["Units"], r["Revenue"]])
            ws.append([f"{reg} Total", None, sum(r["Units"] for r in rs), sum(r["Revenue"] for r in rs)])
        ws.append(["Grand Total", None, UNITS, REV])
    C.append(("subtotals_per_group.xlsx", _xlsx(subtotals), {"num": {"Units": UNITS, "Revenue": REV}, "rows": len(BASE)}))

    def merged_groups(wb):
        ws = wb.active
        ws.append(["Region", "Product", "Units", "Revenue"])
        row = 2
        for reg in REGIONS:
            rs = [r for r in BASE if r["Region"] == reg]
            for k, r in enumerate(rs):
                ws.append([reg if k == 0 else None, r["Product"], r["Units"], r["Revenue"]])
            ws.merge_cells(start_row=row, start_column=1, end_row=row + len(rs) - 1, end_column=1)
            row += len(rs)
    C.append(("merged_group_labels.xlsx", _xlsx(merged_groups), {"num": {"Units": UNITS, "Revenue": REV}, "rows": len(BASE),
                                                                "filled": {"Region": len(BASE)}}))

    def two_row_header(wb):
        ws = wb.active
        ws.append(["Region", "2025", None, "2026", None])
        ws.append([None, "Units", "Revenue", "Units", "Revenue"])
        ws.merge_cells("B1:C1")
        ws.merge_cells("D1:E1")
        for reg in REGIONS:
            ws.append([reg, 10, 1000, 12, 1300])
    C.append(("two_row_header.xlsx", _xlsx(two_row_header), {"rows_min": 4, "cols_contain": ["2025 Units", "2026 Revenue"], "time_any": True}))

    def wide_months(wb):
        ws = wb.active
        months = ["Jan-26", "Feb-26", "Mar-26", "Apr-26", "May-26", "Jun-26"]
        ws.append(["Product"] + months + ["Total"])
        for p in PRODUCTS:
            vals = [100 + 10 * k for k in range(6)]
            ws.append([p] + vals + [sum(vals)])
    C.append(("wide_months_with_total_column.xlsx", _xlsx(wide_months),
              {"time_any": True, "num_any": 3 * sum(100 + 10 * k for k in range(6))}))

    def multi_table(wb):
        ws = wb.active
        std_sheet(ws)
        start = len(BASE) + 4
        ws.cell(start - 1, 1, "Targets")
        ws.cell(start, 1, "Region")
        ws.cell(start, 2, "Target")
        for k, reg in enumerate(REGIONS):
            ws.cell(start + 1 + k, 1, reg)
            ws.cell(start + 1 + k, 2, 5000 + k)
        # a third block side by side
        ws.cell(1, 8, "Product")
        ws.cell(1, 9, "Price")
        for k, p in enumerate(PRODUCTS):
            ws.cell(2 + k, 8, p)
            ws.cell(2 + k, 9, 9.99 + k)
    C.append(("several_tables_one_sheet.xlsx", _xlsx(multi_table), {"tables_min": 3, "num": {"Units": UNITS, "Revenue": REV}}))

    def many_sheets(wb):
        std_sheet(wb.active)
        wb.active.title = "Data"
        wb.create_sheet("Empty")
        notes = wb.create_sheet("Read me")
        notes["A1"] = "This workbook contains sales data."
        notes["A3"] = "Contact finance for questions."
        lk = wb.create_sheet("Lists")
        for i, reg in enumerate(REGIONS, 1):
            lk.cell(i, 1, reg)
        lk.sheet_state = "hidden"
        rc = wb.create_sheet("Rate card")  # hidden, but real data (as in supplier planners)
        rc.append(["Activity", "Rate", "Unit"])
        for k in range(6):
            rc.append([f"Activity {k}", 1000 + 250 * k, "each"])
        rc.sheet_state = "hidden"
        chart_only = wb.create_sheet("Dashboard")
        chart_only["B2"] = "Dashboard"
    C.append(("many_sheets_hidden_empty_notes.xlsx", _xlsx(many_sheets), {**std, "tables_min": 2, "tables_max": 2}))

    def formulas_no_cache(wb):
        ws = wb.active
        ws.append(["Item", "Qty", "Price", "Total"])
        for i in range(10):
            ws.append([f"Item {i}", i + 1, 2.5, f"=B{i + 2}*C{i + 2}"])
    C.append(("formulas_without_saved_results.xlsx", _xlsx(formulas_no_cache), {"num": {"Qty": 55}, "warn_contains": "formula"}))

    def mixed_types(wb):
        ws = wb.active
        ws.append(["Code", "Amount", "Flag", "When", "Comment"])
        for i in range(30):
            ws.append([f"{i:05d}" if i % 2 else i, ["12", 13.5, "N/A", " 1,200 ", "$5"][i % 5], ["Yes", "No", "Y", "n", True][i % 5],
                       [datetime(2026, 1, 1 + i % 28), "2026-02-03", 46000, None, "TBC"][i % 5], "ok 👍" if i % 3 == 0 else "long text " * 30])
    C.append(("mixed_types_in_columns.xlsx", _xlsx(mixed_types), {"rows": 30}))

    def duplicate_and_blank_headers(wb):
        ws = wb.active
        ws.append(["Region", "Value", "Value", None, "  ", "Region"])
        for i in range(20):
            ws.append([REGIONS[i % 4], i, i * 2, "x", "y", "z"])
    C.append(("duplicate_and_blank_headers.xlsx", _xlsx(duplicate_and_blank_headers), {"rows": 20}))

    def transposed(wb):
        ws = wb.active
        ws.append(["Metric", "Q1 2026", "Q2 2026", "Q3 2026", "Q4 2026"])
        ws.append(["Revenue", 100, 120, 130, 150])
        ws.append(["Cost", 60, 70, 75, 80])
        ws.append(["Headcount", 10, 11, 12, 12])
    C.append(("metrics_down_periods_across.xlsx", _xlsx(transposed), {"time_any": True}))

    def huge_wide(wb):
        ws = wb.active
        ws.append(["ID"] + [f"Metric {k}" for k in range(300)])
        for i in range(60):
            ws.append([f"R{i}"] + [i + k for k in range(300)])
    C.append(("very_wide_300_columns.xlsx", _xlsx(huge_wide), {"rows": 60}))

    def comments_and_styles(wb):
        ws = wb.active
        std_sheet(ws)
        ws["A1"].comment = Comment("Month of sale", "me")
        ws.freeze_panes = "B2"
        ws.auto_filter.ref = "A1:E25"
    C.append(("filters_freeze_comments.xlsx", _xlsx(comments_and_styles), std))

    def numbers_as_text(wb):
        ws = wb.active
        ws.append(HEAD)
        for r in BASE:
            ws.append([r["Month"].strftime("%Y-%m"), r["Region"], r["Product"], f"'{r['Units']}", f"{r['Revenue']:,.2f}"])
    C.append(("numbers_stored_as_text.xlsx", _xlsx(numbers_as_text), {"num": {"Revenue": REV}, "rows": len(BASE)}))

    def year_header_numbers(wb):
        ws = wb.active
        ws.append(["Country", 2022, 2023, 2024, 2025])
        for c in ["AU", "NZ", "IN", "UK", "US"]:
            ws.append([c, 1, 2, 3, 4])
    C.append(("numeric_year_headers.xlsx", _xlsx(year_header_numbers), {"time_any": True}))

    def one_row(wb):
        ws = wb.active
        ws.append(["KPI", "Value"])
        ws.append(["Revenue", 1000])
    C.append(("one_data_row.xlsx", _xlsx(one_row), {"rows": 1}))

    def only_text(wb):
        ws = wb.active
        for i in range(12):
            ws.cell(i + 1, 1, f"Paragraph {i}: notes about the plan, not a table.")
    C.append(("only_notes.xlsx", _xlsx(only_text), {"status_in": ["partial", "processed", "failed"]}))

    def sectioned(wb):
        ws = wb.active
        ws.append(["Product", "Month", "Units", "Revenue"])
        for reg in REGIONS:
            ws.append([reg])  # section label row
            for r in [x for x in BASE if x["Region"] == reg]:
                ws.append([r["Product"], r["Month"], r["Units"], r["Revenue"]])
            ws.append([])
    C.append(("section_label_rows_and_blank_lines.xlsx", _xlsx(sectioned),
              {"num": {"Units": UNITS, "Revenue": REV}, "rows": len(BASE), "cols_contain": ["Section"], "tables_max": 1}))

    def repeated_headers(wb):
        ws = wb.active
        for k, r in enumerate(BASE):
            if k % 8 == 0:
                ws.append(HEAD)  # header repeated at each "page"
            ws.append([r[h] for h in HEAD])
    C.append(("header_repeated_every_page.xlsx", _xlsx(repeated_headers), {**std, "tables_max": 1}))

    def serial_dates(wb):
        ws = wb.active
        ws.append(["Date", "Store", "Sales"])
        for i in range(40):
            ws.append([45658 + i * 7, f"S{i % 3}", 100 + i])  # Excel day numbers formatted as General
    C.append(("excel_serial_dates.xlsx", _xlsx(serial_dates), {"time_col": "Date", "num": {"Sales": sum(100 + i for i in range(40))}}))

    def date_headers(wb):
        ws = wb.active
        ws.append(["Product"] + [datetime(2026, m, 1) for m in range(1, 7)] + ["YTD"])
        for p in PRODUCTS:
            vals = [100 + 10 * k for k in range(6)]
            ws.append([p] + vals + [sum(vals)])
    C.append(("month_headers_as_real_dates.xlsx", _xlsx(date_headers), {"time_any": True, "rows": 18}))

    # ------------------------------------------------------------------ other spreadsheet formats
    C.append(("legacy.xls", _legacy_xls(), std))
    C.append(("open_document.ods", _ods(), std))
    C.append(("macro_enabled.xlsm", _xlsm(), std))

    # ------------------------------------------------------------------ broken / hostile
    C.append(("corrupt.xlsx", b"PK\x03\x04" + b"\x00" * 200, {"status": "failed"}))
    C.append(("csv_named_xlsx.xlsx", _csv([HEAD] + [_plain(r) for r in BASE]), std))
    C.append(("xlsx_named_csv.csv", _xlsx(lambda wb: std_sheet(wb.active)), std))
    C.append(("password_protected.xlsx", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 600, {"status": "failed", "error_contains": "password"}))
    return C


def large_csv(rows: int = 150_000) -> bytes:
    s = io.StringIO()
    w = csv.writer(s)
    w.writerow(["Date", "Store", "Product", "Units", "Revenue", "Discount %", "Channel", "Notes"])
    d0 = date(2024, 1, 1)
    for i in range(rows):
        w.writerow([(d0 + timedelta(days=i % 700)).isoformat(), f"Store {i % 40}", PRODUCTS[i % 3], i % 17, f"{(i % 997) * 1.25:.2f}",
                    f"{i % 30}%", ["Online", "Retail", "Wholesale"][i % 3], "" if i % 4 else "promo"])
    return s.getvalue().encode()


def _legacy_xls() -> bytes:
    """A genuine Excel 97-2003 (.xls) file with the standard rows (made-up data, converted once with LibreOffice)."""
    from pathlib import Path

    return (Path(__file__).parent / "fixtures" / "legacy_synthetic.xls").read_bytes()


def _ods() -> bytes:
    """An OpenDocument spreadsheet with the standard rows (made-up data, generated once)."""
    from pathlib import Path

    return (Path(__file__).parent / "fixtures" / "open_document_synthetic.ods").read_bytes()


def _xlsm() -> bytes:
    import zipfile

    src = _xlsx(lambda wb: [wb.active.append(HEAD)] + [wb.active.append([r[h] for h in HEAD]) for r in BASE])
    zin = zipfile.ZipFile(io.BytesIO(src))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "[Content_Types].xml":
                data = data.replace(b"spreadsheetml.sheet.main+xml", b"sheet.macroEnabled.main+xml")
            z.writestr(item, data)
        z.writestr("xl/vbaProject.bin", b"\x00fake-macro-code-that-must-never-run")
    return out.getvalue()
