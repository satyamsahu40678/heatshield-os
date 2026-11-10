"""Candidate eligibility for the planners; deliberately unused by the validator."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from math import ceil
from zoneinfo import ZoneInfo

from .models import Scenario, Task, Worker

MAX_CANDIDATES = 50_000


class CandidateBudgetExceeded(ValueError):
    """Model construction stopped before allocating an oversized CP model."""


@dataclass(frozen=True)
class Candidate:
    task_id: str
    worker_id: str
    start: int
    work_end: int
    end: int
    exposure: int


def restricted(scenario: Scenario, start: datetime, end: datetime) -> bool:
    """Check exact half-open work intervals against local policy windows."""
    policy = scenario.policy
    zone = ZoneInfo(scenario.timezone)
    day = start.astimezone(zone).date()
    final_day = (end - timedelta(microseconds=1)).astimezone(zone).date()
    while day <= final_day:
        if policy.restricted_season_start <= day <= policy.restricted_season_end:
            ban_start = datetime.combine(day, policy.restricted_start, zone)
            ban_end = datetime.combine(day, policy.restricted_end, zone)
            if start < ban_end and ban_start < end:
                return True
        day += timedelta(days=1)
    return False


def policy_covers(scenario: Scenario, start: datetime, end: datetime) -> bool:
    zone = ZoneInfo(scenario.timezone)
    first = start.astimezone(zone).date()
    last = (end - timedelta(microseconds=1)).astimezone(zone).date()
    return scenario.policy.effective_from <= first <= last <= scenario.policy.effective_until


def evidence_exposure(scenario: Scenario, task: Task, start: datetime, end: datetime) -> int | None:
    """Return a conservative ordering proxy, or None without admissible coverage.

    Coverage is a union of qualifying effective intervals. Freshness is evaluated
    throughout the proposed task in the synthetic replay timeline. Forecasts,
    suspect readings and uncalibrated sensor records cannot admit an assignment.
    """
    if not task.heat_exposed:
        return 0
    age = timedelta(minutes=scenario.policy.max_observation_age_minutes)
    intervals = []
    boundaries = {start, end}
    for observation in scenario.observations:
        if (
            observation.zone_id != task.zone_id
            or observation.valid_until <= start
            or observation.valid_from >= end
            or observation.quality != "valid"
            or observation.source == "forecast"
            or (
                observation.source == "sensor"
                and not (observation.calibration_reference or "").strip()
            )
        ):
            continue
        left = max(start, observation.valid_from, observation.observed_at)
        right = min(end, observation.valid_until, observation.observed_at + age)
        if left < right:
            intervals.append((left, right, observation.wbgt_c))
            boundaries.update((left, right))
    points = sorted(boundaries)
    maximum = float("-inf")
    for left, right in zip(points, points[1:], strict=False):
        values = [value for begin, finish, value in intervals if begin <= left and right <= finish]
        if not values:
            return None
        value = max(values)
        if value > scenario.policy.wbgt_limits_c[task.workload]:
            return None
        maximum = max(maximum, value)
    return ceil(maximum * 10) * task.duration_minutes


def heat_limit(scenario: Scenario, worker: Worker) -> int:
    policy = scenario.policy
    return (
        policy.max_heat_minutes_acclimatized
        if worker.acclimatized
        else policy.max_heat_minutes_unacclimatized
    )


def candidate_options(scenario: Scenario) -> dict[str, list[Candidate]]:
    """Enumerate only admitted atomic assignments, with a hard model-size guard."""
    horizon_slots = int((scenario.ends_at - scenario.starts_at).total_seconds() // 900)
    options: dict[str, list[Candidate]] = {task.id: [] for task in scenario.tasks}
    count = 0
    for task in scenario.tasks:
        duration = task.duration_minutes // 15
        recovery = scenario.policy.recovery_minutes // 15 if task.heat_exposed else 0
        for slot in range(horizon_slots - duration - recovery + 1):
            start = scenario.starts_at + timedelta(minutes=slot * 15)
            work_end = start + timedelta(minutes=task.duration_minutes)
            end = work_end + timedelta(minutes=recovery * 15)
            if start < task.release_at or work_end > task.deadline:
                continue
            if not policy_covers(scenario, start, work_end):
                continue
            if task.outdoors and restricted(scenario, start, work_end):
                continue
            exposure = evidence_exposure(scenario, task, start, work_end)
            if exposure is None:
                continue
            for worker in scenario.workers:
                if task.required_skill not in worker.skills:
                    continue
                if start < worker.available_from or end > worker.available_until:
                    continue
                max_bout = (
                    scenario.policy.max_task_minutes_acclimatized
                    if worker.acclimatized
                    else scenario.policy.max_task_minutes_unacclimatized
                )
                if task.heat_exposed and (
                    task.duration_minutes > max_bout
                    or task.duration_minutes > heat_limit(scenario, worker)
                    or scenario.cooling_capacity == 0
                ):
                    continue
                count += 1
                if count > MAX_CANDIDATES:
                    raise CandidateBudgetExceeded(
                        f"More than {MAX_CANDIDATES} eligible candidates; reduce scenario size."
                    )
                options[task.id].append(
                    Candidate(
                        task.id,
                        worker.id,
                        slot,
                        slot + duration,
                        slot + duration + recovery,
                        exposure,
                    )
                )
        options[task.id].sort(key=lambda option: (option.start, option.worker_id))
    return options
