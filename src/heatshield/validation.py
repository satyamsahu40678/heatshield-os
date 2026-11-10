"""Independent reconstruction of the prototype's encoded schedule constraints.

This module deliberately does not import candidate generation or optimizer
eligibility logic. Passing validation describes only the unreviewed demo model.
"""

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from math import ceil
from zoneinfo import ZoneInfo

from .models import Finding, Scenario, Schedule, ValidationReport
from .provenance import content_hash


def _overlaps(a: datetime, b: datetime, c: datetime, d: datetime) -> bool:
    return a < d and c < b


def _restricted_overlap(scenario: Scenario, start: datetime, end: datetime) -> bool:
    """Check exact instant overlap against every applicable local calendar day."""
    if end <= start:
        return False
    zone = ZoneInfo(scenario.timezone)
    policy = scenario.policy
    day = start.astimezone(zone).date()
    last = (end - timedelta(microseconds=1)).astimezone(zone).date()
    while day <= last:
        if policy.restricted_season_start <= day <= policy.restricted_season_end:
            ban_start = datetime.combine(day, policy.restricted_start, zone).astimezone(UTC)
            ban_end = datetime.combine(day, policy.restricted_end, zone).astimezone(UTC)
            if _overlaps(start, end, ban_start, ban_end):
                return True
        if day == last:
            break
        day += timedelta(days=1)
    return False


def _environment(
    scenario: Scenario, zone_id: str, start: datetime, end: datetime
) -> tuple[bool, float]:
    """Reconstruct continuous usable coverage and the most restrictive reading.

    Sweep segment boundaries rather than trusting a single convenient sample.
    Freshness is checked at every segment end on the synthetic replay timeline.
    A sample expiring exactly at task end covers that half-open task interval.
    """
    if end <= start:
        return False, 0.0
    age = timedelta(minutes=scenario.policy.max_observation_age_minutes)
    samples = []
    points = {start, end}
    for observation in scenario.observations:
        if (
            observation.zone_id != zone_id
            or observation.quality != "valid"
            or observation.source == "forecast"
            or observation.valid_from >= end
            or observation.valid_until <= start
            or (
                observation.source == "sensor"
                and not (observation.calibration_reference or "").strip()
            )
        ):
            continue
        # Compare before adding: irrelevant or extreme external timestamps must
        # not overflow while computing expiry. If validity ends sooner, adding
        # the full freshness allowance is unnecessary.
        if observation.valid_until - observation.observed_at <= age:
            usable_end = observation.valid_until
        else:
            usable_end = observation.observed_at + age
        usable_start = max(observation.valid_from, observation.observed_at)
        if usable_start < end and usable_end > start and usable_end > usable_start:
            samples.append((usable_start, usable_end, observation.wbgt_c))
            points.add(max(start, usable_start))
            points.add(min(end, usable_end))
    ordered = sorted(points)
    maximum = float("-inf")
    for left, right in zip(ordered, ordered[1:], strict=False):
        readings = [value for a, b, value in samples if a <= left and right <= b]
        if not readings:
            return False, 0.0
        maximum = max(maximum, max(readings))
    return True, maximum


def validate_schedule(scenario: Scenario, schedule: Schedule) -> ValidationReport:
    """Check an untrusted proposed schedule against the original scenario."""
    violations: list[Finding] = []
    review: list[Finding] = [
        Finding(
            code="DEMO_POLICY",
            message="Policy thresholds and allowances are unreviewed software-test configuration.",
        ),
        Finding(
            code="SYNTHETIC_SCENARIO",
            message="Synthetic planning only; passing constraints does not approve actual work.",
        ),
    ]

    def fail(code: str, message: str, task_id=None, worker_id=None) -> None:
        violations.append(Finding(code=code, message=message, task_id=task_id, worker_id=worker_id))

    if schedule.scenario_hash != content_hash(scenario):
        fail("SCENARIO_HASH", "Schedule is not bound to the supplied complete scenario.")
    if schedule.policy_hash != content_hash(scenario.policy):
        fail("POLICY_HASH", "Schedule is not bound to the supplied policy version and content.")

    zone = ZoneInfo(scenario.timezone)
    first_day = scenario.starts_at.astimezone(zone).date()
    last_day = (scenario.ends_at - timedelta(microseconds=1)).astimezone(zone).date()
    if first_day < scenario.policy.effective_from or last_day > scenario.policy.effective_until:
        fail("POLICY_VALIDITY", "Policy effective dates do not cover the full local horizon.")

    workers = {worker.id: worker for worker in scenario.workers}
    tasks = {task.id: task for task in scenario.tasks}
    assignments_by_task = defaultdict(list)
    recovery_by_task = defaultdict(list)
    counts = Counter(a.task_id for a in schedule.assignments)
    counts.update(schedule.deferred_task_ids)
    for task_id in counts.keys() - tasks.keys():
        fail("UNKNOWN_TASK", "Schedule references a task absent from the scenario.", task_id)
    for task in scenario.tasks:
        if counts[task.id] != 1:
            fail(
                "TASK_PARTITION",
                "Each task must appear exactly once, assigned or explicitly deferred.",
                task.id,
            )
    for task_id in schedule.deferred_task_ids:
        if task_id in tasks and not tasks[task_id].deferrable:
            fail("MANDATORY_DEFERRED", "A mandatory task cannot be deferred.", task_id)

    provenance = {observation.source for observation in scenario.observations}
    for source in sorted(provenance):
        review.append(
            Finding(
                code=f"{source.upper()}_EVIDENCE",
                message=f"Inputs contain {source} provenance within a synthetic replay.",
            )
        )
    if any(observation.quality != "valid" for observation in scenario.observations):
        review.append(
            Finding(code="SUSPECT_EVIDENCE", message="Suspect observations cannot admit heat work.")
        )
    if any(
        observation.source == "sensor" and not (observation.calibration_reference or "").strip()
        for observation in scenario.observations
    ):
        review.append(
            Finding(
                code="UNCALIBRATED_SENSOR",
                message="Sensor records without a calibration reference cannot admit heat work.",
            )
        )
    # Preserve a data-quality finding even when an affected task is deferred.
    for task in scenario.tasks:
        if task.heat_exposed:
            covered, _ = _environment(scenario, task.zone_id, task.release_at, task.deadline)
            if not covered:
                review.append(
                    Finding(
                        code="EVIDENCE_REVIEW_REQUIRED",
                        message=(
                            "The task window has missing, stale or unsuitable WBGT coverage; "
                            "only fully covered intervals may be assigned."
                        ),
                        task_id=task.id,
                    )
                )

    occupied = defaultdict(list)
    heat_minutes = Counter()
    productive_minutes = {worker.id: 0 for worker in scenario.workers}
    exposure = 0
    slot_seconds = scenario.slot_minutes * 60

    for assignment in schedule.assignments:
        task_id, worker_id = assignment.task_id, assignment.worker_id
        assignments_by_task[task_id].append(assignment)
        start, end = assignment.starts_at, assignment.ends_at
        if worker_id not in workers:
            fail("UNKNOWN_WORKER", "Assignment references an unknown worker.", task_id, worker_id)
        if end <= start:
            fail(
                "ASSIGNMENT_INTERVAL", "Assignment must have positive duration.", task_id, worker_id
            )
            continue
        occupied[worker_id].append((start, end, f"task:{task_id}"))
        within_horizon = scenario.starts_at <= start < end <= scenario.ends_at
        if not within_horizon:
            fail("HORIZON", "Assignment is outside the planning horizon.", task_id, worker_id)
        if (start - scenario.starts_at).total_seconds() % slot_seconds:
            fail(
                "START_GRID",
                "Assignment start is off the supported planning grid.",
                task_id,
                worker_id,
            )
        if worker_id in workers:
            worker = workers[worker_id]
            if not worker.available_from <= start < end <= worker.available_until:
                fail(
                    "AVAILABILITY", "Assignment is outside worker availability.", task_id, worker_id
                )
        if task_id not in tasks:
            continue
        task = tasks[task_id]
        duration = (end - start).total_seconds() / 60
        if duration != task.duration_minutes:
            fail(
                "DURATION",
                "Assignment duration differs from the task duration.",
                task_id,
                worker_id,
            )
        if not task.release_at <= start < end <= task.deadline:
            fail("TASK_WINDOW", "Assignment is outside task release/deadline.", task_id, worker_id)
        if within_horizon and task.outdoors and _restricted_overlap(scenario, start, end):
            fail(
                "MIDDAY_RESTRICTION",
                "Outdoor assignment overlaps a restricted local interval.",
                task_id,
                worker_id,
            )
        if worker_id in workers:
            worker = workers[worker_id]
            productive_minutes[worker_id] += task.duration_minutes
            if task.required_skill not in worker.skills:
                fail("SKILL", "Worker lacks the task's required qualification.", task_id, worker_id)
            if task.heat_exposed:
                heat_minutes[worker_id] += task.duration_minutes
                cap = (
                    scenario.policy.max_task_minutes_acclimatized
                    if worker.acclimatized
                    else scenario.policy.max_task_minutes_unacclimatized
                )
                if task.duration_minutes > cap:
                    fail(
                        "UNINTERRUPTED_HEAT_LIMIT",
                        "Heat task exceeds the worker's modeled uninterrupted allowance.",
                        task_id,
                        worker_id,
                    )
        if within_horizon and task.heat_exposed:
            covered, maximum = _environment(scenario, task.zone_id, start, end)
            if not covered:
                fail(
                    "EVIDENCE_GAP",
                    "Heat task lacks full-interval usable WBGT evidence.",
                    task_id,
                    worker_id,
                )
            else:
                if maximum > scenario.policy.wbgt_limits_c[task.workload]:
                    fail(
                        "WBGT_LIMIT",
                        "Heat task exceeds the illustrative workload WBGT limit.",
                        task_id,
                        worker_id,
                    )
                exposure += ceil(maximum * 10) * task.duration_minutes

    for worker in scenario.workers:
        cap = (
            scenario.policy.max_heat_minutes_acclimatized
            if worker.acclimatized
            else scenario.policy.max_heat_minutes_unacclimatized
        )
        if heat_minutes[worker.id] > cap:
            fail(
                "TOTAL_HEAT_LIMIT",
                "Worker exceeds the modeled planning-horizon heat-work allowance.",
                worker_id=worker.id,
            )

    cooling_events = []
    for recovery in schedule.recoveries:
        task_id, worker_id = recovery.task_id, recovery.worker_id
        recovery_by_task[task_id].append(recovery)
        start, end = recovery.starts_at, recovery.ends_at
        if task_id not in tasks:
            fail(
                "UNKNOWN_RECOVERY_TASK", "Recovery references an unknown task.", task_id, worker_id
            )
        if worker_id not in workers:
            fail(
                "UNKNOWN_RECOVERY_WORKER",
                "Recovery references an unknown worker.",
                task_id,
                worker_id,
            )
        if end <= start:
            fail("RECOVERY_INTERVAL", "Recovery must have positive duration.", task_id, worker_id)
            continue
        occupied[worker_id].append((start, end, f"recovery:{task_id}"))
        cooling_events.extend(((start, 1), (end, -1)))
        if not scenario.starts_at <= start < end <= scenario.ends_at:
            fail(
                "RECOVERY_HORIZON", "Recovery is outside the planning horizon.", task_id, worker_id
            )
        if worker_id in workers:
            worker = workers[worker_id]
            if not worker.available_from <= start < end <= worker.available_until:
                fail(
                    "RECOVERY_AVAILABILITY",
                    "Recovery is outside worker availability.",
                    task_id,
                    worker_id,
                )
        matching = assignments_by_task.get(task_id, [])
        if len(matching) != 1 or task_id not in tasks or not tasks[task_id].heat_exposed:
            fail(
                "EXTRA_RECOVERY",
                "Recovery must belong to exactly one assigned heat task.",
                task_id,
                worker_id,
            )
        elif (
            matching[0].worker_id != worker_id
            or start != matching[0].ends_at
            or end - start != timedelta(minutes=scenario.policy.recovery_minutes)
        ):
            fail(
                "RECOVERY_REQUIREMENT",
                "Recovery must immediately follow its task for the required duration and worker.",
                task_id,
                worker_id,
            )

    for task_id, assigned in assignments_by_task.items():
        if task_id in tasks and tasks[task_id].heat_exposed:
            if len(recovery_by_task[task_id]) != len(assigned) or len(assigned) != 1:
                fail(
                    "RECOVERY_COUNT",
                    "Each assigned heat task needs exactly one protected recovery.",
                    task_id,
                )
    for task_id, recoveries in recovery_by_task.items():
        if len(recoveries) > 1:
            fail("DUPLICATE_RECOVERY", "A task cannot have duplicate recovery records.", task_id)

    for worker_id, intervals in occupied.items():
        intervals.sort()
        # Compare all pairs: malformed nested intervals must not hide an overlap.
        for i, (start, end, label) in enumerate(intervals):
            for other_start, other_end, other_label in intervals[i + 1 :]:
                if other_start >= end:
                    break
                if _overlaps(start, end, other_start, other_end):
                    fail(
                        "WORKER_OVERLAP",
                        f"Worker intervals overlap: {label} and {other_label}.",
                        worker_id=worker_id,
                    )
    occupancy = 0
    for _, change in sorted(cooling_events):
        occupancy += change  # Ends (-1) sort before starts (+1) at a shared boundary.
        if occupancy > scenario.cooling_capacity:
            fail(
                "COOLING_CAPACITY", "Concurrent protected recoveries exceed site cooling capacity."
            )
            break

    loads = list(productive_minutes.values())
    return ValidationReport(
        valid=not violations,
        violations=tuple(violations),
        review_findings=tuple(review),
        completed_tasks=len(schedule.assignments),
        deferred_tasks=len(schedule.deferred_task_ids),
        exposure_proxy=exposure,
        workload_spread_minutes=max(loads) - min(loads),
    )
