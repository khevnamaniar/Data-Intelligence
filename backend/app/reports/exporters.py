"""Report exporters: PDF (reportlab), DOCX (python-docx), XLSX (openpyxl).

To add a format, write a function ``export_<fmt>(report: dict, datasets: list) -> bytes`` and
register it in EXPORTERS.
"""
from __future__ import annotations

import io
from typing import Callable

from .charts import render_chart

CONF_LABEL = {"high": "HIGH", "medium": "MEDIUM", "low": "LOW"}
DISCLAIMER = ("Facts are computed from the uploaded data. Interpretations, implications and hypotheses are labelled as such and "
              "must be validated. Correlation is not causation. Values marked 'approximate' were visually interpreted from images.")


def _finding_parts(f: dict) -> list[tuple[str, str]]:
    parts = [("Fact", f["fact"])]
    for label, key in (("Interpretation", "interpretation"), ("Implication", "implication"), ("Hypothesis", "hypothesis"),
                       ("Validation", "validation")):
        if f.get(key):
            parts.append((label, f[key]))
    return parts


# ----------------------------------------------------------------- PDF
def export_pdf(report: dict, datasets: list | None = None) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape

    ss = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=ss["Normal"], fontSize=9, leading=12, alignment=TA_LEFT)
    small = ParagraphStyle("small", parent=body, fontSize=7.5, leading=9.5, textColor=colors.HexColor("#475569"))
    h1 = ParagraphStyle("h1", parent=ss["Heading1"], fontSize=16, textColor=colors.HexColor("#0f172a"), spaceAfter=8)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=12.5, textColor=colors.HexColor("#1e3a8a"), spaceBefore=10, spaceAfter=6)
    h3 = ParagraphStyle("h3", parent=ss["Heading3"], fontSize=10, textColor=colors.HexColor("#0f172a"), spaceBefore=6, spaceAfter=3)

    def P(text, style=body):
        return Paragraph(escape(str(text or "")), style)

    story = [Paragraph(escape(report["title"]), ss["Title"])]
    p = report["project"]
    meta = [f"Generated {report['generated_at'][:16].replace('T', ' ')} UTC", f"Analysis mode: {report['run']['mode'].capitalize()}"]
    for k, lbl in (("industry", "Industry"), ("geography", "Geography"), ("time_period", "Time period"), ("focus_entity", "Focus company/brand")):
        if p.get(k):
            meta.append(f"{lbl}: {p[k]}")
    story += [P(" · ".join(meta), small), Spacer(1, 6)]
    if p.get("business_objective"):
        story += [P(f"Business objective: {p['business_objective']}"), Spacer(1, 4)]
    story += [P(DISCLAIMER, small), Spacer(1, 8)]

    story.append(Paragraph("Executive Summary", h1))
    for line in report["executive_summary"]:
        story.append(P("• " + line))
    if report["questions"]:
        story.append(Paragraph("Your Questions", h2))
        for q in report["questions"]:
            if q["origin"] != "user":
                continue
            story.append(Paragraph("<b>" + escape(q["question"]) + "</b>", body))
            story.append(P(q["answer"] + (f"  [Confidence: {CONF_LABEL.get(q['confidence'], q['confidence'])}]" if q["confidence"] else "")))
            if q["data_needed"]:
                story.append(P("Data needed: " + "; ".join(q["data_needed"]), small))
            story.append(Spacer(1, 4))
    for sec in report["sections"]:
        story.append(Paragraph(sec["title"], h1 if sec["key"] == "key_findings" else h2))
        for f in sec["findings"]:
            block = [Paragraph(f"{escape(f['title'])} <font color='#64748b' size='8'>[{CONF_LABEL[f['confidence']]} confidence"
                               f"{', approximate' if f['approximate'] else ''}]</font>", h3)]
            for label, text in _finding_parts(f):
                block.append(Paragraph(f"<b>{label}:</b> {escape(text)}", body))
            if f["evidence"]:
                block.append(P("Evidence: " + " | ".join(e["source_label"] for e in f["evidence"][:3]), small))
            story.append(KeepTogether(block))
            if f.get("chart") and sec["key"] in ("key_findings", "major_trends", "opportunities", "marketing_insights", "gaps"):
                png = render_chart(f["chart"])
                if png:
                    story.append(Image(io.BytesIO(png), width=15 * cm, height=7.3 * cm))
            story.append(Spacer(1, 6))
    if report["gaps"]:
        story.append(Paragraph("Gap Analysis", h2))
        rows = [["Gap", "Type", "Significance", "Recommended investigation"]]
        for g in report["gaps"][:20]:
            rows.append([P(g["gap"], small), P(g["gap_type"], small), P(g["significance"], small), P(g["recommended_investigation"], small)])
        t = Table(rows, colWidths=[6.5 * cm, 2.2 * cm, 2 * cm, 6.3 * cm], repeatRows=1)
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cbd5e1")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")),
                               ("FONTSIZE", (0, 0), (-1, 0), 8), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story.append(t)
    lim = report["data_limitations"]
    if lim:
        story.append(Paragraph("Data Limitations", h2))
        for l in lim:
            story.append(P(f"• {l['limitation']} — {l['effect']}"))
    r = report["research"] or {}
    if r.get("unknown") or r.get("to_find_out"):
        story.append(Paragraph("What We Still Need to Know", h2))
        for u in r.get("unknown", [])[:12]:
            story.append(P(f"• {u['statement']}"))
    if r.get("to_find_out"):
        story.append(Paragraph("Recommended Research", h2))
        rows = [["Question", "Methods", "Priority"]]
        for q in r["to_find_out"][:12]:
            rows.append([P(q["question"], small), P(", ".join(q["methodologies"]), small), P(q["priority"], small)])
        t = Table(rows, colWidths=[9 * cm, 6 * cm, 2 * cm], repeatRows=1)
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cbd5e1")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")),
                               ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story.append(t)
    if r.get("assumptions"):
        story.append(Paragraph("Assumptions", h3))
        for a in r["assumptions"]:
            story.append(P("• " + a))
    if report["recommendations"]:
        story.append(Paragraph("Recommended Actions", h2))
        for rec in report["recommendations"][:15]:
            story.append(P(f"• [{rec['priority'].upper()}] {rec['recommendation']}"))
    if report["future_questions"]:
        story.append(Paragraph("Future Questions", h2))
        for q in report["future_questions"][:15]:
            story.append(P(f"• {q['question']}  ({q['priority']} priority; methods: {', '.join(q['methodologies'][:3])})"))
    if report["data_quality"]:
        story.append(PageBreak())
        story.append(Paragraph("Data Quality Report", h2))
        rows = [["Issue", "Source", "Field", "Severity", "Suggested resolution"]]
        for i in report["data_quality"][:60]:
            rows.append([P(i["description"], small), P(i["source"], small), P(i["field"], small), P(i["severity"], small),
                         P(i["suggested_resolution"], small)])
        t = Table(rows, colWidths=[5.5 * cm, 3 * cm, 2.5 * cm, 1.6 * cm, 4.4 * cm], repeatRows=1)
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cbd5e1")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")),
                               ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story.append(t)
    story.append(Paragraph("Source References", h2))
    for s in report["sources"]:
        story.append(P(f"• {s['name']} ({s['type'].upper()}, role: {s['role']}, status: {s['status']}) — cited by {s['findings_citing']} finding(s)"))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#64748b"))
        canvas.drawString(2 * cm, 1.2 * cm, "Confidential – generated by Data Intelligence Platform")
        canvas.drawRightString(A4[0] - 2 * cm, 1.2 * cm, f"Page {doc.page}")
        canvas.restoreState()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=1.8 * cm, bottomMargin=1.8 * cm,
                            title=report["title"])
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


# ----------------------------------------------------------------- DOCX
def export_docx(report: dict, datasets: list | None = None) -> bytes:
    import docx
    from docx.shared import Cm, Pt, RGBColor

    d = docx.Document()
    st = d.styles["Normal"]
    st.font.name = "Calibri"
    st.font.size = Pt(10)
    d.add_heading(report["title"], 0)
    p = report["project"]
    meta = [f"Generated {report['generated_at'][:16].replace('T', ' ')} UTC", f"Mode: {report['run']['mode'].capitalize()}"]
    for k, lbl in (("industry", "Industry"), ("geography", "Geography"), ("time_period", "Time period"), ("focus_entity", "Focus company/brand")):
        if p.get(k):
            meta.append(f"{lbl}: {p[k]}")
    para = d.add_paragraph(" · ".join(meta))
    para.runs[0].font.size = Pt(8)
    if p.get("business_objective"):
        d.add_paragraph(f"Business objective: {p['business_objective']}")
    note = d.add_paragraph(DISCLAIMER)
    note.runs[0].font.size = Pt(8)
    note.runs[0].italic = True

    d.add_heading("Executive Summary", 1)
    for line in report["executive_summary"]:
        d.add_paragraph(line, style="List Bullet")
    user_qs = [q for q in report["questions"] if q["origin"] == "user"]
    if user_qs:
        d.add_heading("Your Questions", 1)
        for q in user_qs:
            d.add_paragraph().add_run(q["question"]).bold = True
            d.add_paragraph(q["answer"] + (f"  [Confidence: {CONF_LABEL.get(q['confidence'], '')}]" if q["confidence"] else ""))
    for sec in report["sections"]:
        d.add_heading(sec["title"], 1)
        for f in sec["findings"]:
            d.add_heading(f["title"], 3)
            c = d.add_paragraph()
            r = c.add_run(f"Confidence: {CONF_LABEL[f['confidence']]}" + (" · approximate values" if f["approximate"] else ""))
            r.font.size = Pt(8)
            r.font.color.rgb = RGBColor(0x64, 0x74, 0x8B)
            for label, text in _finding_parts(f):
                para = d.add_paragraph()
                para.add_run(label + ": ").bold = True
                para.add_run(text)
            if f["evidence"]:
                e = d.add_paragraph("Evidence: " + " | ".join(x["source_label"] for x in f["evidence"][:3]))
                e.runs[0].font.size = Pt(8)
            if f.get("chart") and sec["key"] in ("key_findings", "major_trends", "opportunities", "marketing_insights", "gaps"):
                png = render_chart(f["chart"])
                if png:
                    d.add_picture(io.BytesIO(png), width=Cm(15))
    if report["gaps"]:
        d.add_heading("Gap Analysis", 1)
        t = d.add_table(rows=1, cols=7)
        t.style = "Light Grid Accent 1"
        hdr = ["Gap", "Evidence", "Why it matters", "Significance", "Missing information", "Recommended investigation", "Potential action"]
        for i, h in enumerate(hdr):
            t.rows[0].cells[i].text = h
        for g in report["gaps"][:25]:
            row = t.add_row().cells
            for i, k in enumerate(["gap", "evidence", "why_it_matters", "significance", "missing_information", "recommended_investigation", "potential_action"]):
                row[i].text = str(g.get(k, ""))[:400]
    if report["data_limitations"]:
        d.add_heading("Data Limitations", 1)
        for l in report["data_limitations"]:
            d.add_paragraph(f"{l['limitation']} — {l['effect']}", style="List Bullet")
    r = report["research"] or {}
    if r.get("known"):
        d.add_heading("What We Know", 1)
        for k in r["known"][:12]:
            d.add_paragraph(f"{k['statement']} [{k['confidence']}]", style="List Bullet")
    if r.get("unknown"):
        d.add_heading("What We Still Need to Know", 1)
        for u in r["unknown"][:12]:
            d.add_paragraph(u["statement"], style="List Bullet")
    if r.get("to_find_out"):
        d.add_heading("Recommended Research", 1)
        t = d.add_table(rows=1, cols=3)
        t.style = "Light Grid Accent 1"
        for i, h in enumerate(["Question", "How to find out", "Priority"]):
            t.rows[0].cells[i].text = h
        for q in r["to_find_out"][:15]:
            row = t.add_row().cells
            row[0].text, row[1].text, row[2].text = q["question"], ", ".join(q["methodologies"]), q["priority"]
    if report["recommendations"]:
        d.add_heading("Recommended Actions", 1)
        for rec in report["recommendations"][:15]:
            d.add_paragraph(f"[{rec['priority'].upper()}] {rec['recommendation']}", style="List Bullet")
    if report["future_questions"]:
        d.add_heading("Future Questions", 1)
        for q in report["future_questions"][:15]:
            d.add_paragraph(q["question"], style="List Bullet")
    if report["data_quality"]:
        d.add_heading("Data Quality Report", 1)
        t = d.add_table(rows=1, cols=5)
        t.style = "Light Grid Accent 1"
        for i, h in enumerate(["Issue", "Source", "Field", "Severity", "Suggested resolution"]):
            t.rows[0].cells[i].text = h
        for i in report["data_quality"][:60]:
            row = t.add_row().cells
            row[0].text, row[1].text, row[2].text, row[3].text, row[4].text = (i["description"], i["source"] or "", i["field"], i["severity"],
                                                                               i["suggested_resolution"])
    d.add_heading("Source References", 1)
    for s in report["sources"]:
        d.add_paragraph(f"{s['name']} ({s['type'].upper()}, {s['role']}) — cited by {s['findings_citing']} finding(s)", style="List Bullet")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


# ----------------------------------------------------------------- XLSX
def export_xlsx(report: dict, datasets: list | None = None) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    head_fill = PatternFill("solid", fgColor="1E3A8A")
    head_font = Font(color="FFFFFF", bold=True)

    def sheet(title, headers, rows, widths=None):
        ws = wb.create_sheet(title[:31])
        ws.append(headers)
        for c in ws[1]:
            c.fill, c.font = head_fill, head_font
        for r in rows:
            ws.append([_cell(v) for v in r])
        for i, h in enumerate(headers, start=1):
            ws.column_dimensions[get_column_letter(i)].width = (widths or {}).get(h, min(60, max(12, len(h) + 4)))
        for row in ws.iter_rows(min_row=2):
            for c in row:
                c.alignment = Alignment(wrap_text=True, vertical="top")
        ws.freeze_panes = "A2"
        return ws

    ws = wb.active
    ws.title = "Summary"
    ws.append([report["title"]])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([f"Generated {report['generated_at'][:16]} UTC · mode {report['run']['mode']}"])
    ws.append([DISCLAIMER])
    ws.append([])
    ws.append(["Executive summary"])
    ws["A5"].font = Font(bold=True)
    for line in report["executive_summary"]:
        ws.append([line])
    ws.column_dimensions["A"].width = 140
    sheet("Questions", ["Question", "Origin", "Status", "Answer", "Confidence", "Data needed"],
          [[q["question"], q["origin"], q["status"], q["answer"], q["confidence"], "; ".join(q["data_needed"])] for q in report["questions"]],
          {"Question": 40, "Answer": 90, "Data needed": 40})
    sheet("Findings", ["Rank", "Category", "Title", "Fact", "Interpretation", "Implication", "Hypothesis", "Validation", "Confidence", "Approximate", "Sources"],
          [[f["rank"], f["category"], f["title"], f["fact"], f["interpretation"], f["implication"], f["hypothesis"], f["validation"],
            f["confidence"], "yes" if f["approximate"] else "no", " | ".join(e["source_label"] for e in f["evidence"])] for f in report["all_findings"]],
          {"Title": 45, "Fact": 70, "Interpretation": 45, "Implication": 40, "Hypothesis": 40, "Validation": 40, "Sources": 60})
    sheet("Trends", ["Measure", "Dimension", "Value", "Direction", "Start period", "End period", "Start value", "End value", "% change", "CAGR %", "R²", "Acceleration"],
          [[t["measure"], t["dimension"], t["dimension_value"], t["direction"], t["start_period"], t["end_period"], t["start_value"], t["end_value"],
            t["pct_change"], t["cagr"], t["r_squared"], t["acceleration"]] for t in report["trends"]])
    sheet("Comparisons", ["Type", "Subject A", "Subject B", "Measure", "Value A", "Value B", "Difference", "% difference"],
          [[c["comparison_type"], c["subject_a"], c["subject_b"], c["measure"], c["value_a"], c["value_b"], c["difference"], c["pct_difference"]]
           for c in report["comparisons"]])
    sheet("Gaps", ["Gap", "Type", "Evidence", "Why it matters", "Significance", "Missing information", "Recommended investigation", "Potential action"],
          [[g["gap"], g["gap_type"], g["evidence"], g["why_it_matters"], g["significance"], g["missing_information"], g["recommended_investigation"],
            g["potential_action"]] for g in report["gaps"]], {"Gap": 45, "Evidence": 60, "Recommended investigation": 40})
    sheet("Opportunities", ["Product/category", "Classification", "Rationale", "Potential USP", "Competition", "Demand indicators", "Risks", "Confidence", "Needs validation"],
          [[o["subject"], o["classification"], o["rationale"], o["potential_usp"], o["competition_level"], o["demand_indicators"], o["risks"],
            o["confidence"], o["needs_validation"]] for o in report["opportunities"]], {"Rationale": 50, "Potential USP": 60, "Demand indicators": 50})
    r = report["research"] or {}
    sheet("Research", ["Section", "Item", "Methods / context", "Priority / confidence"],
          [["What we know", k["statement"], "", k["confidence"]] for k in r.get("known", [])]
          + [["What we don't know", u["statement"], u.get("context", ""), ""] for u in r.get("unknown", [])]
          + [["What we should find out", q["question"], ", ".join(q["methodologies"]), q["priority"]] for q in r.get("to_find_out", [])]
          + [["Future question", q["question"], ", ".join(q["methodologies"]), q["priority"]] for q in report["future_questions"]],
          {"Item": 80, "Methods / context": 50})
    sheet("Recommendations", ["Recommendation", "Type", "Priority", "Rationale"],
          [[x["recommendation"], x["rec_type"], x["priority"], x["rationale"]] for x in report["recommendations"]], {"Recommendation": 80, "Rationale": 60})
    sheet("Data Quality", ["Issue", "Source", "Field", "Severity", "Suggested resolution", "Type"],
          [[i["description"], i["source"], i["field"], i["severity"], i["suggested_resolution"], i["issue_type"]] for i in report["data_quality"]],
          {"Issue": 70, "Suggested resolution": 50})
    sheet("Evidence", ["Finding", "Source", "Excerpt", "Approximate"],
          [[f["title"], e["source_label"], e["excerpt"], "yes" if e["approximate"] else "no"] for f in report["all_findings"] for e in f["evidence"]],
          {"Finding": 45, "Source": 70, "Excerpt": 70})
    sheet("Sources", ["File", "Type", "Role", "Status", "Findings citing"],
          [[s["name"], s["type"], s["role"], s["status"], s["findings_citing"]] for s in report["sources"]])
    # supporting data (normalised copies, capped)
    for ds in (datasets or [])[:6]:
        df = ds["df"].drop(columns=[c for c in ds["df"].columns if c.startswith("__") and c != "__row__"]).head(5000)
        df = df.rename(columns={"__row__": "Source row"})
        ws = wb.create_sheet(("Data – " + ds["name"])[:31].replace("/", "-").replace(":", "-").replace("?", "").replace("*", "").replace("[", "(").replace("]", ")"))
        ws.append([f"Supporting data from {ds['name']} (normalised copy{', first 5,000 rows' if len(ds['df']) > 5000 else ''}). Original file unchanged."])
        ws.append([str(c) for c in df.columns])
        for c in ws[2]:
            c.fill, c.font = head_fill, head_font
        for row in df.itertuples(index=False):
            ws.append([_cell(v) for v in row])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _cell(v):
    import math

    import numpy as np
    import pandas as pd

    if v is None:
        return None
    if isinstance(v, (float, np.floating)):
        return None if math.isnan(v) or math.isinf(v) else float(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, pd.Timestamp):
        return None if pd.isna(v) else v.to_pydatetime()
    if isinstance(v, (list, dict)):
        return str(v)[:32000]
    if isinstance(v, str):
        s = v[:32000]
        return "'" + s if s[:1] in ("=", "+", "-", "@") and not _is_number(s) else s  # prevent formula injection
    return v


def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


EXPORTERS: dict[str, tuple[Callable, str, str]] = {
    "pdf": (export_pdf, "application/pdf", "pdf"),
    "docx": (export_docx, "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"),
    "xlsx": (export_xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"),
}
