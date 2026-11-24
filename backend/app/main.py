"""Application entry point.

Run locally (from the backend folder):  uvicorn app.main:app --reload --port 8000
"""
from __future__ import annotations

import contextlib
import logging
import os
from pathlib import Path

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route

from .api import admin, analysis, auth, files, projects
from .api.common import ApiError, current_user, error_response, json_response
from .config import settings
from .db import DatabaseUnavailable, describe_db_target, get_db, now
from .logging_setup import configure_logging, RequestLogMiddleware
from .storage import StorageError, StorageQuotaError
from .security import CSRFMiddleware, SecurityHeadersMiddleware

configure_logging()
log = logging.getLogger("dip")


async def health(request: Request):
    """Public health check: is the app running and can it reach the database? Reveals nothing sensitive."""
    from starlette.concurrency import run_in_threadpool

    try:
        await run_in_threadpool(get_db().ping)
        return JSONResponse({"status": "healthy", "database": "connected"}, headers={"Cache-Control": "no-store"})
    except Exception as exc:
        log.warning("Health check: database not reachable (%s)", exc.__class__.__name__)
        return JSONResponse({"status": "unhealthy", "database": "unavailable"}, status_code=503, headers={"Cache-Control": "no-store"})


async def system_info(request: Request):
    """What optional capabilities are available (shown on the Settings page)."""
    current_user(request)
    from .ai.provider import get_ai_provider
    from .parsers.image import tesseract_available
    from .storage import get_storage

    ai = get_ai_provider()
    return json_response({
        "ai": {"enabled": ai.enabled, "provider": ai.name, "note": "Optional. The platform works fully without AI."},
        "ocr": {"enabled": settings.ocr_enabled, "tesseract_installed": tesseract_available()},
        "storage": {"provider": get_storage().name},
        "database": {"dialect": get_db().dialect},
        "limits": {"max_upload_mb": settings.max_upload_mb, "max_files_per_upload": settings.max_files_per_upload},
        "url_fetch_enabled": settings.url_fetch_enabled, "environment": settings.app_env,
    })


async def api_not_found(request: Request):
    raise ApiError(404, "This API endpoint does not exist.")


class SPAStatic:
    """Serves the built frontend (frontend/dist) with index.html fallback."""

    def __init__(self, directory: str):
        self.dir = Path(directory).resolve()

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "/").lstrip("/")
        target = (self.dir / path).resolve() if path else self.dir / "index.html"
        if not str(target).startswith(str(self.dir)) or not target.is_file():
            target = self.dir / "index.html"
        if not target.is_file():
            resp = PlainTextResponse("Frontend not built. In development open http://localhost:5173 (npm run dev).", status_code=404)
        else:
            headers = {"Cache-Control": "public, max-age=31536000, immutable"} if "/assets/" in str(target) else {"Cache-Control": "no-cache"}
            resp = FileResponse(target, headers=headers)
        await resp(scope, receive, send)


async def on_api_error(request: Request, exc: ApiError):
    return error_response(exc)


async def on_http_error(request: Request, exc: HTTPException):
    return JSONResponse({"error": {"what": exc.detail or "Request failed.", "why": "", "next": ""}}, status_code=exc.status_code)


DB_QUOTA_WORDS = ("size limit", "project size", "quota", "disk full", "exceeded", "storage limit")


async def on_db_unavailable(request: Request, exc: DatabaseUnavailable):
    msg = str(exc).lower()
    log.error("Database problem on %s %s: %s", request.method, request.url.path, exc.__class__.__name__)
    if any(w in msg for w in DB_QUOTA_WORDS):
        info = {"what": "The database has reached its free-plan limit.",
                "why": "The hosting plan's database storage or monthly compute allowance is used up.",
                "next": "Delete old projects or analyses, or ask the administrator to check the Neon usage page.", "code": "db_quota"}
    else:
        info = {"what": "The application could not connect to the database.",
                "why": "The database may be waking up or temporarily unavailable.",
                "next": "Wait a few seconds and try again. If it keeps happening, contact the administrator.", "code": "db_unavailable"}
    return JSONResponse({"error": info}, status_code=503)


async def on_storage_error(request: Request, exc: StorageError):
    log.error("File storage problem on %s %s: %s", request.method, request.url.path, exc)
    if isinstance(exc, StorageQuotaError):
        info = {"what": "This file exceeds the current free storage limit.",
                "why": "The file storage allowance for this site is used up.",
                "next": "Upload a smaller file, or delete older files or projects to free space.", "code": "storage_quota"}
        return JSONResponse({"error": info}, status_code=507)
    info = {"what": "The file storage service could not complete the request.",
            "why": "The storage service is unavailable or not configured correctly.",
            "next": "Try again shortly. If it keeps happening, contact the administrator.", "code": "storage_unavailable"}
    return JSONResponse({"error": info}, status_code=503)


async def on_unexpected(request: Request, exc: Exception):
    if isinstance(exc, DatabaseUnavailable):
        return await on_db_unavailable(request, exc)
    if isinstance(exc, StorageError):
        return await on_storage_error(request, exc)
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse({"error": {"what": "Something went wrong on the server.", "why": "An unexpected error occurred and was logged.",
                                   "next": "Try again. If it keeps happening, contact the administrator."}}, status_code=500)


def _startup():
    log.info("Starting Data Intelligence Platform (environment: %s)", settings.app_env)
    problems = settings.validate()
    for p in problems:
        log.error("CONFIGURATION: %s", p)
    if settings.is_production and problems:
        raise RuntimeError("Invalid configuration (see the CONFIGURATION lines above): " + " ".join(problems))
    if not settings.database_url:
        return
    db = get_db()
    log.info("Database: %s", describe_db_target(settings.database_url) if db.dialect == "postgres" else db.dialect)
    try:
        db.ping()
        log.info("Database connection: OK")
    except Exception as exc:
        log.error("Database connection: FAILED (%s). Check DATABASE_URL.", exc.__class__.__name__)
        raise
    if os.getenv("AUTO_MIGRATE", "true").lower() in ("1", "true", "yes"):
        from .migrate import migrate

        applied = migrate(verbose=False)
        log.info("Database migrations: %s", f"applied {', '.join(applied)}" if applied else "already up to date")
    try:
        from .storage import get_storage

        log.info("File storage: %s", get_storage().name)
    except StorageError as exc:
        log.error("File storage: NOT CONFIGURED (%s)", exc)
        if settings.is_production:
            raise
    with db.tx() as c:  # jobs do not survive a restart: mark them clearly and resume file processing
        n = c.execute("""UPDATE analysis_runs SET status = 'failed', stage = 'Interrupted', completed_at = ?,
                     error_json = ? WHERE status IN ('queued', 'running')""",
                      [now(), '[{"what": "The analysis was interrupted.", "why": "The server restarted while it was running (free servers restart after inactivity).", "next": "Run the analysis again."}]'])
        if n:
            log.warning("Marked %s interrupted analysis run(s) as failed", n)
        c.execute("UPDATE files SET status = 'uploaded' WHERE status = 'processing'")
        pending = [r["id"] for r in c.fetchall("SELECT id FROM files WHERE status = 'uploaded' ORDER BY created_at")]
    if pending:
        from .services.ingestion import process_file
        from .services.jobs import submit

        log.info("Resuming processing of %s uploaded file(s)", len(pending))
        for fid in pending:
            submit(process_file, fid)
    log.info("Startup complete")


@contextlib.asynccontextmanager
async def lifespan(app):
    _startup()
    yield


def create_app() -> Starlette:
    routes = [Route("/health", health), Route("/api/health", health), Route("/api/system", system_info)]
    routes += auth.routes + projects.routes + files.routes + analysis.routes + admin.routes
    routes.append(Route("/api/{path:path}", api_not_found, methods=["GET", "POST", "PATCH", "DELETE", "PUT"]))
    routes.append(Mount("/", app=SPAStatic(settings.frontend_dist)))
    app = Starlette(
        routes=routes,
        middleware=[Middleware(RequestLogMiddleware), Middleware(SecurityHeadersMiddleware, hsts=settings.cookie_secure),
                    Middleware(CSRFMiddleware)],
        exception_handlers={ApiError: on_api_error, HTTPException: on_http_error, DatabaseUnavailable: on_db_unavailable,
                            StorageError: on_storage_error, Exception: on_unexpected},
        lifespan=lifespan,
    )
    return app


app = create_app()
