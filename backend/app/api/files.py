"""File upload, listing, details, preview, download, re-processing and deletion."""
from __future__ import annotations

import urllib.parse

from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from ..analytics.capabilities import detect_capabilities, suggested_questions
from ..analytics.loader import load_project_data
from ..config import settings
from ..db import get_db, now
from ..parsers import ParserError
from ..services.ingestion import ROLE_CHOICES, load_dataset_frame, process_file, store_upload
from ..services.jobs import submit
from ..storage import get_storage
from ..security import upload_limiter
from .common import ApiError, audit, current_user, json_response, parse_body, parse_uuid, require_project


def storage_used_bytes(c) -> int:
    a = c.fetchone("SELECT COALESCE(SUM(size_bytes), 0) AS n FROM files")["n"]
    b = c.fetchone("SELECT COALESCE(SUM(size_bytes), 0) AS n FROM report_exports")["n"]
    return int(a or 0) + int(b or 0)


def check_quota(c, incoming: int) -> None:
    """Refuse uploads that would exceed STORAGE_QUOTA_MB (keeps the site inside its free storage allowance)."""
    if not settings.storage_quota_mb:
        return
    limit = settings.storage_quota_mb * 1024 * 1024
    used = storage_used_bytes(c)
    if used + incoming > limit:
        free_mb = max(0, (limit - used) / 1024 / 1024)
        raise ApiError(507, "This upload exceeds the current free storage limit.",
                       f"About {free_mb:.0f} MB of the {settings.storage_quota_mb} MB storage allowance is left.",
                       "Upload a smaller file, or delete older files or projects to free space.", code="storage_quota")


class FilePatch(BaseModel):
    source_role: str | None = Field(default=None, max_length=40)
    period_label: str | None = Field(default=None, max_length=100)


async def upload_files(request: Request):
    user = current_user(request)
    length = int(request.headers.get("content-length") or 0)
    limit = settings.max_upload_mb * 1024 * 1024 * settings.max_files_per_upload
    if length > limit:
        raise ApiError(413, "The upload is too large.", f"Limits: {settings.max_upload_mb} MB per file, {settings.max_files_per_upload} files per upload.",
                       "Upload fewer or smaller files at a time.")
    with get_db().tx() as c:
        p = require_project(c, user, request.path_params["pid"], "editor")
        check_quota(c, length)
    if not upload_limiter.allow(user["id"]):
        raise ApiError(429, "Too many uploads in a short time.", "Uploads are limited to protect the free hosting allowance.",
                       "Wait a while and try again.")
    try:
        form = await request.form(max_files=settings.max_files_per_upload, max_fields=200,
                                  max_part_size=settings.max_upload_mb * 1024 * 1024)
    except Exception as exc:
        raise ApiError(400, "The upload could not be read.", str(exc)[:200], "Try again with fewer files.")
    uploads = form.getlist("files")
    roles = form.getlist("roles")
    periods = form.getlist("periods")
    if not uploads:
        raise ApiError(400, "No files were received.", "", "Choose one or more files and try again.")
    accepted, rejected = [], []
    for i, up in enumerate(uploads):
        if not hasattr(up, "read"):
            continue
        data = await up.read()
        name = up.filename or "file"
        role = roles[i] if i < len(roles) else "auto"
        period = periods[i] if i < len(periods) else ""
        try:
            with get_db().tx() as c:
                check_quota(c, len(data))
                row = store_upload(c, p["id"], user["id"], name, data, str(role), str(period))
                audit(c, request, user["id"], "file.upload", p["id"], "file", row["id"], {"name": name, "size": len(data)})
            accepted.append({"id": row["id"], "name": name, "type": row["file_type"], "role": row["source_role"]})
        except ParserError as exc:
            rejected.append({"name": name, "what": exc.what, "why": exc.why, "next": exc.next_step})
        except ApiError as exc:
            rejected.append({"name": name, "what": exc.payload["what"], "why": exc.payload["why"], "next": exc.payload["next"]})
    for a in accepted:
        submit(process_file, a["id"])
    with get_db().tx() as c:
        c.execute("UPDATE projects SET updated_at = ? WHERE id = ?", [now(), p["id"]])
    return json_response({"accepted": accepted, "rejected": rejected}, 201 if accepted else 400)


async def list_files(request: Request):
    user = current_user(request)
    with get_db().tx() as c:
        p = require_project(c, user, request.path_params["pid"])
        files = c.fetchall("SELECT id, original_name, file_type, size_bytes, source_role, period_label, status, extraction_json, error_json, created_at, updated_at FROM files WHERE project_id = ? ORDER BY created_at", [p["id"]])
    return json_response({"files": files, "roles": sorted(ROLE_CHOICES)})


def _file(c, user, request, min_role="viewer"):
    p = require_project(c, user, request.path_params["pid"], min_role)
    fid = parse_uuid(request.path_params["fid"], "file")
    f = c.fetchone("SELECT * FROM files WHERE id = ? AND project_id = ?", [fid, p["id"]])
    if not f:
        raise ApiError(404, "File not found.", "It may have been deleted.")
    return p, f


async def file_detail(request: Request):
    user = current_user(request)
    with get_db().tx() as c:
        p, f = _file(c, user, request)
        datasets = c.fetchall("SELECT id, name, origin, locator_json, row_count, column_count, is_approximate, transformations_json, profile_json FROM datasets WHERE file_id = ? ORDER BY created_at", [f["id"]])
        for d in datasets:
            d["columns"] = c.fetchall("SELECT position, original_name, normalized_name, data_type, semantic_role, unit, missing_count, distinct_count FROM dataset_columns WHERE dataset_id = ? ORDER BY position", [d["id"]])
            d.pop("profile_json", None)
        chunks = c.fetchall("SELECT id, seq, kind, heading, text, locator_json, is_approximate FROM document_chunks WHERE file_id = ? ORDER BY seq LIMIT 400", [f["id"]])
        issues = c.fetchall("SELECT * FROM data_quality_issues WHERE file_id = ? ORDER BY CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END", [f["id"]])
    f.pop("storage_key", None)
    return json_response({"file": f, "datasets": datasets, "chunks": chunks, "issues": issues})


async def patch_file(request: Request):
    user = current_user(request)
    body = await parse_body(request, FilePatch)
    with get_db().tx() as c:
        p, f = _file(c, user, request, "editor")
        vals = {}
        if body.source_role is not None:
            if body.source_role not in ROLE_CHOICES:
                raise ApiError(422, "Unknown file role.", "Choose one of the listed roles.")
            vals["source_role"] = body.source_role
        if body.period_label is not None:
            vals["period_label"] = body.period_label
        if vals:
            vals["updated_at"] = now()
            c.update("files", f["id"], vals)
    return json_response({"ok": True})


async def delete_file(request: Request):
    user = current_user(request)
    with get_db().tx() as c:
        p, f = _file(c, user, request, "editor")
        audit(c, request, user["id"], "file.delete", p["id"], "file", f["id"], {"name": f["original_name"]})
        c.execute("DELETE FROM files WHERE id = ?", [f["id"]])
    get_storage().delete_prefix(f"projects/{p['id']}/files/{f['id']}/")
    return json_response({"ok": True})


async def download_file(request: Request):
    user = current_user(request)
    with get_db().tx() as c:
        p, f = _file(c, user, request)
        audit(c, request, user["id"], "file.download", p["id"], "file", f["id"])
    data = get_storage().get(f["storage_key"])
    quoted = urllib.parse.quote(f["safe_name"])
    return Response(data, media_type="application/octet-stream",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}", "X-Content-Type-Options": "nosniff"})


async def reprocess_file(request: Request):
    user = current_user(request)
    with get_db().tx() as c:
        p, f = _file(c, user, request, "editor")
        c.execute("UPDATE files SET status = 'uploaded', updated_at = ? WHERE id = ?", [now(), f["id"]])
    submit(process_file, f["id"])
    return json_response({"ok": True})


async def dataset_preview(request: Request):
    user = current_user(request)
    did = parse_uuid(request.path_params["did"], "dataset")
    try:
        offset = max(0, int(request.query_params.get("offset", 0)))
        limit = min(200, max(1, int(request.query_params.get("limit", 50))))
    except ValueError:
        offset, limit = 0, 50
    with get_db().tx() as c:
        p = require_project(c, user, request.path_params["pid"])
        ds = c.fetchone("SELECT * FROM datasets WHERE id = ? AND project_id = ?", [did, p["id"]])
        if not ds:
            raise ApiError(404, "Dataset not found.")
        cols = c.fetchall("SELECT * FROM dataset_columns WHERE dataset_id = ? ORDER BY position", [did])
    df = load_dataset_frame(ds, cols)
    hl = request.query_params.get("rows", "")
    if hl:
        try:
            wanted = {int(x) for x in hl.split(",") if x.strip()}
            df = df[df["__row__"].isin(wanted)] if wanted else df
        except ValueError:
            pass
    page = df.iloc[offset: offset + limit]
    rows = []
    for rec in page.to_dict(orient="records"):
        rows.append({k: (v.isoformat()[:10] if hasattr(v, "isoformat") else v) for k, v in rec.items()})
    return json_response({"columns": ["__row__"] + [c["normalized_name"] for c in cols], "rows": rows, "total": int(len(df)),
                          "offset": offset, "name": ds["name"], "transformations": ds.get("transformations_json") or []})


async def chunk_detail(request: Request):
    user = current_user(request)
    cid = parse_uuid(request.path_params["cid"], "passage")
    with get_db().tx() as c:
        p = require_project(c, user, request.path_params["pid"])
        ch = c.fetchone("SELECT * FROM document_chunks WHERE id = ? AND project_id = ?", [cid, p["id"]])
        if not ch:
            raise ApiError(404, "Passage not found.")
        around = c.fetchall("SELECT id, seq, kind, text, locator_json FROM document_chunks WHERE file_id = ? AND seq BETWEEN ? AND ? ORDER BY seq",
                            [ch["file_id"], ch["seq"] - 2, ch["seq"] + 2])
        f = c.fetchone("SELECT id, original_name FROM files WHERE id = ?", [ch["file_id"]])
    return json_response({"chunk": ch, "context": around, "file": f})


async def quality_report(request: Request):
    user = current_user(request)
    with get_db().tx() as c:
        p = require_project(c, user, request.path_params["pid"])
        rows = c.fetchall("""SELECT q.*, f.original_name AS source FROM data_quality_issues q LEFT JOIN files f ON f.id = q.file_id
                             WHERE q.project_id = ? ORDER BY CASE q.severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, f.original_name""", [p["id"]])
    return json_response({"issues": rows})


async def capabilities(request: Request):
    user = current_user(request)
    with get_db().tx() as c:
        p = require_project(c, user, request.path_params["pid"])
        pdata = load_project_data(c, p["id"])
    caps = detect_capabilities(pdata)
    caps["suggested_questions"] = suggested_questions(pdata)
    caps["pending_files"] = 0
    with get_db().tx() as c:
        caps["pending_files"] = c.fetchone("SELECT COUNT(*) AS n FROM files WHERE project_id = ? AND status IN ('uploaded','processing')", [p["id"]])["n"]
    return json_response(caps)


routes = [
    Route("/api/projects/{pid}/files", upload_files, methods=["POST"]),
    Route("/api/projects/{pid}/files", list_files, methods=["GET"]),
    Route("/api/projects/{pid}/files/{fid}", file_detail, methods=["GET"]),
    Route("/api/projects/{pid}/files/{fid}", patch_file, methods=["PATCH"]),
    Route("/api/projects/{pid}/files/{fid}", delete_file, methods=["DELETE"]),
    Route("/api/projects/{pid}/files/{fid}/download", download_file, methods=["GET"]),
    Route("/api/projects/{pid}/files/{fid}/reprocess", reprocess_file, methods=["POST"]),
    Route("/api/projects/{pid}/datasets/{did}/preview", dataset_preview, methods=["GET"]),
    Route("/api/projects/{pid}/chunks/{cid}", chunk_detail, methods=["GET"]),
    Route("/api/projects/{pid}/quality", quality_report, methods=["GET"]),
    Route("/api/projects/{pid}/capabilities", capabilities, methods=["GET"]),
]
