"""Adversarial validation examples independent of optimizer output."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from heatshield.models import Assignment, Observation, Recovery, Scenario, Schedule
from heatshield.provenance import content_hash
from heatshield.validation import validate_schedule


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 7, 15, hour, minute, tzinfo=ZoneInfo("Asia/Dubai")).astimezone(UTC)


def scenario(**changes) -> Scenario:
    start, end = at(6), at(18)
    observations = [
        {
            "id": f"o{i}",
            "zone_id": "yard",
            "observed_at": start + timedelta(minutes=15 * i),
            "valid_from": start + timedelta(minutes=15 * i),
            "valid_until": start + timedelta(minutes=15 * (i + 1)),
            "wbgt_c": 26.0,
        }
        for i in range(48)
    ]
    data = {
        "id": "validation-fixture",
        "starts_at": start,
        "ends_at": end,
        "cooling_capacity": 2,
        "workers": [
            {"id": "w1", "skills": ["general"], "available_from": start, "available_until": end},
            {"id": "w2", "skills": ["general"], "available_from": start, "available_until": end},
        ],
        "tasks": [
            {
                "id": "t1",
                "required_skill": "general",
                "zone_id": "yard",
                "duration_minutes": 30,
                "release_at": start,
                "deadline": end,
            }
        ],
        "observations": observations,
    }
    return Scenario.model_validate(data | changes)


def assigned(s: Scenario, start=None, **changes) -> Schedule:
    start = start or at(6)
    assignment = Assignment(
        task_id="t1", worker_id="w1", starts_at=start, ends_at=start + timedelta(minutes=30)
    )
    recovery = Recovery(
        task_id="t1",
        worker_id="w1",
        starts_at=assignment.ends_at,
        ends_at=assignment.ends_at + timedelta(minutes=15),
    )
    return Schedule(
        **(
            {
                "scenario_hash": content_hash(s),
                "policy_hash": content_hash(s.policy),
                "assignments": (assignment,),
                "recoveries": (recovery,),
            }
            | changes
        )
    )


def codes(report):
    return {finding.code for finding in report.violations}


def test_valid_plan_is_still_synthetic_and_review_required():
    s = scenario()
    report = validate_schedule(s, assigned(s))
    assert report.valid
    assert report.review_required
    assert not report.operational_approval_allowed
    assert report.exposure_proxy == 260 * 30
    assert report.workload_spread_minutes == 30
    assert "DEMO_POLICY" in {finding.code for finding in report.review_findings}


@pytest.mark.parametrize("start", [at(12), at(15)])
def test_exact_midday_boundaries_permit_ending_at_ban_or_starting_after_it(start):
    s = scenario()
    assert validate_schedule(s, assigned(s, start)).valid


@pytest.mark.parametrize("start", [at(12, 15), at(12, 1)])
def test_full_interval_check_catches_crossing_by_even_one_minute(start):
    s = scenario()
    assert "MIDDAY_RESTRICTION" in codes(validate_schedule(s, assigned(s, start)))


def test_off_grid_start_does_not_hide_exact_restriction_finding():
    s = scenario()
    findings = codes(validate_schedule(s, assigned(s, at(12, 1))))
    assert {"MIDDAY_RESTRICTION", "START_GRID"} <= findings


def test_midday_rule_is_independent_of_heat_exposed_marker():
    base = scenario()
    s = scenario(tasks=[base.tasks[0].model_dump() | {"heat_exposed": False}])
    assert "MIDDAY_RESTRICTION" in codes(validate_schedule(s, assigned(s, at(13), recoveries=())))


def test_indoor_task_avoids_outdoor_restriction_but_still_needs_heat_evidence():
    base = scenario()
    s = scenario(tasks=[base.tasks[0].model_dump() | {"outdoors": False}])
    assert validate_schedule(s, assigned(s, at(13))).valid


def test_seasonal_boundary_is_checked_in_site_local_date():
    # Honolulu is still June 14 when UTC is already June 15. The ban does not
    # apply on June 14 locally even though a UTC-date implementation would block.
    zone = ZoneInfo("Pacific/Honolulu")
    start = datetime(2026, 6, 14, 12, 30, tzinfo=zone).astimezone(UTC)
    end = start + timedelta(hours=1)
    s = Scenario.model_validate(
        {
            "id": "local-date",
            "timezone": "Pacific/Honolulu",
            "starts_at": start,
            "ends_at": end,
            "workers": [
                {"id": "w1", "skills": ["general"], "available_from": start, "available_until": end}
            ],
            "tasks": [
                {
                    "id": "t1",
                    "required_skill": "general",
                    "zone_id": "yard",
                    "duration_minutes": 30,
                    "release_at": start,
                    "deadline": end,
                    "heat_exposed": False,
                }
            ],
        }
    )
    assert validate_schedule(s, assigned(s, start, recoveries=())).valid


@pytest.mark.parametrize("field", ["scenario_hash", "policy_hash"])
def test_tampered_binding_is_rejected(field):
    s = scenario()
    report = validate_schedule(s, assigned(s, **{field: "forged"}))
    assert f"{field.upper()}" in codes(report)


def test_omitted_duplicated_and_assigned_plus_deferred_tasks_fail_partition():
    s = scenario()
    original = assigned(s)
    for assignments, deferred in [
        ((), ()),
        (original.assignments * 2, ()),
        (original.assignments, ("t1",)),
    ]:
        report = validate_schedule(
            s, assigned(s, assignments=assignments, deferred_task_ids=deferred)
        )
        assert "TASK_PARTITION" in codes(report)


def test_unknown_references_are_rejected_without_crashing():
    s = scenario()
    original = assigned(s)
    assignment = original.assignments[0].model_copy(
        update={"task_id": "phantom", "worker_id": "ghost"}
    )
    recovery = original.recoveries[0].model_copy(
        update={"task_id": "phantom", "worker_id": "ghost"}
    )
    report = validate_schedule(s, assigned(s, assignments=(assignment,), recoveries=(recovery,)))
    assert {
        "UNKNOWN_TASK",
        "UNKNOWN_WORKER",
        "UNKNOWN_RECOVERY_TASK",
        "UNKNOWN_RECOVERY_WORKER",
    } <= codes(report)


def test_deferral_is_explicit_and_mandatory_tasks_cannot_be_deferred():
    s = scenario()
    deferred = assigned(s, assignments=(), recoveries=(), deferred_task_ids=("t1",))
    assert validate_schedule(s, deferred).valid
    mandatory = scenario(tasks=[s.tasks[0].model_dump() | {"deferrable": False}])
    deferred = deferred.model_copy(update={"scenario_hash": content_hash(mandatory)})
    assert "MANDATORY_DEFERRED" in codes(validate_schedule(mandatory, deferred))


def test_unqualified_worker_is_rejected():
    original = scenario()
    s = scenario(workers=[original.workers[0].model_dump() | {"skills": ["electrical"]}])
    assert "SKILL" in codes(validate_schedule(s, assigned(s)))


def test_missing_recovery_and_forged_recovery_are_rejected():
    s = scenario()
    original = assigned(s)
    assert "RECOVERY_COUNT" in codes(validate_schedule(s, assigned(s, recoveries=())))
    wrong = original.recoveries[0].model_copy(update={"starts_at": at(6, 45), "ends_at": at(7)})
    assert "RECOVERY_REQUIREMENT" in codes(validate_schedule(s, assigned(s, recoveries=(wrong,))))


def test_reversed_assignment_and_recovery_return_findings():
    s = scenario()
    original = assigned(s)
    assignment = original.assignments[0].model_copy(update={"ends_at": at(5, 45)})
    recovery = original.recoveries[0].model_copy(update={"ends_at": at(6)})
    report = validate_schedule(s, assigned(s, assignments=(assignment,), recoveries=(recovery,)))
    assert {"ASSIGNMENT_INTERVAL", "RECOVERY_INTERVAL"} <= codes(report)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda obs: (),
        lambda obs: tuple(o for o in obs if o.id != "o1"),
        lambda obs: tuple(o.model_copy(update={"source": "forecast"}) for o in obs),
        lambda obs: tuple(o.model_copy(update={"quality": "suspect"}) for o in obs),
        lambda obs: tuple(
            o.model_copy(update={"source": "sensor", "calibration_reference": " "}) for o in obs
        ),
        lambda obs: tuple(
            o.model_copy(update={"observed_at": o.observed_at - timedelta(hours=2)}) for o in obs
        ),
    ],
)
def test_unusable_evidence_blocks_assignments_and_remains_visible_on_deferral(mutate):
    base = scenario()
    s = scenario(observations=mutate(base.observations))
    assert "EVIDENCE_GAP" in codes(validate_schedule(s, assigned(s)))
    deferred = validate_schedule(
        s, assigned(s, assignments=(), recoveries=(), deferred_task_ids=("t1",))
    )
    assert deferred.valid
    assert "EVIDENCE_REVIEW_REQUIRED" in {finding.code for finding in deferred.review_findings}


def test_future_sample_cannot_cover_an_earlier_task():
    observation = Observation(
        id="future",
        zone_id="yard",
        observed_at=at(6, 30),
        valid_from=at(6, 30),
        valid_until=at(7),
        wbgt_c=26,
    )
    s = scenario(observations=(observation,))
    assert "EVIDENCE_GAP" in codes(validate_schedule(s, assigned(s)))


def test_freshness_expiring_midway_cannot_cover_the_full_interval():
    observation = Observation(
        id="expiring",
        zone_id="yard",
        observed_at=at(5, 15),
        valid_from=at(6),
        valid_until=at(7),
        wbgt_c=26,
    )
    s = scenario(observations=(observation,))
    assert "EVIDENCE_GAP" in codes(validate_schedule(s, assigned(s)))


def test_most_restrictive_overlapping_reading_cannot_be_ignored():
    base = scenario()
    hot = Observation(
        id="hot",
        zone_id="yard",
        observed_at=at(6),
        valid_from=at(6),
        valid_until=at(6, 30),
        wbgt_c=35,
    )
    s = scenario(observations=(*base.observations, hot))
    assert "WBGT_LIMIT" in codes(validate_schedule(s, assigned(s)))


def test_evidence_must_match_task_zone():
    base = scenario()
    s = scenario(
        observations=tuple(o.model_copy(update={"zone_id": "other"}) for o in base.observations)
    )
    assert "EVIDENCE_GAP" in codes(validate_schedule(s, assigned(s)))


def test_unacclimatized_worker_and_total_heat_allowances_are_separate():
    base = scenario()
    workers = [base.workers[0].model_dump() | {"acclimatized": False}]
    s = scenario(
        workers=workers,
        policy=base.policy.model_dump()
        | {"max_task_minutes_unacclimatized": 15, "max_heat_minutes_unacclimatized": 15},
    )
    findings = codes(validate_schedule(s, assigned(s)))
    assert {"UNINTERRUPTED_HEAT_LIMIT", "TOTAL_HEAT_LIMIT"} <= findings


def test_worker_cannot_work_during_another_tasks_protected_recovery():
    base = scenario()
    t2 = base.tasks[0].model_copy(update={"id": "t2", "heat_exposed": False})
    s = scenario(tasks=(*base.tasks, t2))
    original = assigned(s)
    overlap = Assignment(task_id="t2", worker_id="w1", starts_at=at(6, 30), ends_at=at(7))
    assert "WORKER_OVERLAP" in codes(
        validate_schedule(s, assigned(s, assignments=(*original.assignments, overlap)))
    )


def test_site_cooling_capacity_counts_different_workers():
    base = scenario()
    t2 = base.tasks[0].model_copy(update={"id": "t2"})
    s = scenario(tasks=(*base.tasks, t2), cooling_capacity=1)
    original = assigned(s)
    assignment = original.assignments[0].model_copy(update={"task_id": "t2", "worker_id": "w2"})
    recovery = original.recoveries[0].model_copy(update={"task_id": "t2", "worker_id": "w2"})
    report = validate_schedule(
        s,
        assigned(
            s,
            assignments=(*original.assignments, assignment),
            recoveries=(*original.recoveries, recovery),
        ),
    )
    assert "COOLING_CAPACITY" in codes(report)
    assert "WORKER_OVERLAP" not in codes(report)


def test_back_to_back_recoveries_use_half_open_resource_occupancy():
    base = scenario()
    t2 = base.tasks[0].model_copy(update={"id": "t2", "duration_minutes": 15})
    s = scenario(tasks=(*base.tasks, t2), cooling_capacity=1)
    original = assigned(s)
    assignment = Assignment(task_id="t2", worker_id="w2", starts_at=at(6, 30), ends_at=at(6, 45))
    recovery = Recovery(task_id="t2", worker_id="w2", starts_at=at(6, 45), ends_at=at(7))
    report = validate_schedule(
        s,
        assigned(
            s,
            assignments=(*original.assignments, assignment),
            recoveries=(*original.recoveries, recovery),
        ),
    )
    assert report.valid


def test_final_task_recovery_must_fit_worker_availability():
    base = scenario()
    s = scenario(workers=[base.workers[0].model_dump() | {"available_until": at(6, 30)}])
    assert "RECOVERY_AVAILABILITY" in codes(validate_schedule(s, assigned(s)))


def test_expired_policy_cannot_validate_even_a_deferred_plan():
    base = scenario()
    policy = base.policy.model_dump() | {
        "effective_from": "2025-01-01",
        "effective_until": "2025-12-31",
        "restricted_season_start": "2025-06-15",
        "restricted_season_end": "2025-09-15",
    }
    s = scenario(policy=policy)
    report = validate_schedule(
        s, assigned(s, assignments=(), recoveries=(), deferred_task_ids=("t1",))
    )
    assert "POLICY_VALIDITY" in codes(report)


def test_outside_horizon_assignment_skips_unbounded_temporal_checks(monkeypatch):
    import heatshield.validation as validation

    s = scenario()
    original = assigned(s)
    assignment = original.assignments[0].model_copy(
        update={
            "starts_at": datetime(2000, 1, 1, tzinfo=UTC),
            "ends_at": datetime(2100, 1, 1, tzinfo=UTC),
        }
    )
    environment = validation._environment

    def bounded_environment(scenario, zone_id, start, end):
        assert scenario.starts_at <= start < end <= scenario.ends_at
        return environment(scenario, zone_id, start, end)

    def no_restriction_scan(*args):
        pytest.fail("Out-of-horizon assignments must not scan arbitrary calendar spans")

    monkeypatch.setattr(validation, "_environment", bounded_environment)
    monkeypatch.setattr(validation, "_restricted_overlap", no_restriction_scan)
    report = validate_schedule(s, assigned(s, assignments=(assignment,)))
    assert not report.valid
    assert {"HORIZON", "DURATION", "TASK_WINDOW"} <= codes(report)


def test_unrelated_extreme_observation_is_ignored_before_freshness_arithmetic():
    base = scenario()
    # Simulate an internal caller bypassing transport validation. The unrelated
    # interval cannot qualify a 2026 task and must be filtered before expiry math.
    extreme = Observation.model_construct(
        id="extreme",
        zone_id="yard",
        observed_at=datetime(9999, 12, 31, 23, 50, tzinfo=UTC),
        valid_from=datetime(9999, 12, 31, 23, 50, tzinfo=UTC),
        valid_until=datetime(9999, 12, 31, 23, 59, tzinfo=UTC),
        wbgt_c=26.0,
    )
    s = base.model_copy(update={"observations": (*base.observations, extreme)})
    assert validate_schedule(s, assigned(s)).valid


def test_overlapping_extreme_observation_caps_freshness_without_overflow():
    from heatshield.validation import _environment

    base = scenario()
    start = datetime(9999, 12, 31, 23, 50, tzinfo=UTC)
    end = datetime(9999, 12, 31, 23, 59, tzinfo=UTC)
    extreme = Observation.model_construct(
        id="extreme",
        zone_id="yard",
        observed_at=start,
        valid_from=start,
        valid_until=end,
        wbgt_c=26.0,
    )
    s = base.model_copy(update={"observations": (extreme,)})
    assert _environment(s, "yard", start, end) == (True, 26.0)
