"""Tenant-scoped operations overview, calculated from retained workflow records."""

from collections import Counter
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import require_roles
from .bootstrap import DEMO_ORGANIZATION_ID
from .database import get_session
from .identity import User
from .models import Contract, OptimizationResult, Scenario
from .provenance import content_hash
from .settings import Settings
from .validation import validate_schedule
from .workflow_models import (
    Approval,
    AuditEvent,
    Intervention,
    OptimizationRun,
    ScenarioRecord,
    ScenarioRevision,
    WorkerHold,
)

router = APIRouter(prefix="/api/v1/demo", tags=["Operations workspace"])
Staff = Annotated[User, Depends(require_roles("admin", "safety_officer", "supervisor"))]
Safety = Annotated[User, Depends(require_roles("admin", "safety_officer"))]
DB = Annotated[Session, Depends(get_session)]


class Worksite(Contract):
    name: str
    location: str
    timezone: str
    replay_label: str


class WorkspaceMetrics(Contract):
    workers: int
    protected_recovery_minutes: int
    verified_interventions: int
    total_interventions: int
    attention_items: int
    published_plans: int


class WorkspaceScenario(Contract):
    id: str
    name: str
    status: str
    revision: int
    starts_at: str
    ends_at: str
    worker_count: int
    task_count: int
    completed_tasks: int | None
    deferred_tasks: int | None
    violation_count: int | None


class CurvePoint(Contract):
    observed_at: str
    wbgt_c: float
    zone_id: str
    source: str


class AttentionItem(Contract):
    id: str
    kind: str
    title: str
    detail: str
    scenario_id: str | None = None
    worker_id: str | None = None


class ActivityItem(Contract):
    id: str
    action: str
    created_at: str
    scenario_id: str | None
    actor_label: str


class WorkspaceWorker(Contract):
    id: str
    display_name: str
    skills: list[str]
    acclimatized: bool | None
    status: str
    work_minutes: int
    recovery_minutes: int


class Workspace(Contract):
    demo_enabled: bool
    worksite: Worksite
    active_scenario_id: str | None
    metrics: WorkspaceMetrics
    curve: list[CurvePoint]
    scenarios: list[WorkspaceScenario]
    attention: list[AttentionItem]
    activity: list[ActivityItem]
    workers: list[WorkspaceWorker]


class LoadShowcaseRequest(Contract):
    pass


def build_workspace(session: Session, actor: User, settings: Settings) -> dict:
    organization = actor.organization_id
    records = list(
        session.scalars(
            select(ScenarioRecord)
            .where(ScenarioRecord.organization_id == organization)
            .order_by(ScenarioRecord.updated_at.desc(), ScenarioRecord.id)
            .limit(200)
        )
    )
    actors = {
        user.id: user
        for user in session.scalars(select(User).where(User.organization_id == organization))
    }
    profiles = {}
    for user in actors.values():
        if user.worker_id and user.worker_id not in profiles:
            profiles[user.worker_id] = user
    holds = list(
        session.scalars(
            select(WorkerHold)
            .where(WorkerHold.organization_id == organization, WorkerHold.active.is_(True))
            .order_by(WorkerHold.created_at.desc())
        )
    )
    held_ids = {hold.worker_id for hold in holds}
    attention = [
        dict(
            id=f"hold:{hold.id}",
            kind="HOLD",
            title="Stop-work review requested",
            detail=f"{hold.worker_id} · owned by the safety officer. Human review is required.",
            scenario_id=None,
            worker_id=hold.worker_id,
        )
        for hold in holds
    ]
    data = {}
    cards = []
    run_to_scenario = {}
    for record in records:
        snapshot = session.scalar(
            select(ScenarioRevision).where(
                ScenarioRevision.organization_id == organization,
                ScenarioRevision.scenario_id == record.id,
                ScenarioRevision.revision == record.revision,
            )
        )
        try:
            scenario = Scenario.model_validate(snapshot.scenario_json) if snapshot else None
            if (
                scenario is None
                or content_hash(scenario) != snapshot.scenario_hash
                or (content_hash(scenario.policy) != snapshot.policy_hash)
            ):
                raise ValueError("Snapshot integrity")
        except (ValidationError, ValueError, TypeError):
            attention.append(
                dict(
                    id=f"integrity:{record.id}",
                    kind="INTEGRITY",
                    title="Input snapshot needs review",
                    detail=record.name,
                    scenario_id=record.id,
                    worker_id=None,
                )
            )
            continue
        run = (
            session.scalar(
                select(OptimizationRun).where(
                    OptimizationRun.id == record.latest_run_id,
                    OptimizationRun.organization_id == organization,
                    OptimizationRun.scenario_id == record.id,
                    OptimizationRun.revision == record.revision,
                )
            )
            if record.latest_run_id
            else None
        )
        report, result = None, None
        if run:
            run_to_scenario[run.id] = record.id
            if run.result_json:
                try:
                    result = OptimizationResult.model_validate(run.result_json)
                    if result.schedule is not None:
                        report = validate_schedule(scenario, result.schedule)
                        if (
                            result.metadata.scenario_hash != snapshot.scenario_hash
                            or result.metadata.policy_hash != snapshot.policy_hash
                            or run.scenario_hash != snapshot.scenario_hash
                            or run.policy_hash != snapshot.policy_hash
                        ):
                            result, report = None, None
                except (ValidationError, ValueError, TypeError):
                    result, report = None, None
        published = False
        if (
            record.status == "PUBLISHED"
            and record.published_at
            and report
            and report.valid
            and result
            and run.status == "COMPLETED"
            and result.status in {"OPTIMAL", "FEASIBLE"}
        ):
            approval = session.scalar(
                select(Approval).where(
                    Approval.id == record.approval_id,
                    Approval.organization_id == organization,
                    Approval.scenario_id == record.id,
                    Approval.revision == record.revision,
                    Approval.run_id == run.id,
                )
            )
            assigned_workers = {item.worker_id for item in result.schedule.assignments}
            published = bool(
                approval
                and approval.scenario_hash == snapshot.scenario_hash
                and approval.policy_hash == snapshot.policy_hash
                and approval.schedule_hash == content_hash(result.schedule)
                and approval.validator_version == report.validator_version
                and not assigned_workers.intersection(held_ids)
            )
        if record.status == "PUBLISHED" and not published:
            attention.append(
                dict(
                    id=f"published-review:{record.id}",
                    kind="INTEGRITY",
                    title="Published plan requires renewed review",
                    detail=record.name,
                    scenario_id=record.id,
                    worker_id=None,
                )
            )
        cards.append(
            dict(
                id=record.id,
                name=record.name,
                status=record.status,
                revision=record.revision,
                starts_at=scenario.starts_at.isoformat(),
                ends_at=scenario.ends_at.isoformat(),
                worker_count=len(scenario.workers),
                task_count=len(scenario.tasks),
                completed_tasks=report.completed_tasks if report else None,
                deferred_tasks=report.deferred_tasks if report else None,
                violation_count=len(report.violations) if report else None,
            )
        )
        data[record.id] = (record, scenario, result, published)
        review_copy = {
            "DRAFT": ("Inputs captured; ready to plan", "Start a durable planning run."),
            "OPTIMIZED": ("A plan is ready for demo review", "Inspect the independent checks."),
            "APPROVED": ("Demo review recorded", "Publication is the next explicit step."),
            "REVIEW_REQUIRED": ("No admissible proposal", "Review staffing, evidence and cooling."),
            "FAILED": ("Planning run needs attention", "Inspect the run before retrying."),
        }
        if record.status in review_copy:
            title, detail = review_copy[record.status]
            attention.append(
                dict(
                    id=f"review:{record.id}",
                    kind="REVIEW",
                    title=title,
                    detail=f"{record.name} · {detail}",
                    scenario_id=record.id,
                    worker_id=None,
                )
            )
    published_ids = [key for key, value in data.items() if value[3]]
    active_id = next((key for key in published_ids if key.startswith("showcase-")), None)
    active_id = active_id or next(iter(published_ids), None) or next(iter(data), None)
    ordered = sorted(data, key=lambda key: (key != active_id, data[key][1].starts_at, key))
    cards.sort(key=lambda item: ordered.index(item["id"]))
    active_scenario = data[active_id][1] if active_id else None
    workforce, work_minutes, recovery_minutes = {}, Counter(), Counter()
    protected_minutes = 0
    for key in ordered:
        record, scenario, result, published = data[key]
        for worker in scenario.workers:
            workforce.setdefault(worker.id, worker)
        if published and result and result.schedule:
            for item in result.schedule.assignments:
                work_minutes[item.worker_id] += int(
                    (item.ends_at - item.starts_at).total_seconds() / 60
                )
            for item in result.schedule.recoveries:
                minutes = int((item.ends_at - item.starts_at).total_seconds() / 60)
                recovery_minutes[item.worker_id] += minutes
                protected_minutes += minutes
    rows = (
        list(
            session.scalars(
                select(Intervention)
                .where(
                    Intervention.organization_id == organization,
                    Intervention.scenario_id.in_(published_ids),
                )
                .order_by(Intervention.created_at, Intervention.id)
            )
        )
        if published_ids
        else []
    )
    interventions = [
        item
        for item in rows
        if item.revision == data[item.scenario_id][0].revision
        and item.run_id == data[item.scenario_id][0].latest_run_id
    ]
    for item in interventions:
        if item.status != "VERIFIED":
            acknowledged = item.status == "ACKNOWLEDGED"
            attention.append(
                dict(
                    id=f"intervention:{item.id}",
                    kind="VERIFICATION" if acknowledged else "ACKNOWLEDGEMENT",
                    title="Verify a protected recovery"
                    if acknowledged
                    else "Instruction awaiting receipt",
                    detail=f"{item.worker_id} · {item.task_id}",
                    scenario_id=item.scenario_id,
                    worker_id=item.worker_id,
                )
            )
    roster = []
    for worker_id in sorted(set(workforce) | held_ids):
        worker = workforce.get(worker_id)
        profile = profiles.get(worker_id)
        roster.append(
            dict(
                id=worker_id,
                display_name=profile.display_name
                if profile
                else worker_id.replace("-", " ").title(),
                skills=list(worker.skills) if worker else [],
                acclimatized=worker.acclimatized if worker else None,
                status="STOP_WORK"
                if worker_id in held_ids
                else "ASSIGNED"
                if work_minutes[worker_id]
                else "AVAILABLE",
                work_minutes=work_minutes[worker_id],
                recovery_minutes=recovery_minutes[worker_id],
            )
        )
    events = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.organization_id == organization)
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id)
        .limit(12)
    )
    activity = []
    for event in events:
        scenario_id = (
            event.entity_id
            if event.entity_type == "scenario" and event.entity_id in data
            else run_to_scenario.get(event.entity_id)
            if event.entity_type == "optimization_run"
            else None
        )
        profile = actors.get(event.actor_id)
        activity.append(
            dict(
                id=event.id,
                action=event.action,
                created_at=event.created_at.isoformat(),
                scenario_id=scenario_id,
                actor_label=profile.display_name if profile else "Planning worker",
            )
        )
    priority = {"HOLD": 0, "INTEGRITY": 1, "VERIFICATION": 2, "REVIEW": 3, "ACKNOWLEDGEMENT": 4}
    attention.sort(key=lambda item: (priority.get(item["kind"], 9), item["id"]))
    return dict(
        demo_enabled=settings.demo_mode and organization == DEMO_ORGANIZATION_ID,
        worksite=dict(
            name=data[active_id][0].name if active_id else "Operations workspace",
            location="Abu Dhabi, UAE"
            if active_scenario and active_scenario.timezone == "Asia/Dubai"
            else active_scenario.timezone
            if active_scenario
            else "No shift selected",
            timezone=active_scenario.timezone if active_scenario else "Asia/Dubai",
            replay_label=(
                "Synthetic shift · "
                + active_scenario.starts_at.astimezone(ZoneInfo(active_scenario.timezone)).strftime(
                    "%d %B %Y"
                )
            )
            if active_scenario
            else "Synthetic workspace",
        ),
        active_scenario_id=active_id,
        metrics=dict(
            workers=len(roster),
            protected_recovery_minutes=protected_minutes,
            verified_interventions=sum(item.status == "VERIFIED" for item in interventions),
            total_interventions=len(interventions),
            attention_items=len(attention),
            published_plans=len(published_ids),
        ),
        curve=[
            dict(
                observed_at=item.observed_at.isoformat(),
                wbgt_c=item.wbgt_c,
                zone_id=item.zone_id,
                source=item.source,
            )
            for item in active_scenario.observations
        ]
        if active_scenario
        else [],
        scenarios=cards,
        attention=attention,
        activity=activity,
        workers=roster,
    )


@router.get("/workspace", response_model=Workspace)
def workspace(actor: Staff, session: DB, request: Request):
    return build_workspace(session, actor, request.app.state.settings)


@router.post("/load", response_model=Workspace)
def load_workspace(body: LoadShowcaseRequest, actor: Safety, session: DB, request: Request):
    settings = request.app.state.settings
    if not settings.demo_mode or actor.organization_id != DEMO_ORGANIZATION_ID:
        raise HTTPException(404, "The synthetic showcase is unavailable for this deployment")
    from .showcase import seed_showcase

    seed_showcase(request.app.state.database, demo_mode=True)
    session.expire_all()
    return build_workspace(session, actor, settings)
