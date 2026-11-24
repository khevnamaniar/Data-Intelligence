"""Project endpoints, URLs/research sources, and the demo project."""
from __future__ import annotations

import ipaddress
import socket
import urllib.parse
import urllib.request
from pathlib import Path

from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.routing import Route

from ..config import REPO_DIR, settings
from ..db import get_db, new_id, now
from ..storage import get_storage
from .common import ApiError, audit, current_user, json_response, parse_body, parse_uuid, require_project

DEMO_DIR = REPO_DIR / "sample-data" / "consumer-health"
DEMO_OBJECTIVES = [
    "What are the major trends?", "Which products have potential?", "What gaps exist?",
    "Is ROAS driven by a particular channel?", "How does future strategy compare with historical performance?",
    "What should we investigate next?",
]


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    industry: str = Field(default="", max_length=200)
    geography: str = Field(default="", max_length=200)
    time_period: str = Field(default="", max_length=200)
    business_objective: str = Field(default="", max_length=5000)
    focus_entity: str = Field(default="", max_length=200)
    learning_objectives: list[str] = Field(default_factory=list, max_length=30)


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    industry: str | None = Field(default=None, max_length=200)
    geography: str | None = Field(default=None, max_length=200)
    time_period: str | None = Field(default=None, max_length=200)
    business_objective: str | None = Field(default=None, max_length=5000)
    focus_entity: str | None = Field(default=None, max_length=200)
    learning_objectives: list[str] | None = Field(default=None, max_length=30)


class UrlIn(BaseModel):
    url: str = Field(min_length=8, max_length=2000)
    title: str = Field(default="", max_length=500)
    notes: str = Field(default="", max_length=5000)
    fetch: bool = False


def public_project(p: dict) -> dict:
    return {k: p.get(k) for k in ("id", "name", "description", "industry", "geography", "time_period", "business_objective",
                                  "focus_entity", "is_demo", "created_at", "updated_at")} | {
        "learning_objectives": p.get("learning_objectives_json") or [], "role": p.get("member_role", "owner"), "is_demo": bool(p.get("is_demo"))}


async def list_projects(request: Request):
    user = current_user(request)
    with get_db().tx() as c:
        rows = c.fetchall(
            """SELECT p.*, m.role AS member_role,
                      (SELECT COUNT(*) FROM files f WHERE f.project_id = p.id) AS file_count,
                      (SELECT MAX(r.created_at) FROM analysis_runs r WHERE r.project_id = p.id) AS last_analysis
               FROM projects p JOIN project_members m ON m.project_id = p.id AND m.user_id = ?
               ORDER BY p.updated_at DESC""", [user["id"]])
    return json_response({"projects": [public_project(r) | {"file_count": r["file_count"], "last_analysis": r["last_analysis"]} for r in rows]})


def _create(c, request, user, body: ProjectIn, is_demo=False) -> dict:
    p = c.insert("projects", {
        "id": new_id(), "owner_id": user["id"], "name": body.name.strip(), "description": body.description, "industry": body.industry,
        "geography": body.geography, "time_period": body.time_period, "business_objective": body.business_objective,
        "focus_entity": body.focus_entity.strip(), "learning_objectives_json": [q.strip()[:500] for q in body.learning_objectives if q.strip()],
        "is_demo": is_demo, "created_at": now(), "updated_at": now()})
    c.insert("project_members", {"id": new_id(), "project_id": p["id"], "user_id": user["id"], "role": "owner", "created_at": now()})
    audit(c, request, user["id"], "project.create", p["id"], "project", p["id"])
    return c.fetchone("SELECT p.*, 'owner' AS member_role FROM projects p WHERE id = ?", [p["id"]])


async def create_project(request: Request):
    user = current_user(request)
    body = await parse_body(request, ProjectIn)
    with get_db().tx() as c:
        p = _create(c, request, user, body)
    return json_response({"project": public_project(p)}, 201)


async def get_project(request: Request):
    from ..services.analysis_service import expire_stale_runs

    user = current_user(request)
    expire_stale_runs(parse_uuid(request.path_params["pid"], "project"))
    with get_db().tx() as c:
        p = require_project(c, user, request.path_params["pid"])
        files = c.fetchall("SELECT id, original_name, file_type, source_role, period_label, status, size_bytes, extraction_json, error_json, created_at FROM files WHERE project_id = ? ORDER BY created_at", [p["id"]])
        runs = c.fetchall("SELECT id, mode, status, progress, stage, created_at, completed_at, summary_json FROM analysis_runs WHERE project_id = ? ORDER BY created_at DESC", [p["id"]])
        latest = next((r for r in runs if r["status"] in ("completed", "partial")), None)
        dashboard = {}
        if latest:
            run = c.fetchone("SELECT * FROM analysis_runs WHERE id = ?", [latest["id"]])
            res = run.get("result_json") or {}
            secs = res.get("sections", {})
            ids = set()
            for k in ("key_findings", "major_trends", "opportunities", "gaps", "risks"):
                ids.update(secs.get(k, [])[:5])
            fmap = {}
            if ids:
                marks = ",".join("?" for _ in ids)
                for f in c.fetchall(f"SELECT id, title, category, confidence, rank FROM findings WHERE id IN ({marks})", list(ids)):
                    fmap[f["id"]] = f
            dashboard = {"run_id": latest["id"], "completed_at": latest["completed_at"],
                         "sections": {k: [fmap[i] for i in secs.get(k, [])[:5] if i in fmap] for k in ("key_findings", "major_trends", "opportunities", "gaps", "risks")},
                         "research_questions": c.fetchall("SELECT question, priority FROM research_questions WHERE run_id = ? ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END LIMIT 6", [latest["id"]]),
                         "next_steps": [r for r in (res.get("recommendations") or [])[:5]]}
        date_range = _date_range(c, p["id"])
        sources = c.fetchall("SELECT * FROM research_sources WHERE project_id = ? ORDER BY created_at", [p["id"]])
        dq = c.fetchall("SELECT severity, COUNT(*) AS n FROM data_quality_issues WHERE project_id = ? GROUP BY severity", [p["id"]])
    st = {k: sum(1 for f in files if f["status"] == k) for k in ("processed", "partial", "failed", "uploaded", "processing")}
    parts = []
    if st["processed"]:
        parts.append(f"{st['processed']} source(s) processed successfully.")
    if st["partial"]:
        parts.append(f"{st['partial']} source(s) partially processed.")
    if st["failed"]:
        parts.append(f"{st['failed']} source(s) could not be processed.")
    if st["uploaded"] + st["processing"]:
        parts.append(f"{st['uploaded'] + st['processing']} source(s) still processing.")
    source_summary = {"counts": st, "message": " ".join(parts),
                      "can_continue": (st["processed"] + st["partial"]) > 0}
    return json_response({"project": public_project(p), "files": files, "runs": runs, "dashboard": dashboard, "source_summary": source_summary,
                          "date_range": date_range, "urls": sources, "quality": {r["severity"]: r["n"] for r in dq}})


def _date_range(c, project_id):
    lo, hi = None, None
    for d in c.fetchall("SELECT profile_json FROM datasets WHERE project_id = ?", [project_id]):
        t = (d.get("profile_json") or {}).get("time")
        if t:
            lo = min(lo, t["start"]) if lo else t["start"]
            hi = max(hi, t["end"]) if hi else t["end"]
    return {"start": lo, "end": hi} if lo else None


async def update_project(request: Request):
    user = current_user(request)
    body = await parse_body(request, ProjectPatch)
    with get_db().tx() as c:
        p = require_project(c, user, request.path_params["pid"], "editor")
        values = {k: v for k, v in body.model_dump(exclude_unset=True).items() if k != "learning_objectives" and v is not None}
        if body.learning_objectives is not None:
            values["learning_objectives_json"] = [q.strip()[:500] for q in body.learning_objectives if q.strip()]
        if values:
            values["updated_at"] = now()
            c.update("projects", p["id"], values)
        audit(c, request, user["id"], "project.update", p["id"], "project", p["id"], {"fields": list(values)})
        p = require_project(c, user, p["id"])
    return json_response({"project": public_project(p)})


async def delete_project(request: Request):
    user = current_user(request)
    with get_db().tx() as c:
        p = require_project(c, user, request.path_params["pid"], "owner")
        audit(c, request, user["id"], "project.delete", p["id"], "project", p["id"], {"name": p["name"]})
        c.execute("DELETE FROM projects WHERE id = ?", [p["id"]])
    removed = get_storage().delete_prefix(f"projects/{p['id']}/")
    return json_response({"ok": True, "files_removed": removed})


# ----------------------------------------------------------------- URLs / external research (optional)
def _safe_public_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ApiError(422, "Only http(s) web addresses can be added.")
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror:
        raise ApiError(422, "That web address could not be found.", "The domain name does not resolve.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ApiError(422, "That address points to a private network and cannot be fetched.", "This protects internal systems.")
    return url.strip()


async def add_url(request: Request):
    user = current_user(request)
    body = await parse_body(request, UrlIn)
    url = body.url.strip()
    if not url.lower().startswith(("http://", "https://")):
        raise ApiError(422, "Enter a full web address starting with https://")
    with get_db().tx() as c:
        p = require_project(c, user, request.path_params["pid"], "editor")
        row = c.insert("research_sources", {"id": new_id(), "project_id": p["id"], "kind": "url", "url": url, "title": (body.title or url)[:500],
                                            "notes": body.notes, "fetch_status": "not_fetched", "created_at": now()})
    if body.fetch:
        if not settings.url_fetch_enabled:
            status = "fetch_disabled"
        else:
            status = _fetch_url_into_project(p, row, user)
        with get_db().tx() as c:
            c.execute("UPDATE research_sources SET fetch_status = ? WHERE id = ?", [status, row["id"]])
        row["fetch_status"] = status
    return json_response({"source": row}, 201)


def _fetch_url_into_project(p, row, user) -> str:
    from ..services.ingestion import process_file, store_upload

    try:
        url = _safe_public_url(row["url"])
        req = urllib.request.Request(url, headers={"User-Agent": "DataIntelligencePlatform/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            ctype = resp.headers.get("Content-Type", "")
            data = resp.read(10 * 1024 * 1024 + 1)
        if len(data) > 10 * 1024 * 1024:
            return "too_large"
        if "pdf" in ctype:
            name = "web_page.pdf"
        elif "html" in ctype or "text" in ctype:
            import re

            text = data.decode("utf-8", "ignore")
            text = re.sub(r"(?is)<(script|style|nav|footer|header).*?</\1>", " ", text)
            text = re.sub(r"(?s)<br\s*/?>|</p>|</h\d>|</li>", "\n\n", text)
            text = re.sub(r"(?s)<[^>]+>", " ", text)
            import html as _html

            data = ("Source: " + url + "\n\n" + _html.unescape(text)).encode("utf-8")
            name = "web_page.txt"
        else:
            return "unsupported_type"
        with get_db().tx() as c:
            f = store_upload(c, p["id"], user["id"], name, data, "report")
            c.execute("UPDATE research_sources SET file_id = ? WHERE id = ?", [f["id"], row["id"]])
        process_file(f["id"])
        return "fetched"
    except ApiError:
        return "blocked"
    except Exception:
        return "failed"


async def delete_url(request: Request):
    user = current_user(request)
    sid = parse_uuid(request.path_params["sid"], "source")
    with get_db().tx() as c:
        p = require_project(c, user, request.path_params["pid"], "editor")
        c.execute("DELETE FROM research_sources WHERE id = ? AND project_id = ?", [sid, p["id"]])
    return json_response({"ok": True})


async def create_demo(request: Request):
    """Create the fictional Consumer Health demo project for the current user."""
    from ..services.ingestion import process_file, store_upload
    from ..services.jobs import submit

    user = current_user(request)
    if not DEMO_DIR.is_dir():
        raise ApiError(500, "Demo files are missing.", "sample-data/consumer-health was not found.",
                       "Run: python scripts/generate_sample_data.py")
    body = ProjectIn(name="Consumer Health Market Analysis (demo)", description="Fictional demo project – all data is invented.",
                     industry="Consumer health", geography="United Kingdom", time_period="2023–2025",
                     business_objective="Identify growth opportunities and assess whether the proposed 2026 strategy is supported by the data.",
                     focus_entity="Verdant Health", learning_objectives=DEMO_OBJECTIVES)
    ids = []
    with get_db().tx() as c:
        p = _create(c, request, user, body, is_demo=True)
        for f in sorted(DEMO_DIR.iterdir()):
            if f.is_file() and f.name != "README.md":
                ids.append(store_upload(c, p["id"], user["id"], f.name, f.read_bytes())["id"])
    for fid in ids:
        submit(process_file, fid)
    return json_response({"project": public_project(p), "file_ids": ids}, 201)


routes = [
    Route("/api/projects", list_projects, methods=["GET"]),
    Route("/api/projects", create_project, methods=["POST"]),
    Route("/api/projects/demo", create_demo, methods=["POST"]),
    Route("/api/projects/{pid}", get_project, methods=["GET"]),
    Route("/api/projects/{pid}", update_project, methods=["PATCH"]),
    Route("/api/projects/{pid}", delete_project, methods=["DELETE"]),
    Route("/api/projects/{pid}/urls", add_url, methods=["POST"]),
    Route("/api/projects/{pid}/urls/{sid}", delete_url, methods=["DELETE"]),
]
