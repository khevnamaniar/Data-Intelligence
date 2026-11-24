"""Test configuration.

Tests run against an in-memory SQLite database by default so they need no network.
To run the same tests against PostgreSQL (e.g. a Neon test branch) set
TEST_DATABASE_URL=postgresql://... before running pytest.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["DATABASE_URL"] = os.getenv("TEST_DATABASE_URL", "sqlite:///:memory:")
os.environ["ANALYSIS_SYNC"] = "true"
os.environ["AUTO_MIGRATE"] = "false"
os.environ["APP_ENV"] = "test"
os.environ["URL_FETCH_ENABLED"] = "false"
os.environ.setdefault("LOCAL_STORAGE_DIR", tempfile.mkdtemp(prefix="dip-test-storage-"))

import pytest  # noqa: E402

from app.db import Database, set_db  # noqa: E402
from app.migrate import migrate  # noqa: E402
from app.security import analysis_limiter, ask_limiter, login_limiter, register_limiter, reset_limiter, upload_limiter  # noqa: E402
from app.storage import LocalStorageProvider, set_storage  # noqa: E402

SAMPLE = ROOT.parent / "sample-data" / "consumer-health"


def _reset_postgres(db: Database) -> None:  # pragma: no cover - only with TEST_DATABASE_URL
    with db.tx() as c:
        c.execute("DROP SCHEMA public CASCADE")
        c.execute("CREATE SCHEMA public")


@pytest.fixture()
def db():
    url = os.environ["DATABASE_URL"]
    database = Database(url)
    if database.dialect == "postgres":
        _reset_postgres(database)
    set_db(database)
    migrate(database, verbose=False)
    yield database
    set_db(None)


@pytest.fixture()
def storage(tmp_path):
    s = LocalStorageProvider(str(tmp_path / "storage"))
    set_storage(s)
    yield s
    set_storage(None)


@pytest.fixture()
def app(db, storage):
    for lim in (login_limiter, reset_limiter, analysis_limiter, ask_limiter, upload_limiter, register_limiter):
        lim.reset()
    from app.main import create_app

    return create_app()


@pytest.fixture()
def client(app):
    from tests.asgi_client import Client

    return Client(app)


@pytest.fixture()
def make_client(app):
    from tests.asgi_client import Client

    def _make(csrf=True):
        return Client(app, csrf=csrf)

    return _make


PASSWORD = "CorrectHorse42battery"


def register(client, email="alice@example.com", password=PASSWORD):
    r = client.post("/api/auth/register", json={"email": email, "password": password, "display_name": email.split("@")[0]})
    assert r.status_code == 201, r.text
    return r.json()["user"]


def sample(name: str) -> bytes:
    if not SAMPLE.exists():
        import subprocess

        subprocess.run([sys.executable, str(ROOT.parent / "scripts" / "generate_sample_data.py")], check=True)
    return (SAMPLE / name).read_bytes()
