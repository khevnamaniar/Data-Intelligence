"""Production logging: readable one-line logs, request timing, and automatic redaction of secrets."""
from __future__ import annotations

import logging
import os
import re
import time

from starlette.middleware.base import BaseHTTPMiddleware

# Anything that looks like a credential is masked before a log line is written.
REDACT = [
    (re.compile(r"(postgres(?:ql)?://[^:/\s]+:)[^@\s]+@"), r"\1***@"),           # passwords in database URLs
    (re.compile(r"\b(nsk_live_|nak_live_|nt_live_|xkeysib-|sk-|sk_live_)[A-Za-z0-9_\-]{6,}"), r"\1***"),  # API keys
    (re.compile(r"(?i)\b(password|secret|token|api[-_]?key|authorization)(\s*[=:]\s*)\S+"), r"\1\2***"),
    (re.compile(r"(?i)(token=)[A-Za-z0-9_\-]{10,}"), r"\1***"),
]


def redact(text: str) -> str:
    for rx, repl in REDACT:
        text = rx.sub(repl, text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        clean = redact(msg)
        if clean != msg:
            record.msg, record.args = clean, ()
        if record.exc_info and record.exc_text is None:
            pass
        return True


def configure_logging() -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    root = logging.getLogger()
    if not getattr(root, "_dip_configured", False):
        logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        root._dip_configured = True  # type: ignore[attr-defined]
    f = RedactingFilter()
    for h in root.handlers:
        if not any(isinstance(x, RedactingFilter) for x in h.filters):
            h.addFilter(f)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        if not any(isinstance(x, RedactingFilter) for x in lg.filters):
            lg.addFilter(f)


log = logging.getLogger("dip.http")


class RequestLogMiddleware(BaseHTTPMiddleware):
    """One line per API request: method, path (no query string), status, duration. No bodies, no cookies."""

    async def dispatch(self, request, call_next):
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.error("%s %s -> error after %.0f ms", request.method, request.url.path, (time.perf_counter() - start) * 1000)
            raise
        path = request.url.path
        if path.startswith("/api/") and path != "/api/health":
            ms = (time.perf_counter() - start) * 1000
            lvl = logging.WARNING if response.status_code >= 500 or ms > 10000 else logging.INFO
            log.log(lvl, "%s %s -> %s (%.0f ms)", request.method, path, response.status_code, ms)
        return response
