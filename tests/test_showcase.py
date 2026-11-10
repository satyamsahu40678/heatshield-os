"""Computed demo stories remain coherent, additive and isolated from user work."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import func, select

from heatshield.bootstrap import DEMO_ORGANIZATION_ID, seed_demo
from heatshield.database import Database
from heatshield.fixtures import demo_scenario
from heatshield.identity import User
from heatshield.models import OptimizationResult, Scenario
from heatshield.provenance import content_hash
from heatshield.showcase import SHOWCASE_HOLD_WORKER, SHOWCASE_IDS, STORIES, seed_showcase
from heatshield.validation import validate_schedule
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
from heatshield.workflows import (
    acknowledge_intervention,
    create_scenario,
    enqueue_optimization,
    list_worker_holds,
    release_worker_hold,
    update_scenario,
    worker_schedule,
)


@pytest.fixture
def database(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'showcase.db'}")
    db.initialize()
    with db.session() as session:
        seed_demo(session)
    yield db
    db.dispose()


def actor(session, email="admin@example.test"):
    return session.scalar(select(User).where(User.email == email))


def counts(session):
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


def test_all_six_stories_use_actual_solver_results_and_independent_validation(database):
    summary = seed_showcase(database)
    assert summary["synthetic_only"]
    assert not summary["warnings"]
    assert len(summary["created"]) == 6
    with database.session() as session:
        records = list(session.scalars(select(ScenarioRecord).order_by(ScenarioRecord.id)))
        assert {record.id: record.status for record in records} == {
            marker: target for marker, _, _, target in STORIES
        }
        assert {record.organization_id for record in records} == {DEMO_ORGANIZATION_ID}
        revisions = {row.scenario_id: row for row in session.scalars(select(ScenarioRevision))}
        assert {
            Scenario.model_validate(row.scenario_json).starts_at.day for row in revisions.values()
        } == set(range(21, 27))
        runs = list(session.scalars(select(OptimizationRun)))
        assert len(runs) == 5
        for run in runs:
            scenario = Scenario.model_validate(revisions[run.scenario_id].scenario_json)
            result = OptimizationResult.model_validate(run.result_json)
            assert run.status == "COMPLETED"
            assert result.metadata.scenario_hash == content_hash(scenario)
            assert result.metadata.policy_hash == content_hash(scenario.policy)
            assert {"fixed", "greedy", "optimized"} <= run.comparison_json.keys()
            if result.schedule:
                report = validate_schedule(scenario, result.schedule)
                assert report.valid
                assert result.validation == report
                assert run.comparison_json["optimized"]["exposure_proxy"] == report.exposure_proxy
                assert run.comparison_json["optimized"]["completed_tasks"] == report.completed_tasks
                assert report.review_required and not report.operational_approval_allowed
            else:
                assert run.scenario_id == "showcase-east-yard"
                assert result.status == "INFEASIBLE"
                assert scenario.cooling_capacity == 0 and scenario.observations == ()


def test_published_worker_has_real_multilingual_actions_at_all_three_stages(database):
    seed_showcase(database)
    with database.session() as session:
        own = worker_schedule(session, actor(session, "worker@example.test"))
        assert not own["stop_work"]
        assert len(own["scenarios"]) == 1
        plan = own["scenarios"][0]
        assert plan["scenario_id"] == "showcase-al-reem"
        assert plan["assignments"] and plan["recoveries"]
        assert {item["status"] for item in plan["interventions"]} == {
            "PENDING",
            "ACKNOWLEDGED",
            "VERIFIED",
        }
        for item in plan["interventions"]:
            assert item["worker_id"] == "worker-01"
            assert set(item["payload"]["messages"]) == {"en", "hi", "ar"}
            assert item["payload"]["synthetic_only"]
            if item["status"] == "VERIFIED":
                assert [event["type"] for event in item["events"]] == ["ACKNOWLEDGED", "VERIFIED"]
        historical = session.get(ScenarioRecord, "showcase-marina-works")
        assert historical.published_at is None and historical.approval_id is not None
        actions = set(
            session.scalars(select(AuditEvent.action).where(AuditEvent.entity_id == historical.id))
        )
        assert {"scenario.demo_published", "scenario.demo_withdrawn"} <= actions


def test_repeat_seed_preserves_user_revisions_receipts_and_released_holds(database):
    seed_showcase(database)
    with database.session() as session:
        admin = actor(session)
        snapshot = session.scalar(
            select(ScenarioRevision).where(ScenarioRevision.scenario_id == "showcase-canal-west")
        )
        scenario = Scenario.model_validate(snapshot.scenario_json)
        update_scenario(
            session, admin, "showcase-canal-west", 1, "User-edited planning story", scenario
        )
        release_worker_hold(
            session,
            admin,
            SHOWCASE_HOLD_WORKER,
            "Synthetic reviewer released the demonstration hold",
        )
        worker = actor(session, "worker@example.test")
        own = worker_schedule(session, worker)
        pending = next(
            item for item in own["scenarios"][0]["interventions"] if item["status"] == "PENDING"
        )
        acknowledge_intervention(session, worker, pending["id"], "user-created-receipt")
        before = counts(session)
        released_at = session.scalar(select(WorkerHold.released_at))
    summary = seed_showcase(database)
    assert summary["created"] == []
    assert set(summary["existing"]) == set(SHOWCASE_IDS)
    assert not summary["health_story_created"]
    with database.session() as session:
        assert counts(session) == before
        record = session.get(ScenarioRecord, "showcase-canal-west")
        assert record.revision == 2 and record.name == "User-edited planning story"
        hold = session.scalar(select(WorkerHold))
        assert not hold.active and hold.released_at == released_at


def test_seed_does_not_claim_or_modify_an_existing_user_queue_job(database):
    with database.session() as session:
        admin = actor(session)
        detail = create_scenario(session, admin, "User work awaiting a worker", demo_scenario())
        queued = enqueue_optimization(session, admin, detail["id"], 1, "user-pending")
        original = session.get(ScenarioRecord, detail["id"])
        original_lock = original.lock_version
        original_updated = original.updated_at
    seed_showcase(database)
    with database.session() as session:
        run = session.get(OptimizationRun, queued["id"])
        assert run.status == "QUEUED" and run.attempts == 0
        assert run.result_json is None and run.lease_owner is None
        record = session.get(ScenarioRecord, detail["id"])
        assert record.status == "QUEUED" and record.latest_run_id == run.id
        assert record.lock_version == original_lock and record.updated_at == original_updated


def test_escalation_is_private_and_never_holds_a_core_demo_worker(database):
    seed_showcase(database)
    with database.session() as session:
        holds = list_worker_holds(session, actor(session, "supervisor@example.test"))
        assert [row["worker_id"] for row in holds["items"]] == [SHOWCASE_HOLD_WORKER]
        assert {row["owner_role"] for row in holds["items"]} == {"safety_officer"}
        assert "symptoms" not in json.dumps(holds)
        private = session.scalar(select(PrivateHealthReport))
        assert private.worker_id == SHOWCASE_HOLD_WORKER and private.severity == "severe"
        assert "fabricated" in private.symptoms_json[0]
        for row in session.scalars(select(AuditEvent)):
            assert "dizziness" not in json.dumps(row.payload_json)
            assert "fatigue" not in json.dumps(row.payload_json)
        for row in session.scalars(select(Outbox)):
            assert "dizziness" not in json.dumps(row.payload_json)
            assert row.delivered_at is None
        linked = actor(session, "worker-07@example.test")
        assert linked.worker_id == SHOWCASE_HOLD_WORKER
        assert linked.organization_id == DEMO_ORGANIZATION_ID


def test_non_demo_guard_precedes_all_writes(database):
    with database.session() as session:
        before = counts(session)
    with pytest.raises(ValueError, match="explicit demo mode"):
        seed_showcase(database, demo_mode=False)
    with database.session() as session:
        assert counts(session) == before


def test_missing_bootstrap_identities_do_not_leave_partial_content(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'unseeded.db'}")
    database.initialize()
    with pytest.raises(ValueError, match="Seed demo identities"):
        seed_showcase(database)
    with database.session() as session:
        assert not session.scalar(select(func.count()).select_from(User))
        assert not session.scalar(select(func.count()).select_from(ScenarioRecord))
    database.dispose()


def test_simultaneous_demo_processes_add_one_coherent_showcase(database):
    gate = Barrier(2)

    def seed():
        gate.wait()
        return seed_showcase(database)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(seed) for _ in range(2)]
        outcomes = [future.result(timeout=20) for future in futures]
    assert sorted(len(outcome["created"]) for outcome in outcomes) == [0, 6]
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(ScenarioRecord)) == 6
        assert session.scalar(select(func.count()).select_from(OptimizationRun)) == 5
        assert session.scalar(select(func.count()).select_from(PrivateHealthReport)) == 1
