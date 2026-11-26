"""Phase 2: production behaviour — health, admin, cloud storage adapter, quotas, safety, friendly errors."""
import io
import zipfile

import pytest

from app.config import settings
from tests.conftest import PASSWORD, register, sample


# ----------------------------------------------------------------- health
def test_health_endpoints_reveal_nothing_sensitive(client):
    for path in ("/health", "/api/health"):
        r = client.get(path)
        assert r.status_code == 200
        assert r.json() == {"status": "healthy", "database": "connected"}


def test_health_reports_database_down(client, monkeypatch):
    from app import db as dbmod

    def boom():
        raise dbmod.DatabaseUnavailable("connection to server at secret-host failed: password=hunter2")

    monkeypatch.setattr(dbmod.get_db(), "ping", boom)
    r = client.get("/health")
    assert r.status_code == 503 and r.json() == {"status": "unhealthy", "database": "unavailable"}
    assert b"hunter2" not in r.content and b"secret-host" not in r.content


def test_database_errors_are_shown_in_plain_language(client, monkeypatch):
    from contextlib import contextmanager

    from app import db as dbmod

    register(client)

    @contextmanager
    def broken_tx():
        raise dbmod.DatabaseUnavailable("FATAL: password authentication failed for user neondb_owner")
        yield  # pragma: no cover

    monkeypatch.setattr(dbmod.get_db(), "tx", broken_tx)
    r = client.get("/api/projects")
    assert r.status_code == 503
    body = r.json()["error"]
    assert body["what"] == "The application could not connect to the database."
    assert "neondb_owner" not in r.text and "FATAL" not in r.text

    @contextmanager
    def full_tx():
        raise dbmod.DatabaseUnavailable("could not extend file: project size limit (1024 MB) has been exceeded")
        yield  # pragma: no cover

    monkeypatch.setattr(dbmod.get_db(), "tx", full_tx)
    r = client.get("/api/projects")
    assert r.json()["error"]["code"] == "db_quota" and "free-plan limit" in r.json()["error"]["what"]


# ----------------------------------------------------------------- admin
@pytest.fixture()
def admin_client(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_emails", ["owner@example.com"])
    register(client, "owner@example.com")
    return client


def test_admin_area_hidden_from_normal_users(client, make_client, monkeypatch):
    monkeypatch.setattr(settings, "admin_emails", ["owner@example.com"])
    register(client, "someone@example.com")
    assert client.get("/api/auth/me").json()["user"]["is_admin"] is False
    for path in ("/api/admin/overview", "/api/admin/users", "/api/admin/backup"):
        assert client.get(path).status_code == 404
    assert client.post("/api/admin/selftest").status_code == 404
    assert make_client().get("/api/admin/overview").status_code == 401


def test_admin_overview_selftest_and_usage(admin_client, monkeypatch):
    c = admin_client
    assert c.get("/api/auth/me").json()["user"]["is_admin"] is True
    p = c.post("/api/projects", json={"name": "Private project name"}).json()["project"]
    c.post(f"/api/projects/{p['id']}/files", files=[("files", ("secret_plans.csv", b"Month,Revenue\n2024-01,1\n2024-02,2\n", "text/csv"))])
    monkeypatch.setattr(settings, "storage_quota_mb", 100)
    ov = c.get("/api/admin/overview").json()
    assert ov["health"]["database"] == "connected" and ov["health"]["storage"] == "connected"
    assert ov["usage"]["counts"]["users"] == 1 and ov["usage"]["counts"]["projects"] == 1 and ov["usage"]["counts"]["files"] == 1
    assert ov["limits"]["file_storage"]["limit_mb"] == 100 and ov["limits"]["file_storage"]["level"] == "ok"
    text = c.get("/api/admin/overview").text
    assert "secret_plans" not in text and "Private project name" not in text  # no private contents
    st = c.post("/api/admin/selftest").json()
    assert st["all_ok"], st
    assert [x["check"] for x in st["checks"]] == ["Database connection", "Database migrations", "Database read/write",
                                                    "File storage upload/download/delete", "Realistic document upload"]


def test_admin_reset_link_works_without_email(admin_client, make_client):
    other = make_client()
    register(other, "user2@example.com")
    users = admin_client.get("/api/admin/users").json()["users"]
    uid = next(u["id"] for u in users if u["email"] == "user2@example.com")
    link = admin_client.post(f"/api/admin/users/{uid}/reset-link").json()["link"]
    token = link.split("token=")[1]
    anon = make_client()
    assert anon.post("/api/auth/password-reset/confirm", json={"token": token, "password": "NewSecret123pw"}).status_code == 200
    assert anon.post("/api/auth/login", json={"email": "user2@example.com", "password": "NewSecret123pw"}).status_code == 200


def test_admin_backup_excludes_password_hashes(admin_client):
    r = admin_client.get("/api/admin/backup")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    users_csv = z.read("users.csv").decode()
    assert "owner@example.com" in users_csv and "password_hash" not in users_csv and "scrypt$" not in users_csv
    assert "sessions.csv" not in z.namelist() and "auth_tokens.csv" not in z.namelist()


def test_registration_can_be_closed(client, make_client, monkeypatch):
    register(client, "first@example.com")
    monkeypatch.setattr(settings, "registration_open", False)
    monkeypatch.setattr(settings, "admin_emails", ["boss@example.com"])
    r = make_client().post("/api/auth/register", json={"email": "stranger@example.com", "password": PASSWORD})
    assert r.status_code == 403
    assert make_client().post("/api/auth/register", json={"email": "boss@example.com", "password": PASSWORD}).status_code == 201


# ----------------------------------------------------------------- cloud storage adapter (fake S3 client)
class FakeClientError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeS3:
    def __init__(self, quota_bytes=None):
        self.objects, self.quota = {}, quota_bytes

    def put_object(self, Bucket, Key, Body, ContentType):
        if self.quota is not None and sum(map(len, self.objects.values())) + len(Body) > self.quota:
            raise FakeClientError("QuotaExceeded")
        self.objects[Key] = bytes(Body)

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise FakeClientError("NoSuchKey")
        return {"Body": io.BytesIO(self.objects[Key])}

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise FakeClientError("404")
        return {}

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)

    def list_objects_v2(self, Bucket, Prefix, MaxKeys, ContinuationToken=None):
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        start = int(ContinuationToken or 0)
        page = keys[start:start + 2]  # tiny pages to exercise pagination
        out = {"Contents": [{"Key": k, "Size": len(self.objects[k])} for k in page], "IsTruncated": start + 2 < len(keys)}
        if out["IsTruncated"]:
            out["NextContinuationToken"] = str(start + 2)
        return out


def test_s3_provider_operations():
    from app.storage import StorageError, StorageQuotaError
    from app.storage.s3 import S3StorageProvider

    s = S3StorageProvider("bucket", "https://example", "k", "s", client=FakeS3(quota_bytes=50))
    s.put("projects/p1/files/a.txt", b"hello")
    assert s.get("projects/p1/files/a.txt") == b"hello" and s.exists("projects/p1/files/a.txt")
    for i in range(4):
        s.put(f"projects/p1/files/f{i}", b"x")
    s.put("projects/p2/other", b"keep")
    assert s.usage_bytes("projects/") == 5 + 4 + 4
    assert s.delete_prefix("projects/p1") == 5  # pagination across 3 pages
    assert not s.exists("projects/p1/files/a.txt") and s.exists("projects/p2/other")
    s.delete("projects/p1/missing")  # deleting a missing object is not an error
    with pytest.raises(StorageError):
        s.get("projects/p1/missing")
    with pytest.raises(StorageQuotaError):
        s.put("projects/p2/big", b"x" * 100)
    with pytest.raises(StorageError):
        s.put("../escape", b"x")


def test_s3_settings_accept_neon_style_names(monkeypatch):
    from app.storage import StorageError
    from app.storage.s3 import S3StorageProvider

    captured = {}

    def fake_init(self, bucket, endpoint_url, access_key, secret_key, region="", client=None):
        captured.update(bucket=bucket, endpoint=endpoint_url, key=access_key, secret=secret_key, region=region)

    monkeypatch.setattr(S3StorageProvider, "__init__", fake_init)
    for k in ("S3_ENDPOINT_URL", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "S3_REGION"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("S3_BUCKET", "dip-files")
    monkeypatch.setenv("AWS_ENDPOINT_URL_S3", "https://br-example.storage.example.neon.tech")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "nak_live_example")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "nsk_live_example")
    monkeypatch.setenv("AWS_REGION", "ap-southeast-1")
    S3StorageProvider.from_env()
    assert captured == {"bucket": "dip-files", "endpoint": "https://br-example.storage.example.neon.tech", "key": "nak_live_example",
                        "secret": "nsk_live_example", "region": "ap-southeast-1"}
    monkeypatch.delenv("S3_BUCKET")
    with pytest.raises(StorageError) as e:
        S3StorageProvider.from_env()
    assert "S3_BUCKET" in str(e.value) and "nsk_live" not in str(e.value)


def test_storage_errors_are_friendly(client, monkeypatch):
    from app.storage import StorageQuotaError, get_storage

    register(client)
    p = client.post("/api/projects", json={"name": "P"}).json()["project"]

    def full(*a, **k):
        raise StorageQuotaError("quota")

    monkeypatch.setattr(get_storage(), "put", full)
    r = client.post(f"/api/projects/{p['id']}/files", files=[("files", ("a.csv", b"a,b\n1,2\n", "text/csv"))])
    assert r.status_code == 507 and r.json()["error"]["what"] == "This file exceeds the current free storage limit."


def test_app_level_storage_quota(client, monkeypatch):
    register(client)
    p = client.post("/api/projects", json={"name": "P"}).json()["project"]
    monkeypatch.setattr(settings, "storage_quota_mb", 1)
    ok = client.post(f"/api/projects/{p['id']}/files", files=[("files", ("small.csv", b"a,b\n1,2\n", "text/csv"))])
    assert ok.status_code == 201
    big = b"a,b\n" + b"1,2\n" * 300_000  # ~1.2 MB
    r = client.post(f"/api/projects/{p['id']}/files", files=[("files", ("big.csv", big, "text/csv"))])
    assert r.status_code == 507
    assert "free storage limit" in r.json()["error"]["what"]


# ----------------------------------------------------------------- uploads
def test_programs_and_macros_are_refused(client):
    register(client)
    p = client.post("/api/projects", json={"name": "P"}).json()["project"]
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as z:
        z.writestr("word/document.xml", "<document/>")
        z.writestr("word/vbaProject.bin", b"macro")
    ebuf = io.BytesIO()
    with zipfile.ZipFile(ebuf, "w") as z:
        z.writestr("xl/workbook.xml", "<workbook/>")
        z.writestr("xl/embeddings/run.exe", b"MZ")
    files = [("files", ("report.pdf", b"MZ\x90\x00 fake exe", "application/pdf")),
             ("files", ("data.csv", b"#!/bin/sh\nrm -rf /\n", "text/csv")),
             ("files", ("letter.docx", zbuf.getvalue(), "application/octet-stream")),
             ("files", ("book.xlsx", ebuf.getvalue(), "application/octet-stream"))]
    r = client.post(f"/api/projects/{p['id']}/files", files=files)
    rej = {x["name"]: x["what"] for x in r.json()["rejected"]}
    assert "program or script" in rej["report.pdf"] and "program or script" in rej["data.csv"]
    assert "macros" in rej["letter.docx"] and "embedded programs" in rej["book.xlsx"]
    # macro-enabled workbooks (.xlsm) are accepted for their data; macros are never run (see test_messy_sheets)


def test_source_processing_summary(client):
    register(client)
    p = client.post("/api/projects", json={"name": "P"}).json()["project"]
    corrupt_pdf = b"%PDF-1.4\n" + b"\x00garbage" * 50
    client.post(f"/api/projects/{p['id']}/files", files=[
        ("files", ("ok.csv", b"a,b\n1,2\n3,4\n", "text/csv")),
        ("files", ("ok2.csv", b"x,y\n1,2\n3,4\n", "text/csv")),
        ("files", ("broken.pdf", corrupt_pdf, "application/pdf"))])
    s = client.get(f"/api/projects/{p['id']}").json()["source_summary"]
    assert s["counts"]["processed"] == 2 and s["counts"]["failed"] == 1
    assert s["message"] == "2 source(s) processed successfully. 1 source(s) could not be processed."
    assert s["can_continue"] is True


# ----------------------------------------------------------------- jobs
def test_stalled_analysis_is_marked_failed(client, db):
    from app.db import new_id

    register(client)
    p = client.post("/api/projects", json={"name": "P"}).json()["project"]
    rid = new_id()
    with db.tx() as c:
        c.insert("analysis_runs", {"id": rid, "project_id": p["id"], "mode": "deep", "status": "running",
                                   "created_at": "2020-01-01T00:00:00+00:00"})
    run = client.get(f"/api/projects/{p['id']}/analyses/{rid}").json()["run"]
    assert run["status"] == "failed" and "took too long" in run["errors"][0]["what"]


# ----------------------------------------------------------------- configuration & logging
def test_production_configuration_checks(monkeypatch):
    from app.config import Settings

    s = Settings()
    s.app_env, s.database_url, s.secret_key, s.storage_provider, s.cookie_secure = "production", "sqlite:///x", "", "local", False
    problems = " ".join(s.validate())
    for word in ("PostgreSQL", "SECRET_KEY", "STORAGE_PROVIDER", "COOKIE_SECURE"):
        assert word in problems
    s.database_url, s.secret_key, s.storage_provider, s.cookie_secure = "postgresql://u:p@h/db", "x" * 40, "s3", True
    assert s.validate() == []


def test_database_url_handling_never_leaks_password():
    from app.db import describe_db_target, postgres_conninfo

    url = "postgres://neondb_owner:SuperSecret@ep-cool-123-pooler.ap-southeast-1.aws.neon.tech/neondb"
    assert postgres_conninfo(url).startswith("postgresql://") and postgres_conninfo(url).endswith("?sslmode=require")
    assert postgres_conninfo(url + "?sslmode=verify-full").count("sslmode") == 1
    d = describe_db_target(url)
    assert "SuperSecret" not in d and "ep-cool-123-pooler" in d


def test_log_redaction():
    from app.logging_setup import redact

    assert "hunter2" not in redact("connect postgresql://user:hunter2@host/db failed")
    assert "abcdef123456" not in redact("key nsk_live_abcdef123456 used")
    assert "xyz987654321" not in redact("api_key=xyz987654321")
    assert "tok123456789abc" not in redact("GET /reset?token=tok123456789abc")


def test_console_email_does_not_log_links_in_production(monkeypatch, caplog, capsys):
    from app.auth.providers import ConsoleEmailProvider

    monkeypatch.setattr(settings, "app_env", "production")
    ConsoleEmailProvider().send("a@b.co", "Reset your password", "link https://x/#/reset-password?token=SECRET123")
    out = capsys.readouterr().out + caplog.text
    assert "SECRET123" not in out and "NOT sent" in caplog.text


def test_destructive_migrations_are_refused(tmp_path, monkeypatch):
    from app import migrate as m
    from app.db import Database

    (tmp_path / "0001_initial.sql").write_text((m.MIGRATIONS_DIR / "0001_initial.sql").read_text())
    (tmp_path / "0002_bad.sql").write_text("DROP TABLE users;")
    monkeypatch.setattr(m, "MIGRATIONS_DIR", tmp_path)
    with pytest.raises(RuntimeError, match="destructive"):
        m.migrate(Database("sqlite:///:memory:"), verbose=False)


def test_file_names_with_spaces_brackets_and_accents_upload(client):
    """Regression: storage paths were rejected for names like 'Q3 Market Report (final).pdf'."""
    register(client)
    p = client.post("/api/projects", json={"name": "P"}).json()["project"]
    names = ["Q3 Market Report (final).csv", "Neo Health – plan & budget 2026.csv", "Café données.csv"]
    files = [("files", (n, b"Month,Revenue\n2024-01,1\n2024-02,2\n", "text/csv")) for n in names]
    r = client.post(f"/api/projects/{p['id']}/files", files=files)
    assert r.status_code == 201 and len(r.json()["accepted"]) == 3, r.text
    listing = client.get(f"/api/projects/{p['id']}/files").json()["files"]
    assert {f["status"] for f in listing} == {"processed"}
    assert "Q3 Market Report (final).csv" in {f["original_name"] for f in listing}  # real name kept for users
    fid = next(f["id"] for f in listing if f["original_name"].startswith("Q3"))
    dl = client.get(f"/api/projects/{p['id']}/files/{fid}/download")
    assert dl.status_code == 200 and dl.content.startswith(b"Month,Revenue")
    assert "Q3%20Market%20Report%20%28final%29.csv" in dl.headers["content-disposition"]


def test_storage_error_shows_safe_code(client, monkeypatch):
    from app.storage import get_storage
    from app.storage.s3 import S3StorageProvider

    class Boom(FakeS3):
        def put_object(self, **kw):
            raise FakeClientError("InvalidArgument")

    s3 = S3StorageProvider("b", "https://e", "k", "s", client=Boom())
    with pytest.raises(Exception) as e:
        s3.put("projects/p/files/f/original/a.pdf", b"x")
    assert "InvalidArgument" in str(e.value)
    register(client)
    p = client.post("/api/projects", json={"name": "P"}).json()["project"]
    monkeypatch.setattr(get_storage(), "put", lambda *a, **k: s3.put("projects/p/x.pdf", b"x"))
    r = client.post(f"/api/projects/{p['id']}/files", files=[("files", ("a.csv", b"a,b\n1,2\n", "text/csv"))])
    assert r.status_code == 503 and "InvalidArgument" in r.json()["error"]["why"]


def test_exact_live_failing_pdf_name_uploads(client):
    """Regression for the live error: 'OptiChoice - Google Ads Report - Oct 2026.pdf' was an invalid storage key."""
    import io

    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    cv = canvas.Canvas(buf)
    cv.drawString(72, 720, "Google Ads Report October 2026. Clicks 1200, Spend 450, Conversions 36.")
    cv.save()
    register(client)
    p = client.post("/api/projects", json={"name": "P"}).json()["project"]
    name = "OptiChoice - Google Ads Report - Oct 2026.pdf"
    r = client.post(f"/api/projects/{p['id']}/files", files=[("files", (name, buf.getvalue(), "application/pdf"))])
    assert r.status_code == 201, r.text
    f = client.get(f"/api/projects/{p['id']}/files").json()["files"][0]
    assert f["original_name"] == name and f["status"] in ("processed", "needs_review"), f
