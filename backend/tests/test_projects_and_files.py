from tests.conftest import register, sample


def _project(client, **kw):
    r = client.post("/api/projects", json={"name": "OptiChoice Market Analysis", "industry": "Retail", **kw})
    assert r.status_code == 201, r.text
    return r.json()["project"]


def test_project_crud(client):
    register(client)
    p = _project(client, learning_objectives=["What are the major trends?", "  ", "What gaps exist?"])
    assert p["learning_objectives"] == ["What are the major trends?", "What gaps exist?"]
    assert [x["id"] for x in client.get("/api/projects").json()["projects"]] == [p["id"]]
    r = client.patch(f"/api/projects/{p['id']}", json={"geography": "UK", "focus_entity": "OptiChoice"})
    assert r.json()["project"]["geography"] == "UK"
    assert client.get(f"/api/projects/{p['id']}").json()["project"]["focus_entity"] == "OptiChoice"
    assert client.delete(f"/api/projects/{p['id']}").status_code == 200
    assert client.get(f"/api/projects/{p['id']}").status_code == 404


def test_project_validation(client):
    register(client)
    assert client.post("/api/projects", json={"name": ""}).status_code == 422
    assert client.get("/api/projects/not-a-uuid").status_code == 404


def test_multi_file_upload_and_processing(client):
    register(client)
    p = _project(client)
    files = [("files", ("sales.csv", b"Month,Product,Revenue\n2024-01,A,100\n2024-02,A,120\n2024-03,A,150\n", "text/csv")),
             ("files", ("notes.txt", b"MARKET NOTES\n\nSales grew 12% in 2024.\n", "text/plain")),
             ("files", ("competitor_dataset.csv", sample("competitor_dataset.csv"), "text/csv")),
             ("files", ("strategy_2026_proposed.docx", sample("strategy_2026_proposed.docx"), "application/octet-stream")),
             ("files", ("business_review_2025.pptx", sample("business_review_2025.pptx"), "application/octet-stream")),
             ("files", ("market_report_2025.pdf", sample("market_report_2025.pdf"), "application/pdf")),
             ("files", ("product_dataset.xlsx", sample("product_dataset.xlsx"), "application/octet-stream")),
             ("files", ("survey_purchase_drivers.png", sample("survey_purchase_drivers.png"), "image/png"))]
    r = client.post(f"/api/projects/{p['id']}/files", files=files, data={"roles": ["sales_data", "auto"]})
    assert r.status_code == 201, r.text
    body = r.json()
    assert len(body["accepted"]) == 8 and not body["rejected"]
    listing = client.get(f"/api/projects/{p['id']}/files").json()["files"]
    assert {f["status"] for f in listing} <= {"processed", "partial"}
    by_name = {f["original_name"]: f for f in listing}
    assert by_name["sales.csv"]["source_role"] == "sales_data"
    assert by_name["strategy_2026_proposed.docx"]["source_role"] == "proposed_strategy"
    detail = client.get(f"/api/projects/{p['id']}/files/{by_name['sales.csv']['id']}").json()
    assert detail["datasets"][0]["row_count"] == 3
    roles = {c["normalized_name"]: c["semantic_role"] for c in detail["datasets"][0]["columns"]}
    assert roles["Month"] == "time" and roles["Revenue"] == "revenue"
    # dataset preview with row numbers back to the source
    ds_id = detail["datasets"][0]["id"]
    prev = client.get(f"/api/projects/{p['id']}/datasets/{ds_id}/preview").json()
    assert prev["rows"][0]["__row__"] == 2  # CSV line 2 is the first data row
    caps = client.get(f"/api/projects/{p['id']}/capabilities").json()
    keys = {c["key"] for c in caps["capabilities"]}
    assert "growth_trends" in keys and "product_opportunity" in keys


def test_invalid_files_rejected_with_explanation(client):
    register(client)
    p = _project(client)
    files = [("files", ("evil.exe", b"MZ\x00\x00", "application/octet-stream")),
             ("files", ("fake.pdf", b"not a pdf at all", "application/pdf")),
             ("files", ("empty.csv", b"", "text/csv"))]
    r = client.post(f"/api/projects/{p['id']}/files", files=files)
    assert r.status_code == 400
    rej = r.json()["rejected"]
    assert len(rej) == 3 and all(x["what"] and x["why"] is not None for x in rej)


def test_partial_failure_keeps_other_files(client):
    register(client)
    p = _project(client)
    corrupt_xlsx = b"PK\x03\x04" + b"\x00" * 200
    files = [("files", ("good.csv", b"a,b\n1,2\n3,4\n", "text/csv")), ("files", ("broken.xlsx", corrupt_xlsx, "application/octet-stream"))]
    r = client.post(f"/api/projects/{p['id']}/files", files=files)
    # broken.xlsx is rejected at validation, good.csv is accepted
    assert len(r.json()["accepted"]) == 1
    assert r.json()["rejected"][0]["name"] == "broken.xlsx"


def test_file_download_and_delete(client, storage):
    register(client)
    p = _project(client)
    r = client.post(f"/api/projects/{p['id']}/files", files=[("files", ("data.csv", b"x,y\n1,2\n2,4\n", "text/csv"))])
    fid = r.json()["accepted"][0]["id"]
    dl = client.get(f"/api/projects/{p['id']}/files/{fid}/download")
    assert dl.status_code == 200 and dl.content == b"x,y\n1,2\n2,4\n"
    assert "attachment" in dl.headers["content-disposition"]
    stored = list((storage.root / "projects" / p["id"] / "files" / fid).rglob("*"))
    assert any(s.is_file() for s in stored)
    assert client.delete(f"/api/projects/{p['id']}/files/{fid}").status_code == 200
    assert client.get(f"/api/projects/{p['id']}/files/{fid}").status_code == 404
    assert not (storage.root / "projects" / p["id"] / "files" / fid).exists()


def test_project_delete_removes_storage(client, storage):
    register(client)
    p = _project(client)
    client.post(f"/api/projects/{p['id']}/files", files=[("files", ("data.csv", b"x,y\n1,2\n", "text/csv"))])
    assert (storage.root / "projects" / p["id"]).exists()
    client.delete(f"/api/projects/{p['id']}")
    assert not (storage.root / "projects" / p["id"]).exists()


def test_project_isolation_between_users(client, make_client):
    register(client, "alice@example.com")
    p = _project(client)
    up = client.post(f"/api/projects/{p['id']}/files", files=[("files", ("data.csv", b"Month,Revenue\n2024-01,1\n2024-02,2\n2024-03,3\n", "text/csv"))])
    fid = up.json()["accepted"][0]["id"]
    client.post(f"/api/projects/{p['id']}/analyses", json={"mode": "quick", "questions": ["What are the major trends?"]})
    run_id = client.get(f"/api/projects/{p['id']}/analyses").json()["runs"][0]["id"]
    rep = client.post(f"/api/projects/{p['id']}/reports", json={}).json()["report"]

    bob = make_client()
    register(bob, "bob@example.com")
    assert bob.get("/api/projects").json()["projects"] == []
    pid = p["id"]
    forbidden = [
        bob.get(f"/api/projects/{pid}"), bob.patch(f"/api/projects/{pid}", json={"name": "hijack"}),
        bob.delete(f"/api/projects/{pid}"), bob.get(f"/api/projects/{pid}/files"),
        bob.get(f"/api/projects/{pid}/files/{fid}"), bob.get(f"/api/projects/{pid}/files/{fid}/download"),
        bob.delete(f"/api/projects/{pid}/files/{fid}"),
        bob.post(f"/api/projects/{pid}/files", files=[("files", ("x.csv", b"a,b\n1,2\n", "text/csv"))]),
        bob.get(f"/api/projects/{pid}/analyses/{run_id}"), bob.post(f"/api/projects/{pid}/ask", json={"question": "What is total revenue?"}),
        bob.get(f"/api/projects/{pid}/reports/{rep['id']}"), bob.get(f"/api/projects/{pid}/reports/{rep['id']}/export/pdf"),
        bob.get(f"/api/projects/{pid}/quality"),
    ]
    assert [r.status_code for r in forbidden] == [404] * len(forbidden)
    # Alice's data is untouched
    assert client.get(f"/api/projects/{pid}").json()["project"]["name"] == "OptiChoice Market Analysis"
    assert len(client.get(f"/api/projects/{pid}/files").json()["files"]) == 1


def test_viewer_role_cannot_modify(client, make_client, db):
    from app.db import new_id, now

    register(client, "alice@example.com")
    p = _project(client)
    viewer = make_client()
    v = register(viewer, "val@example.com")
    with db.tx() as c:
        c.insert("project_members", {"id": new_id(), "project_id": p["id"], "user_id": v["id"], "role": "viewer", "created_at": now()})
    assert viewer.get(f"/api/projects/{p['id']}").status_code == 200
    assert viewer.patch(f"/api/projects/{p['id']}", json={"name": "x"}).status_code == 403
    assert viewer.delete(f"/api/projects/{p['id']}").status_code == 403
    assert viewer.post(f"/api/projects/{p['id']}/files", files=[("files", ("x.csv", b"a,b\n1,2\n", "text/csv"))]).status_code == 403


def test_url_sources(client):
    register(client)
    p = _project(client)
    r = client.post(f"/api/projects/{p['id']}/urls", json={"url": "https://example.com/report", "title": "Example"})
    assert r.status_code == 201
    assert client.get(f"/api/projects/{p['id']}").json()["urls"][0]["url"] == "https://example.com/report"
    assert client.post(f"/api/projects/{p['id']}/urls", json={"url": "ftp://example.com/file"}).status_code == 422


def test_ssrf_protection():
    import pytest

    from app.api.common import ApiError
    from app.api.projects import _safe_public_url

    for bad in ("http://127.0.0.1/admin", "http://localhost:8000/", "http://10.0.0.5/x", "file:///etc/passwd"):
        with pytest.raises(ApiError):
            _safe_public_url(bad)
