from tests.conftest import PASSWORD, register


def test_register_login_logout_me(client):
    user = register(client)
    assert user["email"] == "alice@example.com"
    assert client.get("/api/auth/me").json()["user"]["email"] == "alice@example.com"
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").json()["user"] is None
    r = client.post("/api/auth/login", json={"email": "ALICE@example.com", "password": PASSWORD})
    assert r.status_code == 200
    assert client.get("/api/auth/me").json()["user"]["id"] == user["id"]


def test_wrong_password_and_duplicate_email(client, make_client):
    register(client)
    other = make_client()
    r = other.post("/api/auth/login", json={"email": "alice@example.com", "password": "WrongPassword99"})
    assert r.status_code == 401
    assert "incorrect" in r.json()["error"]["what"]
    r = other.post("/api/auth/register", json={"email": "alice@example.com", "password": PASSWORD})
    assert r.status_code == 409


def test_weak_password_rejected(client):
    r = client.post("/api/auth/register", json={"email": "bob@example.com", "password": "short"})
    assert r.status_code == 422
    assert r.json()["error"]["what"]


def test_password_is_hashed(client, db):
    register(client)
    with db.tx() as c:
        row = c.fetchone("SELECT password_hash FROM users")
    assert row["password_hash"].startswith("scrypt$") and PASSWORD not in row["password_hash"]


def test_password_reset_flow(client, make_client):
    from app.auth.providers import get_email_provider

    register(client)
    anon = make_client()
    r = anon.post("/api/auth/password-reset/request", json={"email": "alice@example.com"})
    assert r.status_code == 200
    # same response for unknown e-mails (no account enumeration)
    r2 = anon.post("/api/auth/password-reset/request", json={"email": "nobody@example.com"})
    assert r2.json()["message"] == r.json()["message"]
    body = get_email_provider().outbox[-1]["body"]
    token = body.split("token=")[1].split()[0]
    new_pw = "BrandNewPassw0rd"
    assert anon.post("/api/auth/password-reset/confirm", json={"token": token, "password": new_pw}).status_code == 200
    # token is single-use
    assert anon.post("/api/auth/password-reset/confirm", json={"token": token, "password": new_pw}).status_code == 400
    # old sessions were revoked
    assert client.get("/api/auth/me").json()["user"] is None
    assert anon.post("/api/auth/login", json={"email": "alice@example.com", "password": new_pw}).status_code == 200


def test_change_password(client):
    register(client)
    r = client.post("/api/auth/change-password", json={"current_password": "nope-nope-1A", "new_password": "AnotherGood1pass"})
    assert r.status_code == 400
    r = client.post("/api/auth/change-password", json={"current_password": PASSWORD, "new_password": "AnotherGood1pass"})
    assert r.status_code == 200


def test_login_rate_limit(client, make_client):
    register(client)
    anon = make_client()
    codes = [anon.post("/api/auth/login", json={"email": "alice@example.com", "password": "Wrong-pass-123"}).status_code for _ in range(12)]
    assert 429 in codes


def test_unauthenticated_requests_are_rejected(make_client):
    anon = make_client()
    assert anon.get("/api/projects").status_code == 401
    assert anon.post("/api/projects", json={"name": "x"}).status_code == 401


def test_delete_account(client, db):
    register(client)
    client.post("/api/projects", json={"name": "P"})
    assert client.post("/api/auth/delete-account", json={"email": "alice@example.com", "password": PASSWORD}).status_code == 200
    with db.tx() as c:
        assert c.fetchone("SELECT COUNT(*) AS n FROM projects")["n"] == 0
