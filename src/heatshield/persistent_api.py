"""Authenticated persistent synthetic workflow HTTP contracts."""

from collections.abc import Callable
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import Field, field_validator
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from . import workflows
from .auth import get_current_user, require_roles
from .database import get_session
from .identity import User
from .models import Assignment, Contract, OptimizationResult, Recovery, Scenario

router = APIRouter(prefix="/api/v1", tags=["Synthetic persistent workflow"])
DB = Annotated[Session, Depends(get_session)]
Actor = Annotated[User, Depends(get_current_user)]
Staff = Annotated[User, Depends(require_roles("admin", "safety_officer", "supervisor"))]
Safety = Annotated[User, Depends(require_roles("admin", "safety_officer"))]
WorkerActor = Annotated[User, Depends(require_roles("worker"))]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


class CreateScenario(Contract):
    name: str = Field(min_length=1, max_length=200)
    scenario: Scenario

    @field_validator("name")
    @classmethod
    def nonempty_name(cls, value):
        if not value.strip():
            raise ValueError("name cannot be blank")
        return value.strip()


class ExpectedRevision(Contract):
    expected_revision: int = Field(ge=1, strict=True)


class ReviseScenario(CreateScenario, ExpectedRevision):
    pass


class ReviewScenario(ExpectedRevision):
    run_id: str = Field(min_length=1, max_length=64)


class EmptyRequest(Contract):
    pass


class NoteRequest(Contract):
    note: str = Field(min_length=1, max_length=2000)

    @field_validator("note")
    @classmethod
    def nonempty_note(cls, value):
        if not value.strip():
            raise ValueError("note cannot be blank")
        return value.strip()


class WithdrawScenario(ExpectedRevision, NoteRequest):
    pass


class SymptomRequest(Contract):
    severity: Literal["mild", "severe"]
    symptoms: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        min_length=1, max_length=20
    )

    @field_validator("symptoms")
    @classmethod
    def nonempty_symptoms(cls, values):
        if any(not item.strip() for item in values):
            raise ValueError("symptoms cannot contain blank entries")
        return [item.strip() for item in values]


class ScenarioSummary(Contract):
    id: str
    name: str
    revision: int
    status: str
    latest_run_id: str | None
    created_at: str
    updated_at: str


class RunResponse(Contract):
    id: str
    scenario_id: str
    revision: int
    status: Literal["QUEUED", "RUNNING", "COMPLETED", "FAILED"]
    solver_status: str | None
    result: OptimizationResult | None
    comparison: dict | None
    attempts: int
    error: str | None
    created_at: str
    updated_at: str


class ApprovalResponse(Contract):
    id: str
    run_id: str
    revision: int
    scenario_hash: str
    policy_hash: str
    schedule_hash: str
    actor_id: str
    created_at: str
    type: Literal["SYNTHETIC_REVIEW"]
    operational_approval_allowed: Literal[False]


class ScenarioDetail(ScenarioSummary):
    scenario: Scenario
    scenario_hash: str
    policy_hash: str
    latest_run: RunResponse | None
    approval: ApprovalResponse | None
    published_at: str | None
    operational_approval_allowed: Literal[False]


class InterventionEventResponse(Contract):
    id: str
    type: Literal["ACKNOWLEDGED", "VERIFIED"]
    actor_id: str
    note: str | None
    created_at: str


class InterventionResponse(Contract):
    id: str
    scenario_id: str
    revision: int
    run_id: str
    task_id: str
    worker_id: str
    kind: str
    status: Literal["PENDING", "ACKNOWLEDGED", "VERIFIED"]
    payload: dict
    created_at: str
    events: list[InterventionEventResponse]


class InterventionList(Contract):
    items: list[InterventionResponse]


class WorkerPlan(Contract):
    scenario_id: str
    name: str
    revision: int
    assignments: list[Assignment]
    recoveries: list[Recovery]
    interventions: list[InterventionResponse]


class WorkerScheduleResponse(Contract):
    worker_id: str
    stop_work: bool
    scenarios: list[WorkerPlan]
    operational_approval_allowed: Literal[False]


class WorkerHoldResponse(Contract):
    worker_id: str
    created_at: str
    active: bool
    owner_role: Literal["safety_officer"]
    medical_clearance: Literal[False]


class WorkerHoldList(Contract):
    items: list[WorkerHoldResponse]


def _write(session: Session, action: Callable):
    """Commit before sending success, including database uniqueness/lock checks."""
    try:
        payload = action()
        session.commit()
        return payload
    except (IntegrityError, OperationalError) as error:
        session.rollback()
        raise HTTPException(409, "Concurrent workflow change; reload and retry") from error
    except Exception:
        session.rollback()
        raise


@router.get("/scenarios", response_model=list[ScenarioSummary])
def scenarios(actor: Staff, session: DB):
    return workflows.list_scenarios(session, actor)


@router.post("/scenarios", status_code=201, response_model=ScenarioDetail)
def create(body: CreateScenario, actor: Staff, session: DB):
    return _write(
        session, lambda: workflows.create_scenario(session, actor, body.name, body.scenario)
    )


@router.get("/scenarios/{scenario_id}", response_model=ScenarioDetail)
def scenario_detail(scenario_id: str, actor: Staff, session: DB):
    return workflows.get_scenario(session, actor, scenario_id)


@router.put("/scenarios/{scenario_id}", response_model=ScenarioDetail)
def revise(scenario_id: str, body: ReviseScenario, actor: Staff, session: DB):
    return _write(
        session,
        lambda: workflows.update_scenario(
            session, actor, scenario_id, body.expected_revision, body.name, body.scenario
        ),
    )


@router.post("/scenarios/{scenario_id}/optimize", status_code=202, response_model=RunResponse)
def enqueue(
    scenario_id: str,
    body: ExpectedRevision,
    actor: Staff,
    session: DB,
    idempotency_key: IdempotencyKey,
):
    return _write(
        session,
        lambda: workflows.enqueue_optimization(
            session, actor, scenario_id, body.expected_revision, idempotency_key
        ),
    )


@router.get("/optimization-runs/{run_id}", response_model=RunResponse)
def optimization_run(run_id: str, actor: Staff, session: DB):
    return workflows.get_run(session, actor, run_id)


@router.post("/scenarios/{scenario_id}/approve", response_model=ScenarioDetail)
def approve(
    scenario_id: str,
    body: ReviewScenario,
    actor: Safety,
    session: DB,
    idempotency_key: IdempotencyKey,
):
    return _write(
        session,
        lambda: workflows.approve_scenario(
            session, actor, scenario_id, body.expected_revision, body.run_id, idempotency_key
        ),
    )


@router.post("/scenarios/{scenario_id}/publish", response_model=ScenarioDetail)
def publish(
    scenario_id: str,
    body: ReviewScenario,
    actor: Safety,
    session: DB,
    idempotency_key: IdempotencyKey,
):
    return _write(
        session,
        lambda: workflows.publish_scenario(
            session, actor, scenario_id, body.expected_revision, body.run_id, idempotency_key
        ),
    )


@router.get("/scenarios/{scenario_id}/compare")
def compare(scenario_id: str, actor: Staff, session: DB):
    return workflows.compare_scenario(session, actor, scenario_id)


@router.post("/scenarios/{scenario_id}/withdraw", response_model=ScenarioDetail)
def withdraw(
    scenario_id: str,
    body: WithdrawScenario,
    actor: Safety,
    session: DB,
    idempotency_key: IdempotencyKey,
):
    return _write(
        session,
        lambda: workflows.withdraw_scenario(
            session, actor, scenario_id, body.expected_revision, body.note, idempotency_key
        ),
    )


@router.get("/scenarios/{scenario_id}/audit")
def audit(scenario_id: str, actor: Staff, session: DB):
    return workflows.audit_scenario(session, actor, scenario_id)


@router.get("/interventions", response_model=InterventionList)
def interventions(actor: Actor, session: DB, scenario_id: str | None = None):
    return workflows.list_interventions(session, actor, scenario_id)


@router.post("/interventions/{intervention_id}/acknowledge", response_model=InterventionResponse)
def acknowledge(
    intervention_id: str,
    body: EmptyRequest,
    actor: WorkerActor,
    session: DB,
    idempotency_key: IdempotencyKey,
):
    return _write(
        session,
        lambda: workflows.acknowledge_intervention(
            session, actor, intervention_id, idempotency_key
        ),
    )


@router.post("/interventions/{intervention_id}/verify", response_model=InterventionResponse)
def verify(
    intervention_id: str,
    body: NoteRequest,
    actor: Staff,
    session: DB,
    idempotency_key: IdempotencyKey,
):
    return _write(
        session,
        lambda: workflows.verify_intervention(
            session, actor, intervention_id, body.note, idempotency_key
        ),
    )


@router.get("/workers/me/schedule", response_model=WorkerScheduleResponse)
def own_schedule(actor: WorkerActor, session: DB):
    return workflows.worker_schedule(session, actor)


@router.post("/workers/me/symptoms", status_code=201)
def symptoms(body: SymptomRequest, actor: WorkerActor, session: DB):
    return _write(
        session, lambda: workflows.report_symptoms(session, actor, body.severity, body.symptoms)
    )


@router.get("/workers/holds", response_model=WorkerHoldList)
def worker_holds(actor: Staff, session: DB):
    return workflows.list_worker_holds(session, actor)


@router.post("/workers/{worker_id}/release-hold")
def release_hold(worker_id: str, body: NoteRequest, actor: Safety, session: DB):
    return _write(
        session, lambda: workflows.release_worker_hold(session, actor, worker_id, body.note)
    )
