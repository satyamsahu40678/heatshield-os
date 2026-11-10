"""Durable leased optimization worker, independent of the FastAPI process.

The database is the queue. A fenced lease prevents a late process from
overwriting a reclaimed job, and terminal results never trigger approval.
"""

import argparse
import logging
import socket
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import perf_counter, sleep

from sqlalchemy import and_, or_, select, update

from .fixtures import fixed_roster
from .models import OptimizationResult, Scenario, Schedule
from .optimization import greedy_schedule, optimize
from .provenance import content_hash
from .validation import validate_schedule
from .workflow_models import (
    AuditEvent,
    OptimizationRun,
    Outbox,
    ScenarioRecord,
    ScenarioRevision,
    WorkerHold,
)

logger = logging.getLogger(__name__)
LEASE_SECONDS = 120


@dataclass(frozen=True)
class _Claim:
    id: str
    organization_id: str
    scenario_id: str
    revision: int
    scenario_hash: str
    policy_hash: str
    owner: str
    attempt: int


class _JobError(Exception):
    """A controlled, safe-to-display reason a queued request cannot be processed."""


def _now() -> datetime:
    return datetime.now(UTC)


def _audit(session, claim: _Claim, action: str, payload: dict) -> None:
    session.add(
        AuditEvent(
            id=str(uuid.uuid4()),
            organization_id=claim.organization_id,
            actor_id=None,
            entity_type="optimization_run",
            entity_id=claim.id,
            action=action,
            payload_json=payload,
            created_at=_now(),
        )
    )


def _eligible(now: datetime):
    return or_(
        OptimizationRun.status == "QUEUED",
        and_(
            OptimizationRun.status == "RUNNING",
            or_(
                OptimizationRun.lease_expires_at <= now,
                OptimizationRun.lease_expires_at.is_(None),
            ),
        ),
    )


def _scenario_transition(session, claim: _Claim, status: str) -> None:
    """Update only this still-current run; never undo an approval or publication."""
    session.execute(
        update(ScenarioRecord)
        .where(
            ScenarioRecord.id == claim.scenario_id,
            ScenarioRecord.organization_id == claim.organization_id,
            ScenarioRecord.revision == claim.revision,
            ScenarioRecord.latest_run_id == claim.id,
            ScenarioRecord.status.in_(("QUEUED", "RUNNING")),
        )
        .values(status=status, updated_at=_now())
        .execution_options(synchronize_session=False)
    )


def _claim(database, owner: str) -> _Claim | None:
    """Claim atomically; PostgreSQL skips locked jobs, SQLite uses compare-and-set."""
    for _ in range(3):
        now = _now()
        with database.session_factory() as session, session.begin():
            statement = (
                select(OptimizationRun)
                .where(_eligible(now))
                .order_by(OptimizationRun.created_at, OptimizationRun.id)
                .limit(1)
            )
            if database.engine.dialect.name == "postgresql":
                statement = statement.with_for_update(skip_locked=True)
            run = session.scalar(statement)
            if run is None:
                return None
            prior_attempts = run.attempts
            reclaimed = run.status == "RUNNING"
            changed = session.execute(
                update(OptimizationRun)
                .where(
                    OptimizationRun.id == run.id,
                    OptimizationRun.organization_id == run.organization_id,
                    OptimizationRun.attempts == prior_attempts,
                    _eligible(now),
                )
                .values(
                    status="RUNNING",
                    attempts=prior_attempts + 1,
                    lease_owner=owner,
                    lease_expires_at=now + timedelta(seconds=LEASE_SECONDS),
                    error=None,
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            if changed.rowcount != 1:
                continue
            claim = _Claim(
                id=run.id,
                organization_id=run.organization_id,
                scenario_id=run.scenario_id,
                revision=run.revision,
                scenario_hash=run.scenario_hash,
                policy_hash=run.policy_hash,
                owner=owner,
                attempt=prior_attempts + 1,
            )
            _scenario_transition(session, claim, "RUNNING")
            _audit(
                session,
                claim,
                "optimization.claimed",
                {"attempt": claim.attempt, "reclaimed": reclaimed},
            )
            return claim
    return None


def _fence(claim: _Claim):
    return and_(
        OptimizationRun.id == claim.id,
        OptimizationRun.organization_id == claim.organization_id,
        OptimizationRun.status == "RUNNING",
        OptimizationRun.lease_owner == claim.owner,
        OptimizationRun.attempts == claim.attempt,
    )


def _held_workers(session, organization_id: str, worker_ids: set[str]) -> set[str]:
    if not worker_ids:
        return set()
    return set(
        session.scalars(
            select(WorkerHold.worker_id).where(
                WorkerHold.organization_id == organization_id,
                WorkerHold.active.is_(True),
                WorkerHold.worker_id.in_(worker_ids),
            )
        )
    )


def _load_snapshot(database, claim: _Claim) -> Scenario:
    with database.session_factory() as session:
        record = session.scalar(
            select(ScenarioRecord).where(
                ScenarioRecord.id == claim.scenario_id,
                ScenarioRecord.organization_id == claim.organization_id,
            )
        )
        if record is None:
            raise _JobError("The scenario is unavailable in the job's organization.")
        snapshot = session.scalar(
            select(ScenarioRevision).where(
                ScenarioRevision.scenario_id == claim.scenario_id,
                ScenarioRevision.organization_id == claim.organization_id,
                ScenarioRevision.revision == claim.revision,
            )
        )
        if snapshot is None:
            raise _JobError("The immutable scenario revision is unavailable.")
        scenario = Scenario.model_validate(snapshot.scenario_json)
        if (
            content_hash(scenario) != snapshot.scenario_hash
            or content_hash(scenario.policy) != snapshot.policy_hash
            or snapshot.scenario_hash != claim.scenario_hash
            or snapshot.policy_hash != claim.policy_hash
        ):
            raise _JobError("The stored scenario or policy failed its integrity check.")
        if _held_workers(session, claim.organization_id, {w.id for w in scenario.workers}):
            raise _JobError(
                "A worker in this scenario has an active stop-work hold; review required."
            )
        return scenario


def _summary(
    scenario: Scenario, schedule: Schedule | None, status: str, elapsed: float, error=None
) -> dict:
    report = validate_schedule(scenario, schedule) if schedule is not None else None
    return {
        "status": status,
        "valid": report.valid if report is not None else False,
        "completed_tasks": report.completed_tasks if report is not None else None,
        "deferred_tasks": report.deferred_tasks if report is not None else None,
        "exposure_proxy": report.exposure_proxy if report is not None else None,
        "workload_spread_minutes": report.workload_spread_minutes if report is not None else None,
        "violations": (
            [finding.model_dump(mode="json") for finding in report.violations]
            if report is not None
            else []
        ),
        "validation": report.model_dump(mode="json") if report is not None else None,
        "elapsed_seconds": round(elapsed, 6),
        "error": error,
    }


def _comparison(scenario: Scenario, result: OptimizationResult) -> dict:
    # B0 is explicitly unsafe and never an operational fallback. Some arbitrary
    # scenarios have no qualified worker, so the reference may be unavailable.
    began = perf_counter()
    try:
        fixed = fixed_roster(scenario)
        fixed_summary = _summary(scenario, fixed, "UNSAFE_REFERENCE", perf_counter() - began)
    except Exception as error:
        fixed_summary = _summary(
            scenario,
            None,
            "UNAVAILABLE",
            perf_counter() - began,
            f"The unchecked fixed roster is unavailable ({type(error).__name__}).",
        )
    began = perf_counter()
    try:
        greedy = greedy_schedule(scenario)
        greedy_summary = _summary(
            scenario,
            greedy,
            "FEASIBLE" if greedy is not None else "NO_PLAN_FOUND",
            perf_counter() - began,
        )
    except Exception as error:
        greedy_summary = _summary(
            scenario,
            None,
            "UNAVAILABLE",
            perf_counter() - began,
            f"The greedy baseline is unavailable ({type(error).__name__}).",
        )
    return {
        "fixed": fixed_summary,
        "greedy": greedy_summary,
        "optimized": _summary(
            scenario,
            result.schedule,
            result.status,
            result.metadata.elapsed_seconds,
        ),
        "mode": "synthetic",
        "operational_approval_allowed": False,
    }


def _complete(database, claim: _Claim, scenario: Scenario, result, comparison) -> bool:
    with database.session_factory() as session, session.begin():
        snapshot = session.scalar(
            select(ScenarioRevision).where(
                ScenarioRevision.scenario_id == claim.scenario_id,
                ScenarioRevision.organization_id == claim.organization_id,
                ScenarioRevision.revision == claim.revision,
            )
        )
        if snapshot is None:
            raise _JobError("The scenario revision disappeared before completion.")
        persisted = Scenario.model_validate(snapshot.scenario_json)
        if (
            content_hash(persisted) != claim.scenario_hash
            or content_hash(persisted.policy) != claim.policy_hash
            or snapshot.scenario_hash != claim.scenario_hash
            or snapshot.policy_hash != claim.policy_hash
        ):
            raise _JobError("The stored revision failed its completion integrity check.")
        if result.schedule is not None:
            fresh_report = validate_schedule(persisted, result.schedule)
            if not fresh_report.valid:
                raise _JobError("Fresh independent validation rejected the completion result.")
            result = result.model_copy(update={"validation": fresh_report})
        # Recheck holds at completion: a report arriving during solving must not
        # silently produce a publishable candidate. Approval also checks holds.
        if _held_workers(session, claim.organization_id, {w.id for w in scenario.workers}):
            raise _JobError("A worker stop-work hold was recorded during optimization.")
        changed = session.execute(
            update(OptimizationRun)
            .where(_fence(claim), OptimizationRun.lease_expires_at > _now())
            .values(
                status="COMPLETED",
                solver_status=result.status,
                result_json=result.model_dump(mode="json"),
                comparison_json=comparison,
                error=None,
                lease_owner=None,
                lease_expires_at=None,
                updated_at=_now(),
            )
            .execution_options(synchronize_session=False)
        )
        if changed.rowcount != 1:
            return False
        _scenario_transition(
            session, claim, "OPTIMIZED" if result.schedule is not None else "REVIEW_REQUIRED"
        )
        _audit(
            session,
            claim,
            "optimization.completed",
            {"attempt": claim.attempt, "solver_status": result.status, "revision": claim.revision},
        )
        session.add(
            Outbox(
                id=str(uuid.uuid4()),
                organization_id=claim.organization_id,
                topic="optimization.completed",
                payload_json={
                    "run_id": claim.id,
                    "scenario_id": claim.scenario_id,
                    "revision": claim.revision,
                    "solver_status": result.status,
                },
                created_at=_now(),
            )
        )
        return True


def _fail(database, claim: _Claim, message: str) -> None:
    with database.session_factory() as session, session.begin():
        changed = session.execute(
            update(OptimizationRun)
            .where(_fence(claim), OptimizationRun.lease_expires_at > _now())
            .values(
                status="FAILED",
                error=message[:500],
                lease_owner=None,
                lease_expires_at=None,
                updated_at=_now(),
            )
            .execution_options(synchronize_session=False)
        )
        if changed.rowcount == 1:
            _scenario_transition(session, claim, "FAILED")
            _audit(session, claim, "optimization.failed", {"error": message[:500]})


def run_once(database, worker_id: str = "heatshield-worker") -> bool:
    """Claim and process at most one job; return False when nothing was claimed.

    Computation runs outside the transaction. A crash leaves a lease that can be
    reclaimed; a late attempt loses its fencing check and cannot overwrite it.
    """
    if not worker_id or len(worker_id) > 100:
        raise ValueError("worker_id must contain between 1 and 100 characters")
    claim = _claim(database, worker_id)
    if claim is None:
        return False
    try:
        scenario = _load_snapshot(database, claim)
        result = optimize(scenario)
        if result.schedule is not None:
            report = validate_schedule(scenario, result.schedule)
            if not report.valid or result.status not in {"OPTIMAL", "FEASIBLE"}:
                raise _JobError("Independent validation rejected the worker's proposed result.")
            result = result.model_copy(update={"validation": report})
        elif result.status in {"OPTIMAL", "FEASIBLE"}:
            raise _JobError("The solver reported a solution without a complete proposed schedule.")
        comparison = _comparison(scenario, result)
        _complete(database, claim, scenario, result, comparison)
    except _JobError as error:
        _fail(database, claim, str(error))
    except Exception as error:
        # Return a bounded diagnostic without exposing connection strings or
        # arbitrary input payloads. Retain full traceback in server-side logs.
        logger.exception("Optimization run %s failed", claim.id)
        _fail(database, claim, f"Worker execution failed ({type(error).__name__}).")
    return True


def main(argv: list[str] | None = None) -> None:
    from .database import Database
    from .settings import Settings

    parser = argparse.ArgumentParser(description="Run HeatShield's durable synthetic planner")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--worker-id", default=f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}")
    parser.add_argument("--poll-seconds", default=1.0, type=float)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    if not 0.05 <= args.poll_seconds <= 60:
        parser.error("--poll-seconds must be between 0.05 and 60")
    settings = Settings.from_env()
    database = Database(args.database_url or settings.database_url)
    try:
        if settings.demo_mode:
            database.initialize()
        else:
            # Deployment owns migrations. This read checks queue readiness and
            # deliberately cannot create or repair an application schema.
            with database.session_factory() as session:
                session.execute(select(OptimizationRun.id).limit(1))
        while True:
            claimed = run_once(database, args.worker_id)
            if args.once:
                return
            if not claimed:
                sleep(args.poll_seconds)
    except KeyboardInterrupt:
        return
    finally:
        database.engine.dispose()


if __name__ == "__main__":
    main()
