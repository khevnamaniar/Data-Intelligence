"""End-to-end: demo project → deep analysis → traceability → Ask Your Data → report → PDF/DOCX/XLSX exports."""
import io
import zipfile

import pytest

from tests.conftest import register

QUESTIONS = ["What are the major trends?", "Which products have potential?", "What gaps exist?",
             "Is ROAS driven by a particular channel?", "How does future strategy compare with historical performance?",
             "What should we investigate next?"]


@pytest.fixture()
def demo(client):
    register(client)
    r = client.post("/api/projects/demo")
    assert r.status_code == 201, r.text
    pid = r.json()["project"]["id"]
    files = client.get(f"/api/projects/{pid}/files").json()["files"]
    assert len(files) == 9 and all(f["status"] in ("processed", "partial") for f in files), [(f["original_name"], f["status"], f["error_json"]) for f in files]
    r = client.post(f"/api/projects/{pid}/analyses", json={"mode": "deep", "questions": QUESTIONS, "auto_discover": True})
    assert r.status_code == 202
    run_id = r.json()["run"]["id"]
    run = client.get(f"/api/projects/{pid}/analyses/{run_id}").json()["run"]
    assert run["status"] == "completed", run["errors"]
    return pid, run


def test_deep_analysis_results(client, demo):
    pid, run = demo
    secs = run["result"]["sections"]
    for s in ("key_findings", "major_trends", "comparisons", "opportunities", "gaps", "marketing_insights", "strategic_implications", "risks"):
        assert secs.get(s), f"section {s} is empty"
    findings = {f["id"]: f for f in run["findings"]}
    cats = {f["category"] for f in findings.values()}
    assert {"trend", "comparison", "gap", "opportunity", "roas", "strategy", "impact"} <= cats
    # every finding has a fact, a confidence level and at least one piece of evidence
    for f in findings.values():
        assert f["fact"] and f["confidence"] in ("high", "medium", "low")
        assert f["evidence"], f["title"]
    # user questions all answered (or explicitly insufficient) and auto questions generated
    user_q = [q for q in run["questions"] if q["origin"] == "user"]
    assert len(user_q) == len(QUESTIONS) and all(q["status"] in ("answered", "insufficient") for q in user_q)
    assert [q for q in run["questions"] if q["origin"] == "auto"]
    roas_q = next(q for q in user_q if "ROAS" in q["question"])
    assert roas_q["status"] == "answered" and "ROAS" in roas_q["answer_json"]["answer"]
    assert run["research_questions"] and run["result"]["future_questions"]
    assert run["result"]["research"]["known"] and run["result"]["research"]["unknown"]
    assert run["opportunities"] and run["gaps"] and run["trends"] and run["comparisons"]


def test_strategy_mismatch_and_low_competition_trap(demo):
    _, run = demo
    titles = [f["title"] for f in run["findings"]]
    assert any("mismatch" in t and "tv" in t.lower() for t in titles)
    assert any("NOT an opportunity" in t and "Collagen" in t for t in titles)
    assert any(t.startswith("Potential opportunity: Magnesium") for t in titles)


def test_traceability_finding_to_source(client, demo):
    pid, run = demo
    trend = next(f for f in run["findings"] if f["category"] == "trend" and f["evidence"][0]["dataset_id"])
    ev = trend["evidence"][0]
    assert ev["source_label"] and ev["locator_json"]["rows"]
    sample_rows = ev["locator_json"]["row_sample"]
    prev = client.get(f"/api/projects/{pid}/datasets/{ev['dataset_id']}/preview", params={"rows": ",".join(map(str, sample_rows))}).json()
    assert prev["rows"] and {r["__row__"] for r in prev["rows"]} <= set(sample_rows)
    doc_f = next(f for f in run["findings"] if any(e["chunk_id"] for e in f["evidence"]))
    chunk_id = next(e["chunk_id"] for e in doc_f["evidence"] if e["chunk_id"])
    ch = client.get(f"/api/projects/{pid}/chunks/{chunk_id}").json()
    assert ch["chunk"]["text"] and ch["file"]["original_name"]
    one = client.get(f"/api/projects/{pid}/findings/{trend['id']}").json()["finding"]
    assert one["evidence"][0]["dataset_id"] == ev["dataset_id"]


def test_ask_your_data(client, demo):
    pid, _ = demo
    r = client.post(f"/api/projects/{pid}/ask", json={"question": "Is ROAS being driven by Meta?"}).json()["message"]["answer"]
    assert "Meta" in r["answer"] and r["evidence"] and not r["insufficient"]
    r = client.post(f"/api/projects/{pid}/ask", json={"question": "Which products are growing?"}).json()["message"]["answer"]
    assert r["facts"] and r["chart"]
    r = client.post(f"/api/projects/{pid}/ask", json={"question": "Compare Meta vs TV"}).json()["message"]["answer"]
    assert "Meta" in r["answer"] and "TV" in r["answer"]
    r = client.post(f"/api/projects/{pid}/ask", json={"question": "How many astronauts visited Mars?"}).json()["message"]["answer"]
    assert r["insufficient"] and r["answer"].startswith("The available data is insufficient to determine this.")
    hist = client.get(f"/api/projects/{pid}/ask").json()["messages"]
    assert len(hist) == 4


def test_project_dashboard(client, demo):
    pid, _ = demo
    d = client.get(f"/api/projects/{pid}").json()
    assert d["dashboard"]["sections"]["key_findings"] and d["date_range"]["start"].startswith("2023")
    assert d["quality"]


def test_report_and_exports(client, demo):
    pid, run = demo
    rep = client.post(f"/api/projects/{pid}/reports", json={"run_id": run["id"]}).json()["report"]
    content = client.get(f"/api/projects/{pid}/reports/{rep['id']}").json()["report"]["content"]
    titles = [s["title"] for s in content["sections"]]
    assert titles[0] == "Key Findings" and "Opportunities" in titles and "Gaps" in titles
    assert content["data_quality"] and content["sources"]
    pdf = client.get(f"/api/projects/{pid}/reports/{rep['id']}/export/pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF" and len(pdf.content) > 20000
    docx = client.get(f"/api/projects/{pid}/reports/{rep['id']}/export/docx")
    assert docx.status_code == 200
    import docx as _docx

    doc = _docx.Document(io.BytesIO(docx.content))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "Executive Summary" in text and "Key Findings" in text and "Fact:" in text
    xlsx = client.get(f"/api/projects/{pid}/reports/{rep['id']}/export/xlsx")
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(xlsx.content))
    assert {"Summary", "Findings", "Trends", "Comparisons", "Gaps", "Opportunities", "Data Quality", "Evidence", "Sources"} <= set(wb.sheetnames)
    assert any(n.startswith("Data – ") for n in wb.sheetnames)
    assert wb["Findings"].max_row > 20
    reports = client.get(f"/api/projects/{pid}/reports").json()["reports"]
    assert {e["format"] for e in reports[0]["exports"]} == {"pdf", "docx", "xlsx"}
    assert client.get(f"/api/projects/{pid}/reports/{rep['id']}/export/odt").status_code == 404


def test_quick_and_research_modes(client, demo):
    pid, deep = demo
    q = client.post(f"/api/projects/{pid}/analyses", json={"mode": "quick", "questions": []}).json()["run"]["id"]
    quick = client.get(f"/api/projects/{pid}/analyses/{q}").json()["run"]
    assert quick["status"] == "completed" and len(quick["findings"]) <= 40
    assert not any(f["category"] == "impact" for f in quick["findings"])
    r = client.post(f"/api/projects/{pid}/analyses", json={"mode": "research", "questions": ["What should we investigate next?"]}).json()["run"]["id"]
    research = client.get(f"/api/projects/{pid}/analyses/{r}").json()["run"]
    assert research["result"]["research"]["assumptions"] and research["result"]["research"]["hypotheses"]
    assert research["result"]["research"]["to_find_out"][0]["methodologies"]


def test_analysis_with_no_files_fails_gracefully(client):
    register(client)
    p = client.post("/api/projects", json={"name": "Empty"}).json()["project"]
    rid = client.post(f"/api/projects/{p['id']}/analyses", json={"mode": "quick"}).json()["run"]["id"]
    run = client.get(f"/api/projects/{p['id']}/analyses/{rid}").json()["run"]
    assert run["status"] == "failed" and run["errors"][0]["next"]


def test_executive_dashboard(client, demo):
    pid, run = demo
    ex = run["executive"]
    assert [t["label"] for t in ex["tiles"]] == ["Sources analysed", "Opportunities to validate", "Issues needing attention", "Evidence strength"]
    assert len(ex["cards"]) == len(QUESTIONS)  # one plain-language card per question asked
    fids = {f["id"] for f in run["findings"]}
    for c in ex["cards"]:
        assert c["headline"] and c["status"] in ("positive", "watch", "action", "insufficient", "info")
        assert set(c["finding_ids"]) <= fids  # every card links back to evidence
        assert "R²" not in c["headline"] + c["summary"] and "HHI" not in c["summary"]
    strategy = next(c for c in ex["cards"] if "strategy" in c["question"])
    assert strategy["status"] == "action" and "TV" in strategy["headline"] and "Collagen" in strategy["headline"]
    potential = next(c for c in ex["cards"] if "potential" in c["question"])
    assert potential["kpi"] and potential["kpi"]["value"].startswith("+")
    rep = client.post(f"/api/projects/{pid}/reports", json={"run_id": run["id"]}).json()["report"]
    import docx as _docx
    import openpyxl

    d = _docx.Document(io.BytesIO(client.get(f"/api/projects/{pid}/reports/{rep['id']}/export/docx").content))
    assert any(p.text == "Executive Dashboard" for p in d.paragraphs)
    wb = openpyxl.load_workbook(io.BytesIO(client.get(f"/api/projects/{pid}/reports/{rep['id']}/export/xlsx").content))
    assert wb.sheetnames[:2] == ["Summary", "Executive Dashboard"]
    pdf = client.get(f"/api/projects/{pid}/reports/{rep['id']}/export/pdf").content
    assert pdf[:4] == b"%PDF"


def test_executive_dashboard_unanswerable_question(client):
    register(client)
    p = client.post("/api/projects", json={"name": "P"}).json()["project"]
    client.post(f"/api/projects/{p['id']}/files", files=[("files", ("s.csv", b"Month,Revenue\n2024-01,10\n2024-02,12\n2024-03,15\n2024-04,18\n", "text/csv"))])
    rid = client.post(f"/api/projects/{p['id']}/analyses", json={"mode": "quick", "questions": ["How many employees left last year?"], "auto_discover": False}).json()["run"]["id"]
    ex = client.get(f"/api/projects/{p['id']}/analyses/{rid}").json()["run"]["executive"]
    card = ex["cards"][0]
    assert card["status"] == "insufficient" and "can't answer" in card["headline"] and card["next_step"].startswith("Provide")
