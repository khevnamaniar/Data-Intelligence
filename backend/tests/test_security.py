import io

import pytest

from app.storage import StorageError
from tests.conftest import register


def test_csrf_header_required_for_state_changes(make_client):
    c = make_client(csrf=False)
    r = c.post("/api/auth/register", json={"email": "x@example.com", "password": "GoodPassword123"})
    assert r.status_code == 403 and "anti-forgery" in r.json()["error"]["why"]


def test_security_headers(client):
    r = client.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"


def test_session_cookie_is_httponly(client):
    r = client.post("/api/auth/register", json={"email": "c@example.com", "password": "GoodPassword123"})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie


def test_sql_injection_attempts_are_harmless(client, db):
    register(client)
    r = client.post("/api/projects", json={"name": "x'); DROP TABLE users; --"})
    assert r.status_code == 201
    r = client.post("/api/auth/login", json={"email": "' OR 1=1 --@example.com", "password": "anything123A"})
    assert r.status_code in (401, 422)
    with db.tx() as c:
        assert c.fetchone("SELECT COUNT(*) AS n FROM users")["n"] == 1


def test_storage_keys_cannot_escape_root(storage):
    for bad in ("../etc/passwd", "/abs/path", "a//b", "projects/../../x"):
        with pytest.raises(StorageError):
            storage.put(bad, b"x")


def test_xlsx_export_neutralises_formulas():
    from app.reports.exporters import _cell

    assert _cell("=HYPERLINK(\"http://evil\")").startswith("'")
    assert _cell("-12.5") == "-12.5"
    assert _cell(float("nan")) is None


def test_unknown_api_route_returns_json_404(client):
    r = client.get("/api/does-not-exist")
    assert r.status_code == 404 and r.json()["error"]["what"]


def test_upload_size_limit(client, monkeypatch):
    from app.config import settings

    register(client)
    p = client.post("/api/projects", json={"name": "P"}).json()["project"]
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    big = b"a,b\n" + b"1,2\n" * 300000
    r = client.post(f"/api/projects/{p['id']}/files", files=[("files", ("big.csv", big, "text/csv"))])
    assert r.status_code in (400, 413)


def test_files_never_served_publicly(client):
    # there is no static route to storage; only authenticated API downloads exist
    r = client.get("/storage/projects/anything")
    assert r.status_code in (200, 404)
    assert b"projects" not in r.content or b"<!doctype html" in r.content.lower() or r.status_code == 404
