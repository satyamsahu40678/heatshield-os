"""Additive, computed synthetic walkthrough data for an explicitly enabled demo.

Only designated showcase inputs are solved. Existing queued jobs, user edits,
released holds and historical events are never reset or consumed by this seed.
"""

from datetime import UTC, datetime
from functools import lru_cache

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .auth import hash_password
from .bootstrap import DEMO_ORGANIZATION_ID, DEMO_PASSWORD
from .fixtures import demo_scenario
from .identity import Organization, User
from .models import Scenario
from .optimization import optimize
from .provenance import content_hash
from .validation import validate_schedule
from .worker import _comparison
from .workflow_models import (
    AuditEvent,
    OptimizationRun,
    Outbox,
    PrivateHealthReport,
    ScenarioRecord,
    ScenarioRevision,
)
from .workflows import (
    acknowledge_intervention,
    approve_scenario,
    list_interventions,
    publish_scenario,
    report_symptoms,
    verify_intervention,
    withdraw_scenario,
)

STORIES = (
    ("showcase-canal-west", "Canal West · crew readiness", 21, "DRAFT"),
    ("showcase-port-logistics", "Port logistics · morning redesign", 22, "OPTIMIZED"),
    ("showcase-solar-roof", "Solar roof · reviewed shift", 23, "APPROVED"),
    ("showcase-al-reem", "Al Reem · live simulation", 24, "PUBLISHED"),
    ("showcase-east-yard", "East yard · cooling outage", 25, "REVIEW_REQUIRED"),
    ("showcase-marina-works", "Marina works · withdrawn plan", 26, "WITHDRAWN"),
)
SHOWCASE_IDS = tuple(story[0] for story in STORIES)
SHOWCASE_HOLD_WORKER = "showcase-worker-07"


@lru_cache(maxsize=1)
def _password_hash():
    return hash_password(DEMO_PASSWORD)


def _scenario(marker: str, day: int, target: str) -> Scenario:
    base = demo_scenario(0)
    delta = datetime(2026, 7, day, 2, tzinfo=UTC) - base.starts_at
    # The published walkthrough guarantees several recovery instructions for
    # worker-01 while leaving all six core demo workers free of stop-work holds.
    worker_ids = (
        {"worker-01", "worker-03", "worker-04"}
        if target == "PUBLISHED"
        else {"worker-01", "worker-02", "worker-03", "worker-04"}
    )
    workers = [
        worker.model_dump()
        | {
            "available_from": worker.available_from + delta,
            "available_until": worker.available_until + delta,
        }
        for worker in base.workers
        if worker.id in worker_ids
    ]
    skills = {skill for worker in workers for skill in worker["skills"]}
    tasks = [
        task.model_dump()
        | {"release_at": task.release_at + delta, "deadline": task.deadline + delta}
        for task in base.tasks
        if task.required_skill in skills
    ]
    observations = [
        observation.model_dump()
        | {
            "observed_at": observation.observed_at + delta,
            "valid_from": observation.valid_from + delta,
            "valid_until": observation.valid_until + delta,
        }
        for observation in base.observations
    ]
    return Scenario.model_validate(
        base.model_dump()
        | {
            "id": marker,
            "starts_at": base.starts_at + delta,
            "ends_at": base.ends_at + delta,
            "workers": workers,
            "tasks": tasks,
            "observations": [] if target == "REVIEW_REQUIRED" else observations,
            "cooling_capacity": 0 if target == "REVIEW_REQUIRED" else 2,
            "solver_time_limit_seconds": 0.35,
        }
    )


def _event(session, actor, entity_type, entity_id, action, **payload):
    session.add(
        AuditEvent(
            organization_id=DEMO_ORGANIZATION_ID,
            actor_id=actor.id,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            payload_json=payload | {"source": "showcase_seed", "synthetic_only": True},
        )
    )


def _actors(session):
    actors = {}
    for key, email in (
        ("admin", "admin@example.test"),
        ("safety", "safety@example.test"),
        ("supervisor", "supervisor@example.test"),
        ("worker", "worker@example.test"),
    ):
        actor = session.scalar(
            select(User).where(User.email == email, User.organization_id == DEMO_ORGANIZATION_ID)
        )
        if actor is None:
            raise ValueError("Seed demo identities before adding the showcase")
        actors[key] = actor
    return actors


def _health_story(session, warnings):
    email = "worker-07@example.test"
    actor = session.scalar(select(User).where(User.email == email))
    if actor is None:
        actor = User(
            id="showcase-worker-07-user",
            organization_id=DEMO_ORGANIZATION_ID,
            email=email,
            display_name="Nabil · synthetic escalation walkthrough",
            role="worker",
            worker_id=SHOWCASE_HOLD_WORKER,
            password_hash=_password_hash(),
        )
        session.add(actor)
        session.flush()
    if (
        actor.organization_id != DEMO_ORGANIZATION_ID
        or actor.role != "worker"
        or actor.worker_id != SHOWCASE_HOLD_WORKER
    ):
        warnings.append("Escalation account was already customized; it was preserved.")
        return False
    # Retain a user's subsequent hold release. A prior private fixture report is
    # the additive marker; absence of an active hold does not trigger re-creation.
    existing = session.scalar(
        select(PrivateHealthReport.id).where(
            PrivateHealthReport.organization_id == DEMO_ORGANIZATION_ID,
            PrivateHealthReport.user_id == actor.id,
            PrivateHealthReport.worker_id == SHOWCASE_HOLD_WORKER,
        )
    )
    if existing:
        return False
    report_symptoms(
        session,
        actor,
        "severe",
        ["SIMULATION: fabricated dizziness and fatigue for the escalation walkthrough."],
    )
    return True


def _create_story(session, actor, marker, name, scenario):
    record = ScenarioRecord(id=marker, organization_id=DEMO_ORGANIZATION_ID, name=name)
    session.add(record)
    session.flush()
    session.add(
        ScenarioRevision(
            organization_id=DEMO_ORGANIZATION_ID,
            scenario_id=record.id,
            revision=record.revision,
            scenario_json=scenario.model_dump(mode="json"),
            scenario_hash=content_hash(scenario),
            policy_hash=content_hash(scenario.policy),
            created_by=actor.id,
        )
    )
    _event(session, actor, "scenario", record.id, "scenario.created", revision=1)
    session.flush()
    return record


def _computed_run(session, actor, record, scenario):
    result = optimize(scenario)
    if result.schedule is not None:
        report = validate_schedule(scenario, result.schedule)
        if not report.valid:
            raise RuntimeError("Computed showcase candidate failed independent validation")
        result = result.model_copy(update={"validation": report})
    comparison = _comparison(scenario, result)
    run = OptimizationRun(
        organization_id=DEMO_ORGANIZATION_ID,
        scenario_id=record.id,
        revision=record.revision,
        scenario_hash=content_hash(scenario),
        policy_hash=content_hash(scenario.policy),
        requested_by=actor.id,
        status="COMPLETED",
        solver_status=result.status,
        attempts=1,
        result_json=result.model_dump(mode="json"),
        comparison_json=comparison,
    )
    session.add(run)
    session.flush()
    record.latest_run_id = run.id
    record.status = "OPTIMIZED" if result.schedule else "REVIEW_REQUIRED"
    _event(
        session,
        actor,
        "optimization_run",
        run.id,
        "optimization.completed",
        revision=record.revision,
        solver_status=result.status,
    )
    session.add(
        Outbox(
            organization_id=DEMO_ORGANIZATION_ID,
            topic="optimization.completed",
            payload_json={
                "run_id": run.id,
                "scenario_id": record.id,
                "revision": record.revision,
                "solver_status": result.status,
                "source": "showcase_seed",
                "synthetic_only": True,
            },
        )
    )
    session.flush()
    return run, result


def _seed(session):
    if session.get(Organization, DEMO_ORGANIZATION_ID) is None:
        raise ValueError("Seed demo identities before adding the showcase")
    actors = _actors(session)
    # Acquire worker locks in the same sorted order as ordinary publication.
    # Later workflow calls may re-enter these locks without reversing ordering.
    list(
        session.scalars(
            select(User)
            .where(User.organization_id == DEMO_ORGANIZATION_ID)
            .order_by(User.id)
            .with_for_update()
        )
    )
    created, existing, warnings = [], [], []
    health_created = _health_story(session, warnings)
    for marker, name, day, target in STORIES:
        record = session.get(ScenarioRecord, marker)
        if record is not None:
            # Preserve revisions, names, withdrawals, acknowledgements and any
            # other changes made after the first additive showcase seed.
            existing.append(marker)
            continue
        scenario = _scenario(marker, day, target)
        record = _create_story(session, actors["admin"], marker, name, scenario)
        if target != "DRAFT":
            run, result = _computed_run(session, actors["admin"], record, scenario)
            if target in {"APPROVED", "PUBLISHED", "WITHDRAWN"}:
                if result.schedule is None:
                    warnings.append(f"{name}: solver produced {result.status}; review is required.")
                else:
                    try:
                        approve_scenario(
                            session, actors["safety"], marker, 1, run.id, f"{marker}-review"
                        )
                        if target in {"PUBLISHED", "WITHDRAWN"}:
                            publish_scenario(
                                session, actors["safety"], marker, 1, run.id, f"{marker}-publish"
                            )
                        if target == "PUBLISHED":
                            interventions = list_interventions(session, actors["worker"], marker)[
                                "items"
                            ]
                            if len(interventions) < 3:
                                raise RuntimeError(
                                    "Published showcase needs three worker recovery actions"
                                )
                            acknowledge_intervention(
                                session,
                                actors["worker"],
                                interventions[0]["id"],
                                f"{marker}-receipt-1",
                            )
                            verify_intervention(
                                session,
                                actors["supervisor"],
                                interventions[0]["id"],
                                "SIMULATION: protected recovery observed in the walkthrough.",
                                f"{marker}-verified-1",
                            )
                            acknowledge_intervention(
                                session,
                                actors["worker"],
                                interventions[1]["id"],
                                f"{marker}-receipt-2",
                            )
                        if target == "WITHDRAWN":
                            withdraw_scenario(
                                session,
                                actors["safety"],
                                marker,
                                1,
                                "SIMULATION: superseded planning walkthrough; history retained.",
                                f"{marker}-withdraw",
                            )
                    except HTTPException as error:
                        # Existing user holds/plans remain authoritative. A demo
                        # startup must never override them to manufacture a state.
                        warnings.append(
                            f"{name}: workflow gate retained {record.status} ({error.status_code})."
                        )
        session.flush()
        created.append({"id": record.id, "name": record.name, "status": record.status})
    return {
        "created": created,
        "existing": existing,
        "health_story_created": health_created,
        "warnings": warnings,
        "synthetic_only": True,
    }


def seed_showcase(database, *, demo_mode: bool = True) -> dict:
    """Commit additive demonstration data; reject non-demo use before any writes."""
    if not demo_mode:
        raise ValueError("Showcase content is only available in explicit demo mode")
    for attempt in range(2):
        try:
            with database.session() as session:
                return _seed(session)
        except IntegrityError:
            if attempt:
                raise
            # Two demo processes may start together. Deterministic primary keys
            # let the loser retry as a pure additive read after the first commits.
    raise RuntimeError("Showcase transaction did not complete")
