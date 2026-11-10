"""Transactional, tenant-scoped synthetic planning and intervention workflows."""

import hashlib
import json
from datetime import datetime

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from .identity import Organization, User
from .messages import render_message
from .models import OptimizationResult, Scenario
from .provenance import content_hash
from .validation import validate_schedule
from .workflow_models import (
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
    utcnow,
)

STAFF = {"admin", "safety_officer", "supervisor"}
SAFETY = {"admin", "safety_officer"}


def _role(actor, allowed) -> None:
    if actor.role not in allowed:
        raise HTTPException(403, "This role cannot perform that operation")


def _worker_id(actor) -> str:
    _role(actor, {"worker"})
    if not actor.worker_id:
        raise HTTPException(403, "This account is not linked to a worker")
    return actor.worker_id


def _iso(value: datetime | None):
    return value.isoformat() if value else None


def _record(session: Session, actor, scenario_id: str, *, lock=False) -> ScenarioRecord:
    query = select(ScenarioRecord).where(
        ScenarioRecord.id == scenario_id,
        ScenarioRecord.organization_id == actor.organization_id,
    )
    if lock:
        query = query.with_for_update()
    record = session.scalar(query)
    if record is None:
        raise HTTPException(404, "Scenario not found")
    if lock:
        # PostgreSQL row locks and a conditional version write also serialize
        # SQLite writers. An obsolete read cannot overwrite a newer transition.
        changed = session.execute(
            update(ScenarioRecord)
            .where(
                ScenarioRecord.id == record.id,
                ScenarioRecord.organization_id == actor.organization_id,
                ScenarioRecord.lock_version == record.lock_version,
            )
            .values(lock_version=ScenarioRecord.lock_version + 1)
            .execution_options(synchronize_session=False)
        )
        if changed.rowcount != 1:
            raise HTTPException(409, "Scenario changed concurrently; reload and retry")
        session.expire(record, ["lock_version"])
    return record


def _revision(session: Session, record: ScenarioRecord) -> ScenarioRevision:
    revision = session.scalar(
        select(ScenarioRevision).where(
            ScenarioRevision.organization_id == record.organization_id,
            ScenarioRevision.scenario_id == record.id,
            ScenarioRevision.revision == record.revision,
        )
    )
    if revision is None:
        raise HTTPException(409, "Current immutable snapshot is missing")
    return revision


def _check_revision(record: ScenarioRecord, expected: int) -> None:
    if record.revision != expected:
        raise HTTPException(409, "Scenario revision changed; reload the current snapshot")


def _audit(session: Session, actor, entity_type: str, entity_id: str, action: str, **payload):
    session.add(
        AuditEvent(
            organization_id=actor.organization_id,
            actor_id=actor.id,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            payload_json=payload,
        )
    )


def _emit(session: Session, actor, topic: str, **payload):
    session.add(Outbox(organization_id=actor.organization_id, topic=topic, payload_json=payload))


def _cached(session: Session, actor, operation: str, key: str, payload: dict):
    if not key or len(key) > 128 or key.strip() != key:
        raise HTTPException(
            400, "Idempotency-Key must contain 1–128 characters without outer spaces"
        )
    request_hash = hashlib.sha256(
        json.dumps(
            {"actor_id": actor.id, "payload": payload},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    existing = session.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.organization_id == actor.organization_id,
            IdempotencyRecord.operation == operation,
            IdempotencyRecord.key == key,
        )
    )
    if existing is not None and existing.request_hash != request_hash:
        raise HTTPException(409, "Idempotency-Key was already used for a different request")
    return existing.response_json if existing else None, request_hash


def _remember(session: Session, actor, operation, key, request_hash, response):
    session.add(
        IdempotencyRecord(
            organization_id=actor.organization_id,
            operation=operation,
            key=key,
            request_hash=request_hash,
            response_json=response,
        )
    )
    session.flush()
    return response


def run_payload(run: OptimizationRun) -> dict:
    return {
        "id": run.id,
        "scenario_id": run.scenario_id,
        "revision": run.revision,
        "status": run.status,
        "solver_status": run.solver_status,
        "result": run.result_json,
        "comparison": run.comparison_json,
        "attempts": run.attempts,
        "error": run.error,
        "created_at": _iso(run.created_at),
        "updated_at": _iso(run.updated_at),
    }


def _summary(record: ScenarioRecord) -> dict:
    return {
        "id": record.id,
        "name": record.name,
        "revision": record.revision,
        "status": record.status,
        "latest_run_id": record.latest_run_id,
        "created_at": _iso(record.created_at),
        "updated_at": _iso(record.updated_at),
    }


def _detail(session: Session, record: ScenarioRecord) -> dict:
    revision = _revision(session, record)
    run = (
        session.scalar(
            select(OptimizationRun).where(
                OptimizationRun.id == record.latest_run_id,
                OptimizationRun.organization_id == record.organization_id,
                OptimizationRun.scenario_id == record.id,
                OptimizationRun.revision == record.revision,
            )
        )
        if record.latest_run_id
        else None
    )
    approval = (
        session.scalar(
            select(Approval).where(
                Approval.id == record.approval_id,
                Approval.organization_id == record.organization_id,
                Approval.scenario_id == record.id,
                Approval.revision == record.revision,
            )
        )
        if record.approval_id
        else None
    )
    return _summary(record) | {
        "scenario": revision.scenario_json,
        "scenario_hash": revision.scenario_hash,
        "policy_hash": revision.policy_hash,
        "latest_run": run_payload(run) if run else None,
        "approval": (
            {
                "id": approval.id,
                "run_id": approval.run_id,
                "revision": approval.revision,
                "scenario_hash": approval.scenario_hash,
                "policy_hash": approval.policy_hash,
                "schedule_hash": approval.schedule_hash,
                "actor_id": approval.actor_id,
                "created_at": _iso(approval.created_at),
                "type": "SYNTHETIC_REVIEW",
                "operational_approval_allowed": False,
            }
            if approval
            else None
        ),
        "published_at": _iso(record.published_at),
        "operational_approval_allowed": False,
    }


def list_scenarios(session: Session, actor) -> list[dict]:
    _role(actor, STAFF)
    return [
        _summary(record)
        for record in session.scalars(
            select(ScenarioRecord)
            .where(ScenarioRecord.organization_id == actor.organization_id)
            .order_by(ScenarioRecord.created_at.desc(), ScenarioRecord.id)
        )
    ]


def create_scenario(session: Session, actor, name: str, scenario: Scenario) -> dict:
    _role(actor, STAFF)
    record = ScenarioRecord(organization_id=actor.organization_id, name=name.strip())
    session.add(record)
    session.flush()
    session.add(
        ScenarioRevision(
            organization_id=actor.organization_id,
            scenario_id=record.id,
            revision=record.revision,
            scenario_json=scenario.model_dump(mode="json"),
            scenario_hash=content_hash(scenario),
            policy_hash=content_hash(scenario.policy),
            created_by=actor.id,
        )
    )
    _audit(session, actor, "scenario", record.id, "scenario.created", revision=record.revision)
    session.flush()
    return _detail(session, record)


def get_scenario(session: Session, actor, scenario_id: str) -> dict:
    _role(actor, STAFF)
    return _detail(session, _record(session, actor, scenario_id))


def update_scenario(session, actor, scenario_id, expected_revision, name, scenario) -> dict:
    _role(actor, STAFF)
    record = _record(session, actor, scenario_id, lock=True)
    _check_revision(record, expected_revision)
    record.revision += 1
    record.name = name.strip()
    record.status = "DRAFT"
    record.latest_run_id = None
    record.approval_id = None
    record.published_at = None
    record.updated_at = utcnow()
    session.add(
        ScenarioRevision(
            organization_id=actor.organization_id,
            scenario_id=record.id,
            revision=record.revision,
            scenario_json=scenario.model_dump(mode="json"),
            scenario_hash=content_hash(scenario),
            policy_hash=content_hash(scenario.policy),
            created_by=actor.id,
        )
    )
    _audit(session, actor, "scenario", record.id, "scenario.revised", revision=record.revision)
    session.flush()
    return _detail(session, record)


def _holds(session, organization_id, worker_ids) -> set[str]:
    if not worker_ids:
        return set()
    return set(
        session.scalars(
            select(WorkerHold.worker_id).where(
                WorkerHold.organization_id == organization_id,
                WorkerHold.worker_id.in_(worker_ids),
                WorkerHold.active.is_(True),
            )
        )
    )


def _lock_workers(session, organization_id, worker_ids):
    """Serialize stop-work reports against planning and publication checks."""
    if not worker_ids:
        return
    rows = list(
        session.scalars(
            select(User)
            .where(
                User.organization_id == organization_id,
                User.worker_id.in_(worker_ids),
            )
            .order_by(User.id)
            .with_for_update()
        )
    )
    if session.bind.dialect.name == "sqlite" and rows:
        # SQLite ignores FOR UPDATE. A harmless guarded write acquires its
        # transaction writer lock before reading the associated hold state.
        session.execute(
            update(User)
            .where(User.id.in_([row.id for row in rows]))
            .values(token_version=User.token_version)
            .execution_options(synchronize_session=False)
        )


def enqueue_optimization(session, actor, scenario_id, expected_revision, key) -> dict:
    _role(actor, STAFF)
    record = _record(session, actor, scenario_id, lock=True)
    operation = f"optimize:{record.id}"
    cached, request_hash = _cached(session, actor, operation, key, {"revision": expected_revision})
    if cached is not None:
        return cached
    _check_revision(record, expected_revision)
    revision = _revision(session, record)
    scenario = Scenario.model_validate(revision.scenario_json)
    _lock_workers(session, actor.organization_id, [worker.id for worker in scenario.workers])
    if _holds(session, actor.organization_id, [worker.id for worker in scenario.workers]):
        raise HTTPException(409, "A worker has an active stop-work hold requiring human review")
    pending = session.scalar(
        select(OptimizationRun).where(
            OptimizationRun.organization_id == actor.organization_id,
            OptimizationRun.scenario_id == record.id,
            OptimizationRun.revision == record.revision,
            OptimizationRun.status.in_(("QUEUED", "RUNNING")),
        )
    )
    if pending:
        raise HTTPException(409, "A planning job is already queued or running for this revision")
    run = OptimizationRun(
        organization_id=actor.organization_id,
        scenario_id=record.id,
        revision=record.revision,
        scenario_hash=revision.scenario_hash,
        policy_hash=revision.policy_hash,
        requested_by=actor.id,
    )
    session.add(run)
    session.flush()
    record.latest_run_id = run.id
    record.status = "QUEUED"
    record.approval_id = None
    record.published_at = None
    record.updated_at = utcnow()
    _audit(
        session,
        actor,
        "scenario",
        record.id,
        "optimization.queued",
        run_id=run.id,
        revision=record.revision,
    )
    return _remember(session, actor, operation, key, request_hash, run_payload(run))


def get_run(session, actor, run_id: str) -> dict:
    _role(actor, STAFF)
    run = session.scalar(
        select(OptimizationRun).where(
            OptimizationRun.id == run_id,
            OptimizationRun.organization_id == actor.organization_id,
        )
    )
    if run is None:
        raise HTTPException(404, "Optimization run not found")
    return run_payload(run)


def _validated_candidate(session, actor, record, run_id):
    revision = _revision(session, record)
    run = session.scalar(
        select(OptimizationRun).where(
            OptimizationRun.id == run_id,
            OptimizationRun.organization_id == actor.organization_id,
            OptimizationRun.scenario_id == record.id,
            OptimizationRun.revision == record.revision,
        )
    )
    if run is None or record.latest_run_id != run_id:
        raise HTTPException(409, "Run does not match the latest current scenario revision")
    if run.status != "COMPLETED" or not run.result_json:
        raise HTTPException(409, "Run has no completed candidate")
    try:
        scenario = Scenario.model_validate(revision.scenario_json)
        result = OptimizationResult.model_validate(run.result_json)
    except ValidationError as error:
        raise HTTPException(409, "Stored input or result no longer matches its contract") from error
    if (
        revision.scenario_hash != content_hash(scenario)
        or revision.policy_hash != content_hash(scenario.policy)
        or run.scenario_hash != revision.scenario_hash
        or run.policy_hash != revision.policy_hash
        or result.metadata.scenario_hash != revision.scenario_hash
        or result.metadata.policy_hash != revision.policy_hash
        or result.status not in {"OPTIMAL", "FEASIBLE"}
        or result.schedule is None
    ):
        raise HTTPException(409, "Candidate inputs, policy or result integrity check failed")
    report = validate_schedule(scenario, result.schedule)
    if not report.valid:
        raise HTTPException(
            409,
            {
                "message": "Fresh independent validation failed",
                "violations": [f.model_dump() for f in report.violations],
            },
        )
    assigned_worker_ids = {a.worker_id for a in result.schedule.assignments}
    _lock_workers(session, actor.organization_id, assigned_worker_ids)
    if _holds(session, actor.organization_id, assigned_worker_ids):
        raise HTTPException(409, "An assigned worker has an active stop-work hold")
    return revision, result, report


def _publication_lock(session, organization_id):
    organization = session.scalar(
        select(Organization.id).where(Organization.id == organization_id).with_for_update()
    )
    if organization is None:
        raise HTTPException(409, "Organization is unavailable for publication")
    # SQLite already holds the writer lock from _record's conditional write.


def _published_schedule(session, record):
    """Read another active plan without acquiring any worker or scenario locks."""
    revision = _revision(session, record)
    run = session.scalar(
        select(OptimizationRun).where(
            OptimizationRun.id == record.latest_run_id,
            OptimizationRun.organization_id == record.organization_id,
            OptimizationRun.scenario_id == record.id,
            OptimizationRun.revision == record.revision,
        )
    )
    approval = session.scalar(
        select(Approval).where(
            Approval.id == record.approval_id,
            Approval.organization_id == record.organization_id,
            Approval.scenario_id == record.id,
            Approval.revision == record.revision,
            Approval.run_id == record.latest_run_id,
        )
    )
    try:
        scenario = Scenario.model_validate(revision.scenario_json)
        result = OptimizationResult.model_validate(run.result_json) if run else None
    except ValidationError as error:
        raise HTTPException(
            409,
            f"Published scenario '{record.name}' has corrupt inputs; withdraw it for review",
        ) from error
    if (
        run is None
        or run.status != "COMPLETED"
        or result is None
        or result.status not in {"OPTIMAL", "FEASIBLE"}
        or result.schedule is None
        or approval is None
        or revision.scenario_hash != content_hash(scenario)
        or revision.policy_hash != content_hash(scenario.policy)
        or run.scenario_hash != revision.scenario_hash
        or run.policy_hash != revision.policy_hash
        or result.metadata.scenario_hash != revision.scenario_hash
        or result.metadata.policy_hash != revision.policy_hash
        or approval.scenario_hash != revision.scenario_hash
        or approval.policy_hash != revision.policy_hash
        or approval.schedule_hash != content_hash(result.schedule)
    ):
        raise HTTPException(
            409,
            f"Published scenario '{record.name}' failed integrity checks; withdraw it for review",
        )
    report = validate_schedule(scenario, result.schedule)
    if not report.valid or approval.validator_version != report.validator_version:
        raise HTTPException(
            409,
            f"Published scenario '{record.name}' no longer validates; withdraw it for review",
        )
    return result.schedule


def _check_published_conflicts(session, record, schedule):
    """Admit a plan against all active tenant schedules under its tenant lock."""
    candidate = {}
    for kind, periods in (("work", schedule.assignments), ("recovery", schedule.recoveries)):
        for period in periods:
            candidate.setdefault(period.worker_id, []).append(
                (period.starts_at, period.ends_at, kind)
            )
    published = session.scalars(
        select(ScenarioRecord)
        .where(
            ScenarioRecord.organization_id == record.organization_id,
            ScenarioRecord.id != record.id,
            ScenarioRecord.status == "PUBLISHED",
        )
        .order_by(ScenarioRecord.id)
    )
    for other in published:
        other_schedule = _published_schedule(session, other)
        for kind, periods in (
            ("work", other_schedule.assignments),
            ("recovery", other_schedule.recoveries),
        ):
            for period in periods:
                for start, end, candidate_kind in candidate.get(period.worker_id, ()):
                    if start < period.ends_at and period.starts_at < end:
                        raise HTTPException(
                            409,
                            f"Worker {period.worker_id} has overlapping {candidate_kind} and "
                            f"{kind} in published scenario '{other.name}' ({other.id}), "
                            f"{max(start, period.starts_at).isoformat()} to "
                            f"{min(end, period.ends_at).isoformat()}. "
                            "Withdraw the overlapping plan before publishing its replacement.",
                        )


def approve_scenario(session, actor, scenario_id, expected_revision, run_id, key) -> dict:
    _role(actor, SAFETY)
    record = _record(session, actor, scenario_id, lock=True)
    operation = f"approve:{record.id}"
    cached, request_hash = _cached(
        session, actor, operation, key, {"revision": expected_revision, "run_id": run_id}
    )
    if cached is not None:
        return cached
    _check_revision(record, expected_revision)
    revision, result, report = _validated_candidate(session, actor, record, run_id)
    if record.published_at:
        raise HTTPException(409, "This revision is already published")
    if record.approval_id:
        approval = session.scalar(
            select(Approval).where(
                Approval.id == record.approval_id,
                Approval.organization_id == actor.organization_id,
                Approval.scenario_id == record.id,
                Approval.revision == record.revision,
            )
        )
        if approval is None or approval.run_id != run_id:
            raise HTTPException(409, "A different run was reviewed")
    else:
        approval = Approval(
            organization_id=actor.organization_id,
            scenario_id=record.id,
            revision=record.revision,
            run_id=run_id,
            scenario_hash=revision.scenario_hash,
            policy_hash=revision.policy_hash,
            schedule_hash=content_hash(result.schedule),
            validator_version=report.validator_version,
            actor_id=actor.id,
        )
        session.add(approval)
        session.flush()
        record.approval_id = approval.id
        record.status = "APPROVED"
        record.updated_at = utcnow()
        _audit(
            session,
            actor,
            "scenario",
            record.id,
            "scenario.demo_reviewed",
            revision=record.revision,
            run_id=run_id,
            approval_id=approval.id,
        )
    session.flush()
    return _remember(session, actor, operation, key, request_hash, _detail(session, record))


def publish_scenario(session, actor, scenario_id, expected_revision, run_id, key) -> dict:
    _role(actor, SAFETY)
    record = _record(session, actor, scenario_id, lock=True)
    operation = f"publish:{record.id}"
    cached, request_hash = _cached(
        session, actor, operation, key, {"revision": expected_revision, "run_id": run_id}
    )
    if cached is not None:
        return cached
    _check_revision(record, expected_revision)
    revision, result, report = _validated_candidate(session, actor, record, run_id)
    approval = (
        session.scalar(
            select(Approval).where(
                Approval.id == record.approval_id,
                Approval.organization_id == actor.organization_id,
                Approval.scenario_id == record.id,
                Approval.revision == record.revision,
            )
        )
        if record.approval_id
        else None
    )
    if (
        approval is None
        or approval.organization_id != actor.organization_id
        or approval.revision != record.revision
        or approval.run_id != run_id
        or approval.scenario_hash != revision.scenario_hash
        or approval.policy_hash != revision.policy_hash
        or approval.schedule_hash != content_hash(result.schedule)
        or approval.validator_version != report.validator_version
    ):
        raise HTTPException(409, "Current candidate requires a matching fresh synthetic review")
    # Acquire this after our own hold checks. Checking other plans below is pure:
    # it must not lock their users and create a cross-plan lock-order cycle.
    _publication_lock(session, actor.organization_id)
    _check_published_conflicts(session, record, result.schedule)
    if record.published_at is None:
        record.published_at = utcnow()
        record.status = "PUBLISHED"
        record.updated_at = utcnow()
        for recovery in result.schedule.recoveries:
            existing = session.scalar(
                select(Intervention.id).where(
                    Intervention.organization_id == actor.organization_id,
                    Intervention.scenario_id == record.id,
                    Intervention.revision == record.revision,
                    Intervention.run_id == run_id,
                    Intervention.task_id == recovery.task_id,
                )
            )
            if existing is not None:
                continue  # Re-publication preserves original receipt/verification events.
            starts_at, ends_at = recovery.starts_at.isoformat(), recovery.ends_at.isoformat()
            intervention = Intervention(
                organization_id=actor.organization_id,
                scenario_id=record.id,
                revision=record.revision,
                run_id=run_id,
                task_id=recovery.task_id,
                worker_id=recovery.worker_id,
                payload_json={
                    "starts_at": starts_at,
                    "ends_at": ends_at,
                    "template": "recovery",
                    "messages": {
                        language: render_message(
                            "recovery", language=language, starts_at=starts_at, ends_at=ends_at
                        )
                        for language in ("en", "hi", "ar")
                    },
                    "synthetic_only": True,
                },
            )
            session.add(intervention)
        _audit(
            session,
            actor,
            "scenario",
            record.id,
            "scenario.demo_published",
            revision=record.revision,
            run_id=run_id,
        )
        _emit(
            session,
            actor,
            "schedule.demo_published",
            scenario_id=record.id,
            revision=record.revision,
            run_id=run_id,
            dispatch_performed=False,
        )
    session.flush()
    return _remember(session, actor, operation, key, request_hash, _detail(session, record))


def withdraw_scenario(session, actor, scenario_id, expected_revision, note, key):
    _role(actor, SAFETY)
    record = _record(session, actor, scenario_id, lock=True)
    operation = f"withdraw:{record.id}"
    cached, request_hash = _cached(
        session, actor, operation, key, {"revision": expected_revision, "note": note}
    )
    if cached is not None:
        return cached
    _check_revision(record, expected_revision)
    _publication_lock(session, actor.organization_id)
    if record.status not in {"PUBLISHED", "WITHDRAWN"}:
        raise HTTPException(409, "Only a published demo plan can be withdrawn")
    if record.status == "PUBLISHED":
        record.status = "WITHDRAWN"
        record.published_at = None
        record.updated_at = utcnow()
        _audit(
            session,
            actor,
            "scenario",
            record.id,
            "scenario.demo_withdrawn",
            revision=record.revision,
            run_id=record.latest_run_id,
            note=note,
        )
        _emit(
            session,
            actor,
            "schedule.demo_withdrawn",
            scenario_id=record.id,
            revision=record.revision,
            run_id=record.latest_run_id,
        )
    session.flush()
    return _remember(session, actor, operation, key, request_hash, _detail(session, record))


def compare_scenario(session, actor, scenario_id):
    _role(actor, STAFF)
    record = _record(session, actor, scenario_id)
    if not record.latest_run_id:
        raise HTTPException(409, "Scenario has no planning run")
    run = get_run(session, actor, record.latest_run_id)
    if run["comparison"] is None:
        raise HTTPException(409, "Comparison is not available yet")
    return run["comparison"]


def audit_scenario(session, actor, scenario_id):
    _role(actor, STAFF)
    _record(session, actor, scenario_id)
    run_ids = select(OptimizationRun.id).where(
        OptimizationRun.organization_id == actor.organization_id,
        OptimizationRun.scenario_id == scenario_id,
    )
    events = session.scalars(
        select(AuditEvent)
        .where(
            AuditEvent.organization_id == actor.organization_id,
            or_(
                and_(AuditEvent.entity_type == "scenario", AuditEvent.entity_id == scenario_id),
                and_(
                    AuditEvent.entity_type == "optimization_run", AuditEvent.entity_id.in_(run_ids)
                ),
            ),
        )
        .order_by(AuditEvent.created_at, AuditEvent.id)
    )
    return {
        "events": [
            {
                "id": e.id,
                "entity_type": e.entity_type,
                "entity_id": e.entity_id,
                "actor_id": e.actor_id,
                "action": e.action,
                "payload": e.payload_json,
                "created_at": _iso(e.created_at),
            }
            for e in events
        ]
    }


def intervention_payload(session, intervention):
    events = session.scalars(
        select(InterventionEvent)
        .where(InterventionEvent.intervention_id == intervention.id)
        .order_by(InterventionEvent.created_at, InterventionEvent.id)
    )
    return {
        "id": intervention.id,
        "scenario_id": intervention.scenario_id,
        "revision": intervention.revision,
        "run_id": intervention.run_id,
        "task_id": intervention.task_id,
        "worker_id": intervention.worker_id,
        "kind": intervention.kind,
        "status": intervention.status,
        "payload": intervention.payload_json,
        "created_at": _iso(intervention.created_at),
        "events": [
            {
                "id": e.id,
                "type": e.event_type,
                "actor_id": e.actor_id,
                "note": e.note,
                "created_at": _iso(e.created_at),
            }
            for e in events
        ],
    }


def list_interventions(session, actor, scenario_id=None):
    _role(actor, STAFF | {"worker"})
    query = (
        select(Intervention)
        .join(ScenarioRecord, ScenarioRecord.id == Intervention.scenario_id)
        .where(
            Intervention.organization_id == actor.organization_id,
            ScenarioRecord.organization_id == actor.organization_id,
            ScenarioRecord.revision == Intervention.revision,
            ScenarioRecord.status == "PUBLISHED",
            ScenarioRecord.latest_run_id == Intervention.run_id,
        )
    )
    if scenario_id:
        if actor.role in STAFF:
            _record(session, actor, scenario_id)
        query = query.where(Intervention.scenario_id == scenario_id)
    if actor.role == "worker":
        query = query.where(Intervention.worker_id == _worker_id(actor))
    return {
        "items": [
            intervention_payload(session, item)
            for item in session.scalars(query.order_by(Intervention.created_at, Intervention.id))
        ]
    }


def _locked_intervention(session, actor, intervention_id):
    item = session.scalar(
        select(Intervention)
        .where(
            Intervention.id == intervention_id,
            Intervention.organization_id == actor.organization_id,
        )
        .with_for_update()
    )
    if item is None or (actor.role == "worker" and item.worker_id != _worker_id(actor)):
        raise HTTPException(404, "Intervention not found")
    record = _record(session, actor, item.scenario_id, lock=True)
    if (
        record.status != "PUBLISHED"
        or record.revision != item.revision
        or record.latest_run_id != item.run_id
    ):
        raise HTTPException(409, "Intervention belongs to a superseded or unpublished plan")
    return item


def acknowledge_intervention(session, actor, intervention_id, key):
    _worker_id(actor)
    item = _locked_intervention(session, actor, intervention_id)
    operation = f"acknowledge:{item.id}"
    cached, request_hash = _cached(session, actor, operation, key, {})
    if cached is not None:
        return cached
    if item.status == "PENDING":
        session.add(
            InterventionEvent(
                organization_id=actor.organization_id,
                intervention_id=item.id,
                event_type="ACKNOWLEDGED",
                actor_id=actor.id,
            )
        )
        item.status = "ACKNOWLEDGED"
        _audit(
            session,
            actor,
            "scenario",
            item.scenario_id,
            "intervention.acknowledged",
            intervention_id=item.id,
            revision=item.revision,
        )
        _emit(session, actor, "intervention.acknowledged", intervention_id=item.id)
    session.flush()
    return _remember(
        session, actor, operation, key, request_hash, intervention_payload(session, item)
    )


def verify_intervention(session, actor, intervention_id, note, key):
    _role(actor, STAFF)
    item = _locked_intervention(session, actor, intervention_id)
    operation = f"verify:{item.id}"
    cached, request_hash = _cached(session, actor, operation, key, {"note": note})
    if cached is not None:
        return cached
    acknowledged = session.scalar(
        select(InterventionEvent.id).where(
            InterventionEvent.intervention_id == item.id,
            InterventionEvent.event_type == "ACKNOWLEDGED",
        )
    )
    if acknowledged is None:
        raise HTTPException(
            409, "Worker acknowledgement is required before implementation verification"
        )
    if item.status != "VERIFIED":
        session.add(
            InterventionEvent(
                organization_id=actor.organization_id,
                intervention_id=item.id,
                event_type="VERIFIED",
                actor_id=actor.id,
                note=note,
            )
        )
        item.status = "VERIFIED"
        _audit(
            session,
            actor,
            "scenario",
            item.scenario_id,
            "intervention.verified",
            intervention_id=item.id,
            revision=item.revision,
        )
        _emit(session, actor, "intervention.verified", intervention_id=item.id)
    session.flush()
    return _remember(
        session, actor, operation, key, request_hash, intervention_payload(session, item)
    )


def worker_schedule(session, actor):
    worker_id = _worker_id(actor)
    records = session.scalars(
        select(ScenarioRecord)
        .where(
            ScenarioRecord.organization_id == actor.organization_id,
            ScenarioRecord.status == "PUBLISHED",
        )
        .order_by(ScenarioRecord.created_at)
    )
    scenarios = []
    for record in records:
        try:
            revision, result, report = _validated_candidate(
                session, actor, record, record.latest_run_id
            )
        except HTTPException:
            continue
        approval = session.scalar(
            select(Approval).where(
                Approval.id == record.approval_id,
                Approval.organization_id == actor.organization_id,
                Approval.scenario_id == record.id,
                Approval.revision == record.revision,
                Approval.run_id == record.latest_run_id,
            )
        )
        if (
            approval is None
            or approval.scenario_hash != revision.scenario_hash
            or approval.policy_hash != revision.policy_hash
            or approval.schedule_hash != content_hash(result.schedule)
            or approval.validator_version != report.validator_version
        ):
            continue
        assignments = [
            a.model_dump(mode="json")
            for a in result.schedule.assignments
            if a.worker_id == worker_id
        ]
        if not assignments:
            continue
        scenarios.append(
            {
                "scenario_id": record.id,
                "name": record.name,
                "revision": record.revision,
                "assignments": assignments,
                "recoveries": [
                    r.model_dump(mode="json")
                    for r in result.schedule.recoveries
                    if r.worker_id == worker_id
                ],
                "interventions": list_interventions(session, actor, record.id)["items"],
            }
        )
    return {
        "worker_id": worker_id,
        "stop_work": bool(_holds(session, actor.organization_id, {worker_id})),
        "scenarios": scenarios,
        "operational_approval_allowed": False,
    }


def report_symptoms(session, actor, severity, symptoms):
    worker_id = _worker_id(actor)
    _lock_workers(session, actor.organization_id, {worker_id})
    urgent = severity == "severe"
    report = PrivateHealthReport(
        organization_id=actor.organization_id,
        worker_id=worker_id,
        user_id=actor.id,
        severity=severity,
        symptoms_json=symptoms,
        urgent=urgent,
    )
    session.add(report)
    if urgent:
        hold = session.scalar(
            select(WorkerHold)
            .where(
                WorkerHold.organization_id == actor.organization_id,
                WorkerHold.worker_id == worker_id,
            )
            .with_for_update()
        )
        if hold:
            if not hold.active:
                hold.created_at = utcnow()
            hold.active = True
            hold.released_at = None
            hold.released_by = None
            hold.release_note = None
        else:
            session.add(WorkerHold(organization_id=actor.organization_id, worker_id=worker_id))
        _emit(
            session,
            actor,
            "worker.stop_work_review_required",
            worker_id=worker_id,
            dispatch_performed=False,
        )
    session.flush()
    # Symptoms and severity stay out of operational audit/outbox payloads.
    _audit(
        session,
        actor,
        "worker",
        worker_id,
        "worker.private_report_recorded",
        action_required=urgent,
    )
    return {
        "id": report.id,
        "stop_work": urgent or bool(_holds(session, actor.organization_id, {worker_id})),
        "urgent": urgent,
        "instructions": render_message("stop_work", language="en")
        if urgent
        else "SIMULATION: Pause work and contact the designated on-site responder for review.",
        "dispatch_performed": False,
    }


def list_worker_holds(session, actor):
    _role(actor, STAFF)
    holds = session.scalars(
        select(WorkerHold)
        .where(
            WorkerHold.organization_id == actor.organization_id,
            WorkerHold.active.is_(True),
        )
        .order_by(WorkerHold.created_at, WorkerHold.worker_id)
    )
    return {
        "items": [
            {
                "worker_id": hold.worker_id,
                "created_at": _iso(hold.created_at),
                "active": hold.active,
                "owner_role": "safety_officer",
                "medical_clearance": False,
            }
            for hold in holds
        ]
    }


def release_worker_hold(session, actor, worker_id, note):
    _role(actor, SAFETY)
    hold = session.scalar(
        select(WorkerHold)
        .where(
            WorkerHold.organization_id == actor.organization_id, WorkerHold.worker_id == worker_id
        )
        .with_for_update()
    )
    if hold is None:
        raise HTTPException(404, "Stop-work hold not found")
    hold.active = False
    hold.released_at = utcnow()
    hold.released_by = actor.id
    hold.release_note = note
    _audit(
        session,
        actor,
        "worker",
        worker_id,
        "worker.demo_hold_released",
        note=note,
        medical_clearance=False,
    )
    session.flush()
    return {"worker_id": worker_id, "stop_work": False, "medical_clearance": False}
