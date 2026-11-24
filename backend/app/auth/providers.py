"""Authentication & e-mail provider abstractions.

The analytical engine never imports anything from here: authentication is a
separate concern. New sign-in methods (e.g. magic links, SSO) are added by
implementing ``AuthProvider``; new e-mail services by implementing ``EmailProvider``.
"""
from __future__ import annotations

import logging
import os
import smtplib
from abc import ABC, abstractmethod
from email.message import EmailMessage

from ..db import Connection
from ..security import hash_token, verify_password

log = logging.getLogger("dip.auth")


class AuthProvider(ABC):
    name: str

    @abstractmethod
    def authenticate(self, c: Connection, credentials: dict) -> dict | None:
        """Return the user row when the credentials are valid, otherwise None."""


class PasswordAuthProvider(AuthProvider):
    name = "password"

    def authenticate(self, c, credentials):
        email = (credentials.get("email") or "").strip().lower()
        user = c.fetchone("SELECT * FROM users WHERE email = ?", [email])
        if not user or not user["is_active"]:
            # Still spend time hashing so response time does not reveal which e-mails exist.
            verify_password(credentials.get("password", ""), "scrypt$16384$8$1$AAAAAAAAAAAAAAAAAAAAAA==$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
            return None
        return user if verify_password(credentials.get("password", ""), user["password_hash"]) else None


class MagicLinkAuthProvider(AuthProvider):
    """Sign in with a single-use e-mailed token (enable with MAGIC_LINK_ENABLED=true)."""

    name = "magic_link"

    def authenticate(self, c, credentials):
        from ..db import now

        token = credentials.get("token") or ""
        row = c.fetchone(
            "SELECT * FROM auth_tokens WHERE token_hash = ? AND purpose = 'magic_link' AND used_at IS NULL AND expires_at > ?",
            [hash_token(token), now()],
        )
        if not row:
            return None
        c.execute("UPDATE auth_tokens SET used_at = ? WHERE id = ?", [now(), row["id"]])
        return c.fetchone("SELECT * FROM users WHERE id = ? AND is_active = ?", [row["user_id"], True])


AUTH_PROVIDERS: dict[str, AuthProvider] = {p.name: p for p in (PasswordAuthProvider(), MagicLinkAuthProvider())}


# ----------------------------------------------------------------- e-mail
class EmailProvider(ABC):
    @abstractmethod
    def send(self, to: str, subject: str, body: str) -> None: ...


class ConsoleEmailProvider(EmailProvider):
    """Used when no e-mail service is configured.

    Development: prints the e-mail (including reset links) in the backend terminal.
    Production: never prints the content — reset links are secrets. The owner can instead create a
    reset link from the Admin page.
    """

    def __init__(self):
        self.outbox: list[dict] = []

    def send(self, to, subject, body):
        from ..config import settings

        self.outbox.append({"to": to, "subject": subject, "body": body})
        self.outbox = self.outbox[-50:]
        if settings.is_production:
            log.warning("E-mail '%s' was NOT sent: no e-mail service is configured (EMAIL_PROVIDER=console). "
                        "Use Admin → Users → 'Create reset link', or configure Brevo.", subject)
            return
        log.warning("E-MAIL (console provider, not sent)\nTo: %s\nSubject: %s\n\n%s", to, subject, body)
        print(f"\n===== E-MAIL (not sent - console provider) =====\nTo: {to}\nSubject: {subject}\n\n{body}\n================================================\n")


class BrevoEmailProvider(EmailProvider):  # pragma: no cover - needs a Brevo account
    """Sends e-mail through Brevo's HTTPS API (free plan: 300 e-mails/day).

    HTTPS is used instead of SMTP because Render's free instances block outgoing SMTP ports.
    Settings: BREVO_API_KEY (secret), EMAIL_FROM (a sender address verified in Brevo).
    """

    URL = "https://api.brevo.com/v3/smtp/email"

    def send(self, to, subject, body):
        import json
        import urllib.request

        from ..config import settings

        payload = {"sender": {"email": settings.email_from, "name": "Data Intelligence Platform"}, "to": [{"email": to}],
                   "subject": subject, "textContent": body}
        req = urllib.request.Request(self.URL, data=json.dumps(payload).encode(), method="POST",
                                     headers={"api-key": settings.brevo_api_key, "Content-Type": "application/json",
                                              "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                log.info("E-mail '%s' sent via Brevo (status %s)", subject, resp.status)
        except Exception as exc:
            code = getattr(exc, "code", "")
            log.error("Brevo could not send e-mail '%s' (%s %s)", subject, exc.__class__.__name__, code)
            raise


class SMTPEmailProvider(EmailProvider):  # pragma: no cover - needs a mail server
    """Sends e-mail through any SMTP server (SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM)."""

    def send(self, to, subject, body):
        msg = EmailMessage()
        msg["From"] = os.getenv("SMTP_FROM", "no-reply@example.com")
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        with smtplib.SMTP(os.getenv("SMTP_HOST", "localhost"), int(os.getenv("SMTP_PORT", "587")), timeout=20) as s:
            s.starttls()
            if os.getenv("SMTP_USER"):
                s.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD", ""))
            s.send_message(msg)


_email: EmailProvider | None = None


def get_email_provider() -> EmailProvider:
    global _email
    if _email is None:
        from ..config import settings

        if settings.email_provider == "brevo":
            _email = BrevoEmailProvider()
        elif settings.email_provider == "smtp":
            _email = SMTPEmailProvider()
        else:
            _email = ConsoleEmailProvider()
    return _email
