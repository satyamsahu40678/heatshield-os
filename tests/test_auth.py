"""Identity boundary tests: untrusted tokens never provide roles or tenant identity."""

from datetime import timedelta
from typing import Annotated

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from heatshield.auth import (
    create_access_token,
    hash_password,
    public_user,
    require_roles,
    router,
    verify_password,
)
from heatshield.bootstrap import DEMO_PASSWORD, seed_demo
from heatshield.database import Base, Database, utcnow
from heatshield.identity import Organization, User
from heatshield.settings import DEMO_JWT_SECRET, Settings


@pytest.fixture(scope="module")
def password_hash():
    return hash_password("test-only-password")


@pytest.fixture
def auth_client(password_hash):
    database = Database("sqlite:///:memory:")
    Base.metadata.create_all(database.engine)
    settings = Settings(database_url="sqlite:///:memory:")
    with database.session() as session:
        session.add_all(
            [
                Organization(id="org-one", name="Organization one"),
                Organization(id="org-two", name="Organization two"),
            ]
        )
        session.flush()
        session.add_all(
            [
                User(
                    id="user-one",
                    organization_id="org-one",
                    email="one@example.test",
                    display_name="Worker",
                    role="worker",
                    worker_id="worker-01",
                    password_hash=password_hash,
                ),
                User(
                    id="admin-two",
                    organization_id="org-two",
                    email="two@example.test",
                    display_name="Administrator",
                    role="admin",
                    password_hash=password_hash,
                ),
            ]
        )
    app = FastAPI()
    app.state.database = database
    app.state.settings = settings
    app.include_router(router)

    @app.get("/admin-only")
    def admin_only(user: Annotated[User, Depends(require_roles("admin"))]):
        return public_user(user)

    with TestClient(app) as client:
        yield client, database, settings
    database.dispose()


def login(client, email="one@example.test"):
    response = client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": "test-only-password",
        },
    )
    assert response.status_code == 200
    return response.json()


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_password_hash_is_salted_and_checked(password_hash):
    assert "test-only-password" not in password_hash
    assert hash_password("test-only-password") != password_hash
    assert verify_password("test-only-password", password_hash)
    assert not verify_password("wrong-password", password_hash)
    assert not verify_password("anything", "invalid hash")


def test_login_uses_server_identity_and_does_not_expose_password(auth_client):
    client, _, _ = auth_client
    result = login(client, " ONE@EXAMPLE.TEST ")
    assert result["token_type"] == "bearer"
    assert result["user"]["organization_id"] == "org-one"
    assert result["user"]["role"] == "worker"
    assert "password_hash" not in result["user"]
    assert "token_version" not in result["user"]
    me = client.get("/api/v1/auth/me", headers=bearer(result["access_token"]))
    assert me.status_code == 200
    assert me.json() == result["user"]


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "missing@example.test", "password": "test-only-password"},
        {"email": "one@example.test", "password": "wrong"},
    ],
)
def test_invalid_login_is_generic(auth_client, payload):
    response = auth_client[0].post("/api/v1/auth/login", json=payload)
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or expired credentials"}


def test_login_rejects_actor_role_injection(auth_client):
    response = auth_client[0].post(
        "/api/v1/auth/login",
        json={
            "email": "one@example.test",
            "password": "test-only-password",
            "role": "admin",
        },
    )
    assert response.status_code == 422


def test_missing_token_and_wrong_role(auth_client):
    client, _, _ = auth_client
    assert client.get("/api/v1/auth/me").status_code == 401
    token = login(client)["access_token"]
    assert client.get("/admin-only", headers=bearer(token)).status_code == 403
    admin_token = login(client, "two@example.test")["access_token"]
    assert client.get("/admin-only", headers=bearer(admin_token)).json()["id"] == "admin-two"


def test_logout_revokes_all_preexisting_sessions_but_allows_new_login(auth_client):
    client, _, _ = auth_client
    token_one = login(client)["access_token"]
    token_two = login(client)["access_token"]
    assert client.post("/api/v1/auth/logout", headers=bearer(token_one)).status_code == 200
    for token in (token_one, token_two):
        assert client.get("/api/v1/auth/me", headers=bearer(token)).status_code == 401
    fresh_token = login(client)["access_token"]
    assert client.get("/api/v1/auth/me", headers=bearer(fresh_token)).status_code == 200


@pytest.mark.parametrize("change", ["expired", "issuer", "audience", "signature", "version"])
def test_invalid_jwt_claims_are_rejected(auth_client, change):
    client, _, settings = auth_client
    token = login(client)["access_token"]
    claims = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=["HS256"],
        audience=settings.jwt_audience,
        issuer=settings.jwt_issuer,
    )
    secret = settings.jwt_secret
    if change == "expired":
        claims["exp"] = utcnow() - timedelta(seconds=1)
    elif change == "issuer":
        claims["iss"] = "untrusted-issuer"
    elif change == "audience":
        claims["aud"] = "different-application"
    elif change == "signature":
        secret = "different-secret-at-least-thirty-two-bytes-long"
    else:
        claims["version"] = 12345
    token = jwt.encode(claims, secret, algorithm="HS256")
    assert client.get("/api/v1/auth/me", headers=bearer(token)).status_code == 401


def test_database_role_and_active_status_override_preexisting_token(auth_client):
    client, database, _ = auth_client
    token = login(client, "two@example.test")["access_token"]
    with database.session() as session:
        user = session.get(User, "admin-two")
        user.role = "worker"
    assert client.get("/admin-only", headers=bearer(token)).status_code == 403
    with database.session() as session:
        session.get(User, "admin-two").active = False
    assert client.get("/api/v1/auth/me", headers=bearer(token)).status_code == 401
    assert (
        client.post(
            "/api/v1/auth/login",
            json={
                "email": "two@example.test",
                "password": "test-only-password",
            },
        ).status_code
        == 401
    )


def test_forged_role_or_tenant_claims_have_no_effect(auth_client):
    client, database, settings = auth_client
    with database.session() as session:
        user = session.get(User, "user-one")
        token = create_access_token(user, settings)
    claims = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=["HS256"],
        audience=settings.jwt_audience,
        issuer=settings.jwt_issuer,
    )
    claims.update(role="admin", organization_id="org-two")
    token = jwt.encode(claims, settings.jwt_secret, algorithm="HS256")
    response = client.get("/api/v1/auth/me", headers=bearer(token))
    assert response.json()["organization_id"] == "org-one"
    assert response.json()["role"] == "worker"
    assert client.get("/admin-only", headers=bearer(token)).status_code == 403


def test_non_demo_configuration_requires_independent_secret(monkeypatch):
    monkeypatch.setenv("HEATSHIELD_DEMO", "false")
    monkeypatch.delenv("HEATSHIELD_JWT_SECRET", raising=False)
    with pytest.raises(ValueError, match="32 bytes"):
        Settings.from_env()
    with pytest.raises(ValueError, match="32 bytes"):
        Settings(demo_mode=False, jwt_secret=DEMO_JWT_SECRET)
    monkeypatch.setenv("HEATSHIELD_JWT_SECRET", "independent-production-key-longer-than-32-bytes")
    assert not Settings.from_env().demo_mode


def test_demo_seed_is_idempotent_and_guarded(auth_client):
    _, database, _ = auth_client
    with database.session() as session:
        with pytest.raises(ValueError, match="outside demo mode"):
            seed_demo(session, demo_mode=False)
        first = seed_demo(session)
        second = seed_demo(session)
        assert {user["id"] for user in first} == {user["id"] for user in second}
        worker = session.scalar(select(User).where(User.email == "worker@example.test"))
        assert worker.worker_id == "worker-01"
        assert verify_password(DEMO_PASSWORD, worker.password_hash)


def test_sqlite_foreign_keys_and_utc_round_trip(auth_client):
    _, database, _ = auth_client
    with database.session() as session:
        assert session.scalar(text("PRAGMA foreign_keys")) == 1
        assert session.get(User, "user-one").created_at.utcoffset() == timedelta(0)
