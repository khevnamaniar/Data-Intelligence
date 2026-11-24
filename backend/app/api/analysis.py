"""Analysis runs, findings, Ask Your Data and reports/exports."""
from __future__ import annotations

import urllib.parse
from typing import Literal

from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from ..ai.provider import get_ai_provider
from ..analytics.ask import ask
from ..analytics.loader import load_project_data
from ..db import get_db, new_id, now
from ..reports.builder import build_report
from ..reports.exporters import EXPORTERS
from ..security import analysis_limiter, ask_limiter
from ..services.analysis_service import create_run, execute_run, expire_stale_runs, finding_rows, serialize_run
from ..services.jobs import submit
from ..storage import get_storage
from .common import ApiError, audit, current_user, json_response, parse_body, parse_uuid, require_project


class RunIn(BaseModel):
    mode: Literal["quick", "deep", "research"] = "deep"
    questions: list[str] = Field(default_factory=list, max_length=30)
    auto_discover: bool = True
    file_ids: list[str] | None = None


class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=1000)


class ReportIn(BaseModel):
    run_id: str | None = None
    title: str | None = Field(default=None, max_length=300)


async def start_run(request: Request):
    user = current_user(request)
    body = await parse_body(request, RunIn)
    if not analysis_limiter.allow(user["id"]):
        raise ApiError(429, "Too many analyses started in the last hour.", "Analyses are limited to protect the free hosting allowance.",
                       "Wait a while, or reuse the results of an earlier run.")
    qs = [q.strip()[:1000] for q in body.questions if q and q.strip()]
    with get_db().tx() as c:
        p = require_project(c, user, request.path_params["pid"], "editor")
        if body.file_ids:
            ids = [parse_uuid(i, "file") for i in body.file_ids]
            marks = ",".join("?" for _ in ids)
            ok = c.fetchall(f"SELECT id FROM files WHERE project_id = ? AND id IN ({marks})", [p["id"], *ids])
            if len(ok) != len(set(ids)):
                raise ApiError(422, "Some selected files do not belong to this project.")
        pending = c.fetchone("SELECT COUNT(*) AS n FROM files WHERE project_id = ? AND status IN ('uploaded','processing')", [p["id"]])["n"]
        run = create_run(c, p["id"], user["id"], body.mode, qs, body.auto_discover or not qs, body.file_ids)
        if qs:
            c.update("projects", p["id"], {"learning_objectives_json": qs, "updated_at": now()})
        audit(c, request, user["id"], "analysis.start", p["id"], "analysis_run", run["id"], {"mode": body.mode})
    submit(execute_run, run["id"])
    return json_response({"run": {"id": run["id"], "status": "queued"}, "pending_files": pending}, 202)


async def list_runs(request: Request):
    user = current_user(request)
    with get_db().tx() as c:
        p = require_project(c, user, request.path_params["pid"])
        runs = c.fetchall("SELECT id, mode, status, progress, stage, created_at, completed_at, summary_json, error_json FROM analysis_runs WHERE project_id = ? ORDER BY created_at DESC", [p["id"]])
    return json_response({"runs": runs})


def _run(c, user, request):
    p = require_project(c, user, request.path_params["pid"])
    rid = parse_uuid(request.path_params["rid"], "analysis")
    run = c.fetchone("SELECT * FROM analysis_runs WHERE id = ? AND project_id = ?", [rid, p["id"]])
    if not run:
        raise ApiError(404, "Analysis not found.")
    return p, run


async def get_run(request: Request):
    user = current_user(request)
    expire_stale_runs()
    with get_db().tx() as c:
        p, run = _run(c, user, request)
        data = serialize_run(c, run, include_findings=request.query_params.get("light") != "1")
    return json_response({"run": data})


async def delete_run(request: Request):
    user = current_user(request)
    with get_db().tx() as c:
        p, run = _run(c, user, request)
        require_project(c, user, p["id"], "editor")
        c.execute("DELETE FROM analysis_runs WHERE id = ?", [run["id"]])
    return json_response({"ok": True})


async def get_finding(request: Request):
    user = current_user(request)
    fid = parse_uuid(request.path_params["fid"], "finding")
    with get_db().tx() as c:
        p = require_project(c, user, request.path_params["pid"])
        f = c.fetchone("SELECT * FROM findings WHERE id = ? AND project_id = ?", [fid, p["id"]])
        if not f:
            raise ApiError(404, "Finding not found.")
        f["evidence"] = c.fetchall("SELECT * FROM finding_evidence WHERE finding_id = ?", [fid])
    return json_response({"finding": f})


async def ask_data(request: Request):
    user = current_user(request)
    body = await parse_body(request, AskIn)
    if not ask_limiter.allow(user["id"]):
        raise ApiError(429, "Too many questions in the last hour.", "", "Wait a little and try again.")
    with get_db().tx() as c:
        p = require_project(c, user, request.path_params["pid"])
        latest = c.fetchone("SELECT id FROM analysis_runs WHERE project_id = ? AND status IN ('completed','partial') ORDER BY created_at DESC LIMIT 1", [p["id"]])
        findings = finding_rows(c, latest["id"]) if latest else []
        pdata = load_project_data(c, p["id"])
    if not pdata.datasets and not pdata.documents:
        raise ApiError(400, "There is no processed data in this project yet.", "", "Upload files first.")
    answer = await run_in_threadpool(ask, pdata, body.question, findings)
    provider = get_ai_provider()
    if provider.enabled and not answer["insufficient"]:
        narrative = await run_in_threadpool(provider.summarise, body.question, answer["facts"], answer["hypotheses"])
        if narrative:
            answer["ai_summary"] = {"text": narrative, "provider": provider.name,
                                    "note": "AI-written summary of the computed facts above. Check it against the facts."}
    answer["based_on_run"] = latest["id"] if latest else None
    with get_db().tx() as c:
        row = c.insert("ask_messages", {"id": new_id(), "project_id": p["id"], "user_id": user["id"], "question": body.question,
                                        "answer_json": answer, "created_at": now()})
    return json_response({"message": {"id": row["id"], "question": body.question, "answer": answer, "created_at": row["created_at"]}})


async def ask_history(request: Request):
    user = current_user(request)
    with get_db().tx() as c:
        p = require_project(c, user, request.path_params["pid"])
        rows = c.fetchall("SELECT id, question, answer_json, created_at FROM ask_messages WHERE project_id = ? ORDER BY created_at", [p["id"]])
    return json_response({"messages": [{"id": r["id"], "question": r["question"], "answer": r["answer_json"], "created_at": r["created_at"]} for r in rows]})


# ----------------------------------------------------------------- reports
async def create_report(request: Request):
    user = current_user(request)
    body = await parse_body(request, ReportIn)
    with get_db().tx() as c:
        p = require_project(c, user, request.path_params["pid"], "editor")
        if body.run_id:
            run = c.fetchone("SELECT * FROM analysis_runs WHERE id = ? AND project_id = ?", [parse_uuid(body.run_id, "analysis"), p["id"]])
        else:
            run = c.fetchone("SELECT * FROM analysis_runs WHERE project_id = ? AND status IN ('completed','partial') ORDER BY created_at DESC LIMIT 1", [p["id"]])
        if not run or run["status"] not in ("completed", "partial"):
            raise ApiError(400, "There is no completed analysis to report on.", "", "Run an analysis first.")
        content = build_report(c, p, run, body.title)
        rep = c.insert("reports", {"id": new_id(), "project_id": p["id"], "run_id": run["id"], "created_by": user["id"],
                                   "title": content["title"][:500], "content_json": content, "created_at": now()})
        audit(c, request, user["id"], "report.create", p["id"], "report", rep["id"])
    return json_response({"report": {"id": rep["id"], "title": rep["title"], "created_at": rep["created_at"], "run_id": run["id"]}}, 201)


async def list_reports(request: Request):
    user = current_user(request)
    with get_db().tx() as c:
        p = require_project(c, user, request.path_params["pid"])
        rows = c.fetchall("SELECT id, title, run_id, created_at FROM reports WHERE project_id = ? ORDER BY created_at DESC", [p["id"]])
        for r in rows:
            r["exports"] = c.fetchall("SELECT format, size_bytes, created_at FROM report_exports WHERE report_id = ?", [r["id"]])
    return json_response({"reports": rows})


def _report(c, user, request):
    p = require_project(c, user, request.path_params["pid"])
    rid = parse_uuid(request.path_params["rid"], "report")
    rep = c.fetchone("SELECT * FROM reports WHERE id = ? AND project_id = ?", [rid, p["id"]])
    if not rep:
        raise ApiError(404, "Report not found.")
    return p, rep


async def get_report(request: Request):
    user = current_user(request)
    with get_db().tx() as c:
        p, rep = _report(c, user, request)
    return json_response({"report": {"id": rep["id"], "title": rep["title"], "created_at": rep["created_at"], "content": rep["content_json"]}})


async def delete_report(request: Request):
    user = current_user(request)
    with get_db().tx() as c:
        p, rep = _report(c, user, request)
        require_project(c, user, p["id"], "editor")
        c.execute("DELETE FROM reports WHERE id = ?", [rep["id"]])
    get_storage().delete_prefix(f"projects/{p['id']}/reports/{rep['id']}/")
    return json_response({"ok": True})


def _supporting_datasets(c, project_id, run_id):
    from ..services.ingestion import load_dataset_frame

    run = c.fetchone("SELECT result_json FROM analysis_runs WHERE id = ?", [run_id]) if run_id else None
    out = []
    for ds in c.fetchall("SELECT * FROM datasets WHERE project_id = ? ORDER BY row_count DESC", [project_id])[:6]:
        cols = c.fetchall("SELECT * FROM dataset_columns WHERE dataset_id = ? ORDER BY position", [ds["id"]])
        try:
            out.append({"name": ds["name"], "df": load_dataset_frame(ds, cols)})
        except Exception:
            continue
    return out


async def export_report(request: Request):
    user = current_user(request)
    fmt = request.path_params["fmt"].lower()
    if fmt not in EXPORTERS:
        raise ApiError(404, "Unknown export format.", "Use pdf, docx or xlsx.")
    fn, mime, ext = EXPORTERS[fmt]
    with get_db().tx() as c:
        p, rep = _report(c, user, request)
        datasets = _supporting_datasets(c, p["id"], rep["run_id"]) if fmt == "xlsx" else []
    try:
        data = await run_in_threadpool(fn, rep["content_json"], datasets)
    except Exception as exc:
        raise ApiError(500, f"The {fmt.upper()} export failed.", f"{exc.__class__.__name__}: {str(exc)[:200]}",
                       "Try another format; the report itself is unaffected.")
    key = f"projects/{p['id']}/reports/{rep['id']}/report.{ext}"
    get_storage().put(key, data, mime)
    with get_db().tx() as c:
        c.execute("DELETE FROM report_exports WHERE report_id = ? AND format = ?", [rep["id"], fmt])
        c.insert("report_exports", {"id": new_id(), "report_id": rep["id"], "format": fmt, "storage_key": key, "size_bytes": len(data), "created_at": now()})
        audit(c, request, user["id"], "report.export", p["id"], "report", rep["id"], {"format": fmt})
    safe = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in rep["title"])[:80].strip() or "report"
    return Response(data, media_type=mime, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{urllib.parse.quote(safe)}.{ext}"})


routes = [
    Route("/api/projects/{pid}/analyses", start_run, methods=["POST"]),
    Route("/api/projects/{pid}/analyses", list_runs, methods=["GET"]),
    Route("/api/projects/{pid}/analyses/{rid}", get_run, methods=["GET"]),
    Route("/api/projects/{pid}/analyses/{rid}", delete_run, methods=["DELETE"]),
    Route("/api/projects/{pid}/findings/{fid}", get_finding, methods=["GET"]),
    Route("/api/projects/{pid}/ask", ask_data, methods=["POST"]),
    Route("/api/projects/{pid}/ask", ask_history, methods=["GET"]),
    Route("/api/projects/{pid}/reports", create_report, methods=["POST"]),
    Route("/api/projects/{pid}/reports", list_reports, methods=["GET"]),
    Route("/api/projects/{pid}/reports/{rid}", get_report, methods=["GET"]),
    Route("/api/projects/{pid}/reports/{rid}", delete_report, methods=["DELETE"]),
    Route("/api/projects/{pid}/reports/{rid}/export/{fmt}", export_report, methods=["GET"]),
]
