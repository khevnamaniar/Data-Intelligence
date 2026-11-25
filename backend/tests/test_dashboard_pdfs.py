"""Dashboard-style PDF exports (Looker Studio / Google Ads style): KPI tiles, borderless tables, vector charts,
report period detection, top-N completion and month-by-month combination across separate monthly reports.

The PDFs are generated here with made-up numbers so no customer data is stored in the repository."""
from __future__ import annotations

import calendar
import io

import pytest
from reportlab.lib.colors import Color, HexColor, white
from reportlab.pdfgen import canvas

from app.parsers.pdf import parse_pdf
from tests.test_production import register

W, H = 750, 900
_DEJAVU = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
try:  # a font with the rupee sign when available; otherwise the dollar sign keeps the test portable
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    pdfmetrics.registerFont(TTFont("DejaVu", _DEJAVU))
    FONT, BOLD, CUR = "DejaVu", "DejaVu", "₹"
except Exception:  # pragma: no cover
    FONT, BOLD, CUR = "Helvetica", "Helvetica-Bold", "$"
BLUE, TEAL, PINK, GRID, DARK = HexColor("#0072F0"), HexColor("#00B6CB"), HexColor("#F10096"), Color(0.82, 0.82, 0.82), HexColor("#2E3A59")

MONTHS = {
    8: {"kpi": ("4,591", "157", "88.89", "13.96K"), "daily_total": 157,
        "keywords": [("brand eye drops", "Brand | Search", 2260, 97, "4.29%", 8376.06), ("dry eye treatment", "Brand | Search", 2117, 51, "2.41%", 4837.33),
                     ("brand", "Brand | Search", 104, 6, "5.77%", 468.92), ("dry eye relief", "Brand | Search", 110, 3, "2.73%", 273.69)],
        "ages": [("gt64", 98), ("55to64", 29), ("45to54", 11), ("Undetermined", 11), ("25to34", 5), ("35to44", 3), ("18to24", 0)],
        "devices": [("mobile devices", 136, True), ("computers", 11, True), ("tablets", 10, True)]},
    9: {"kpi": ("2,048", "157", "125.17", "19.65K"), "daily_total": 157,
        "keywords": [("brand eye drops", "Brand | Search", 822, 85, "10.34%", 7904.71), ("dry eye treatment", "Dry eye | Search", 885, 43, "4.86%", 5843.62),
                     ("brand", "Brand | Search", 58, 10, "17.24%", 2216.70)],
        "ages": [("gt64", 98), ("55to64", 22), ("Undetermined", 19), ("45to54", 12), ("35to44", 4), ("18to24", 1), ("25to34", 1)],
        "devices": [("mobile devices", 126, True), ("computers", 17, True), ("tablets", 14, True)]},
    10: {"kpi": ("29,846", "765", "28.97", "22.16K"), "daily_total": 765,
         "keywords": [("dry eye treatment", "Dry eye | Search", 1575, 69, "4.38%", 6934.65), ("itchy eye drops", "Allergy | Search", 474, 17, "3.59%", 1778.27),
                      ("brand", "Brand | Search", 75, 8, "10.67%", 1080.96)],
         "ages": [("gt64", 162), ("18to24", 158), ("55to64", 112), ("35to44", 109), ("25to34", 107), ("45to54", 79), ("Undetermined", 38)],
         "devices": [("mobile devices", 708, True), ("computers", 49, True), ("tablets", 8, False)]},  # tiny slice without a label
}


def _y(top):  # reportlab measures from the bottom
    return H - top


def _daily(month, total):
    days = calendar.monthrange(2026, month)[1]
    base = [total // days] * days
    for i in range(total - sum(base)):
        base[(i * 7) % days] += 1
    return base


def dashboard_pdf(month: int) -> bytes:
    spec = MONTHS[month]
    days = calendar.monthrange(2026, month)[1]
    mname = calendar.month_abbr[month]
    period = f"{mname} 1, 2026 - {mname} {days}, 2026"
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(W, H))

    def header():
        c.setFillColor(DARK)
        c.rect(0, _y(70), W, 10, stroke=0, fill=1)
        c.setFont(FONT, 12)
        c.drawString(530, _y(160), period)

    def title_bar(text, top):
        c.setFillColor(DARK)
        c.rect(36, _y(top + 26), 680, 26, stroke=0, fill=1)
        c.setFillColor(white)
        c.setFont(BOLD, 16)
        c.drawCentredString(376, _y(top + 19), text)
        c.setFillColor(Color(0, 0, 0))

    # ---------------- page 1: overview tiles, daily line, keyword table
    header()
    title_bar("Overview", 214)
    labels = ["Impressions", "Clicks", "Avg. CPC", "Cost"]
    for i, (lab, val) in enumerate(zip(labels, spec["kpi"])):
        x = 36 + i * 170
        c.setStrokeColor(Color(0, 0, 0))
        c.rect(x, _y(292), 170, 52, stroke=1, fill=0)
        c.setFont(FONT, 8)
        c.drawCentredString(x + 85, _y(255), lab)
        c.setFont(FONT, 16)
        cur = CUR if lab in ("Avg. CPC", "Cost") else ""
        c.drawCentredString(x + 85, _y(278), (cur + " " if cur else "") + val)
    title_bar("Daily Clicks Data", 340)
    c.setFont(FONT, 8)
    c.drawString(97, _y(410), "Clicks")
    vals = _daily(month, spec["daily_total"])
    vmax = 10 if max(vals) <= 10 else (50 if max(vals) <= 50 else 150)
    top, bottom, x0, x1 = 422.0, 459.0, 74.0, 692.0
    for k in range(0, 5):  # gridlines and tick labels
        yy = top + (bottom - top) * k / 4
        c.setFillColor(GRID)
        c.rect(x0, _y(yy) - 0.4, x1 - x0, 0.8, stroke=0, fill=1)
    c.setFillColor(Color(0, 0, 0))
    for v in (vmax, vmax // 2, 0):
        yy = bottom - (bottom - top) * v / vmax
        c.drawRightString(64, _y(yy) - 3, str(v))
    xs = [x0 + (x1 - x0) * d / (days - 1) for d in range(days)]
    for d in range(0, days, 2):
        c.drawCentredString(xs[d], _y(472), f"{mname} {d + 1}")
    c.setStrokeColor(BLUE)
    c.setLineWidth(1.5)
    path = c.beginPath()
    for d, v in enumerate(vals):
        yy = bottom - (bottom - top) * v / vmax
        (path.moveTo if d == 0 else path.lineTo)(xs[d], _y(yy))
    c.drawPath(path, stroke=1, fill=0)
    c.setStrokeColor(Color(0, 0, 0))
    title_bar("Targeted Keywords Performance", 520)
    c.setFont(BOLD, 7)
    cols = [40, 190, 380, 450, 520, 600]
    for x, h in zip(cols, ["Search keyword", "Campaign", "Impressions", "Clicks ▼" if FONT == "DejaVu" else "Clicks", "CTR", "Cost"]):
        c.drawString(x, _y(565), h)
    c.setFont(FONT, 7)
    for i, (kw, camp, imp, clk, ctr, cost) in enumerate(spec["keywords"]):
        yy = 583 + i * 18
        c.drawString(cols[0], _y(yy), kw)
        c.drawString(cols[1], _y(yy), camp)
        c.drawRightString(cols[2] + 40, _y(yy), f"{imp:,}")
        c.drawRightString(cols[3] + 25, _y(yy), str(clk))
        c.drawRightString(cols[4] + 30, _y(yy), ctr)
        c.drawRightString(cols[5] + 50, _y(yy), f"{CUR}{cost:,.2f}")
    c.showPage()

    # ---------------- page 2: demographic bars and device pie
    header()
    title_bar("Demographics Wise Clicks", 212)
    c.setFont(FONT, 8)
    c.setFillColor(BLUE)
    c.rect(64, _y(271), 20, 9, stroke=0, fill=1)
    c.setFillColor(Color(0, 0, 0))
    c.drawString(88, _y(269), "Clicks")
    ages = spec["ages"]
    amax = 200 if max(v for _, v in ages) > 100 else 100
    base, ptop = 413.0, 286.0
    for k in range(5):
        v = amax * k / 4
        yy = base - (base - ptop) * v / amax
        c.setFillColor(GRID)
        c.rect(64, _y(yy) - 0.4, 640, 0.8, stroke=0, fill=1)
        c.setFillColor(Color(0, 0, 0))
        c.drawRightString(54, _y(yy) - 3, f"{v:g}")
    for i, (lab, v) in enumerate(ages):
        bx = 74 + i * 91.5
        hgt = max((base - ptop) * v / amax, 0.4)
        c.setFillColor(BLUE)
        c.rect(bx, _y(base), 73, hgt, stroke=0, fill=1)
        c.setFillColor(Color(0, 0, 0))
        c.drawCentredString(bx + 36.5, _y(base - hgt - 4), str(v))
        c.drawCentredString(bx + 36.5, _y(430), lab)
    title_bar("Devices Wise Clicks", 484)
    total = sum(v for _, v, _ in spec["devices"])
    cx, cy, r = 240, _y(622), 98
    start = 90.0
    colors = [BLUE, TEAL, PINK]
    for (lab, v, labelled), col, ly in zip(spec["devices"], colors, (603, 622, 641)):
        extent = -360.0 * v / total
        c.setFillColor(col)
        p = c.beginPath()
        p.moveTo(cx, cy)
        p.arcTo(cx - r, cy - r, cx + r, cy + r, startAng=start, extent=extent)
        p.close()
        c.drawPath(p, stroke=0, fill=1)
        mid = (start + extent / 2) * 3.14159265 / 180
        if labelled:
            c.setFillColor(Color(0, 0, 0))
            import math
            c.drawCentredString(cx + 0.65 * r * math.cos(mid), cy + 0.65 * r * math.sin(mid) - 3, str(v))
        start += extent
        c.setFillColor(col)
        c.circle(467, _y(ly), 6, stroke=0, fill=1)
        c.setFillColor(Color(0, 0, 0))
        c.drawString(478, _y(ly) - 3, lab)
    c.showPage()
    # ---------------- page 3: closing page with a small logo-like image area and no data
    c.setFont(FONT, 20)
    c.drawCentredString(W / 2, _y(400), "Thank You")
    c.save()
    return buf.getvalue()


def _tables(res):
    return {t.locator.get("title") or t.name: t for t in res.tables}


@pytest.mark.parametrize("month", [8, 9, 10])
def test_dashboard_pdf_extracts_every_element(month):
    spec = MONTHS[month]
    res = parse_pdf(dashboard_pdf(month), f"Ads Report {month}.pdf", ocr_enabled=False)
    assert res.status == "processed" and not res.errors, res.errors  # the "Thank You" page is not a "scanned" page
    assert res.metadata["report_period"]["value"] == f"2026-{month:02d}"
    t = _tables(res)
    assert set(t) >= {"Overview", "Daily Clicks Data", "Targeted Keywords Performance", "Demographics Wise Clicks", "Devices Wise Clicks"}, set(t)
    ov = t["Overview"].df
    assert list(ov.columns) == ["Period", "Impressions", "Clicks", "Avg. CPC", "Cost"]
    assert ov["Clicks"].iloc[0] == spec["kpi"][1] and ov["Cost"].iloc[0].endswith(spec["kpi"][3])
    # daily line chart: approximate, but it must add up to the report's total clicks
    daily = t["Daily Clicks Data"]
    assert daily.approximate and len(daily.df) == calendar.monthrange(2026, month)[1]
    assert abs(sum(float(v) for v in daily.df["Clicks"]) - spec["daily_total"]) <= max(3, 0.02 * spec["daily_total"])
    # bar chart: every category with its printed value (including a zero-height bar)
    bars = t["Demographics Wise Clicks"].df
    assert dict(zip(bars["Demographics"], bars["Clicks"].astype(float))) == {k: float(v) for k, v in spec["ages"]}
    # pie chart: colour-matched legend; an unlabeled slice is estimated from its angle
    pie = t["Devices Wise Clicks"]
    got = dict(zip(pie.df["Devices"], pie.df["Clicks"].astype(float)))
    for lab, v, labelled in spec["devices"]:
        assert abs(got[lab] - v) <= (0 if labelled else 2), (lab, got)
    assert pie.approximate == (not all(x[2] for x in spec["devices"]))
    # keyword table without ruling lines; a top-N listing is completed to the report's totals
    kw = t["Targeted Keywords Performance"].df
    assert list(kw.columns) == ["Period", "Search keyword", "Campaign", "Impressions", "Clicks", "CTR", "Cost"]
    names = list(kw["Search keyword"])
    assert names[: len(spec["keywords"])] == [k[0] for k in spec["keywords"]]
    listed_clicks = sum(k[3] for k in spec["keywords"])
    if listed_clicks < int(spec["kpi"][1]):
        assert names[-1] == "(other — not listed in the report)"
        assert float(kw["Clicks"].iloc[-1]) == int(spec["kpi"][1]) - listed_clicks
    # the closing page and chart text are not repeated as paragraphs
    assert not any("Aug 1 Aug 3" in ch.text or ch.text.startswith("dry eye treatment") for ch in res.chunks)


def test_scanned_page_message_is_plain_language():
    from PIL import Image

    img = Image.new("RGB", (600, 800), "white")
    b = io.BytesIO()
    c = canvas.Canvas(b, pagesize=(600, 800))
    c.drawString(72, 700, "A normal page with enough selectable text to be read without OCR.")
    c.showPage()
    tmp = io.BytesIO()
    img.save(tmp, format="PNG")
    from reportlab.lib.utils import ImageReader

    c.drawImage(ImageReader(io.BytesIO(tmp.getvalue())), 0, 0, 600, 800)
    c.showPage()
    c.save()
    res = parse_pdf(b.getvalue(), "scan.pdf", ocr_enabled=False)
    assert res.errors and "Page(s) 2" in res.errors[0]["what"] and "OCR" in res.errors[0]["why"]


def test_monthly_dashboards_combine_into_month_by_month_analysis(client):
    register(client)
    pid = client.post("/api/projects", json={"name": "Ads"}).json()["project"]["id"]
    files = [("files", (f"Google Ads Report - {calendar.month_abbr[m]} 2026.pdf", dashboard_pdf(m), "application/pdf")) for m in (8, 9, 10)]
    assert client.post(f"/api/projects/{pid}/files", files=files).status_code == 201
    listing = client.get(f"/api/projects/{pid}/files").json()["files"]
    assert {f["status"] for f in listing} == {"processed"}
    assert sorted(f["period_label"] for f in listing) == ["Aug 2026", "Oct 2026", "Sep 2026"]  # read from the reports
    qs = ["How did performance change month over month?", "Which keywords should we invest more in?",
          "Which audience and device should we focus on?"]
    rid = client.post(f"/api/projects/{pid}/analyses", json={"mode": "deep", "questions": qs}).json()["run"]["id"]
    run = client.get(f"/api/projects/{pid}/analyses/{rid}").json()["run"]
    assert run["status"] == "completed"
    titles = [f["title"] for f in run["findings"]]
    facts = " ".join(f["fact"] for f in run["findings"])
    # totals come from the full reports (157 -> 765 clicks), never from the top-N keyword rows alone
    assert any(t.startswith("Clicks grew 387.3%") for t in titles), titles[:20]
    assert sum(1 for t in titles if t.startswith("Clicks grew 387")) == 1  # same total in several tables -> one finding
    assert "not listed in the report" not in " ".join(titles)  # the completion row is never a "segment"
    assert "total 157 → 765" in facts
    user_qs = [q for q in run["questions"] if q["origin"] == "user"]
    assert len(user_qs) == 3 and all(q["status"] == "answered" for q in user_qs), user_qs
    cards = run["executive"]["cards"]
    assert all(c["status"] != "insufficient" for c in cards[:3])
