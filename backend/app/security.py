"""Password hashing, tokens, rate limiting and HTTP security middleware."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

# ----------------------------------------------------------------- passwords
# scrypt is a memory-hard key-derivation function from Python's standard library.
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N, _SCRYPT_R, _SCRYPT_P, base64.b64encode(salt).decode(), base64.b64encode(digest).decode()
    )


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        algo, n, r, p, salt_b64, digest_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected)
        )
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def password_problems(password: str) -> list[str]:
    problems = []
    if len(password) < 10:
        problems.append("Password must be at least 10 characters long.")
    if len(password) > 200:
        problems.append("Password must be at most 200 characters long.")
    if password.lower() == password or password.upper() == password:
        problems.append("Use a mix of upper- and lower-case letters.")
    if not any(ch.isdigit() for ch in password):
        problems.append("Include at least one number.")
    return problems


# ----------------------------------------------------------------- tokens
def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Only hashes of session/reset tokens are stored. With SECRET_KEY set the hash is keyed (HMAC),
    so a leaked database copy cannot be used to forge sessions."""
    from .config import settings

    if settings.secret_key:
        return hmac.new(settings.secret_key.encode(), token.encode("utf-8"), hashlib.sha256).hexdigest()
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ----------------------------------------------------------------- rate limiting
class RateLimiter:
    """Simple in-memory sliding-window limiter (per process)."""

    def __init__(self, limit: int, window_seconds: int):
        self.limit = limit
        self.window = window_seconds
        self.hits: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self.lock:
            q = self.hits[key]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True

    def reset(self) -> None:  # used by tests
        with self.lock:
            self.hits.clear()


login_limiter = RateLimiter(limit=10, window_seconds=300)
reset_limiter = RateLimiter(limit=5, window_seconds=900)
# protect free-tier resources from runaway use (per user)
analysis_limiter = RateLimiter(limit=30, window_seconds=3600)
ask_limiter = RateLimiter(limit=120, window_seconds=3600)
upload_limiter = RateLimiter(limit=60, window_seconds=3600)
register_limiter = RateLimiter(limit=10, window_seconds=3600)


# ----------------------------------------------------------------- middleware
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    ),
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, hsts: bool = False):
        super().__init__(app)
        self.hsts = hsts

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if self.hsts:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response


class CSRFMiddleware(BaseHTTPMiddleware):
    """Requires a custom header on state-changing API requests.

    Browsers cannot attach custom headers to cross-site requests without a CORS
    pre-flight, and this API does not allow cross-origin requests, so the header
    proves the request came from our own frontend. Combined with SameSite=Lax
    session cookies this protects against cross-site request forgery.
    """

    SAFE = {"GET", "HEAD", "OPTIONS"}

    async def dispatch(self, request: Request, call_next):
        if request.method not in self.SAFE and request.url.path.startswith("/api/"):
            if request.headers.get("x-requested-with", "").lower() != "dip":
                return JSONResponse(
                    {"error": {"what": "Request blocked", "why": "Missing anti-forgery header.", "next": "Reload the page and try again."}},
                    status_code=403,
                )
        return await call_next(request)
