"""Independent authorization and integrity checks against the complete product API.

These tests use real persistence, authentication and solver execution. Demo
seeding is disabled so the authority of each test identity is explicit.
"""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from heatshield.auth import create_access_token, hash_password
from heatshield.database import Database
from heatshield.identity import Organization, User
from heatshield.models import Scenario
from heatshield.settings import Settings
from heatshield.worker import run_once
from heatshield.workflow_models import OptimizationRun


@pytest.fixture(scope="module")
def security_password_hash():
    return hash_password("independent-product-security-test-password")


@pytest.fixture
def product_security(tmp_path, security_password_hash):
    from heatshield.api import create_app

    database_url = f"sqlite:///{tmp_path / 'security.db'}"
    database = Database(database_url)
    database.initialize()
    settings = Settings(
        database_url=database_url,
        demo_mode=False,
        jwt_secret="independent-security-test-signing-secret-48-bytes-long",
    )
    tokens = {}
    with database.session() as session:
        session.add_all(
            [
                Organization(id="tenant-a", name="Tenant A"),
                Organization(id="tenant-b", name="Tenant B"),
            ]
        )
        session.flush()
        for tenant in ("a", "b"):
            for role, binding in (
                ("admin", None),
                ("safety_officer", None),
                ("supervisor", None),
                ("worker", "worker-01"),
                ("other_worker", "worker-02"),
            ):
                name = f"{tenant}-{role}"
                user = User(
                    id=name,
                    organization_id=f"tenant-{tenant}",
                    email=f"{name}@example.test",
                    display_name=name,
                    role="worker" if role == "other_worker" else role,
                    worker_id=binding,
                    password_hash=security_password_hash,
                )
                session.add(user)
                session.flush()
                tokens[name] = {"Authorization": f"Bearer {create_access_token(user, settings)}"}
    with TestClient(create_app(settings=settings, database=database)) as client:
        yield client, database, tokens
    database.dispose()


def security_scenario():
    """Two qualified workers ensure published filtering is genuinely exercised."""
    start = datetime(2026, 7, 15, 2, tzinfo=UTC)
    end = start + timedelta(hours=2)
    return Scenario.model_validate(
        {
            "id": "security-scenario",
            "starts_at": start,
            "ends_at": end,
            "cooling_capacity": 2,
            "workers": [
                {
                    "id": f"worker-{index:02}",
                    "skills": [f"skill-{index}"],
                    "available_from": start,
                    "available_until": end,
                }
                for index in (1, 2)
            ],
            "tasks": [
                {
                    "id": f"task-{index}",
                    "required_skill": f"skill-{index}",
                    "zone_id": "yard",
                    "duration_minutes": 30,
                    "release_at": start,
                    "deadline": end,
                    "deferrable": False,
                }
                for index in (1, 2)
            ],
            "observations": [
                {
                    "id": f"observation-{index}",
                    "zone_id": "yard",
                    "observed_at": start + timedelta(minutes=15 * index),
                    "valid_from": start + timedelta(minutes=15 * index),
                    "valid_until": start + timedelta(minutes=15 * (index + 1)),
                    "wbgt_c": 26.0,
                }
                for index in range(8)
            ],
        }
    ).model_dump(mode="json")


@pytest.mark.parametrize("path", ["/api/v1/scenarios/optimize", "/api/v1/scenarios/validate"])
def test_legacy_expensive_routes_cannot_bypass_authentication(product_security, path):
    client, _, _ = product_security
    response = client.post(path, json={})
    assert response.status_code == 401


def test_product_identity_ignores_untrusted_role_and_tenant_headers(product_security):
    client, _, tokens = product_security
    response = client.get(
        "/api/v1/auth/me",
        headers=tokens["a-worker"]
        | {"X-Role": "admin", "X-Organization-ID": "tenant-b", "X-Worker-ID": "worker-02"},
    )
    assert response.status_code == 200
    assert response.json()["role"] == "worker"
    assert response.json()["organization_id"] == "tenant-a"
    assert response.json()["worker_id"] == "worker-01"


def test_non_demo_app_does_not_create_public_demo_accounts(product_security):
    client, database, _ = product_security
    with database.session() as session:
        assert session.scalar(select(User).where(User.email == "admin@example.test")) is None
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.test", "password": "HeatShieldDemo2026!"},
    )
    assert response.status_code == 401


def _create(client, tokens, tenant="a", *, name="Security planning fixture"):
    response = client.post(
        "/api/v1/scenarios",
        headers=tokens[f"{tenant}-supervisor"],
        json={"name": name, "scenario": security_scenario()},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _command(
    client, tokens, scenario, command, *, tenant="a", role="safety_officer", key=None, **body
):
    return client.post(
        f"/api/v1/scenarios/{scenario['id']}/{command}",
        headers=tokens[f"{tenant}-{role}"] | {"Idempotency-Key": key or f"security-{command}"},
        json={"expected_revision": scenario["revision"]} | body,
    )


def _completed(product_security):
    client, database, tokens = product_security
    scenario = _create(client, tokens)
    response = _command(client, tokens, scenario, "optimize", role="supervisor")
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]
    assert run_once(database, worker_id="independent-security-test")
    response = client.get(f"/api/v1/optimization-runs/{run_id}", headers=tokens["a-supervisor"])
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "COMPLETED", response.text
    assert response.json()["result"]["validation"]["valid"]
    return scenario, run_id


def _published(product_security):
    client, _, tokens = product_security
    scenario, run_id = _completed(product_security)
    for command in ("approve", "publish"):
        response = _command(client, tokens, scenario, command, run_id=run_id)
        assert response.status_code == 200, response.text
    return scenario, run_id


def test_scenario_lists_and_details_are_tenant_scoped(product_security):
    client, _, tokens = product_security
    own = _create(client, tokens, name="Tenant A confidential fixture")
    other = _create(client, tokens, tenant="b", name="Tenant B confidential fixture")
    for tenant, expected, forbidden in (("a", own, other), ("b", other, own)):
        listed = client.get("/api/v1/scenarios", headers=tokens[f"{tenant}-supervisor"])
        assert listed.status_code == 200
        assert {item["id"] for item in listed.json()} == {expected["id"]}
        detail = client.get(
            f"/api/v1/scenarios/{forbidden['id']}", headers=tokens[f"{tenant}-supervisor"]
        )
        assert detail.status_code == 404
        assert forbidden["name"] not in detail.text


def test_cross_tenant_runs_approvals_publication_and_audit_are_hidden(product_security):
    client, _, tokens = product_security
    scenario, run_id = _completed(product_security)
    for path in (
        f"/api/v1/optimization-runs/{run_id}",
        f"/api/v1/scenarios/{scenario['id']}/compare",
        f"/api/v1/scenarios/{scenario['id']}/audit",
    ):
        response = client.get(path, headers=tokens["b-safety_officer"])
        assert response.status_code == 404, (path, response.text)
    for command in ("optimize", "approve", "publish"):
        body = {} if command == "optimize" else {"run_id": run_id}
        response = _command(client, tokens, scenario, command, tenant="b", **body)
        assert response.status_code == 404, response.text


@pytest.mark.parametrize("role", ["worker", "supervisor"])
@pytest.mark.parametrize("command", ["approve", "publish"])
def test_workers_and_supervisors_cannot_act_as_safety_reviewer(product_security, role, command):
    client, _, tokens = product_security
    scenario, run_id = _completed(product_security)
    response = _command(client, tokens, scenario, command, role=role, run_id=run_id)
    assert response.status_code == 403, response.text


def test_idempotency_replays_same_request_and_rejects_changed_request(product_security):
    client, database, tokens = product_security
    scenario = _create(client, tokens)
    first = _command(client, tokens, scenario, "optimize", role="supervisor", key="same-request")
    second = _command(client, tokens, scenario, "optimize", role="supervisor", key="same-request")
    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    changed = client.post(
        f"/api/v1/scenarios/{scenario['id']}/optimize",
        headers=tokens["a-supervisor"] | {"Idempotency-Key": "same-request"},
        json={"expected_revision": scenario["revision"] + 1},
    )
    assert changed.status_code == 409
    with database.session() as session:
        assert len(list(session.scalars(select(OptimizationRun)))) == 1


def test_idempotency_key_does_not_replay_another_actors_command(product_security):
    client, _, tokens = product_security
    scenario = _create(client, tokens)
    first = _command(client, tokens, scenario, "optimize", role="supervisor", key="shared-key")
    assert first.status_code == 202
    replay = _command(client, tokens, scenario, "optimize", role="admin", key="shared-key")
    assert replay.status_code == 409


def test_revision_change_invalidates_review_and_old_run_cannot_publish(product_security):
    client, _, tokens = product_security
    scenario, run_id = _completed(product_security)
    approved = _command(client, tokens, scenario, "approve", run_id=run_id)
    assert approved.status_code == 200, approved.text
    revised_input = security_scenario()
    revised_input["policy"]["version"] = "changed-unreviewed-demo-policy"
    revised = client.put(
        f"/api/v1/scenarios/{scenario['id']}",
        headers=tokens["a-supervisor"],
        json={
            "expected_revision": scenario["revision"],
            "name": "Revised policy requires another review",
            "scenario": revised_input,
        },
    )
    assert revised.status_code == 200, revised.text
    assert revised.json()["revision"] == 2
    assert revised.json()["approval"] is None
    assert revised.json()["published_at"] is None
    for revision in (scenario, revised.json()):
        stale = _command(
            client,
            tokens,
            revision,
            "publish",
            key=f"stale-publish-{revision['revision']}",
            run_id=run_id,
        )
        assert stale.status_code == 409, stale.text
    own = client.get("/api/v1/workers/me/schedule", headers=tokens["a-worker"])
    assert own.status_code == 200
    assert own.json()["scenarios"] == []


@pytest.mark.parametrize("review_first", [False, True])
def test_persisted_result_is_freshly_validated_before_review_or_publication(
    product_security, review_first
):
    client, database, tokens = product_security
    scenario, run_id = _completed(product_security)
    if review_first:
        approved = _command(client, tokens, scenario, "approve", run_id=run_id)
        assert approved.status_code == 200
    with database.session() as session:
        run = session.get(OptimizationRun, run_id)
        tampered = deepcopy(run.result_json)
        original = tampered["schedule"]["assignments"][0]
        original["worker_id"] = "worker-02" if original["worker_id"] == "worker-01" else "worker-01"
        # Leave the old successful validation intact: the API must recompute it.
        assert tampered["validation"]["valid"] is True
        session.execute(
            update(OptimizationRun).where(OptimizationRun.id == run_id).values(result_json=tampered)
        )
    response = _command(
        client, tokens, scenario, "publish" if review_first else "approve", run_id=run_id
    )
    assert response.status_code == 409, response.text
    current = client.get(f"/api/v1/scenarios/{scenario['id']}", headers=tokens["a-safety_officer"])
    assert current.json()["published_at"] is None


def test_workers_only_receive_own_published_tasks(product_security):
    client, _, tokens = product_security
    scenario, run_id = _completed(product_security)
    before = client.get("/api/v1/workers/me/schedule", headers=tokens["a-worker"])
    assert before.json()["scenarios"] == []
    for command in ("approve", "publish"):
        response = _command(client, tokens, scenario, command, run_id=run_id)
        assert response.status_code == 200, response.text
    for account, worker_id, task_id in (
        ("a-worker", "worker-01", "task-1"),
        ("a-other_worker", "worker-02", "task-2"),
    ):
        own = client.get("/api/v1/workers/me/schedule", headers=tokens[account])
        assert own.status_code == 200
        assert own.json()["worker_id"] == worker_id
        published = own.json()["scenarios"]
        assert len(published) == 1
        assert {item["task_id"] for item in published[0]["assignments"]} == {task_id}
        assert {item["worker_id"] for item in published[0]["recoveries"]} == {worker_id}
        assert {item["worker_id"] for item in published[0]["interventions"]} == {worker_id}
    other = client.get("/api/v1/workers/me/schedule", headers=tokens["b-worker"])
    assert other.json()["scenarios"] == []


def test_intervention_reads_and_acknowledgements_keep_tenant_and_worker_boundaries(
    product_security,
):
    client, _, tokens = product_security
    scenario, _ = _published(product_security)
    response = client.get(
        "/api/v1/interventions",
        params={"scenario_id": scenario["id"]},
        headers=tokens["a-supervisor"],
    )
    assert response.status_code == 200, response.text
    interventions = response.json()["items"]
    own = next(item for item in interventions if item["worker_id"] == "worker-01")
    for account in ("a-other_worker", "b-worker"):
        response = client.post(
            f"/api/v1/interventions/{own['id']}/acknowledge",
            headers=tokens[account] | {"Idempotency-Key": f"forged-{account}"},
            json={},
        )
        assert response.status_code == 404, response.text
    foreign_list = client.get(
        "/api/v1/interventions",
        params={"scenario_id": scenario["id"]},
        headers=tokens["b-supervisor"],
    )
    assert foreign_list.status_code == 404
    foreign_verify = client.post(
        f"/api/v1/interventions/{own['id']}/verify",
        headers=tokens["b-supervisor"] | {"Idempotency-Key": "foreign-verify"},
        json={"note": "Attempted cross-tenant verification"},
    )
    assert foreign_verify.status_code == 404


def test_raw_health_data_is_not_exposed_in_operational_audit_or_outbox(product_security):
    from heatshield.workflow_models import AuditEvent, Outbox, PrivateHealthReport

    client, database, tokens = product_security
    scenario, _ = _published(product_security)
    symptom = "private-symptom-security-marker"
    response = client.post(
        "/api/v1/workers/me/symptoms",
        headers=tokens["a-worker"],
        json={"severity": "severe", "symptoms": [symptom]},
    )
    assert response.status_code == 201, response.text
    assert response.json()["stop_work"] is True
    assert response.json()["dispatch_performed"] is False
    with database.session() as session:
        reports = list(session.scalars(select(PrivateHealthReport)))
        assert len(reports) == 1
        assert reports[0].worker_id == "worker-01"
        assert reports[0].symptoms_json == [symptom]
        for model in (AuditEvent, Outbox):
            for row in session.scalars(select(model)):
                assert symptom not in str(row.payload_json)
                assert "symptoms" not in row.payload_json
                assert "severity" not in row.payload_json
    audit = client.get(f"/api/v1/scenarios/{scenario['id']}/audit", headers=tokens["a-supervisor"])
    assert audit.status_code == 200
    assert symptom not in audit.text
    own_schedule = client.get("/api/v1/workers/me/schedule", headers=tokens["a-worker"])
    assert own_schedule.json()["stop_work"] is True
    foreign_schedule = client.get("/api/v1/workers/me/schedule", headers=tokens["b-worker"])
    assert foreign_schedule.json()["stop_work"] is False


def test_commands_reject_request_owned_tenant_actor_and_worker_identity(product_security):
    client, _, tokens = product_security
    created = client.post(
        "/api/v1/scenarios",
        headers=tokens["a-supervisor"],
        json={
            "name": "Forged tenant binding",
            "scenario": security_scenario(),
            "organization_id": "tenant-b",
            "actor_id": "b-admin",
        },
    )
    assert created.status_code == 422
    symptom = client.post(
        "/api/v1/workers/me/symptoms",
        headers=tokens["a-worker"],
        json={
            "severity": "mild",
            "symptoms": ["Synthetic report"],
            "worker_id": "worker-02",
        },
    )
    assert symptom.status_code == 422


def test_published_worker_view_hides_a_persisted_schedule_changed_after_review(product_security):
    client, database, tokens = product_security
    _, run_id = _published(product_security)
    with database.session() as session:
        run = session.get(OptimizationRun, run_id)
        changed = deepcopy(run.result_json)
        changed["schedule"]["assignments"][0]["worker_id"] = "unapproved-worker"
        session.execute(
            update(OptimizationRun).where(OptimizationRun.id == run_id).values(result_json=changed)
        )
    for account in ("a-worker", "a-other_worker"):
        response = client.get("/api/v1/workers/me/schedule", headers=tokens[account])
        assert response.status_code == 200
        assert response.json()["scenarios"] == []


def test_api_responses_prevent_sensitive_caching_and_embedding(product_security):
    client, _, tokens = product_security
    response = client.get("/api/v1/auth/me", headers=tokens["a-supervisor"])
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_request_body_limit_precedes_expensive_or_authenticated_work(product_security):
    client, _, _ = product_security
    response = client.post(
        "/api/v1/scenarios/optimize",
        content=b"x" * (1_048_576 + 1),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413


def test_corrupted_linked_record_ids_cannot_disclose_another_tenants_data(product_security):
    from heatshield.workflow_models import Approval, ScenarioRecord

    client, database, tokens = product_security
    own = _create(client, tokens)
    foreign = _create(client, tokens, tenant="b")
    with database.session() as session:
        foreign_run = OptimizationRun(
            organization_id="tenant-b",
            scenario_id=foreign["id"],
            revision=foreign["revision"],
            scenario_hash=foreign["scenario_hash"],
            policy_hash=foreign["policy_hash"],
            requested_by="b-supervisor",
            status="COMPLETED",
            result_json={"private_marker": "tenant-b-linked-record-confidential"},
        )
        session.add(foreign_run)
        session.flush()
        foreign_approval = Approval(
            organization_id="tenant-b",
            scenario_id=foreign["id"],
            revision=foreign["revision"],
            run_id=foreign_run.id,
            scenario_hash=foreign["scenario_hash"],
            policy_hash=foreign["policy_hash"],
            schedule_hash="unrelated-tenant-schedule",
            validator_version="security-test",
            actor_id="b-safety_officer",
        )
        session.add(foreign_approval)
        session.flush()
        # Simulate corrupted pointers without granting the API caller SQL access.
        session.execute(
            update(ScenarioRecord)
            .where(ScenarioRecord.id == own["id"])
            .values(latest_run_id=foreign_run.id, approval_id=foreign_approval.id)
        )
    response = client.get(f"/api/v1/scenarios/{own['id']}", headers=tokens["a-supervisor"])
    assert response.status_code == 200
    assert response.json()["latest_run"] is None
    assert response.json()["approval"] is None
    assert "tenant-b-linked-record-confidential" not in response.text
