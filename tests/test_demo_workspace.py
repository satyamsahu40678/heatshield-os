"""HTTP-level checks for additive, private and computed demo workspace data."""

import json
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from heatshield.api import create_app
from heatshield.auth import create_access_token
from heatshield.bootstrap import DEMO_ORGANIZATION_ID, DEMO_PASSWORD
from heatshield.database import Database
from heatshield.fixtures import demo_scenario
from heatshield.identity import Organization, User
from heatshield.models import OptimizationResult, Scenario
from heatshield.provenance import content_hash
from heatshield.settings import Settings
from heatshield.showcase import SHOWCASE_HOLD_WORKER, SHOWCASE_IDS
from heatshield.validation import validate_schedule
from heatshield.worker import run_once
from heatshield.workflow_models import (
    Approval,
    AuditEvent,
    IdempotencyRecord,
    Intervention,
    InterventionEvent,
    OptimizationRun,
    Outbox,
    PrivateHealthReport,
    ScenarioRecord,
    ScenarioRevision,
    WorkerHold,
)


def _headers(database, settings, email):
    with database.session() as session:
        actor = session.scalar(select(User).where(User.email == email))
        assert actor is not None
        return {"Authorization": f"Bearer {create_access_token(actor, settings)}"}


@contextmanager
def _application(database, *, demo_content=False, demo_mode=True):
    settings = Settings(
        database_url=str(database.engine.url),
        demo_mode=demo_mode,
        demo_content=demo_content,
        jwt_secret="isolated-demo-workspace-test-signing-secret-48-bytes",
    )
    with TestClient(create_app(settings=settings, database=database)) as client:
        yield client, settings


@pytest.fixture
def database(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'demo-workspace.db'}")
    database.initialize()
    yield database
    database.dispose()


@pytest.fixture(scope="module")
def populated_workspace(tmp_path_factory):
    path = tmp_path_factory.mktemp("populated-workspace") / "workspace.db"
    database = Database(f"sqlite:///{path}")
    database.initialize()
    try:
        with _application(database, demo_content=True) as (client, settings):
            yield client, database, settings
    finally:
        database.dispose()


def _workspace(client, database, settings, email="supervisor@example.test"):
    response = client.get("/api/v1/demo/workspace", headers=_headers(database, settings, email))
    assert response.status_code == 200, response.text
    return response.json()


def _counts(database):
    with database.session() as session:
        return {
            model.__tablename__: session.scalar(select(func.count()).select_from(model))
            for model in (
                User,
                ScenarioRecord,
                ScenarioRevision,
                OptimizationRun,
                Approval,
                Intervention,
                InterventionEvent,
                PrivateHealthReport,
                WorkerHold,
                AuditEvent,
                Outbox,
                IdempotencyRecord,
            )
        }


def _create(client, database, settings, name="User-created scenario"):
    response = client.post(
        "/api/v1/scenarios",
        headers=_headers(database, settings, "supervisor@example.test"),
        json={"name": name, "scenario": demo_scenario().model_dump(mode="json")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _enqueue(client, database, settings, record):
    response = client.post(
        f"/api/v1/scenarios/{record['id']}/optimize",
        headers=_headers(database, settings, "supervisor@example.test")
        | {"Idempotency-Key": "user-planning-job"},
        json={"expected_revision": record["revision"]},
    )
    assert response.status_code == 202, response.text
    return response.json()


def _review_command(client, database, settings, record, run_id, command):
    return client.post(
        f"/api/v1/scenarios/{record['id']}/{command}",
        headers=_headers(database, settings, "safety@example.test")
        | {"Idempotency-Key": f"user-{command}"},
        json={"expected_revision": record["revision"], "run_id": run_id},
    )


def test_operations_aggregate_requires_authentication_and_a_staff_role(populated_workspace):
    client, database, settings = populated_workspace
    assert client.get("/api/v1/demo/workspace").status_code == 401
    worker = client.get(
        "/api/v1/demo/workspace",
        headers=_headers(database, settings, "worker@example.test"),
    )
    assert worker.status_code == 403
    for email in ("admin@example.test", "safety@example.test", "supervisor@example.test"):
        response = client.get("/api/v1/demo/workspace", headers=_headers(database, settings, email))
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"


def test_workspace_metrics_are_computed_from_current_validated_published_records(
    populated_workspace,
):
    client, database, settings = populated_workspace
    body = _workspace(client, database, settings)
    workforce, minutes, published = set(), 0, {}
    work_minutes, recovery_minutes = Counter(), Counter()
    with database.session() as session:
        records = list(
            session.scalars(
                select(ScenarioRecord).where(ScenarioRecord.organization_id == DEMO_ORGANIZATION_ID)
            )
        )
        assert {item["id"] for item in body["scenarios"]} == {record.id for record in records}
        for record in records:
            snapshot = session.scalar(
                select(ScenarioRevision).where(
                    ScenarioRevision.scenario_id == record.id,
                    ScenarioRevision.revision == record.revision,
                )
            )
            scenario = Scenario.model_validate(snapshot.scenario_json)
            workforce.update(worker.id for worker in scenario.workers)
            if record.status != "PUBLISHED":
                continue
            run = session.get(OptimizationRun, record.latest_run_id)
            result = OptimizationResult.model_validate(run.result_json)
            assert run.status == "COMPLETED" and result.status in {"OPTIMAL", "FEASIBLE"}
            assert validate_schedule(scenario, result.schedule).valid
            approval = session.get(Approval, record.approval_id)
            assert approval.schedule_hash == content_hash(result.schedule)
            published[record.id] = record
            for interval in result.schedule.recoveries:
                duration = int((interval.ends_at - interval.starts_at).total_seconds() / 60)
                minutes += duration
                recovery_minutes[interval.worker_id] += duration
            for interval in result.schedule.assignments:
                work_minutes[interval.worker_id] += int(
                    (interval.ends_at - interval.starts_at).total_seconds() / 60
                )
        held_ids = set(
            session.scalars(
                select(WorkerHold.worker_id).where(
                    WorkerHold.organization_id == DEMO_ORGANIZATION_ID,
                    WorkerHold.active.is_(True),
                )
            )
        )
        current_interventions = [
            item
            for item in session.scalars(select(Intervention))
            if item.scenario_id in published
            and item.revision == published[item.scenario_id].revision
            and item.run_id == published[item.scenario_id].latest_run_id
        ]
    assert body["metrics"] == {
        "workers": len(workforce | held_ids),
        "protected_recovery_minutes": minutes,
        "verified_interventions": sum(item.status == "VERIFIED" for item in current_interventions),
        "total_interventions": len(current_interventions),
        "attention_items": len(body["attention"]),
        "published_plans": len(published),
    }
    assert minutes > 0 and current_interventions
    assert {item["id"] for item in body["workers"]} == workforce | held_ids
    for worker in body["workers"]:
        assert worker["work_minutes"] == work_minutes[worker["id"]]
        assert worker["recovery_minutes"] == recovery_minutes[worker["id"]]
    unknown = next(item for item in body["workers"] if item["id"] == SHOWCASE_HOLD_WORKER)
    assert unknown["skills"] == [] and unknown["acclimatized"] is None
    assert unknown["status"] == "STOP_WORK"


def test_curve_and_shift_date_are_retained_synthetic_evidence(populated_workspace):
    client, database, settings = populated_workspace
    body = _workspace(client, database, settings)
    with database.session() as session:
        record = session.get(ScenarioRecord, body["active_scenario_id"])
        snapshot = session.scalar(
            select(ScenarioRevision).where(
                ScenarioRevision.scenario_id == record.id,
                ScenarioRevision.revision == record.revision,
            )
        )
        scenario = Scenario.model_validate(snapshot.scenario_json)
    assert body["curve"] == [
        {
            "observed_at": observation.observed_at.isoformat(),
            "wbgt_c": observation.wbgt_c,
            "zone_id": observation.zone_id,
            "source": observation.source,
        }
        for observation in scenario.observations
    ]
    assert body["worksite"]["timezone"] == scenario.timezone
    assert "Synthetic shift" in body["worksite"]["replay_label"]
    assert "2026" in body["worksite"]["replay_label"]
    assert {point["source"] for point in body["curve"]} == {"simulated"}


def test_demo_load_is_role_guarded_additive_and_rejects_authority_injection(populated_workspace):
    client, database, settings = populated_workspace
    before = _counts(database)
    assert client.post("/api/v1/demo/load", json={}).status_code == 401
    for email in ("worker@example.test", "supervisor@example.test"):
        response = client.post(
            "/api/v1/demo/load", headers=_headers(database, settings, email), json={}
        )
        assert response.status_code == 403
    authority = client.post(
        "/api/v1/demo/load",
        headers=_headers(database, settings, "admin@example.test"),
        json={"organization_id": "second-demo-organization", "actor_id": "untrusted"},
    )
    assert authority.status_code == 422
    for email in ("safety@example.test", "admin@example.test"):
        response = client.post(
            "/api/v1/demo/load", headers=_headers(database, settings, email), json={}
        )
        assert response.status_code == 200
        assert {item["id"] for item in response.json()["scenarios"]} == set(SHOWCASE_IDS)
    assert _counts(database) == before


def test_workspace_is_tenant_scoped_and_never_exposes_private_or_credential_payloads(
    populated_workspace,
):
    client, database, settings = populated_workspace
    with database.session() as session:
        session.add(
            User(
                id="foreign-workspace-admin",
                organization_id="second-demo-organization",
                email="foreign-workspace@example.test",
                display_name="Foreign private actor marker",
                role="admin",
                password_hash="foreign-credential-marker",
            )
        )
        session.add(
            PrivateHealthReport(
                organization_id="second-demo-organization",
                worker_id="foreign-worker-marker",
                user_id="foreign-workspace-admin",
                severity="severe",
                symptoms_json=["foreign-health-payload-marker"],
                urgent=True,
            )
        )
        session.add(
            AuditEvent(
                organization_id="second-demo-organization",
                actor_id="foreign-workspace-admin",
                entity_type="worker",
                entity_id="foreign-worker-marker",
                action="foreign-action-marker",
                payload_json={"private": "foreign-audit-payload-marker"},
            )
        )
        private_texts = [
            symptom
            for report in session.scalars(select(PrivateHealthReport))
            for symptom in report.symptoms_json
        ]
    own = _workspace(client, database, settings)
    encoded = json.dumps(own)
    for text in private_texts + [DEMO_PASSWORD, settings.jwt_secret, "foreign-"]:
        assert text not in encoded

    def keys(value):
        if isinstance(value, dict):
            yield from value
            for child in value.values():
                yield from keys(child)
        elif isinstance(value, list):
            for child in value:
                yield from keys(child)

    assert not {
        "password",
        "password_hash",
        "token_version",
        "access_token",
        "symptoms",
        "symptoms_json",
        "severity",
    }.intersection(keys(own))
    foreign = _workspace(client, database, settings, "foreign-workspace@example.test")
    assert foreign["demo_enabled"] is False
    assert foreign["scenarios"] == foreign["workers"] == foreign["curve"] == []
    assert foreign["active_scenario_id"] is None
    assert all(value == 0 for value in foreign["metrics"].values())
    assert {item["action"] for item in foreign["activity"]} == {"foreign-action-marker"}
    assert "showcase-" not in json.dumps(foreign)
    denied = client.post(
        "/api/v1/demo/load",
        headers=_headers(database, settings, "foreign-workspace@example.test"),
        json={},
    )
    assert denied.status_code == 404


def test_non_demo_load_is_unavailable_without_writes(database):
    with database.session() as session:
        session.add(Organization(id=DEMO_ORGANIZATION_ID, name="Preexisting organization"))
        session.flush()
        session.add(
            User(
                id="non-demo-admin",
                organization_id=DEMO_ORGANIZATION_ID,
                email="non-demo-admin@example.test",
                display_name="Non-demo administrator",
                role="admin",
                password_hash="not-used-by-authenticated-token-fixture",
            )
        )
    before = _counts(database)
    with _application(database, demo_mode=False, demo_content=True) as (client, settings):
        response = client.post(
            "/api/v1/demo/load",
            headers=_headers(database, settings, "non-demo-admin@example.test"),
            json={},
        )
        assert response.status_code == 404
        assert not _workspace(client, database, settings, "non-demo-admin@example.test")[
            "demo_enabled"
        ]
    assert _counts(database) == before


def test_startup_and_reload_preserve_custom_revisions_credentials_and_revocation(database):
    with _application(database, demo_content=False) as (client, settings):
        record = _create(client, database, settings, "User-owned planning inputs")
        revised_input = deepcopy(record["scenario"])
        revised_input["policy"]["version"] = "custom-user-policy-kept"
        revised = client.put(
            f"/api/v1/scenarios/{record['id']}",
            headers=_headers(database, settings, "supervisor@example.test"),
            json={
                "expected_revision": 1,
                "name": "User-owned revision two",
                "scenario": revised_input,
            },
        )
        assert revised.status_code == 200
        expected = revised.json()
    with database.session() as session:
        user = session.scalar(select(User).where(User.email == "supervisor@example.test"))
        user.display_name = "Customized supervisor identity"
        user.password_hash = "custom-password-hash-preserved"
        user.token_version = 19
    with _application(database, demo_content=True) as (client, settings):
        current = client.get(
            f"/api/v1/scenarios/{record['id']}",
            headers=_headers(database, settings, "supervisor@example.test"),
        )
        assert current.status_code == 200
        for field in ("name", "revision", "scenario", "scenario_hash", "policy_hash"):
            assert current.json()[field] == expected[field]
        before = _counts(database)
        reloaded = client.post(
            "/api/v1/demo/load",
            headers=_headers(database, settings, "safety@example.test"),
            json={},
        )
        assert reloaded.status_code == 200
        assert _counts(database) == before
    with database.session() as session:
        user = session.scalar(select(User).where(User.email == "supervisor@example.test"))
        assert user.display_name == "Customized supervisor identity"
        assert user.password_hash == "custom-password-hash-preserved"
        assert user.token_version == 19
        assert (
            session.scalar(
                select(func.count())
                .select_from(ScenarioRevision)
                .where(ScenarioRevision.scenario_id == record["id"])
            )
            == 2
        )


def test_enabling_startup_content_never_claims_an_existing_user_job(database):
    with _application(database, demo_content=False) as (client, settings):
        record = _create(client, database, settings, "Keep this queued user work untouched")
        queued = _enqueue(client, database, settings, record)
    with database.session() as session:
        original = session.get(ScenarioRecord, record["id"])
        previous = (original.lock_version, original.updated_at)
    with _application(database, demo_content=True) as (client, settings):
        dashboard = _workspace(client, database, settings)
        assert record["id"] in {item["id"] for item in dashboard["scenarios"]}
        with database.session() as session:
            run = session.get(OptimizationRun, queued["id"])
            assert run.status == "QUEUED" and run.attempts == 0
            assert run.result_json is None and run.lease_owner is None
            current = session.get(ScenarioRecord, record["id"])
            assert current.latest_run_id == run.id and current.status == "QUEUED"
            assert (current.lock_version, current.updated_at) == previous
        reloaded = client.post(
            "/api/v1/demo/load",
            headers=_headers(database, settings, "admin@example.test"),
            json={},
        )
        assert reloaded.status_code == 200
        with database.session() as session:
            run = session.get(OptimizationRun, queued["id"])
            assert run.status == "QUEUED" and run.attempts == 0
            assert run.result_json is None


def test_rich_content_does_not_block_normal_core_worker_planning_and_publication(database):
    with _application(database, demo_content=True) as (client, settings):
        with database.session() as session:
            held = set(
                session.scalars(select(WorkerHold.worker_id).where(WorkerHold.active.is_(True)))
            )
        assert not held.intersection({f"worker-{index:02}" for index in range(1, 7)})
        record = _create(client, database, settings)
        run = _enqueue(client, database, settings, record)
        assert run_once(database, worker_id="demo-workspace-integration-worker")
        for command in ("approve", "publish"):
            response = _review_command(client, database, settings, record, run["id"], command)
            assert response.status_code == 200, response.text
            assert response.json()["operational_approval_allowed"] is False
        assert response.json()["status"] == "PUBLISHED"


def test_seeded_review_and_verification_are_explicitly_simulated(populated_workspace):
    client, database, settings = populated_workspace
    staff = _headers(database, settings, "safety@example.test")
    body = _workspace(client, database, settings)
    for card in body["scenarios"]:
        detail = client.get(f"/api/v1/scenarios/{card['id']}", headers=staff)
        assert detail.status_code == 200
        current = detail.json()
        assert current["scenario"]["mode"] == "synthetic"
        assert current["scenario"]["policy"]["demo_only"] is True
        assert current["operational_approval_allowed"] is False
        if current["approval"]:
            assert current["approval"]["type"] == "SYNTHETIC_REVIEW"
            assert current["approval"]["operational_approval_allowed"] is False
    response = client.get("/api/v1/interventions", headers=staff)
    for intervention in response.json()["items"]:
        assert intervention["payload"]["synthetic_only"] is True
        assert intervention["payload"]["messages"]["en"].startswith("SIMULATION:")
        for event in intervention["events"]:
            if event["type"] == "VERIFIED":
                assert event["note"].startswith("SIMULATION:")


@pytest.mark.parametrize("corruption", ["run_status", "result_status", "schedule"])
def test_invalid_published_candidate_is_removed_from_protection_metrics(database, corruption):
    with _application(database, demo_content=True) as (client, settings):
        initial = _workspace(client, database, settings)
        assert initial["metrics"]["published_plans"] == 1
        assert initial["metrics"]["protected_recovery_minutes"] > 0
        with database.session() as session:
            record = session.scalar(
                select(ScenarioRecord).where(ScenarioRecord.status == "PUBLISHED")
            )
            run = session.get(OptimizationRun, record.latest_run_id)
            if corruption == "run_status":
                run.status = "FAILED"
            else:
                changed = deepcopy(run.result_json)
                if corruption == "result_status":
                    changed["status"] = "UNKNOWN"
                else:
                    changed["schedule"]["assignments"][0]["worker_id"] = "unknown-worker"
                session.execute(
                    update(OptimizationRun)
                    .where(OptimizationRun.id == run.id)
                    .values(result_json=changed)
                )
        current = _workspace(client, database, settings)
        assert current["metrics"]["published_plans"] == 0
        assert current["metrics"]["protected_recovery_minutes"] == 0
        assert current["metrics"]["verified_interventions"] == 0
        assert current["metrics"]["total_interventions"] == 0
        assert all(worker["recovery_minutes"] == 0 for worker in current["workers"])
        assert any(
            item["kind"] == "INTEGRITY" and item["scenario_id"] == record.id
            for item in current["attention"]
        )


def test_content_constructor_and_environment_defaults_keep_test_and_real_data_separate(monkeypatch):
    monkeypatch.delenv("HEATSHIELD_DEMO", raising=False)
    monkeypatch.delenv("HEATSHIELD_DEMO_CONTENT", raising=False)
    assert Settings().demo_content is False
    assert Settings.from_env().demo_content is True
    monkeypatch.setenv("HEATSHIELD_DEMO_CONTENT", "false")
    assert Settings.from_env().demo_content is False
    monkeypatch.setenv("HEATSHIELD_DEMO", "false")
    monkeypatch.setenv("HEATSHIELD_DEMO_CONTENT", "true")
    monkeypatch.setenv("HEATSHIELD_JWT_SECRET", "independent-environment-secret-at-least-32-bytes")
    assert Settings.from_env().demo_content is False
    monkeypatch.setenv("HEATSHIELD_DEMO_CONTENT", "maybe")
    with pytest.raises(ValueError, match="HEATSHIELD_DEMO_CONTENT"):
        Settings.from_env()
