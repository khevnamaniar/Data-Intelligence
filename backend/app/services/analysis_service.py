"""Create, execute, persist and read analysis runs."""
from __future__ import annotations

import logging
import traceback

from ..analytics.loader import load_project_data
from ..analytics.model import Finding
from ..analytics.pipeline import run_pipeline
from ..analytics.questions import detect_intents
from ..db import Connection, get_db, new_id, now

log = logging.getLogger("dip.analysis")


def create_run(c: Connection, project_id: str, user_id: str, mode: str, questions: list[str], auto_discover: bool,
               file_ids: list[str] | None) -> dict:
    run = c.insert("analysis_runs", {
        "id": new_id(), "project_id": project_id, "created_by": user_id, "mode": mode, "auto_discover": auto_discover,
        "status": "queued", "progress": 0, "stage": "Queued", "file_ids_json": file_ids or [], "created_at": now()})
    for i, q in enumerate(questions):
        c.insert("analysis_questions", {"id": new_id(), "run_id": run["id"], "project_id": project_id, "question": q[:2000],
                                        "origin": "user", "intents_json": detect_intents(q), "status": "pending", "position": i})
    return run


def expire_stale_runs(project_id: str | None = None) -> int:
    """Mark analyses that have run far longer than expected as failed (e.g. the server stopped mid-way)."""
    from ..config import settings

    import datetime as dt

    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=settings.analysis_max_minutes)).isoformat()
    err = [{"what": "The analysis took too long and was stopped.",
            "why": f"It did not finish within {settings.analysis_max_minutes} minutes (the free server may have restarted or run out of resources).",
            "can_still": "Your files and earlier results are safe.",
            "next": "Run it again; if it keeps happening use Quick analysis or fewer/smaller files."}]
    with get_db().tx() as c:
        sql = "UPDATE analysis_runs SET status = 'failed', stage = 'Timed out', completed_at = ?, error_json = ? WHERE status IN ('queued','running') AND created_at < ?"
        params = [now(), err, cutoff]
        if project_id:
            sql += " AND project_id = ?"
            params.append(project_id)
        from ..db import to_json

        params[1] = to_json(err)
        n = c.execute(sql, params)
    if n:
        log.warning("Marked %s stalled analysis run(s) as timed out", n)
    return n


def _progress(run_id: str, pct: int, stage: str) -> None:
    with get_db().tx() as c:
        c.execute("UPDATE analysis_runs SET progress = ?, stage = ? WHERE id = ?", [pct, stage[:100], run_id])


def execute_run(run_id: str) -> None:
    db = get_db()
    with db.tx() as c:
        run = c.fetchone("SELECT * FROM analysis_runs WHERE id = ?", [run_id])
        if not run:
            return
        c.execute("UPDATE analysis_runs SET status = 'running', started_at = ?, stage = 'Loading data' WHERE id = ?", [now(), run_id])
        questions = [q["question"] for q in c.fetchall("SELECT question FROM analysis_questions WHERE run_id = ? AND origin = 'user' ORDER BY position", [run_id])]
    try:
        with db.tx() as c:
            pdata = load_project_data(c, run["project_id"], run.get("file_ids_json") or None)
        if not pdata.datasets and not pdata.documents:
            raise _NothingToAnalyse()
        import time as _time

        t0 = _time.perf_counter()
        log.info("Analysis %s started (%s mode, %s datasets, %s documents)", run_id[:8], run["mode"], len(pdata.datasets), len(pdata.documents))
        result = run_pipeline(pdata, run["mode"], questions, bool(run["auto_discover"]), lambda p, s: _progress(run_id, p, s))
        persist(run, result)
        log.info("Analysis %s finished: %s findings, %s engine error(s) (%.1fs)", run_id[:8], len(result["findings"]),
                 len(result["errors"]), _time.perf_counter() - t0)
    except _NothingToAnalyse:
        err = [{"what": "There is nothing to analyse yet.", "why": "No processed files were found in this project.",
                "can_still": "", "next": "Upload files on the Sources page, wait for them to finish processing, then run the analysis."}]
        with db.tx() as c:
            c.update("analysis_runs", run_id, {"status": "failed", "error_json": err, "completed_at": now(), "stage": "Failed"})
    except MemoryError:
        log.error("Analysis %s ran out of memory", run_id[:8])
        err = [{"what": "The analysis needed more memory than this server has.", "why": "The free server has 512 MB of memory.",
                "can_still": "Your files are safe.", "next": "Try Quick analysis, or select fewer/smaller files on the Analyse page."}]
        with db.tx() as c:
            c.update("analysis_runs", run_id, {"status": "failed", "error_json": err, "completed_at": now(), "stage": "Failed"})
    except Exception as exc:
        log.error("Analysis %s failed: %s", run_id[:8], traceback.format_exc())
        err = [{"what": "The analysis could not be completed.", "why": f"{exc.__class__.__name__}: {str(exc)[:300]}",
                "can_still": "Your files are safe and can still be viewed on the Sources page.",
                "next": "Try Quick Analysis, or remove the file mentioned in the Data Quality report and run again."}]
        with db.tx() as c:
            c.update("analysis_runs", run_id, {"status": "failed", "error_json": err, "completed_at": now(), "stage": "Failed"})


class _NothingToAnalyse(Exception):
    pass


def persist(run: dict, result: dict) -> None:
    findings: list[Finding] = result["findings"]
    run_id, project_id = run["id"], run["project_id"]
    key_to_id = {}
    with get_db().tx() as c:
        for f in findings:
            fid = new_id()
            key_to_id[f.key] = fid
            f.id = fid
            c.insert("findings", {
                "id": fid, "run_id": run_id, "project_id": project_id, "category": f.category, "title": f.title[:2000],
                "fact": f.fact, "interpretation": f.interpretation, "implication": f.implication, "hypothesis": f.hypothesis,
                "validation": f.validation, "confidence": f.confidence, "is_approximate": f.approximate, "score": float(round(f.score, 4)),
                "rank": f.rank, "tags_json": [t for t in f.tags if t], "chart_json": f.chart, "details_json": {**f.details, "key": f.key},
                "created_at": now()})
            for e in f.evidence:
                c.insert("finding_evidence", {
                    "id": new_id(), "finding_id": fid, "file_id": e.file_id, "dataset_id": e.dataset_id, "chunk_id": e.chunk_id,
                    "source_label": e.source_label[:2000], "locator_json": e.locator, "excerpt": e.excerpt[:2000],
                    "values_json": e.values, "is_approximate": e.approximate})
            rec = f.records
            if "trend" in rec:
                t, st = rec["trend"], rec["trend"]["stats"]
                c.insert("trends", {
                    "id": new_id(), "run_id": run_id, "finding_id": fid, "dataset_id": t.get("dataset_id"), "measure": t["measure"][:500],
                    "dimension": t.get("dimension", "")[:500], "dimension_value": t.get("dimension_value", "")[:500],
                    "direction": st.get("direction", "")[:20], "start_period": str(st.get("start_period", ""))[:50],
                    "end_period": str(st.get("end_period", ""))[:50], "start_value": st.get("start"), "end_value": st.get("end"),
                    "abs_change": st.get("abs_change"), "pct_change": st.get("pct_change"), "cagr": st.get("cagr"), "slope": st.get("slope"),
                    "r_squared": st.get("r_squared"), "acceleration": st.get("acceleration") or "", "series_json": st.get("series")})
            if "comparison" in rec:
                cm = rec["comparison"]
                va, vb = cm.get("value_a"), cm.get("value_b")
                diff = (vb - va) if va is not None and vb is not None else None
                c.insert("comparisons", {
                    "id": new_id(), "run_id": run_id, "finding_id": fid, "comparison_type": cm["comparison_type"],
                    "subject_a": str(cm["subject_a"])[:500], "subject_b": str(cm["subject_b"])[:500], "measure": str(cm.get("measure", ""))[:500],
                    "value_a": va, "value_b": vb, "difference": diff,
                    "pct_difference": (diff / abs(va) * 100) if diff is not None and va else None, "details_json": cm.get("details")})
            for g in rec.get("gaps", []):
                c.insert("gaps", {"id": new_id(), "run_id": run_id, "finding_id": fid, **{k: (str(v) if v is not None else "") for k, v in g.items()}})
            if "opportunity" in rec:
                o = rec["opportunity"]
                c.insert("opportunities", {"id": new_id(), "run_id": run_id, "finding_id": fid,
                                           **{k: v for k, v in o.items() if k != "metrics"}, "metrics_json": o.get("metrics")})
            for r in rec.get("risks", []):
                c.insert("risks", {"id": new_id(), "run_id": run_id, "finding_id": fid, **r})
            for r in rec.get("recommendations", []):
                c.insert("recommendations", {"id": new_id(), "run_id": run_id, "finding_id": fid, **r})
        # research questions: future questions + research to-find-out
        for q in result["future_questions"]:
            c.insert("research_questions", {
                "id": new_id(), "run_id": run_id, "project_id": project_id, "finding_id": key_to_id.get(q.get("finding_key")),
                "question": q["question"], "question_type": q["question_type"], "rationale": q["rationale"],
                "methodologies_json": q["methodologies"], "priority": q["priority"]})
        for q in result["research"]["to_find_out"]:
            c.insert("research_questions", {
                "id": new_id(), "run_id": run_id, "project_id": project_id, "finding_id": key_to_id.get(q.get("finding_key")),
                "question": q["question"], "question_type": "research", "rationale": q.get("hypothesis", ""),
                "methodologies_json": q["methodologies"], "priority": q["priority"]})
        # question answers
        qrows = c.fetchall("SELECT * FROM analysis_questions WHERE run_id = ? ORDER BY position", [run_id])
        for qr, ans in zip(qrows, result["answers"]):
            ans = {**ans, "finding_ids": [key_to_id[k] for k in ans.get("finding_keys", []) if k in key_to_id]}
            c.update("analysis_questions", qr["id"], {"answer_json": ans, "status": ans["status"]})
        for i, ans in enumerate(result["auto_answers"]):
            ans = {**ans, "finding_ids": [key_to_id[k] for k in ans.get("finding_keys", []) if k in key_to_id]}
            c.insert("analysis_questions", {"id": new_id(), "run_id": run_id, "project_id": project_id, "question": ans["question"],
                                            "origin": "auto", "intents_json": ans.get("intents"), "answer_json": ans, "status": ans["status"],
                                            "position": 1000 + i})

        def ids(keys):
            return [key_to_id[k] for k in keys if k in key_to_id]

        research = result["research"]
        for part in ("known", "to_find_out"):
            for item in research.get(part, []):
                item["finding_id"] = key_to_id.get(item.pop("finding_key", None))
        for item in research.get("hypotheses", []):
            item["finding_id"] = key_to_id.get(item.pop("finding_key", None))
        recs = [{**r, "finding_id": key_to_id.get(r.pop("finding_key", None))} for r in result["recommendations"]]
        fut = [{**q, "finding_id": key_to_id.get(q.pop("finding_key", None))} for q in result["future_questions"]]
        payload = {
            "executive_summary": result["executive_summary"],
            "sections": {k: ids(v) for k, v in result["sections"].items()},
            "research": research, "future_questions": fut, "recommendations": recs,
            "data_limitations": result["data_limitations"], "sources": result["sources"], "capabilities": result["capabilities"],
            "auto_questions": result["auto_questions"], "errors": result["errors"], "stats": result["stats"],
        }
        status = "partial" if result["errors"] else "completed"
        c.update("analysis_runs", run_id, {"status": status, "progress": 100, "stage": "Done", "result_json": payload,
                                           "summary_json": {"findings": len(findings), "top": [f.title for f in findings[:5]],
                                                            "by_category": result["stats"]["by_category"]},
                                           "error_json": result["errors"] or None, "completed_at": now()})


def finding_rows(c: Connection, run_id: str) -> list[dict]:
    rows = c.fetchall("SELECT * FROM findings WHERE run_id = ? ORDER BY rank", [run_id])
    ev = c.fetchall("""SELECT e.* FROM finding_evidence e JOIN findings f ON f.id = e.finding_id WHERE f.run_id = ?""", [run_id])
    by = {}
    for e in ev:
        by.setdefault(e["finding_id"], []).append(e)
    for r in rows:
        r["evidence"] = by.get(r["id"], [])
    return rows


def serialize_run(c: Connection, run: dict, include_findings: bool = True) -> dict:
    out = {k: run[k] for k in ("id", "project_id", "mode", "auto_discover", "status", "progress", "stage", "created_at",
                               "started_at", "completed_at")}
    out["auto_discover"] = bool(out["auto_discover"])
    out["errors"] = run.get("error_json") or []
    out["summary"] = run.get("summary_json") or {}
    out["file_ids"] = run.get("file_ids_json") or []
    out["questions"] = c.fetchall("SELECT id, question, origin, status, answer_json, intents_json, position FROM analysis_questions WHERE run_id = ? ORDER BY position", [run["id"]])
    if include_findings and run["status"] in ("completed", "partial"):
        out["result"] = run.get("result_json") or {}
        out["findings"] = finding_rows(c, run["id"])
        out["gaps"] = c.fetchall("SELECT * FROM gaps WHERE run_id = ?", [run["id"]])
        out["opportunities"] = c.fetchall("SELECT * FROM opportunities WHERE run_id = ?", [run["id"]])
        out["risks"] = c.fetchall("SELECT * FROM risks WHERE run_id = ?", [run["id"]])
        out["research_questions"] = c.fetchall("SELECT * FROM research_questions WHERE run_id = ? ORDER BY priority", [run["id"]])
        out["comparisons"] = c.fetchall("SELECT * FROM comparisons WHERE run_id = ?", [run["id"]])
        out["trends"] = c.fetchall("SELECT id, finding_id, measure, dimension, dimension_value, direction, start_period, end_period, start_value, end_value, pct_change, cagr, r_squared, acceleration FROM trends WHERE run_id = ?", [run["id"]])
    return out
