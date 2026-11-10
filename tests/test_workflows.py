"""Persistent synthetic workflows, tested through authenticated HTTP boundaries."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from heatshield.auth import create_access_token
from heatshield.database import Database
from heatshield.identity import Organization, User
from heatshield.models import Scenario
from heatshield.persistent_api import router
from heatshield.settings import Settings
from heatshield.worker import run_once
from heatshield.workflow_models import (
    AuditEvent,
    IdempotencyRecord,
    InterventionEvent,
    OptimizationRun,
    Outbox,
    PrivateHealthReport,
    ScenarioRevision,
)


def small_scenario():
    start = datetime(2026, 7, 15, 2, tzinfo=UTC)
    end = start + timedelta(hours=2)
    return Scenario.model_validate(
        {
            "id": "workflow-fixture",
            "starts_at": start,
            "ends_at": end,
            "workers": [
                {
                    "id": worker_id,
                    "skills": ["general"],
                    "available_from": start,
                    "available_until": end,
                }
                for worker_id in ("worker-01", "worker-02")
            ],
            "tasks": [
                {
                    "id": f"task-{number}",
                    "required_skill": "general",
                    "zone_id": "yard",
                    "duration_minutes": 15,
                    "release_at": start,
                    "deadline": end,
                }
                for number in range(3)
            ],
            "observations": [
                {
                    "id": f"sample-{hour}",
                    "zone_id": "yard",
                    "observed_at": start + timedelta(hours=hour),
                    "valid_from": start + timedelta(hours=hour),
                    "valid_until": start + timedelta(hours=hour + 1),
                    "wbgt_c": 26.0,
                }
                for hour in range(2)
            ],
        }
    )


@pytest.fixture
def workspace(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'workflow.db'}")
    database.initialize()
    settings = Settings(database_url=str(database.engine.url))
    tokens = {}
    with database.session() as session:
        session.add_all([Organization(id="org-a", name="A"), Organization(id="org-b", name="B")])
        session.flush()
        for role, worker_id, org in (
            ("admin", None, "org-a"),
            ("safety_officer", None, "org-a"),
            ("supervisor", None, "org-a"),
            ("worker", "worker-01", "org-a"),
            ("worker2", "worker-02", "org-a"),
            ("other", None, "org-b"),
        ):
            user = User(
                id=f"user-{role}",
                organization_id=org,
                email=f"{role}@example.test",
                display_name=role,
                role="worker" if role == "worker2" else "admin" if role == "other" else role,
                worker_id=worker_id,
                password_hash="unused-in-token-fixture",
            )
            session.add(user)
            session.flush()
            tokens[role] = create_access_token(user, settings)
    app = FastAPI()
    app.state.settings = settings
    app.state.database = database
    app.include_router(router)
    with TestClient(app) as client:
        yield client, database, tokens
    database.dispose()


def headers(tokens, role="admin", key=None):
    result = {"Authorization": f"Bearer {tokens[role]}"}
    if key:
        result["Idempotency-Key"] = key
    return result


def created(workspace, scenario=None, role="admin"):
    client, _, tokens = workspace
    response = client.post(
        "/api/v1/scenarios",
        headers=headers(tokens, role),
        json={
            "name": "Synthetic yard",
            "scenario": (scenario or small_scenario()).model_dump(mode="json"),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def completed(workspace, scenario=None, role="admin"):
    client, database, tokens = workspace
    detail = created(workspace, scenario, role)
    response = client.post(
        f"/api/v1/scenarios/{detail['id']}/optimize",
        headers=headers(tokens, role, key="queue-one"),
        json={"expected_revision": 1},
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]
    assert run_once(database)
    run = client.get(f"/api/v1/optimization-runs/{run_id}", headers=headers(tokens, role)).json()
    assert run["status"] == "COMPLETED", run
    assert run["result"]["validation"]["valid"]
    return detail, run


def published(workspace, scenario=None, role="admin"):
    client, _, tokens = workspace
    detail, run = completed(workspace, scenario, role)
    reviewer = "other" if role == "other" else "safety_officer"
    payload = {"expected_revision": 1, "run_id": run["id"]}
    response = client.post(
        f"/api/v1/scenarios/{detail['id']}/approve",
        headers=headers(tokens, reviewer, "review-one"),
        json=payload,
    )
    assert response.status_code == 200, response.text
    assert response.json()["approval"]["type"] == "SYNTHETIC_REVIEW"
    response = client.post(
        f"/api/v1/scenarios/{detail['id']}/publish",
        headers=headers(tokens, reviewer, "publish-one"),
        json=payload,
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "PUBLISHED"
    assert not response.json()["operational_approval_allowed"]
    return detail, run


def test_complete_workflow_keeps_acknowledgement_and_verification_distinct(workspace):
    client, database, tokens = workspace
    detail, _ = published(workspace)
    own = client.get("/api/v1/workers/me/schedule", headers=headers(tokens, "worker")).json()
    assert own["scenarios"]
    assert not own["stop_work"]
    assert all(a["worker_id"] == "worker-01" for s in own["scenarios"] for a in s["assignments"])
    item = own["scenarios"][0]["interventions"][0]
    assert set(item["payload"]["messages"]) == {"en", "hi", "ar"}
    verify_path = f"/api/v1/interventions/{item['id']}/verify"
    assert (
        client.post(
            verify_path,
            headers=headers(tokens, "supervisor", "too-early"),
            json={"note": "Observed recovery"},
        ).status_code
        == 409
    )
    acknowledge_path = f"/api/v1/interventions/{item['id']}/acknowledge"
    first = client.post(acknowledge_path, headers=headers(tokens, "worker", "received"), json={})
    replay = client.post(acknowledge_path, headers=headers(tokens, "worker", "received"), json={})
    assert first.status_code == 200
    assert replay.json() == first.json()
    verified = client.post(
        verify_path,
        headers=headers(tokens, "supervisor", "observed"),
        json={"note": "Observed recovery"},
    )
    assert verified.status_code == 200, verified.text
    assert [event["type"] for event in verified.json()["events"]] == ["ACKNOWLEDGED", "VERIFIED"]
    assert verified.json()["status"] == "VERIFIED"
    audit = client.get(f"/api/v1/scenarios/{detail['id']}/audit", headers=headers(tokens)).json()
    assert {
        "scenario.demo_reviewed",
        "scenario.demo_published",
        "intervention.acknowledged",
        "intervention.verified",
    } <= {event["action"] for event in audit["events"]}
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(InterventionEvent)) == 2
        assert session.scalar(select(func.count()).select_from(Outbox)) >= 3


def test_enqueue_idempotency_replays_original_response_and_conflicts_on_changed_request(workspace):
    client, database, tokens = workspace
    detail = created(workspace)
    path = f"/api/v1/scenarios/{detail['id']}/optimize"
    first = client.post(path, headers=headers(tokens, key="stable"), json={"expected_revision": 1})
    second = client.post(path, headers=headers(tokens, key="stable"), json={"expected_revision": 1})
    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    assert (
        client.post(
            path, headers=headers(tokens, key="stable"), json={"expected_revision": 2}
        ).status_code
        == 409
    )
    assert (
        client.post(
            path, headers=headers(tokens, key="different"), json={"expected_revision": 1}
        ).status_code
        == 409
    )
    assert run_once(database)
    replay = client.post(path, headers=headers(tokens, key="stable"), json={"expected_revision": 1})
    assert replay.json() == first.json()
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(OptimizationRun)) == 1
        assert session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 1


def test_revision_preserves_snapshot_history_and_invalidates_publication(workspace):
    client, database, tokens = workspace
    detail, run = published(workspace)
    revised = client.put(
        f"/api/v1/scenarios/{detail['id']}",
        headers=headers(tokens),
        json={
            "expected_revision": 1,
            "name": "Changed inputs",
            "scenario": small_scenario().model_dump(mode="json"),
        },
    )
    assert revised.status_code == 200, revised.text
    assert revised.json()["revision"] == 2
    assert revised.json()["approval"] is None
    assert revised.json()["published_at"] is None
    assert revised.json()["latest_run"] is None
    assert (
        client.get("/api/v1/workers/me/schedule", headers=headers(tokens, "worker")).json()[
            "scenarios"
        ]
        == []
    )
    assert (
        client.post(
            f"/api/v1/scenarios/{detail['id']}/publish",
            headers=headers(tokens, key="stale"),
            json={"expected_revision": 1, "run_id": run["id"]},
        ).status_code
        == 409
    )
    with database.session() as session:
        revisions = list(
            session.scalars(select(ScenarioRevision).order_by(ScenarioRevision.revision))
        )
        assert [item.revision for item in revisions] == [1, 2]
        revisions[0].scenario_hash = "tampered"
        with pytest.raises(ValueError, match="append-only"):
            session.flush()
        session.rollback()


def test_tenant_and_worker_resource_boundaries(workspace):
    client, _, tokens = workspace
    detail, run = published(workspace)
    for path in (
        f"/api/v1/scenarios/{detail['id']}",
        f"/api/v1/scenarios/{detail['id']}/audit",
        f"/api/v1/optimization-runs/{run['id']}",
    ):
        assert client.get(path, headers=headers(tokens, "other")).status_code == 404
    assert client.get("/api/v1/scenarios", headers=headers(tokens, "other")).json() == []
    assert (
        client.get(
            f"/api/v1/scenarios/{detail['id']}", headers=headers(tokens, "worker")
        ).status_code
        == 403
    )
    items = client.get("/api/v1/interventions", headers=headers(tokens, "worker")).json()["items"]
    assert items and all(item["worker_id"] == "worker-01" for item in items)
    assert (
        client.post(
            f"/api/v1/interventions/{items[0]['id']}/acknowledge",
            headers=headers(tokens, "worker2", "not-own"),
            json={},
        ).status_code
        == 404
    )


def test_private_report_stops_work_without_leaking_symptoms_to_operational_records(workspace):
    client, database, tokens = workspace
    detail, run = published(workspace)
    response = client.post(
        "/api/v1/workers/me/symptoms",
        headers=headers(tokens, "worker"),
        json={"severity": "severe", "symptoms": ["private-health-marker"]},
    )
    assert response.status_code == 201, response.text
    assert response.json()["urgent"] and response.json()["stop_work"]
    assert not response.json()["dispatch_performed"]
    holds = client.get("/api/v1/workers/holds", headers=headers(tokens, "supervisor"))
    assert holds.status_code == 200
    assert holds.json()["items"][0]["worker_id"] == "worker-01"
    assert holds.json()["items"][0]["owner_role"] == "safety_officer"
    assert "private-health-marker" not in holds.text
    assert (
        client.get("/api/v1/workers/holds", headers=headers(tokens, "other")).json()["items"] == []
    )
    assert client.get("/api/v1/workers/holds", headers=headers(tokens, "worker")).status_code == 403
    own = client.get("/api/v1/workers/me/schedule", headers=headers(tokens, "worker")).json()
    assert own["stop_work"] and own["scenarios"] == []
    assert (
        client.post(
            f"/api/v1/scenarios/{detail['id']}/optimize",
            headers=headers(tokens, key="held"),
            json={"expected_revision": 1},
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/v1/scenarios/{detail['id']}/approve",
            headers=headers(tokens, key="held-review"),
            json={"expected_revision": 1, "run_id": run["id"]},
        ).status_code
        == 409
    )
    with database.session() as session:
        assert session.scalar(select(PrivateHealthReport)).symptoms_json == [
            "private-health-marker"
        ]
        for event in session.scalars(select(AuditEvent)):
            assert "private-health-marker" not in str(event.payload_json)
        for event in session.scalars(select(Outbox)):
            assert "private-health-marker" not in str(event.payload_json)
    release = client.post(
        "/api/v1/workers/worker-01/release-hold",
        headers=headers(tokens, "safety_officer"),
        json={"note": "Synthetic human review recorded"},
    )
    assert release.status_code == 200, release.text
    assert not release.json()["medical_clearance"]
    assert not client.get("/api/v1/workers/me/schedule", headers=headers(tokens, "worker")).json()[
        "stop_work"
    ]


def test_invalid_or_missing_idempotency_and_role_payloads_are_rejected(workspace):
    client, _, tokens = workspace
    detail = created(workspace)
    path = f"/api/v1/scenarios/{detail['id']}/optimize"
    assert (
        client.post(path, headers=headers(tokens), json={"expected_revision": 1}).status_code == 422
    )
    assert (
        client.post(
            path,
            headers=headers(tokens, key="bad"),
            json={"expected_revision": 1, "organization_id": "org-b"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            path, headers=headers(tokens, "worker", "bad-role"), json={"expected_revision": 1}
        ).status_code
        == 403
    )


def test_changed_but_still_valid_candidate_cannot_reuse_an_approval(workspace):
    from heatshield.models import OptimizationResult
    from heatshield.validation import validate_schedule

    client, database, tokens = workspace
    detail, run = completed(workspace)
    path = f"/api/v1/scenarios/{detail['id']}"
    payload = {"expected_revision": 1, "run_id": run["id"]}
    assert (
        client.post(
            f"{path}/approve", headers=headers(tokens, key="before-tamper"), json=payload
        ).status_code
        == 200
    )
    with database.session() as session:
        stored = session.get(OptimizationRun, run["id"])
        result = OptimizationResult.model_validate(stored.result_json)
        moved = result.schedule.model_copy(
            update={
                "assignments": tuple(
                    item.model_copy(
                        update={
                            "starts_at": item.starts_at + timedelta(minutes=15),
                            "ends_at": item.ends_at + timedelta(minutes=15),
                        }
                    )
                    for item in result.schedule.assignments
                ),
                "recoveries": tuple(
                    item.model_copy(
                        update={
                            "starts_at": item.starts_at + timedelta(minutes=15),
                            "ends_at": item.ends_at + timedelta(minutes=15),
                        }
                    )
                    for item in result.schedule.recoveries
                ),
            }
        )
        assert validate_schedule(small_scenario(), moved).valid
        stored.result_json = result.model_copy(update={"schedule": moved}).model_dump(mode="json")
    response = client.post(
        f"{path}/publish", headers=headers(tokens, key="after-tamper"), json=payload
    )
    assert response.status_code == 409
    assert (
        client.get("/api/v1/workers/me/schedule", headers=headers(tokens, "worker")).json()[
            "scenarios"
        ]
        == []
    )


def single_task_scenario(start):
    end = start + timedelta(minutes=30)
    base = small_scenario()
    return Scenario.model_validate(
        base.model_dump()
        | {
            "starts_at": start,
            "ends_at": end,
            "workers": [
                base.workers[0].model_dump()
                | {
                    "available_from": start,
                    "available_until": end,
                }
            ],
            "tasks": [
                base.tasks[0].model_dump()
                | {
                    "release_at": start,
                    "deadline": start + timedelta(minutes=15),
                    "deferrable": False,
                }
            ],
            "observations": [
                base.observations[0].model_dump()
                | {
                    "observed_at": start,
                    "valid_from": start,
                    "valid_until": end,
                }
            ],
        }
    )


def review_existing(workspace, detail, run):
    client, _, tokens = workspace
    payload = {"expected_revision": 1, "run_id": run["id"]}
    response = client.post(
        f"/api/v1/scenarios/{detail['id']}/approve",
        headers=headers(tokens, key="second-review"),
        json=payload,
    )
    assert response.status_code == 200, response.text
    return payload


def test_other_published_plans_protect_recovery_as_well_as_work(workspace):
    client, _, tokens = workspace
    start = datetime(2026, 7, 15, 2, tzinfo=UTC)
    first, _ = published(workspace, single_task_scenario(start))
    second, run = completed(workspace, single_task_scenario(start + timedelta(minutes=15)))
    payload = review_existing(workspace, second, run)
    response = client.post(
        f"/api/v1/scenarios/{second['id']}/publish",
        headers=headers(tokens, key="conflicting"),
        json=payload,
    )
    assert response.status_code == 409
    assert "worker-01" in response.text and "recovery" in response.text
    assert first["id"] in response.text
    own = client.get("/api/v1/workers/me/schedule", headers=headers(tokens, "worker")).json()
    assert [plan["scenario_id"] for plan in own["scenarios"]] == [first["id"]]


def test_adjacent_cross_plan_work_and_recovery_are_allowed(workspace):
    client, _, tokens = workspace
    start = datetime(2026, 7, 15, 2, tzinfo=UTC)
    first, first_run = published(workspace, single_task_scenario(start))
    second, second_run = published(workspace, single_task_scenario(start + timedelta(minutes=30)))
    assert (
        first_run["result"]["schedule"]["recoveries"][0]["ends_at"]
        == second_run["result"]["schedule"]["assignments"][0]["starts_at"]
    )
    own = client.get("/api/v1/workers/me/schedule", headers=headers(tokens, "worker")).json()
    assert {plan["scenario_id"] for plan in own["scenarios"]} == {first["id"], second["id"]}


def test_withdrawal_admits_replacement_and_preserves_intervention_history(workspace):
    client, database, tokens = workspace
    scenario = single_task_scenario(datetime(2026, 7, 15, 2, tzinfo=UTC))
    first, _ = published(workspace, scenario)
    old_item = client.get("/api/v1/interventions", headers=headers(tokens, "worker")).json()[
        "items"
    ][0]
    assert (
        client.post(
            f"/api/v1/interventions/{old_item['id']}/acknowledge",
            headers=headers(tokens, "worker", "original-receipt"),
            json={},
        ).status_code
        == 200
    )
    second, run = completed(workspace, scenario)
    payload = review_existing(workspace, second, run)
    path = f"/api/v1/scenarios/{first['id']}/withdraw"
    withdrawal = {"expected_revision": 1, "note": "Replace this synthetic plan"}
    assert (
        client.post(
            path, headers=headers(tokens, "supervisor", "forbidden-withdraw"), json=withdrawal
        ).status_code
        == 403
    )
    withdrawn = client.post(path, headers=headers(tokens, key="withdraw-one"), json=withdrawal)
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["status"] == "WITHDRAWN"
    assert withdrawn.json()["published_at"] is None
    assert withdrawn.json()["approval"] is not None
    assert (
        client.post(path, headers=headers(tokens, key="withdraw-one"), json=withdrawal).json()
        == withdrawn.json()
    )
    assert (
        client.get("/api/v1/workers/me/schedule", headers=headers(tokens, "worker")).json()[
            "scenarios"
        ]
        == []
    )
    assert (
        client.post(
            f"/api/v1/interventions/{old_item['id']}/acknowledge",
            headers=headers(tokens, "worker", "stale-receipt"),
            json={},
        ).status_code
        == 409
    )
    response = client.post(
        f"/api/v1/scenarios/{second['id']}/publish",
        headers=headers(tokens, key="replacement"),
        json=payload,
    )
    assert response.status_code == 200, response.text
    audit = client.get(f"/api/v1/scenarios/{first['id']}/audit", headers=headers(tokens)).json()
    assert {"intervention.acknowledged", "scenario.demo_withdrawn"} <= {
        e["action"] for e in audit["events"]
    }
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(InterventionEvent)) == 1
    own = client.get("/api/v1/workers/me/schedule", headers=headers(tokens, "worker")).json()
    assert [plan["scenario_id"] for plan in own["scenarios"]] == [second["id"]]


def test_publication_conflicts_are_tenant_scoped(workspace):
    client, _, tokens = workspace
    scenario = single_task_scenario(datetime(2026, 7, 15, 2, tzinfo=UTC))
    first, _ = published(workspace, scenario)
    second, _ = published(workspace, scenario, role="other")
    own = client.get("/api/v1/workers/me/schedule", headers=headers(tokens, "worker")).json()
    assert [plan["scenario_id"] for plan in own["scenarios"]] == [first["id"]]
    other = client.get("/api/v1/interventions", headers=headers(tokens, "other")).json()
    assert {item["scenario_id"] for item in other["items"]} == {second["id"]}


def test_republishing_withdrawn_plan_reuses_history_without_duplicate_interventions(workspace):
    client, database, tokens = workspace
    detail, run = published(workspace)
    before = client.get("/api/v1/interventions", headers=headers(tokens)).json()["items"]
    path = f"/api/v1/scenarios/{detail['id']}"
    assert (
        client.post(
            f"{path}/withdraw",
            headers=headers(tokens, key="pause"),
            json={"expected_revision": 1, "note": "Synthetic pause"},
        ).status_code
        == 200
    )
    republished = client.post(
        f"{path}/publish",
        headers=headers(tokens, key="resume"),
        json={"expected_revision": 1, "run_id": run["id"]},
    )
    assert republished.status_code == 200, republished.text
    after = client.get("/api/v1/interventions", headers=headers(tokens)).json()["items"]
    assert {item["id"] for item in before} == {item["id"] for item in after}
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(InterventionEvent)) == 0
