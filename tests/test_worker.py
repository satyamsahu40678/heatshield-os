"""Durable worker state, lease fencing and restart recovery."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update

import heatshield.worker as worker
from heatshield.database import Database
from heatshield.models import Scenario
from heatshield.provenance import content_hash
from heatshield.workflow_models import (
    Approval,
    AuditEvent,
    OptimizationRun,
    Outbox,
    ScenarioRecord,
    ScenarioRevision,
    WorkerHold,
)


def small_scenario() -> Scenario:
    start = datetime(2026, 7, 15, 2, tzinfo=UTC)
    end = start + timedelta(hours=2)
    return Scenario.model_validate(
        {
            "id": "worker-test",
            "starts_at": start,
            "ends_at": end,
            "workers": [
                {
                    "id": "w1",
                    "skills": ["general"],
                    "available_from": start,
                    "available_until": end,
                },
                {
                    "id": "w2",
                    "skills": ["general"],
                    "available_from": start,
                    "available_until": end,
                },
            ],
            "tasks": [
                {
                    "id": "t1",
                    "required_skill": "general",
                    "zone_id": "yard",
                    "duration_minutes": 30,
                    "release_at": start,
                    "deadline": end,
                    "deferrable": False,
                }
            ],
            "observations": [
                {
                    "id": f"o{i}",
                    "zone_id": "yard",
                    "observed_at": start + timedelta(hours=i),
                    "valid_from": start + timedelta(hours=i),
                    "valid_until": start + timedelta(hours=i + 1),
                    "wbgt_c": 26.0,
                }
                for i in range(2)
            ],
        }
    )


@pytest.fixture
def database(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'worker.db'}")
    database.initialize()
    yield database
    database.dispose()


def enqueue(database, scenario=None, organization_id="org-a", **changes):
    scenario = scenario or small_scenario()
    scenario_id, run_id = str(uuid4()), str(uuid4())
    with database.session_factory() as session, session.begin():
        session.add(
            ScenarioRecord(
                id=scenario_id,
                organization_id=organization_id,
                name="Synthetic worker fixture",
                revision=1,
                status="QUEUED",
                latest_run_id=run_id,
            )
        )
        session.flush()
        session.add(
            ScenarioRevision(
                scenario_id=scenario_id,
                organization_id=organization_id,
                revision=1,
                scenario_json=scenario.model_dump(mode="json"),
                scenario_hash=content_hash(scenario),
                policy_hash=content_hash(scenario.policy),
                created_by="test-actor",
            )
        )
        session.add(
            OptimizationRun(
                **(
                    {
                        "id": run_id,
                        "organization_id": organization_id,
                        "scenario_id": scenario_id,
                        "revision": 1,
                        "scenario_hash": content_hash(scenario),
                        "policy_hash": content_hash(scenario.policy),
                        "requested_by": "test-actor",
                        "status": "QUEUED",
                    }
                    | changes
                )
            )
        )
    return run_id


def get_run(database, run_id):
    with database.session_factory() as session:
        return session.get(OptimizationRun, run_id)


def test_processes_queued_job_and_persists_audited_unapproved_result(database):
    run_id = enqueue(database)
    assert worker.run_once(database, "test-worker")
    run = get_run(database, run_id)
    assert run.status == "COMPLETED"
    assert run.solver_status in {"OPTIMAL", "FEASIBLE"}
    assert run.result_json["validation"]["valid"]
    assert not run.result_json["validation"]["operational_approval_allowed"]
    assert {"fixed", "greedy", "optimized"} <= run.comparison_json.keys()
    assert run.comparison_json["optimized"]["completed_tasks"] == 1
    assert run.lease_owner is None
    assert run.attempts == 1
    with database.session_factory() as session:
        record = session.get(ScenarioRecord, run.scenario_id)
        assert record.status == "OPTIMIZED"
        assert session.scalar(select(func.count()).select_from(Approval)) == 0
        assert session.scalar(select(func.count()).select_from(Outbox)) == 1
        actions = set(session.scalars(select(AuditEvent.action)))
        assert {"optimization.claimed", "optimization.completed"} <= actions
    assert not worker.run_once(database)
    assert get_run(database, run_id).attempts == 1


def test_result_survives_database_restart(database):
    run_id = enqueue(database)
    assert worker.run_once(database)
    url = str(database.engine.url)
    database.dispose()
    reopened = Database(url)
    try:
        assert get_run(reopened, run_id).status == "COMPLETED"
        assert not worker.run_once(reopened)
    finally:
        reopened.dispose()


def test_reclaims_expired_running_job_after_crash(database):
    run_id = enqueue(
        database,
        status="RUNNING",
        attempts=1,
        lease_owner="crashed-worker",
        lease_expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    assert worker.run_once(database, "replacement-worker")
    run = get_run(database, run_id)
    assert run.status == "COMPLETED"
    assert run.attempts == 2
    with database.session_factory() as session:
        event = session.scalar(
            select(AuditEvent).where(AuditEvent.action == "optimization.claimed")
        )
        assert event.payload_json["reclaimed"] is True


def test_active_lease_cannot_be_double_claimed(database):
    run_id = enqueue(
        database,
        status="RUNNING",
        attempts=1,
        lease_owner="active-worker",
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=120),
    )
    assert not worker.run_once(database, "other-worker")
    assert get_run(database, run_id).attempts == 1
    assert get_run(database, run_id).lease_owner == "active-worker"


def test_late_worker_cannot_overwrite_reclaimed_lease(database, monkeypatch):
    run_id = enqueue(database)
    original_optimize = worker.optimize

    def lease_stolen(scenario):
        result = original_optimize(scenario)
        with database.session_factory() as session, session.begin():
            session.execute(
                update(OptimizationRun)
                .where(OptimizationRun.id == run_id)
                .values(
                    lease_owner="new-owner",
                    attempts=2,
                    lease_expires_at=datetime.now(UTC) + timedelta(seconds=120),
                )
            )
        return result

    monkeypatch.setattr(worker, "optimize", lease_stolen)
    assert worker.run_once(database, "stale-owner")
    run = get_run(database, run_id)
    assert run.status == "RUNNING"
    assert run.result_json is None
    assert run.lease_owner == "new-owner"
    with database.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Outbox)) == 0


def test_queue_integrity_is_organization_scoped(database):
    run_id = enqueue(database, organization_id="organization-b")
    with database.session_factory() as session, session.begin():
        session.execute(
            update(OptimizationRun)
            .where(OptimizationRun.id == run_id)
            .values(organization_id="organization-a")
        )
    assert worker.run_once(database)
    run = get_run(database, run_id)
    assert run.status == "FAILED"
    assert run.result_json is None
    assert "organization" in run.error


def test_queue_hash_mismatch_fails_before_optimization(database, monkeypatch):
    run_id = enqueue(database, scenario_hash="f" * 64)

    def forbidden(*args):
        pytest.fail("A mismatched queued snapshot must not reach the optimizer")

    monkeypatch.setattr(worker, "optimize", forbidden)
    assert worker.run_once(database)
    assert get_run(database, run_id).status == "FAILED"
    assert "integrity" in get_run(database, run_id).error


def test_active_worker_hold_blocks_job(database, monkeypatch):
    run_id = enqueue(database)
    with database.session_factory() as session, session.begin():
        session.add(WorkerHold(organization_id="org-a", worker_id="w1", active=True))

    def forbidden(*args):
        pytest.fail("A held worker must not reach optimization")

    monkeypatch.setattr(worker, "optimize", forbidden)
    assert worker.run_once(database)
    assert get_run(database, run_id).status == "FAILED"
    assert "stop-work" in get_run(database, run_id).error
    with database.session_factory() as session:
        assert session.get(ScenarioRecord, get_run(database, run_id).scenario_id).status == "FAILED"


def test_hold_recorded_during_solving_rejects_result(database, monkeypatch):
    run_id = enqueue(database)
    original_optimize = worker.optimize

    def held_during_solve(scenario):
        result = original_optimize(scenario)
        with database.session_factory() as session, session.begin():
            session.add(WorkerHold(organization_id="org-a", worker_id="w1", active=True))
        return result

    monkeypatch.setattr(worker, "optimize", held_during_solve)
    assert worker.run_once(database)
    assert get_run(database, run_id).status == "FAILED"
    assert get_run(database, run_id).result_json is None


def test_infeasible_job_completes_without_manufactured_plan(database):
    scenario = small_scenario()
    task = scenario.tasks[0].model_copy(update={"required_skill": "unavailable"})
    scenario = Scenario.model_validate(scenario.model_dump() | {"tasks": (task,)})
    run_id = enqueue(database, scenario)
    assert worker.run_once(database)
    run = get_run(database, run_id)
    assert run.status == "COMPLETED"
    assert run.solver_status == "INFEASIBLE"
    assert run.result_json["schedule"] is None
    assert run.comparison_json["fixed"]["status"] == "UNSAFE_REFERENCE"
    assert "SKILL" in {finding["code"] for finding in run.comparison_json["fixed"]["violations"]}
    with database.session_factory() as session:
        assert session.get(ScenarioRecord, run.scenario_id).status == "REVIEW_REQUIRED"


def test_forged_optimizer_result_is_not_persisted(database, monkeypatch):
    run_id = enqueue(database)
    original_optimize = worker.optimize

    def forged(scenario):
        result = original_optimize(scenario)
        schedule = result.schedule.model_copy(update={"assignments": (), "recoveries": ()})
        return result.model_copy(update={"schedule": schedule})

    monkeypatch.setattr(worker, "optimize", forged)
    assert worker.run_once(database)
    assert get_run(database, run_id).status == "FAILED"
    assert "Independent validation" in get_run(database, run_id).error


def test_failed_job_does_not_expose_arbitrary_exception_payload(database, monkeypatch):
    run_id = enqueue(database)

    def fail(*args):
        raise ValueError("sensitive-connection-string-or-payload")

    monkeypatch.setattr(worker, "optimize", fail)
    assert worker.run_once(database)
    run = get_run(database, run_id)
    assert run.status == "FAILED"
    assert run.error == "Worker execution failed (ValueError)."


def test_concurrent_workers_cannot_solve_the_same_live_claim(database, monkeypatch):
    run_id = enqueue(database)
    entered, release = Event(), Event()
    original_optimize = worker.optimize

    def paused_solve(scenario):
        with database.session_factory() as session:
            record = session.get(ScenarioRecord, get_run(database, run_id).scenario_id)
            assert record.status == "RUNNING"
        entered.set()
        assert release.wait(timeout=10)
        return original_optimize(scenario)

    monkeypatch.setattr(worker, "optimize", paused_solve)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(worker.run_once, database, "first")
        try:
            assert entered.wait(timeout=10)
            second = pool.submit(worker.run_once, database, "second")
            assert second.result(timeout=10) is False
        finally:
            release.set()
        assert first.result(timeout=10) is True
    assert get_run(database, run_id).status == "COMPLETED"
    assert get_run(database, run_id).attempts == 1


def test_snapshot_integrity_is_rechecked_after_solving(database, monkeypatch):
    run_id = enqueue(database)
    original_optimize = worker.optimize

    def changed_during_solve(scenario):
        result = original_optimize(scenario)
        with database.session_factory() as session, session.begin():
            session.execute(update(ScenarioRevision).values(scenario_hash="f" * 64))
        return result

    monkeypatch.setattr(worker, "optimize", changed_during_solve)
    assert worker.run_once(database)
    run = get_run(database, run_id)
    assert run.status == "FAILED"
    assert run.result_json is None
    assert "integrity" in run.error


def test_baseline_failure_does_not_discard_valid_optimizer_result(database, monkeypatch):
    run_id = enqueue(database)

    def baseline_unavailable(scenario):
        raise RuntimeError("baseline-specific failure")

    monkeypatch.setattr(worker, "fixed_roster", baseline_unavailable)
    monkeypatch.setattr(worker, "greedy_schedule", baseline_unavailable)
    assert worker.run_once(database)
    run = get_run(database, run_id)
    assert run.status == "COMPLETED"
    assert run.result_json["validation"]["valid"]
    assert run.comparison_json["fixed"]["status"] == "UNAVAILABLE"
    assert run.comparison_json["greedy"]["status"] == "UNAVAILABLE"


@pytest.mark.parametrize("new_status", ["APPROVED", "PUBLISHED", "DRAFT"])
def test_worker_completion_does_not_overwrite_newer_workflow_state(
    database, monkeypatch, new_status
):
    run_id = enqueue(database)
    original_optimize = worker.optimize

    def scenario_state_changed(scenario):
        result = original_optimize(scenario)
        with database.session_factory() as session, session.begin():
            session.execute(update(ScenarioRecord).values(status=new_status))
        return result

    monkeypatch.setattr(worker, "optimize", scenario_state_changed)
    assert worker.run_once(database)
    run = get_run(database, run_id)
    assert run.status == "COMPLETED"
    with database.session_factory() as session:
        assert session.get(ScenarioRecord, run.scenario_id).status == new_status


def test_non_demo_worker_does_not_create_schema(database, monkeypatch):
    from heatshield.settings import Settings

    calls = []

    def forbidden_initialization(*args):
        pytest.fail("Non-demo workers must use externally migrated schemas")

    settings = Settings(database_url=str(database.engine.url), demo_mode=False, jwt_secret="a" * 40)
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: settings))
    monkeypatch.setattr(Database, "initialize", forbidden_initialization)
    monkeypatch.setattr(worker, "run_once", lambda *args: calls.append(True) or False)
    worker.main(["--once"])
    assert calls == [True]
