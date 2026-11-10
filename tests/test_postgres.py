"""Real PostgreSQL migrations, durability and transaction serialization.

Set HEATSHIELD_TEST_DATABASE_URL to an explicitly disposable PostgreSQL database.
Each test creates and drops only its own randomly named schema. No public-schema
tables are cleared, and no database URL is inferred from application settings.
"""

import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Event
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi import HTTPException
from sqlalchemy import func, inspect, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema

import heatshield.worker as worker
from heatshield import workflows
from heatshield.database import Base, Database, utcnow
from heatshield.identity import Organization, User
from heatshield.models import Scenario
from heatshield.optimization import optimize
from heatshield.workflow_models import (
    AuditEvent,
    OptimizationRun,
    Outbox,
    PrivateHealthReport,
    ScenarioRecord,
    ScenarioRevision,
    WorkerHold,
)

TEST_URL = os.getenv("HEATSHIELD_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="HEATSHIELD_TEST_DATABASE_URL is not configured"
)
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _alembic_config() -> Config:
    return Config(str(PROJECT_ROOT / "alembic.ini"))


@pytest.fixture
def pg_database(monkeypatch):
    assert TEST_URL is not None
    parsed = make_url(TEST_URL)
    assert parsed.get_backend_name() == "postgresql", "Integration target must be PostgreSQL"
    admin = Database(TEST_URL)
    schema = f"heatshield_test_{uuid4().hex}"
    with admin.engine.begin() as connection:
        connection.execute(CreateSchema(schema))
    # Every pooled connection and Alembic uses the isolated schema.
    scoped_url = parsed.update_query_dict({"options": f"-csearch_path={schema}"})
    url = scoped_url.render_as_string(hide_password=False)
    monkeypatch.setenv("HEATSHIELD_DATABASE_URL", url)
    database = Database(url)
    try:
        command.upgrade(_alembic_config(), "head")
        yield database
    finally:
        database.dispose()
        with admin.engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()


@pytest.fixture
def actors(pg_database):
    with pg_database.session() as session:
        session.add(Organization(id="pg-organization", name="PostgreSQL synthetic fixture"))
        session.flush()
        session.add_all(
            [
                User(
                    id="pg-safety",
                    organization_id="pg-organization",
                    email="safety@postgres.example.test",
                    display_name="Synthetic safety officer",
                    role="safety_officer",
                    password_hash="not-a-login-fixture",
                ),
                User(
                    id="pg-worker",
                    organization_id="pg-organization",
                    email="worker@postgres.example.test",
                    display_name="Synthetic worker",
                    role="worker",
                    worker_id="worker-01",
                    password_hash="not-a-login-fixture",
                ),
            ]
        )
    return {"safety": "pg-safety", "worker": "pg-worker"}


def _scenario() -> Scenario:
    start = datetime(2026, 7, 15, 2, tzinfo=UTC)
    end = start + timedelta(hours=1)
    return Scenario.model_validate(
        {
            "id": "postgres-synthetic-fixture",
            "starts_at": start,
            "ends_at": end,
            "workers": [
                {
                    "id": "worker-01",
                    "skills": ["general"],
                    "available_from": start,
                    "available_until": end,
                }
            ],
            "tasks": [
                {
                    "id": "task-01",
                    "required_skill": "general",
                    "zone_id": "yard",
                    "duration_minutes": 15,
                    "release_at": start,
                    "deadline": end,
                    "deferrable": False,
                }
            ],
            "observations": [
                {
                    "id": "sample-01",
                    "zone_id": "yard",
                    "observed_at": start,
                    "valid_from": start,
                    "valid_until": end,
                    "wbgt_c": 26.0,
                }
            ],
        }
    )


def _enqueue(database, actors, scenario=None):
    scenario = scenario or _scenario()
    with database.session() as session:
        actor = session.get(User, actors["safety"])
        detail = workflows.create_scenario(session, actor, "PostgreSQL fixture", scenario)
        run = workflows.enqueue_optimization(session, actor, detail["id"], 1, "queue-test")
    return detail["id"], run["id"], scenario


def _reviewed(database, actors, scenario=None):
    scenario_id, run_id, scenario = _enqueue(database, actors, scenario)
    assert worker.run_once(database, "pg-review-worker")
    with database.session() as session:
        run = session.get(OptimizationRun, run_id)
        assert run.status == "COMPLETED", run.error
        assert run.result_json["validation"]["valid"]
        workflows.approve_scenario(
            session, session.get(User, actors["safety"]), scenario_id, 1, run_id, "review-test"
        )
    return scenario_id, run_id, scenario


def _count(session, model, *conditions):
    return session.scalar(select(func.count()).select_from(model).where(*conditions))


def _database_url(database):
    return database.engine.url.render_as_string(hide_password=False)


def test_postgres_migration_round_trip_matches_model_metadata(pg_database):
    assert pg_database.engine.dialect.name == "postgresql"
    assert set(Base.metadata.tables) <= set(inspect(pg_database.engine).get_table_names())
    command.check(_alembic_config())
    command.downgrade(_alembic_config(), "base")
    assert set(inspect(pg_database.engine).get_table_names()) == {"alembic_version"}
    command.upgrade(_alembic_config(), "head")
    command.check(_alembic_config())
    with pg_database.session() as session:
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == "0001"


def test_postgres_rollback_and_restarted_database_preserve_committed_jobs(pg_database, actors):
    with pytest.raises(RuntimeError, match="rollback fixture"):
        with pg_database.session() as session:
            session.add(Organization(id="rollback-fixture", name="Must not persist"))
            session.flush()
            raise RuntimeError("rollback fixture")
    with pg_database.session() as session:
        assert session.get(Organization, "rollback-fixture") is None
    scenario_id, run_id, scenario = _enqueue(pg_database, actors)
    url = _database_url(pg_database)
    pg_database.dispose()
    reopened = Database(url)
    try:
        with reopened.session() as session:
            detail = workflows.get_scenario(
                session, session.get(User, actors["safety"]), scenario_id
            )
            assert detail["scenario"] == scenario.model_dump(mode="json")
        assert worker.run_once(reopened, "restarted-postgres-worker")
        reopened.dispose()
        with reopened.session() as session:
            run = session.get(OptimizationRun, run_id)
            assert run.status == "COMPLETED", run.error
            assert run.result_json["validation"]["valid"]
            assert run.attempts == 1
            assert run.created_at.utcoffset() == timedelta(0)
        assert not worker.run_once(reopened, "another-postgres-worker")
    finally:
        reopened.dispose()


def test_postgres_accepts_maximum_length_worker_identity_and_audit_reference(pg_database, actors):
    worker_id = "worker-" + "a" * 73
    assert len(worker_id) == 80
    with pg_database.session() as session:
        user = session.get(User, actors["worker"])
        user.worker_id = worker_id
    with pg_database.session() as session:
        response = workflows.report_symptoms(
            session, session.get(User, actors["worker"]), "severe", ["dizziness"]
        )
        assert response["stop_work"]
    with pg_database.session() as session:
        assert session.get(User, actors["worker"]).worker_id == worker_id
        assert session.scalar(select(WorkerHold)).worker_id == worker_id
        event = session.scalar(
            select(AuditEvent).where(AuditEvent.action == "worker.private_report_recorded")
        )
        assert event.entity_id == worker_id


def test_postgres_skip_locked_does_not_wait_for_a_locked_queue_row(pg_database, actors):
    _, run_id, _ = _enqueue(pg_database, actors)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with pg_database.session() as session:
            session.scalar(
                select(OptimizationRun).where(OptimizationRun.id == run_id).with_for_update()
            )
            future = pool.submit(worker._claim, pg_database, "skip-locked-worker")
            assert future.result(timeout=3) is None
    assert worker._claim(pg_database, "after-unlock-worker").id == run_id


def test_postgres_two_workers_claim_once_and_complete_once(pg_database, actors):
    _, run_id, scenario = _enqueue(pg_database, actors)
    other = Database(_database_url(pg_database))
    barrier = Barrier(2)

    def claim(database, owner):
        barrier.wait(timeout=5)
        return worker._claim(database, owner)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            one = pool.submit(claim, pg_database, "postgres-worker-one")
            two = pool.submit(claim, other, "postgres-worker-two")
            claims = [one.result(timeout=10), two.result(timeout=10)]
        winners = [claim for claim in claims if claim is not None]
        assert len(winners) == 1
        winner = winners[0]
        result = optimize(scenario)
        comparison = worker._comparison(scenario, result)
        assert worker._complete(pg_database, winner, scenario, result, comparison)
        assert not worker._complete(other, winner, scenario, result, comparison)
        with pg_database.session() as session:
            run = session.get(OptimizationRun, run_id)
            assert run.status == "COMPLETED"
            assert run.attempts == 1
            assert _count(session, AuditEvent, AuditEvent.action == "optimization.claimed") == 1
            assert _count(session, AuditEvent, AuditEvent.action == "optimization.completed") == 1
            assert _count(session, Outbox, Outbox.topic == "optimization.completed") == 1
    finally:
        other.dispose()


def test_postgres_expired_lease_is_reclaimed_and_old_completion_is_fenced(pg_database, actors):
    _, run_id, scenario = _enqueue(pg_database, actors)
    old_claim = worker._claim(pg_database, "old-postgres-worker")
    with pg_database.session() as session:
        session.execute(
            update(OptimizationRun)
            .where(OptimizationRun.id == run_id)
            .values(lease_expires_at=utcnow() - timedelta(seconds=1))
        )
    new_claim = worker._claim(pg_database, "replacement-postgres-worker")
    assert old_claim.attempt == 1
    assert new_claim.attempt == 2
    result = optimize(scenario)
    comparison = worker._comparison(scenario, result)
    assert not worker._complete(pg_database, old_claim, scenario, result, comparison)
    worker._fail(pg_database, old_claim, "Late stale failure must not overwrite the new lease")
    with pg_database.session() as session:
        assert session.get(OptimizationRun, run_id).lease_owner == new_claim.owner
    assert worker._complete(pg_database, new_claim, scenario, result, comparison)
    assert not worker._complete(pg_database, old_claim, scenario, result, comparison)
    with pg_database.session() as session:
        run = session.get(OptimizationRun, run_id)
        assert run.status == "COMPLETED"
        assert run.attempts == 2
        assert _count(session, AuditEvent, AuditEvent.action == "optimization.claimed") == 2
        assert _count(session, AuditEvent, AuditEvent.action == "optimization.completed") == 1
        assert _count(session, Outbox, Outbox.topic == "optimization.completed") == 1


@pytest.mark.parametrize("first_action", ["revise", "publish"])
def test_postgres_publication_revision_race_never_publishes_stale_revision(
    pg_database, actors, first_action
):
    scenario_id, run_id, scenario = _reviewed(pg_database, actors)
    started = Event()

    def transition(session, action):
        actor = session.get(User, actors["safety"])
        if action == "revise":
            return workflows.update_scenario(
                session, actor, scenario_id, 1, "New revision", scenario
            )
        return workflows.publish_scenario(session, actor, scenario_id, 1, run_id, "publish-test")

    def second_transition():
        started.set()
        try:
            with pg_database.session() as session:
                return transition(session, "publish" if first_action == "revise" else "revise")
        except HTTPException as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=1) as pool:
        with pg_database.session() as session:
            first = transition(session, first_action)
            future = pool.submit(second_transition)
            assert started.wait(timeout=3)
            with pytest.raises(TimeoutError):
                future.result(timeout=0.2)
        second = future.result(timeout=10)
    assert first["revision"] == (2 if first_action == "revise" else 1)
    if first_action == "revise":
        assert second == 409
    else:
        assert second["revision"] == 2
    with pg_database.session() as session:
        record = session.get(ScenarioRecord, scenario_id)
        assert record.revision == 2
        assert record.status == "DRAFT"
        assert record.published_at is None
        assert record.approval_id is None
        assert record.latest_run_id is None
        assert _count(session, ScenarioRevision, ScenarioRevision.scenario_id == scenario_id) == 2
        assert (
            workflows.list_interventions(session, session.get(User, actors["worker"]), scenario_id)[
                "items"
            ]
            == []
        )


@pytest.mark.parametrize("first_action", ["report", "publish"])
def test_postgres_symptom_hold_publication_race_serializes_worker_delivery(
    pg_database, actors, first_action
):
    scenario_id, run_id, _ = _reviewed(pg_database, actors)
    started = Event()

    def transition(session, action):
        if action == "report":
            return workflows.report_symptoms(
                session, session.get(User, actors["worker"]), "severe", ["dizziness"]
            )
        return workflows.publish_scenario(
            session, session.get(User, actors["safety"]), scenario_id, 1, run_id, "publish-test"
        )

    def second_transition():
        started.set()
        try:
            with pg_database.session() as session:
                return transition(session, "publish" if first_action == "report" else "report")
        except HTTPException as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=1) as pool:
        with pg_database.session() as session:
            transition(session, first_action)
            future = pool.submit(second_transition)
            assert started.wait(timeout=3)
            with pytest.raises(TimeoutError):
                future.result(timeout=0.2)
        second = future.result(timeout=10)
    if first_action == "report":
        assert second == 409
    else:
        assert second["stop_work"] is True
    with pg_database.session() as session:
        assert session.scalar(select(WorkerHold)).active
        assert _count(session, PrivateHealthReport) == 1
        plan = workflows.worker_schedule(session, session.get(User, actors["worker"]))
        assert plan["stop_work"] is True
        assert plan["scenarios"] == []
        if first_action == "report":
            assert session.get(ScenarioRecord, scenario_id).status == "APPROVED"
            assert _count(session, Outbox, Outbox.topic == "schedule.demo_published") == 0


def test_postgres_concurrent_publications_for_unprovisioned_worker_publish_only_one(
    pg_database, actors
):
    # A scenario worker need not have a login. Per-user locks alone cannot
    # serialize two publications for this otherwise valid domain identity.
    worker_id = "unprovisioned-worker"
    scenario = _scenario()
    scenario = scenario.model_copy(
        update={"workers": (scenario.workers[0].model_copy(update={"id": worker_id}),)}
    )
    first = _reviewed(pg_database, actors, scenario)
    second = _reviewed(pg_database, actors, scenario)
    with pg_database.session() as session:
        assert _count(session, User, User.worker_id == worker_id) == 0
        assert _count(session, ScenarioRecord, ScenarioRecord.status == "APPROVED") == 2

    barrier = Barrier(2)

    def publish(prepared):
        scenario_id, run_id, _ = prepared
        try:
            with pg_database.session() as session:
                actor = session.get(User, actors["safety"])
                barrier.wait(timeout=5)
                detail = workflows.publish_scenario(
                    session, actor, scenario_id, 1, run_id, "concurrent-publish"
                )
                assert detail["status"] == "PUBLISHED"
            return 200
        except HTTPException as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(publish, prepared) for prepared in (first, second)]
        assert sorted(future.result(timeout=10) for future in futures) == [200, 409]

    with pg_database.session() as session:
        assert _count(session, ScenarioRecord, ScenarioRecord.status == "PUBLISHED") == 1
        assert _count(session, ScenarioRecord, ScenarioRecord.status == "APPROVED") == 1
        assert _count(session, Outbox, Outbox.topic == "schedule.demo_published") == 1
