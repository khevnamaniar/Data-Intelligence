"""Shared helpers for API routes: errors, JSON bodies, authentication and access checks."""
from __future__ import annotations

import datetime as dt
import json
import uuid
from typing import Any, Type, TypeVar

from pydantic import BaseModel, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ..db import Connection, clean_json, get_db, new_id, now
from ..security import hash_token

SESSION_COOKIE = "dip_session"
T = TypeVar("T", bound=BaseModel)


class ApiError(Exception):
    """An error explained in plain language: what happened, why, what still works, what to do next."""

    def __init__(self, status: int, what: str, why: str = "", next_step: str = "", can_still: str = "", code: str = ""):
        super().__init__(what)
        self.status = status
        self.payload = {"what": what, "why": why, "next": next_step, "can_still": can_still, "code": code}


def json_response(data: Any, status: int = 200) -> Response:
    return Response(
        json.dumps(clean_json(data), allow_nan=False, separators=(",", ":")),
        status_code=status,
        media_type="application/json",
    )


def error_response(err: ApiError) -> JSONResponse:
    return JSONResponse({"error": err.payload}, status_code=err.status)


async def parse_body(request: Request, model: Type[T]) -> T:
    try:
        raw = await request.body()
        data = json.loads(raw or b"{}")
    except ValueError:
        raise ApiError(400, "The request could not be read.", "The body was not valid JSON.", "Try again.")
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:5]
        )
        raise ApiError(422, "Some of the information entered is not valid.", details, "Correct the highlighted fields and try again.")


def parse_uuid(value: str, what: str = "item") -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError):
        raise ApiError(404, f"The requested {what} was not found.", "The link or identifier is not valid.")


# ----------------------------------------------------------------- auth
def current_user(request: Request, required: bool = True) -> dict | None:
    token = request.cookies.get(SESSION_COOKIE)
    user = None
    if token:
        with get_db().tx() as c:
            user = c.fetchone(
                """SELECT u.id, u.email, u.display_name, u.is_active, u.settings_json, s.id AS session_id
                   FROM sessions s JOIN users u ON u.id = s.user_id
                   WHERE s.token_hash = ? AND s.revoked_at IS NULL AND s.expires_at > ?""",
                [hash_token(token), now()],
            )
        if user and not user["is_active"]:
            user = None
    if required and not user:
        raise ApiError(401, "You need to sign in.", "Your session is missing or has expired.", "Sign in again.", code="auth_required")
    return user


ROLE_RANK = {"viewer": 1, "editor": 2, "owner": 3}


def require_project(c: Connection, user: dict, project_id: str, min_role: str = "viewer") -> dict:
    """Server-side project isolation: returns the project only if the user is a member."""
    project_id = parse_uuid(project_id, "project")
    row = c.fetchone(
        """SELECT p.*, m.role AS member_role FROM projects p
           JOIN project_members m ON m.project_id = p.id AND m.user_id = ?
           WHERE p.id = ?""",
        [user["id"], project_id],
    )
    # Same response for "does not exist" and "not yours" so IDs cannot be probed.
    if not row:
        raise ApiError(404, "Project not found.", "It does not exist or you do not have access to it.", "Return to your projects list.")
    if ROLE_RANK.get(row["member_role"], 0) < ROLE_RANK[min_role]:
        raise ApiError(403, "You do not have permission to do that.", f"This action needs the '{min_role}' role.")
    return row


def audit(c: Connection, request: Request | None, user_id: str | None, action: str, project_id: str | None = None,
          target_type: str = "", target_id: str = "", details: dict | None = None) -> None:
    ip = ""
    if request is not None and request.client:
        ip = request.client.host or ""
    c.insert(
        "audit_logs",
        {
            "id": new_id(),
            "user_id": user_id,
            "project_id": project_id,
            "action": action,
            "target_type": target_type,
            "target_id": target_id,
            "details_json": details or {},
            "ip_address": ip,
            "created_at": now(),
        },
    )


def utc_in(days: float = 0, hours: float = 0) -> str:
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=days, hours=hours)).isoformat()
