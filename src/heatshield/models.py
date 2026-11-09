"""Strict, bounded contracts shared by the API and deterministic planning core."""

from datetime import UTC, date, datetime, time
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)


def aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must include a UTC offset")
    try:
        normalized = value.astimezone(UTC)
    except (OverflowError, ValueError) as error:
        raise ValueError("timestamp cannot be represented in UTC") from error
    if not 2000 <= normalized.year <= 2100:
        raise ValueError("supported UTC timestamps are limited to years 2000 through 2100")
    return normalized


def datetime_or_iso(value):
    if not isinstance(value, (datetime, str)):
        raise ValueError("timestamps must be datetimes or ISO 8601 strings")
    if isinstance(value, str) and "T" not in value and " " not in value:
        raise ValueError("timestamps must be ISO 8601 date-time strings")
    return value


Timestamp = Annotated[datetime, BeforeValidator(datetime_or_iso), AfterValidator(aware_utc)]
Identifier = Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class PolicyPack(Contract):
    id: Identifier = "uae-synthetic-v1"
    version: str = "0.1.0"
    demo_only: Literal[True] = True
    effective_from: date = date(2026, 1, 1)
    effective_until: date = date(2026, 12, 31)
    restricted_season_start: date = date(2026, 6, 15)
    restricted_season_end: date = date(2026, 9, 15)
    restricted_start: time = time(12, 30)
    restricted_end: time = time(15, 0)
    max_task_minutes_acclimatized: int = Field(default=60, ge=15, le=240, strict=True)
    max_task_minutes_unacclimatized: int = Field(default=30, ge=15, le=240, strict=True)
    max_heat_minutes_acclimatized: int = Field(default=240, ge=15, le=1440, strict=True)
    max_heat_minutes_unacclimatized: int = Field(default=120, ge=15, le=1440, strict=True)
    recovery_minutes: int = Field(default=15, ge=15, le=120, multiple_of=15, strict=True)
    max_observation_age_minutes: int = Field(default=60, ge=1, le=240, strict=True)
    # Illustrative software-test values, NOT reviewed occupational-health limits.
    wbgt_limits_c: dict[
        Literal["light", "moderate", "heavy"], Annotated[float, Field(strict=True)]
    ] = Field(default_factory=lambda: {"light": 32.0, "moderate": 30.0, "heavy": 28.0})
    source_urls: tuple[str, ...] = (
        "https://dmt.gov.ae/en/adm/Media-Centre/News/02Jul2026",
        "https://www.osha.gov/heat-exposure/hazards",
    )

    @model_validator(mode="after")
    def consistent(self):
        if self.effective_from > self.effective_until:
            raise ValueError("policy effective dates are reversed")
        if not (
            self.effective_from
            <= self.restricted_season_start
            <= self.restricted_season_end
            <= self.effective_until
        ):
            raise ValueError("restricted season must be inside policy validity")
        if self.restricted_start.tzinfo or self.restricted_end.tzinfo:
            raise ValueError("restriction times are site-local wall times without offsets")
        if self.restricted_start >= self.restricted_end:
            raise ValueError("restriction must end after it starts on the same local day")
        if set(self.wbgt_limits_c) != {"light", "moderate", "heavy"}:
            raise ValueError("all three illustrative workload limits are required")
        if any(not 0 <= limit <= 60 for limit in self.wbgt_limits_c.values()):
            raise ValueError("illustrative WBGT limits must be between 0 and 60 C")
        return self


class Worker(Contract):
    id: Identifier
    skills: tuple[Identifier, ...] = Field(min_length=1, max_length=20)
    available_from: Timestamp
    available_until: Timestamp
    acclimatized: StrictBool = True

    @model_validator(mode="after")
    def interval(self):
        if self.available_from >= self.available_until:
            raise ValueError("worker availability must have positive duration")
        return self


class Task(Contract):
    id: Identifier
    required_skill: Identifier
    zone_id: Identifier
    duration_minutes: int = Field(ge=15, le=240, multiple_of=15, strict=True)
    release_at: Timestamp
    deadline: Timestamp
    deferrable: StrictBool = True
    outdoors: StrictBool = True
    heat_exposed: StrictBool = True
    workload: Literal["light", "moderate", "heavy"] = "moderate"

    @model_validator(mode="after")
    def interval(self):
        if self.release_at >= self.deadline:
            raise ValueError("task deadline must be after release")
        return self


class Observation(Contract):
    id: Identifier
    zone_id: Identifier
    observed_at: Timestamp
    valid_from: Timestamp
    valid_until: Timestamp
    wbgt_c: float = Field(ge=-20, le=70, strict=True)
    source: Literal["simulated", "manual", "sensor", "forecast"] = "simulated"
    quality: Literal["valid", "suspect"] = "valid"
    calibration_reference: str | None = Field(default=None, max_length=200)

    @field_validator("calibration_reference")
    @classmethod
    def trim_calibration(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @model_validator(mode="after")
    def interval(self):
        if self.valid_from >= self.valid_until:
            raise ValueError("observation effective interval must have positive duration")
        if self.observed_at > self.valid_from:
            raise ValueError("observation cannot be measured after its validity begins")
        return self


class Scenario(Contract):
    id: Identifier
    schema_version: Literal["1"] = "1"
    mode: Literal["synthetic"] = "synthetic"
    timezone: str = "Asia/Dubai"
    starts_at: Timestamp
    ends_at: Timestamp
    slot_minutes: Literal[15] = 15
    cooling_capacity: int = Field(default=2, ge=0, le=50, strict=True)
    policy: PolicyPack = Field(default_factory=PolicyPack)
    workers: tuple[Worker, ...] = Field(min_length=1, max_length=40)
    tasks: tuple[Task, ...] = Field(min_length=1, max_length=60)
    observations: tuple[Observation, ...] = Field(default=(), max_length=500)
    solver_time_limit_seconds: float = Field(default=2.0, gt=0, le=5, strict=True)
    seed: int = Field(default=7, ge=0, le=2_147_483_647, strict=True)

    @field_validator("timezone")
    @classmethod
    def known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("timezone must be a known IANA zone") from error
        return value

    @model_validator(mode="after")
    def consistent(self):
        length = (self.ends_at - self.starts_at).total_seconds()
        if length <= 0 or length > 24 * 3600 or length % (self.slot_minutes * 60):
            raise ValueError("horizon must be a positive whole number of slots, at most 24 hours")
        if self.starts_at.second or self.starts_at.microsecond or self.starts_at.minute % 15:
            raise ValueError("horizon start must be on a 15-minute UTC grid")
        for collection in (self.workers, self.tasks, self.observations):
            ids = [item.id for item in collection]
            if len(ids) != len(set(ids)):
                raise ValueError("identifiers must be unique within each collection")
        for worker in self.workers:
            if not self.starts_at <= worker.available_from < worker.available_until <= self.ends_at:
                raise ValueError("worker availability must be inside the planning horizon")
        for task in self.tasks:
            if not self.starts_at <= task.release_at < task.deadline <= self.ends_at:
                raise ValueError("task windows must be inside the planning horizon")
        return self


class Assignment(Contract):
    task_id: Identifier
    worker_id: Identifier
    starts_at: Timestamp
    ends_at: Timestamp


class Recovery(Contract):
    task_id: Identifier
    worker_id: Identifier
    starts_at: Timestamp
    ends_at: Timestamp


class Schedule(Contract):
    scenario_hash: str
    policy_hash: str
    assignments: tuple[Assignment, ...] = Field(default=(), max_length=120)
    recoveries: tuple[Recovery, ...] = Field(default=(), max_length=120)
    deferred_task_ids: tuple[Identifier, ...] = Field(default=(), max_length=120)


class Finding(Contract):
    code: str
    message: str
    task_id: str | None = None
    worker_id: str | None = None


class ValidationReport(Contract):
    valid: bool
    review_required: Literal[True] = True
    operational_approval_allowed: Literal[False] = False
    validator_version: str = "0.1.0"
    violations: tuple[Finding, ...] = ()
    review_findings: tuple[Finding, ...] = ()
    completed_tasks: int = 0
    deferred_tasks: int = 0
    exposure_proxy: int = 0
    workload_spread_minutes: int = 0


class ObjectiveStage(Contract):
    name: str
    status: str
    value: int | None = None
    best_bound: float | None = None


class SolverMetadata(Contract):
    scenario_hash: str
    policy_hash: str
    schema_version: str
    solver_version: str
    seed: int
    elapsed_seconds: float
    time_limit_seconds: float
    candidate_count: int
    stages: tuple[ObjectiveStage, ...] = ()


class OptimizationResult(Contract):
    status: Literal["OPTIMAL", "FEASIBLE", "INFEASIBLE", "UNKNOWN", "MODEL_INVALID"]
    review_required: Literal[True] = True
    operational_approval_allowed: Literal[False] = False
    schedule: Schedule | None = None
    validation: ValidationReport | None = None
    metadata: SolverMetadata
    diagnostics: tuple[str, ...] = ()


class ValidationRequest(Contract):
    scenario: Scenario
    schedule: Schedule
