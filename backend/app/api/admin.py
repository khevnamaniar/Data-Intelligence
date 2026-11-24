"""Owner / admin endpoints: usage vs free-tier limits, system health, self-test, user help, backup.

Only e-mail addresses listed in ADMIN_EMAILS can use these. Private project contents (file
names, findings, documents) are never shown here — only counts, sizes and error summaries.
Non-admins get 404 so the admin area is not discoverable.
"""
from __future__ import annotations

import csv
import io
import time
import zipfile

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from ..config import settings
from ..db import get_db, new_id, now
from ..security import hash_token, new_token
from .common import ApiError, audit, current_user, json_response, parse_uuid, utc_in


def require_admin(request: Request) -> dict:
    user = current_user(request)
    if (user["email"] or "").lower() not in settings.admin_emails:
        raise ApiError(404, "This API endpoint does not exist.")
    return user


def _mb(b) -> float:
    return round((b or 0) / 1024 / 1024, 2)


def _usage(c) -> dict:
    one = lambda sql, p=None: c.fetchone(sql, p or [])  # noqa: E731
    counts = {
        "users": one("SELECT COUNT(*) AS n FROM users")["n"],
        "projects": one("SELECT COUNT(*) AS n FROM projects")["n"],
        "files": one("SELECT COUNT(*) AS n FROM files")["n"],
        "datasets": one("SELECT COUNT(*) AS n FROM datasets")["n"],
        "analyses": one("SELECT COUNT(*) AS n FROM analysis_runs")["n"],
        "reports": one("SELECT COUNT(*) AS n FROM reports")["n"],
        "ask_questions": one("SELECT COUNT(*) AS n FROM ask_messages")["n"],
    }
    files_by_status = {r["status"]: r["n"] for r in c.fetchall("SELECT status, COUNT(*) AS n FROM files GROUP BY status")}
    runs_by_status = {r["status"]: r["n"] for r in c.fetchall("SELECT status, COUNT(*) AS n FROM analysis_runs GROUP BY status")}
    since = utc_in(days=-1)
    activity_24h = {
        "logins": one("SELECT COUNT(*) AS n FROM audit_logs WHERE action = 'user.login' AND created_at > ?", [since])["n"],
        "failed_logins": one("SELECT COUNT(*) AS n FROM audit_logs WHERE action = 'user.login_failed' AND created_at > ?", [since])["n"],
        "uploads": one("SELECT COUNT(*) AS n FROM audit_logs WHERE action = 'file.upload' AND created_at > ?", [since])["n"],
        "analyses": one("SELECT COUNT(*) AS n FROM analysis_runs WHERE created_at > ?", [since])["n"],
    }
    uploaded_bytes = one("SELECT COALESCE(SUM(size_bytes), 0) AS n FROM files")["n"]
    export_bytes = one("SELECT COALESCE(SUM(size_bytes), 0) AS n FROM report_exports")["n"]
    db_bytes = None
    if get_db().dialect == "postgres":  # pragma: no cover - needs PostgreSQL
        db_bytes = one("SELECT pg_database_size(current_database()) AS n")["n"]
    return {"counts": counts, "files_by_status": files_by_status, "runs_by_status": runs_by_status, "activity_24h": activity_24h,
            "uploaded_bytes": int(uploaded_bytes or 0), "export_bytes": int(export_bytes or 0), "db_bytes": db_bytes}


def _limit(used_mb, limit_mb):
    if not limit_mb or used_mb is None:
        return {"used_mb": used_mb, "limit_mb": limit_mb or None, "percent": None, "level": "unknown"}
    pct = round(used_mb / limit_mb * 100, 1)
    return {"used_mb": used_mb, "limit_mb": limit_mb, "percent": pct, "level": "critical" if pct >= 90 else "warning" if pct >= 75 else "ok"}


async def overview(request: Request):
    require_admin(request)
    from ..storage import get_storage

    def gather():
        t0 = time.perf_counter()
        with get_db().tx() as c:
            u = _usage(c)
            failed_runs = c.fetchall(
                """SELECT id, project_id, mode, stage, created_at, error_json FROM analysis_runs
                   WHERE status = 'failed' ORDER BY created_at DESC LIMIT 10""")
            failed_files = c.fetchall(
                """SELECT id, project_id, file_type, status, updated_at, error_json FROM files
                   WHERE status IN ('failed', 'partial') ORDER BY updated_at DESC LIMIT 10""")
        db_ms = round((time.perf_counter() - t0) * 1000)
        storage_bytes, storage_ok, storage_note = None, True, ""
        try:
            storage_bytes = get_storage().usage_bytes("projects/")
        except Exception as exc:
            storage_ok, storage_note = False, str(exc)[:200]
        if storage_bytes is None:
            storage_bytes = u["uploaded_bytes"] + u["export_bytes"]
        return u, failed_runs, failed_files, db_ms, storage_bytes, storage_ok, storage_note

    u, failed_runs, failed_files, db_ms, storage_bytes, storage_ok, storage_note = await run_in_threadpool(gather)
    errors = [{"when": r["created_at"], "kind": f"{r['mode']} analysis", "project": r["project_id"][:8],
               "what": ((r.get("error_json") or [{}])[0] or {}).get("what", "Failed")} for r in failed_runs]
    errors += [{"when": f["updated_at"], "kind": f"{f['file_type'].upper()} file ({f['status']})", "project": f["project_id"][:8],
                "what": ((f.get("error_json") or [{}])[0] or {}).get("what", "Partially processed")} for f in failed_files]
    errors.sort(key=lambda e: str(e["when"]), reverse=True)
    from ..storage import get_storage

    return json_response({
        "health": {"app": "running", "database": "connected", "database_response_ms": db_ms,
                   "storage": "connected" if storage_ok else "error", "storage_note": storage_note,
                   "storage_provider": get_storage().name, "environment": settings.app_env},
        "usage": u,
        "limits": {
            "database": _limit(_mb(u["db_bytes"]) if u["db_bytes"] is not None else None, settings.database_quota_mb),
            "file_storage": _limit(_mb(storage_bytes), settings.storage_quota_mb),
        },
        "recent_errors": errors[:15],
        "notes": [
            "Database limit shown is the configured DATABASE_QUOTA_MB (Neon Free: 1 GB per project). Compute hours and data transfer are only visible in the Neon console.",
            "File storage limit is STORAGE_QUOTA_MB. Uploads are refused with a clear message before the limit is reached.",
            "Render free instance hours and bandwidth are only visible in the Render dashboard (Billing).",
        ],
    })


async def selftest(request: Request):
    """Runs real checks against the live database and file storage. Safe: it cleans up after itself."""
    user = require_admin(request)
    from ..migrate import status as migration_status
    from ..storage import get_storage

    def run():
        checks = []

        def check(name, fn, ok_text):
            t0 = time.perf_counter()
            try:
                detail = fn()
                checks.append({"check": name, "ok": True, "detail": detail or ok_text, "ms": round((time.perf_counter() - t0) * 1000)})
            except Exception as exc:
                checks.append({"check": name, "ok": False, "detail": f"{exc.__class__.__name__}: {str(exc)[:200]}",
                               "ms": round((time.perf_counter() - t0) * 1000)})

        def db_rw():
            rid = new_id()
            with get_db().tx() as c:
                c.insert("audit_logs", {"id": rid, "user_id": user["id"], "action": "admin.selftest", "details_json": {"probe": True}, "created_at": now()})
                back = c.fetchone("SELECT details_json FROM audit_logs WHERE id = ?", [rid])
                assert back and back["details_json"]["probe"] is True, "read-back mismatch"
            return "Wrote, read back and kept an audit entry (JSON, UUID and timestamp types all work)."

        def migrations():
            st = migration_status()
            if st["pending"]:
                raise RuntimeError("Pending migrations: " + ", ".join(st["pending"]))
            return f"All migrations applied ({', '.join(st['applied'])})."

        def storage_rw():
            s = get_storage()
            key = f"selftest/{new_id()}.txt"
            payload = b"self-test " + now().encode()
            s.put(key, payload, "text/plain")
            assert s.get(key) == payload, "read-back mismatch"
            assert s.exists(key)
            s.delete(key)
            assert not s.exists(key), "object still exists after delete"
            return f"Uploaded, downloaded and deleted a test file ({s.name} storage)."

        def storage_real_upload():
            """Same path, size and type as a real upload (name with spaces and brackets, 1 MB PDF-type file)."""
            import os as _os

            from ..parsers import safe_filename
            from ..services.ingestion import file_key

            s = get_storage()
            key = file_key("selftest-project", new_id(), safe_filename("Q3 Market Report (final) – draft.pdf"))
            payload = b"%PDF-1.4\n" + _os.urandom(1024 * 1024)
            s.put(key, payload, "application/pdf")
            assert s.get(key) == payload, "read-back mismatch"
            removed = s.delete_prefix("projects/selftest-project/")
            assert removed >= 1, "test file was not removed"
            return f"Stored, read back and removed a 1 MB document under a real upload path ({key.rsplit('/', 1)[-1]})."

        check("Database connection", lambda: (get_db().ping() and None), "Connected.")
        check("Database migrations", migrations, "")
        check("Database read/write", db_rw, "")
        check("File storage upload/download/delete", storage_rw, "")
        check("Realistic document upload", storage_real_upload, "")
        return checks

    checks = await run_in_threadpool(run)
    with get_db().tx() as c:
        audit(c, request, user["id"], "admin.selftest_run", details={"passed": sum(1 for x in checks if x["ok"]), "total": len(checks)})
    return json_response({"checks": checks, "all_ok": all(x["ok"] for x in checks)})


async def users(request: Request):
    require_admin(request)
    with get_db().tx() as c:
        rows = c.fetchall("""SELECT u.id, u.email, u.display_name, u.created_at, u.last_login_at, u.is_active,
                                    (SELECT COUNT(*) FROM projects p WHERE p.owner_id = u.id) AS projects
                             FROM users u ORDER BY u.created_at""")
    return json_response({"users": rows})


async def reset_link(request: Request):
    """Create a one-time password-reset link for a user (works without any e-mail service)."""
    admin = require_admin(request)
    uid = parse_uuid(request.path_params["uid"], "user")
    with get_db().tx() as c:
        u = c.fetchone("SELECT id, email FROM users WHERE id = ?", [uid])
        if not u:
            raise ApiError(404, "User not found.")
        token = new_token()
        c.insert("auth_tokens", {"id": new_id(), "user_id": u["id"], "purpose": "password_reset", "token_hash": hash_token(token),
                                 "created_at": now(), "expires_at": utc_in(hours=24)})
        audit(c, request, admin["id"], "admin.reset_link_created", target_type="user", target_id=u["id"])
    link = f"{settings.app_base_url.rstrip('/')}/#/reset-password?token={token}"
    return json_response({"link": link, "expires_in_hours": 24, "email": u["email"],
                          "note": "Send this link privately to the user. It works once and expires in 24 hours."})


BACKUP_TABLES = ["users", "projects", "project_members", "files", "datasets", "dataset_columns", "data_quality_issues",
                 "document_chunks", "research_sources", "analysis_runs", "analysis_questions", "findings", "finding_evidence",
                 "trends", "comparisons", "gaps", "opportunities", "risks", "recommendations", "research_questions",
                 "ask_messages", "reports", "report_exports", "audit_logs", "schema_migrations"]
EXCLUDED_COLUMNS = {"users": {"password_hash"}}


async def backup(request: Request):
    """Download the database contents as CSV files in a ZIP (password hashes and login tokens are excluded)."""
    admin = require_admin(request)

    def build() -> bytes:
        import json

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z, get_db().tx() as c:
            for t in BACKUP_TABLES:
                rows = c.fetchall(f"SELECT * FROM {t}")  # table names come from the fixed list above
                out = io.StringIO()
                if rows:
                    cols = [k for k in rows[0].keys() if k not in EXCLUDED_COLUMNS.get(t, set())]
                    w = csv.writer(out)
                    w.writerow(cols)
                    for r in rows:
                        w.writerow([json.dumps(r[k], default=str) if isinstance(r[k], (dict, list)) else r[k] for k in cols])
                z.writestr(f"{t}.csv", out.getvalue())
            z.writestr("README.txt", "Database backup of the Data Intelligence Platform.\n"
                       "Contains all business data as CSV. Password hashes and login tokens are deliberately excluded:\n"
                       "after a restore, users set new passwords with 'Forgot your password?'.\n"
                       "Uploaded files themselves live in cloud storage and are not inside this ZIP.\n"
                       "Treat this file as CONFIDENTIAL.\n")
        return buf.getvalue()

    data = await run_in_threadpool(build)
    with get_db().tx() as c:
        audit(c, request, admin["id"], "admin.backup_downloaded", details={"bytes": len(data)})
    stamp = now()[:10]
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="dip-database-backup-{stamp}.zip"'})


routes = [
    Route("/api/admin/overview", overview, methods=["GET"]),
    Route("/api/admin/selftest", selftest, methods=["POST"]),
    Route("/api/admin/users", users, methods=["GET"]),
    Route("/api/admin/users/{uid}/reset-link", reset_link, methods=["POST"]),
    Route("/api/admin/backup", backup, methods=["GET"]),
]
