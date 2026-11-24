"""Account & session endpoints."""
from __future__ import annotations

import os

from pydantic import BaseModel, Field, field_validator
from starlette.requests import Request
from starlette.routing import Route

from ..auth.providers import AUTH_PROVIDERS, get_email_provider
from ..config import settings
from ..db import get_db, new_id, now
from ..security import hash_password, hash_token, login_limiter, new_token, password_problems, register_limiter, reset_limiter
from .common import SESSION_COOKIE, ApiError, audit, current_user, json_response, parse_body, utc_in

import re

_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[A-Za-z]{2,}$")


class EmailField(BaseModel):
    email: str = Field(max_length=320)

    @field_validator("email")
    @classmethod
    def _valid(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL_RE.match(v):
            raise ValueError("Enter a valid e-mail address")
        return v


class RegisterIn(EmailField):
    password: str = Field(max_length=200)
    display_name: str = Field(default="", max_length=200)


class LoginIn(EmailField):
    password: str = Field(max_length=200)


class ResetConfirmIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    password: str = Field(max_length=200)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(max_length=200)
    new_password: str = Field(max_length=200)


class TokenIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)


def public_user(u: dict) -> dict:
    return {"id": u["id"], "email": u["email"], "display_name": u.get("display_name") or "",
            "is_admin": (u.get("email") or "").lower() in settings.admin_emails}


def _start_session(c, request: Request, user: dict):
    token = new_token()
    c.insert(
        "sessions",
        {
            "id": new_id(),
            "user_id": user["id"],
            "token_hash": hash_token(token),
            "user_agent": (request.headers.get("user-agent") or "")[:500],
            "created_at": now(),
            "expires_at": utc_in(days=settings.session_days),
        },
    )
    c.execute("UPDATE users SET last_login_at = ? WHERE id = ?", [now(), user["id"]])
    return token


def _with_cookie(resp, token: str):
    resp.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_days * 86400,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return resp


async def register(request: Request):
    body = await parse_body(request, RegisterIn)
    ip = request.client.host if request.client else "?"
    if not register_limiter.allow(ip):
        raise ApiError(429, "Too many accounts created from this connection.", "", "Try again later.")
    if not settings.registration_open:
        with get_db().tx() as c:
            has_users = c.fetchone("SELECT COUNT(*) AS n FROM users")["n"] > 0
        if has_users and body.email not in settings.admin_emails:
            raise ApiError(403, "New registrations are closed.", "The owner has limited this site to invited users.",
                           "Ask the site owner to open registration.")
    problems = password_problems(body.password)
    if problems:
        raise ApiError(422, "Please choose a stronger password.", " ".join(problems), "Try a longer password with letters and numbers.")
    with get_db().tx() as c:
        if c.fetchone("SELECT id FROM users WHERE email = ?", [body.email]):
            raise ApiError(409, "An account with this e-mail already exists.", "", "Sign in instead, or reset your password.")
        user = c.insert(
            "users",
            {
                "id": new_id(),
                "email": body.email,
                "display_name": body.display_name.strip() or body.email.split("@")[0],
                "password_hash": hash_password(body.password),
                "auth_provider": "password",
                "is_active": True,
                "email_verified": False,
                "settings_json": {},
                "created_at": now(),
                "updated_at": now(),
            },
        )
        token = _start_session(c, request, user)
        audit(c, request, user["id"], "user.register")
    return _with_cookie(json_response({"user": public_user(user)}, 201), token)


async def login(request: Request):
    body = await parse_body(request, LoginIn)
    ip = request.client.host if request.client else "?"
    if not login_limiter.allow(f"{ip}:{body.email}"):
        raise ApiError(429, "Too many sign-in attempts.", "For your security sign-in is paused for a few minutes.", "Wait 5 minutes and try again.")
    with get_db().tx() as c:
        user = AUTH_PROVIDERS["password"].authenticate(c, {"email": body.email, "password": body.password})
        if not user:
            audit(c, request, None, "user.login_failed", details={"email": body.email})
            raise ApiError(401, "The e-mail or password is incorrect.", "", "Check them and try again, or reset your password.")
        token = _start_session(c, request, user)
        audit(c, request, user["id"], "user.login")
    return _with_cookie(json_response({"user": public_user(user)}), token)


async def logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        with get_db().tx() as c:
            c.execute("UPDATE sessions SET revoked_at = ? WHERE token_hash = ?", [now(), hash_token(token)])
    resp = json_response({"ok": True})
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


async def me(request: Request):
    user = current_user(request, required=False)
    return json_response({"user": public_user(user) if user else None})


async def request_password_reset(request: Request):
    body = await parse_body(request, EmailField)
    ip = request.client.host if request.client else "?"
    if not reset_limiter.allow(ip):
        raise ApiError(429, "Too many reset requests.", "", "Wait 15 minutes and try again.")
    with get_db().tx() as c:
        user = c.fetchone("SELECT * FROM users WHERE email = ? AND is_active = ?", [body.email, True])
        if user:
            token = new_token()
            c.insert(
                "auth_tokens",
                {"id": new_id(), "user_id": user["id"], "purpose": "password_reset", "token_hash": hash_token(token),
                 "created_at": now(), "expires_at": utc_in(hours=1)},
            )
            link = f"{settings.app_base_url.rstrip('/')}/#/reset-password?token={token}"
            try:
                get_email_provider().send(
                    user["email"],
                    "Reset your password",
                    f"Someone asked to reset the password for this account.\n\nOpen this link within 1 hour to choose a new password:\n{link}\n\nIf this was not you, ignore this e-mail.",
                )
            except Exception:
                pass  # logged by the provider; the response stays the same (no account enumeration)
            audit(c, request, user["id"], "user.password_reset_requested")
    # Always the same answer, so the endpoint cannot be used to discover accounts.
    return json_response({"ok": True, "message": "If an account exists for that e-mail, a reset link has been sent."})


async def confirm_password_reset(request: Request):
    body = await parse_body(request, ResetConfirmIn)
    problems = password_problems(body.password)
    if problems:
        raise ApiError(422, "Please choose a stronger password.", " ".join(problems))
    with get_db().tx() as c:
        row = c.fetchone(
            "SELECT * FROM auth_tokens WHERE token_hash = ? AND purpose = 'password_reset' AND used_at IS NULL AND expires_at > ?",
            [hash_token(body.token), now()],
        )
        if not row:
            raise ApiError(400, "This reset link is invalid or has expired.", "Links work once and expire after 1 hour.", "Request a new reset link.")
        c.execute("UPDATE auth_tokens SET used_at = ? WHERE id = ?", [now(), row["id"]])
        c.execute("UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?", [hash_password(body.password), now(), row["user_id"]])
        c.execute("UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL", [now(), row["user_id"]])
        audit(c, request, row["user_id"], "user.password_reset")
    return json_response({"ok": True})


async def change_password(request: Request):
    user = current_user(request)
    body = await parse_body(request, ChangePasswordIn)
    problems = password_problems(body.new_password)
    if problems:
        raise ApiError(422, "Please choose a stronger password.", " ".join(problems))
    with get_db().tx() as c:
        if not AUTH_PROVIDERS["password"].authenticate(c, {"email": user["email"], "password": body.current_password}):
            raise ApiError(400, "Your current password is incorrect.")
        c.execute("UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?", [hash_password(body.new_password), now(), user["id"]])
        c.execute("UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND id <> ? AND revoked_at IS NULL", [now(), user["id"], user["session_id"]])
        audit(c, request, user["id"], "user.password_changed")
    return json_response({"ok": True})


def _magic_enabled() -> bool:
    return os.getenv("MAGIC_LINK_ENABLED", "false").lower() in {"1", "true", "yes"}


async def request_magic_link(request: Request):
    if not _magic_enabled():
        raise ApiError(404, "Magic-link sign-in is not enabled.", "", "Use your e-mail and password.")
    body = await parse_body(request, EmailField)
    with get_db().tx() as c:
        user = c.fetchone("SELECT * FROM users WHERE email = ? AND is_active = ?", [body.email, True])
        if user:
            token = new_token()
            c.insert("auth_tokens", {"id": new_id(), "user_id": user["id"], "purpose": "magic_link",
                                     "token_hash": hash_token(token), "created_at": now(), "expires_at": utc_in(hours=0.25)})
            get_email_provider().send(user["email"], "Your sign-in link",
                                      f"Sign in: {settings.app_base_url.rstrip('/')}/#/magic?token={token}\nThe link expires in 15 minutes.")
    return json_response({"ok": True})


async def consume_magic_link(request: Request):
    if not _magic_enabled():
        raise ApiError(404, "Magic-link sign-in is not enabled.")
    body = await parse_body(request, TokenIn)
    with get_db().tx() as c:
        user = AUTH_PROVIDERS["magic_link"].authenticate(c, {"token": body.token})
        if not user:
            raise ApiError(400, "This sign-in link is invalid or has expired.", "", "Request a new link.")
        token = _start_session(c, request, user)
    return _with_cookie(json_response({"user": public_user(user)}), token)


async def delete_account(request: Request):
    user = current_user(request)
    body = await parse_body(request, LoginIn)
    from ..storage import get_storage

    with get_db().tx() as c:
        if body.email != user["email"] or not AUTH_PROVIDERS["password"].authenticate(c, {"email": body.email, "password": body.password}):
            raise ApiError(400, "Confirmation failed.", "Enter your own e-mail and current password to delete the account.")
        owned = c.fetchall("SELECT id FROM projects WHERE owner_id = ?", [user["id"]])
        audit(c, request, user["id"], "user.deleted")
        c.execute("DELETE FROM users WHERE id = ?", [user["id"]])
    for p in owned:
        get_storage().delete_prefix(f"projects/{p['id']}/")
    resp = json_response({"ok": True})
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


routes = [
    Route("/api/auth/register", register, methods=["POST"]),
    Route("/api/auth/login", login, methods=["POST"]),
    Route("/api/auth/logout", logout, methods=["POST"]),
    Route("/api/auth/me", me, methods=["GET"]),
    Route("/api/auth/password-reset/request", request_password_reset, methods=["POST"]),
    Route("/api/auth/password-reset/confirm", confirm_password_reset, methods=["POST"]),
    Route("/api/auth/change-password", change_password, methods=["POST"]),
    Route("/api/auth/magic-link/request", request_magic_link, methods=["POST"]),
    Route("/api/auth/magic-link/consume", consume_magic_link, methods=["POST"]),
    Route("/api/auth/delete-account", delete_account, methods=["POST"]),
]
