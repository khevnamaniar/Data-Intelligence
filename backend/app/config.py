"""Application settings, read from environment variables (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # optional: load a local .env file during development
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except Exception:  # pragma: no cover - dotenv is optional
    pass


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = BACKEND_DIR.parent


@dataclass
class Settings:
    app_env: str = field(default_factory=lambda: os.getenv("APP_ENV", "development"))
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", ""))
    secret_key: str = field(default_factory=lambda: os.getenv("SECRET_KEY", ""))
    app_base_url: str = field(default_factory=lambda: os.getenv("APP_BASE_URL", "http://localhost:5173"))
    cookie_secure: bool = field(default_factory=lambda: _bool("COOKIE_SECURE", False))
    session_days: int = field(default_factory=lambda: _int("SESSION_DAYS", 7))

    storage_provider: str = field(default_factory=lambda: os.getenv("STORAGE_PROVIDER", "local"))
    local_storage_dir: str = field(
        default_factory=lambda: os.getenv("LOCAL_STORAGE_DIR", str(REPO_DIR / ".data" / "storage"))
    )
    max_upload_mb: int = field(default_factory=lambda: _int("MAX_UPLOAD_MB", 50))
    max_files_per_upload: int = field(default_factory=lambda: _int("MAX_FILES_PER_UPLOAD", 30))

    email_provider: str = field(default_factory=lambda: os.getenv("EMAIL_PROVIDER", "console"))
    ocr_enabled: bool = field(default_factory=lambda: _bool("OCR_ENABLED", True))
    url_fetch_enabled: bool = field(default_factory=lambda: _bool("URL_FETCH_ENABLED", True))

    ai_provider: str = field(default_factory=lambda: os.getenv("AI_PROVIDER", "none"))
    ai_base_url: str = field(default_factory=lambda: os.getenv("AI_BASE_URL", ""))
    ai_model: str = field(default_factory=lambda: os.getenv("AI_MODEL", ""))
    ai_api_key: str = field(default_factory=lambda: os.getenv("AI_API_KEY", ""))

    admin_emails: list = field(default_factory=lambda: [e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()])
    storage_quota_mb: int = field(default_factory=lambda: _int("STORAGE_QUOTA_MB", 0))
    database_quota_mb: int = field(default_factory=lambda: _int("DATABASE_QUOTA_MB", 1024))
    analysis_max_minutes: int = field(default_factory=lambda: _int("ANALYSIS_MAX_MINUTES", 20))
    email_from: str = field(default_factory=lambda: os.getenv("EMAIL_FROM", os.getenv("SMTP_FROM", "")))
    brevo_api_key: str = field(default_factory=lambda: os.getenv("BREVO_API_KEY", ""))
    registration_open: bool = field(default_factory=lambda: _bool("REGISTRATION_OPEN", True))
    allow_local_storage_in_production: bool = field(default_factory=lambda: _bool("ALLOW_LOCAL_STORAGE_IN_PRODUCTION", False))

    analysis_sync: bool = field(default_factory=lambda: _bool("ANALYSIS_SYNC", False))
    frontend_dist: str = field(
        default_factory=lambda: os.getenv("FRONTEND_DIST", str(REPO_DIR / "frontend" / "dist"))
    )

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    def validate(self) -> list[str]:
        """Return a list of plain-language configuration problems (no secret values are ever included)."""
        problems = []
        if not self.database_url:
            problems.append(
                "DATABASE_URL is not set. Copy .env.example to .env and paste your Neon connection string."
            )
        if self.is_production:
            if not self.database_url.startswith(("postgresql://", "postgres://")):
                problems.append("DATABASE_URL must be a PostgreSQL (Neon) connection string in production.")
            if not self.secret_key or len(self.secret_key) < 32:
                problems.append("SECRET_KEY must be set to a random value of at least 32 characters in production.")
            if self.storage_provider.lower() == "local" and not self.allow_local_storage_in_production:
                problems.append("STORAGE_PROVIDER is 'local' but the server's disk is temporary on Render. "
                                "Set STORAGE_PROVIDER=s3 and the storage settings (see docs/ENVIRONMENT_VARIABLES_GUIDE.md).")
            if not self.cookie_secure:
                problems.append("COOKIE_SECURE must be true in production (the site is served over https).")
        if self.email_provider == "brevo" and (not self.brevo_api_key or not self.email_from):
            problems.append("EMAIL_PROVIDER is 'brevo' but BREVO_API_KEY or EMAIL_FROM is missing.")
        return problems


settings = Settings()
