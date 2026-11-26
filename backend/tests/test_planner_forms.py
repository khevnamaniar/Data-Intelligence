"""Planner / order-form spreadsheets: blank cells in text columns, unused template rows, ID-number columns,
copied cost columns, a month-by-month view of planned spend - and files never getting stuck in 'processing'."""
from __future__ import annotations

import io
from datetime import datetime

from openpyxl import Workbook

from tests.test_production import register

HEAD = ["BUYER GROUP", "SUPPLIER", "CATALOGUE", "Promo Start", "Promo End", "BRAND", "PRODUCT NAME", "ACTIVITY TYPE",
        "ACTIVITY", "Activity No", "QTY", "COST ex GST (EACH)", "TOTAL ex GST", "COMMENTS (if needed)"]
ROWS = [
    ("Health", "Acme", "Jan catalogue", datetime(2027, 1, 14), datetime(2027, 2, 3), "Brand A", "Drops 5ml", "PACKAGES", "Base package", 1119971, 1, 17000, 17000, None),
    ("Health", "Acme", "Mar catalogue", datetime(2027, 3, 11), datetime(2027, 3, 31), "Brand A", "Drops 5ml", "PACKAGES", "Blitz package", 1236370, 1, 30000, 30000, "Replay included"),
    ("Retail Media", "Acme", "Mar catalogue", datetime(2027, 3, 11), datetime(2027, 3, 31), "Brand A", "Drops 5ml", "DIGITAL", "Meta ad", 1177439, 1, 4000, 4000, None),
    ("Health", "Acme", "May catalogue", datetime(2027, 5, 6), datetime(2027, 5, 26), "Brand A", "Gel 10g", "CATALOGUE", "Half page", 1177442, 1, 8000, 8000, None),
    ("Retail Media", "Acme", "Aug catalogue", datetime(2027, 8, 19), datetime(2027, 9, 7), "Brand A", "Gel 10g", "DIGITAL", "Search ads", 1196471, 1, 2500, 2500, None),
    ("Health", "Acme", "Sep catalogue", datetime(2027, 9, 9), datetime(2027, 9, 29), "Brand A", "Drops 5ml", "PACKAGES", "Base package", 1119971, 1, 17000, 17000, None),
    ("Health", "Acme", "Nov catalogue", datetime(2027, 11, 4), datetime(2027, 11, 24), "Brand A", "Drops 5ml", "INSTORE", "Shelf strip", 1241993, 1, 3000, 3000, None),
    ("Retail Media", "Acme", "Dec catalogue", datetime(2027, 12, 2), datetime(2027, 12, 22), "Brand A", "Gel 10g", "PACKAGES", "Xmas package", 1241994, 1, 13000, 13000, None),
]


def planner_xlsx(template_rows: int = 40) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Planner Form"
    ws.append(HEAD)
    for r in ROWS:
        ws.append(list(r))
    for _ in range(template_rows):  # unused form rows: only formula defaults
        ws.append([None] * 9 + [None, 1, 0, 0, None])
    ws.append([None] * 10 + ["-", "Check Qty Column", "Check Qty Column", None])
    rates = wb.create_sheet("Rates")
    rates.append(["Activity Type", "Description", "Item Number", "Price"])
    for i in range(30):
        rates.append([["PACKAGES", "DIGITAL", "CATALOGUE"][i % 3], f"Item {i}", 1177440 + i if i != 5 else "N/A", 1000 + 500 * (i % 7)])
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def _upload(client, data, name="Supplier Planner 2027.xlsx"):
    register(client)
    pid = client.post("/api/projects", json={"name": "Planner"}).json()["project"]["id"]
    r = client.post(f"/api/projects/{pid}/files", files=[("files", (name, data, "application/octet-stream"))])
    assert r.status_code == 201, r.text
    return pid, client.get(f"/api/projects/{pid}/files").json()["files"][0]


def test_blank_text_cells_do_not_break_processing():
    """pandas 3 text columns hold NaN for blank cells; date detection must not crash on them."""
    import pandas as pd

    from app.analytics.profiling import normalize_table, parse_dates

    s = pd.Series(["Health", None, "Retail Media", None], dtype="str")
    dt, _ = parse_dates(s)
    assert dt.isna().all()
    n = normalize_table(pd.DataFrame({"Group": s, "Value": [1, 2, 3, 4]}))
    assert "nan" not in set(n.df["Group"].dropna().astype(str))


def test_planner_is_processed_with_template_rows_and_codes(client):
    pid, f = _upload(client, planner_xlsx())
    assert f["status"] == "processed", f["error_json"]
    ds = {d["name"].split(" › ")[-1]: d for d in f["extraction_json"]["datasets"]}
    assert ds["Planner Form"]["rows"] == len(ROWS)  # 41 unused form rows ignored
    from app.db import get_db

    with get_db().tx() as c:
        def types(did):
            return {r["normalized_name"]: r["data_type"] for r in
                    c.fetchall("SELECT normalized_name, data_type FROM dataset_columns WHERE dataset_id = ?", [did])}
        planner, rates = types(ds["Planner Form"]["id"]), types(ds["Rates"]["id"])
    assert planner["Activity No"] != "numeric"  # an ID, not an amount
    assert planner["TOTAL ex GST"] == "numeric"  # no longer text because of the 'Check Qty Column' row
    assert rates["Item Number"] != "numeric" and rates["Price"] == "numeric"


def test_planner_analysis_answers_budget_and_timing_questions(client):
    pid, _ = _upload(client, planner_xlsx())
    qs = ["Where is the promotional budget going?", "Which activities cost the most?", "How is spend spread across 2027?"]
    rid = client.post(f"/api/projects/{pid}/analyses", json={"mode": "deep", "questions": qs}).json()["run"]["id"]
    run = client.get(f"/api/projects/{pid}/analyses/{rid}").json()["run"]
    assert run["status"] == "completed", run["errors"]
    titles = [x["title"] for x in run["findings"]]
    # the busiest month of the plan, not a "growth trend"
    sched = [x for x in run["findings"] if " by month: busiest is " in x["title"]]
    assert sched and "Mar 2027" in sched[0]["title"] and "Nothing is planned in" in sched[0]["fact"]
    assert not any(t.startswith(("TOTAL ex GST grew", "COST ex GST (EACH) grew")) for t in titles)
    # identical cost columns are analysed once; a single supplier is not a "competitive landscape"
    assert not any("COST ex GST (EACH)" in t and "concentrated" in t for t in titles)
    assert not any("low competition" in t for t in titles)
    assert not any("move together" in t and "COST ex GST (EACH)" in t and "TOTAL ex GST" in t for t in titles)
    user_qs = [q for q in run["questions"] if q["origin"] == "user"]
    assert all(q["status"] == "answered" for q in user_qs), user_qs


def test_one_bad_table_does_not_leave_file_stuck(client, monkeypatch):
    from app.services import ingestion

    real = ingestion.normalize_table

    def flaky(df, rows=None):
        if "Rates" in " ".join(map(str, df.columns)) or "Item Number" in df.columns:
            raise ValueError("boom")
        return real(df, rows)

    monkeypatch.setattr(ingestion, "normalize_table", flaky)
    _, f = _upload(client, planner_xlsx())
    assert f["status"] == "partial"
    assert "could not be prepared" in f["error_json"][-1]["what"] and "Rates" in f["error_json"][-1]["what"]
    assert len(f["extraction_json"]["datasets"]) == 1


def test_unexpected_error_after_reading_marks_file_failed(client, monkeypatch):
    from app.services import ingestion

    def broken(*a, **k):
        raise RuntimeError("database hiccup")

    monkeypatch.setattr(ingestion, "_store_result", broken)
    _, f = _upload(client, planner_xlsx())
    assert f["status"] == "failed" and "Re-process" in f["error_json"][0]["next"]


def test_interrupted_file_is_not_left_processing_after_restart(client):
    from app.db import get_db
    from app.main import _startup

    pid, f = _upload(client, planner_xlsx())
    with get_db().tx() as c:
        c.execute("UPDATE files SET status = 'processing' WHERE id = ?", [f["id"]])
    _startup()
    f2 = client.get(f"/api/projects/{pid}/files").json()["files"][0]
    assert f2["status"] == "failed" and "interrupted" in f2["error_json"][0]["what"]
