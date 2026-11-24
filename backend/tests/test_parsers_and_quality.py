import io

import numpy as np
import pandas as pd
import pytest

from app.analytics.profiling import normalize_table, parse_numeric, reshape_wide
from app.parsers import ParserError, detect_type, parse_file, safe_filename
from tests.conftest import sample


# ----------------------------------------------------------------- parsers
def test_csv_extraction_detects_delimiter_and_header():
    data = "Report title\n\nRegion;Sales (£);Share\nNorth;£1,200;12%\nSouth;£2,300.50;18%\n".encode()
    res = parse_file("csv", data, "x.csv")
    t = res.tables[0]
    assert list(t.df.columns) == ["Region", "Sales (£)", "Share"]
    assert t.row_numbers == [4, 5]  # traceable to CSV line numbers


def test_xlsx_extraction_with_title_rows():
    res = parse_file("xlsx", sample("sales_dataset.xlsx"), "sales_dataset.xlsx")
    t = res.tables[0]
    assert t.locator["sheet"] == "Sales" and t.locator["header_row"] == 3
    assert "Revenue (GBP)" in t.df.columns and len(t.df) > 8000
    assert t.row_numbers[0] == 4


def test_xlsx_multiple_sheets():
    res = parse_file("xlsx", sample("product_dataset.xlsx"), "product_dataset.xlsx")
    assert {t.locator["sheet"] for t in res.tables} == {"Portfolio", "Plan vs Actual 2025"}


def test_pdf_text_tables_captions_footnotes_and_chart():
    res = parse_file("pdf", sample("market_report_2025.pdf"), "r.pdf")
    kinds = {c.kind for c in res.chunks}
    assert {"heading", "paragraph", "caption"} <= kinds
    assert any("Sleep Aids grew 27%" in c.text for c in res.chunks)
    assert all(c.locator.get("page") for c in res.chunks if c.kind == "paragraph")
    table = next(t for t in res.tables if t.origin == "pdf_table")
    assert table.locator["page"] == 1 and "2025" in table.df.columns
    if any(t.origin == "image_chart" for t in res.tables):  # needs Tesseract for labels; bars are always detected
        chart = next(t for t in res.tables if t.origin == "image_chart")
        assert chart.approximate is True


def test_docx_extraction_sections_and_lists():
    res = parse_file("docx", sample("strategy_2026_proposed.docx"), "s.docx")
    items = [c for c in res.chunks if c.kind == "list_item"]
    assert any("Increase TV investment by 30%" in c.text for c in items)
    assert items[0].locator["section"] == "Portfolio priorities"


def test_pptx_extraction_chart_data_and_notes():
    res = parse_file("pptx", sample("business_review_2025.pptx"), "b.pptx")
    chart = next(t for t in res.tables if t.origin == "pptx_chart")
    assert chart.approximate is False and "Category" in chart.df.columns and chart.locator["slide"] == 2
    assert any(c.kind == "note" for c in res.chunks)
    assert any(c.kind == "slide_title" for c in res.chunks)


def test_markdown_and_text():
    md = b"# Title\n\nSome paragraph text here.\n\n| Product | Sales |\n|---|---|\n| A | 10 |\n| B | 20 |\n\n- bullet one\n"
    res = parse_file("md", md, "n.md")
    assert res.tables and list(res.tables[0].df.columns) == ["Product", "Sales"]
    assert res.tables[0].row_numbers == [7, 8]
    assert any(c.kind == "heading" for c in res.chunks) and any(c.kind == "list_item" for c in res.chunks)


def test_image_chart_reading_is_marked_approximate():
    from app.parsers.image import tesseract_available

    res = parse_file("png", sample("survey_purchase_drivers.png"), "s.png")
    chart = next(t for t in res.tables if t.origin == "image_chart")
    assert chart.approximate and len(chart.df) == 5
    if tesseract_available():
        vals = dict(zip(chart.df["Label"], chart.df["Value"]))
        assert vals.get("Efficacy") == pytest.approx(71, abs=2)
        assert any(c.approximate for c in res.chunks)


def test_type_validation_and_safe_names():
    with pytest.raises(ParserError):
        detect_type("x.pdf", b"hello")
    with pytest.raises(ParserError):
        detect_type("x.docx", b"PK\x03\x04garbage")
    with pytest.raises(ParserError):
        detect_type("malware.exe", b"MZ")
    assert safe_filename("../../etc/pass wd.CSV") == "pass wd.csv"
    assert safe_filename("C:\\Users\\me\\Report<1>.pdf") == "Report_1_.pdf"


# ----------------------------------------------------------------- cleaning
def test_parse_numeric_variants():
    assert parse_numeric("£1,234.50")[0] == 1234.5
    assert parse_numeric("12%") == (12.0, {"percent": True})
    assert parse_numeric("(1,000)")[0] == -1000
    assert parse_numeric("2.5m")[0] == 2_500_000
    assert parse_numeric("1.2 bn")[0] == 1_200_000_000
    assert parse_numeric("n/a")[0] is None
    assert parse_numeric("abc")[0] is None


def test_normalize_parses_and_records_transformations_without_touching_original():
    raw = pd.DataFrame({"Month": ["2024-01", "2024-02", "2024-03", "2024-04"], "Channel": ["Meta", "meta ", "Google", "META"],
                        "Spend ($)": ["$1,000", "$2,000", "$1,500", "$3,000"]})
    original = raw.copy()
    norm = normalize_table(raw)
    pd.testing.assert_frame_equal(raw, original)  # never silently modify original data
    assert norm.meta("Spend ($)").dtype == "numeric" and norm.meta("Spend ($)").role == "spend"
    assert norm.df["Spend ($)"].tolist() == [1000, 2000, 1500, 3000]
    assert norm.meta("Month").role == "time" and norm.meta("Month").period_granularity == "month"
    assert set(norm.df["Channel"]) == {"Meta", "Google"}
    actions = {t["action"] for t in norm.transformations}
    assert "harmonised label spelling" in actions and "converted text to numbers" in actions
    assert any(i["issue_type"] == "inconsistent_naming" for i in norm.issues)


def test_data_quality_checks():
    raw = pd.DataFrame({
        "Date": ["2024-01-01", "2024-02-01", "2024-03-01", "2024-05-01", "2024-05-01", "not a date"],
        "Spend": ["100", "-50", "200", "300", "300", "120"],
        "Impressions": [1000, 1000, 1000, 1000, 1000, 1000],
        "Clicks": [10, 20, 2000, 30, 30, 15],
        "Price": ["£5", "€6", "£5", "£7", "£7", None],
    })
    norm = normalize_table(raw)
    types = {i["issue_type"] for i in norm.issues}
    assert {"impossible_values", "duplicates", "inconsistent_currency", "inconsistent_dates", "incomplete_periods"} <= types
    neg = next(i for i in norm.issues if i["issue_type"] == "impossible_values" and "negative" in i["description"])
    assert neg["severity"] == "high" and neg["locator"]["rows"] == "3"  # spreadsheet row of the bad value
    for i in norm.issues:
        assert i["suggested_resolution"] and i["field"]


def test_reshape_wide_period_columns():
    raw = pd.DataFrame({"Category": ["A", "B"], "2023": ["10", "20"], "2024": ["12", "18"], "2025": ["15", "17"]})
    long, rows, note = reshape_wide(raw, [5, 6])
    assert note and len(long) == 6 and set(long.columns) == {"Category", "Period", "Value"}
    norm = normalize_table(long, rows)
    assert norm.meta("Period").role == "time" and norm.meta("Period").period_granularity == "year"


def test_header_scale_and_period_groups():
    raw = pd.DataFrame({"Brand": ["X", "Y", "Z"], "Sales 2024 (£m)": [1, 2, 3], "Sales 2025 (£m)": [2, 2, 4]})
    norm = normalize_table(raw)
    assert norm.df["Sales 2024 (£m)"].tolist() == [1e6, 2e6, 3e6]
    assert norm.meta("Sales 2024 (£m)").unit == "GBP"
    assert list(norm.period_groups.values())[0] == {"2024": "Sales 2024 (£m)", "2025": "Sales 2025 (£m)"}
