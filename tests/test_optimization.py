"""Small independently understood scheduling cases and solver termination contracts."""

from datetime import UTC, datetime, timedelta

import pytest
from ortools.sat.python import cp_model

from heatshield.models import Observation, Scenario, Task, Worker
from heatshield.optimization import greedy_schedule, optimize


def scenario(*, workers=2, task_count=2, heat=False, cooling=1):
    start = datetime(2026, 7, 15, 2, tzinfo=UTC)  # 06:00 in Abu Dhabi.
    end = start + timedelta(hours=2)
    return Scenario(
        id="test",
        starts_at=start,
        ends_at=end,
        cooling_capacity=cooling,
        workers=tuple(
            Worker(id=f"w{i}", skills=("fit",), available_from=start, available_until=end)
            for i in range(workers)
        ),
        tasks=tuple(
            Task(
                id=f"t{i}",
                required_skill="fit",
                zone_id="z",
                duration_minutes=30,
                release_at=start,
                deadline=end,
                heat_exposed=heat,
                deferrable=False,
            )
            for i in range(task_count)
        ),
        observations=tuple(
            Observation(
                id=f"o{i}",
                zone_id="z",
                observed_at=start + timedelta(hours=i),
                valid_from=start + timedelta(hours=i),
                valid_until=start + timedelta(hours=i + 1),
                wbgt_c=26,
            )
            for i in range(2)
        ),
    )


def test_balanced_complete_plan_and_metadata():
    result = optimize(scenario())
    assert result.status == "OPTIMAL"
    assert result.schedule is not None
    assert len(result.schedule.assignments) == 2
    assert len({a.worker_id for a in result.schedule.assignments}) == 2
    assert result.validation.valid
    assert result.validation.workload_spread_minutes == 0
    assert result.validation.operational_approval_allowed is False
    assert len(result.metadata.stages) == 3
    assert all(stage.status == "OPTIMAL" for stage in result.metadata.stages)
    assert result.metadata.seed == 7
    assert result.metadata.solver_version


def test_impossible_mandatory_task_is_infeasible_not_deferred():
    original = scenario(task_count=1)
    impossible = original.tasks[0].model_copy(update={"required_skill": "electrician"})
    result = optimize(original.model_copy(update={"tasks": (impossible,)}))
    assert result.status == "INFEASIBLE"
    assert result.schedule is None
    assert "Mandatory task t0" in result.diagnostics[0]


def test_optional_unqualified_task_is_explicitly_deferred():
    original = scenario(task_count=1)
    optional = original.tasks[0].model_copy(
        update={"required_skill": "electrician", "deferrable": True}
    )
    result = optimize(original.model_copy(update={"tasks": (optional,)}))
    assert result.status == "OPTIMAL"
    assert result.schedule.assignments == ()
    assert result.schedule.deferred_task_ids == ("t0",)
    assert result.validation.valid


def test_cooling_capacity_is_reserved_for_explicit_recovery():
    result = optimize(scenario(heat=True))
    assert result.status == "OPTIMAL"
    assert result.validation.valid
    rests = sorted(result.schedule.recoveries, key=lambda item: item.starts_at)
    assert len(rests) == 2
    assert rests[0].ends_at <= rests[1].starts_at
    for recovery in rests:
        assignment = next(a for a in result.schedule.assignments if a.task_id == recovery.task_id)
        assert recovery.starts_at == assignment.ends_at
        assert recovery.ends_at - recovery.starts_at == timedelta(minutes=15)


def test_zero_cooling_capacity_prevents_heat_task():
    result = optimize(scenario(task_count=1, heat=True, cooling=0))
    assert result.status == "INFEASIBLE"


def test_exposure_objective_prefers_later_cooler_evidence():
    original = scenario(workers=1, task_count=1, heat=True)
    observations = tuple(
        o.model_copy(update={"wbgt_c": 29 if i == 0 else 25})
        for i, o in enumerate(original.observations)
    )
    result = optimize(original.model_copy(update={"observations": observations}))
    assert result.validation.valid
    assert result.schedule.assignments[0].starts_at >= original.starts_at + timedelta(hours=1)
    assert result.validation.exposure_proxy == 250 * 30


def test_greedy_result_passes_independent_validation():
    from heatshield.validation import validate_schedule

    original = scenario(heat=True)
    result = greedy_schedule(original)
    assert result is not None
    assert validate_schedule(original, result).valid


def test_unknown_status_never_becomes_infeasible(monkeypatch):
    monkeypatch.setattr(cp_model.CpSolver, "solve", lambda self, model: cp_model.UNKNOWN)
    result = optimize(scenario())
    assert result.status == "UNKNOWN"
    assert result.schedule is None
    assert result.metadata.stages[0].status == "UNKNOWN"


def test_feasible_stage_stops_without_claiming_lexicographic_optimum(monkeypatch):
    real_solve = cp_model.CpSolver.solve

    def feasible(solver, model):
        assert real_solve(solver, model) == cp_model.OPTIMAL
        return cp_model.FEASIBLE

    monkeypatch.setattr(cp_model.CpSolver, "solve", feasible)
    result = optimize(scenario())
    assert result.status == "FEASIBLE"
    assert result.validation.valid
    assert len(result.metadata.stages) == 1


def test_later_unknown_retains_incumbent_with_feasible_status(monkeypatch):
    real_solve = cp_model.CpSolver.solve
    calls = 0

    def timeout_on_second_stage(solver, model):
        nonlocal calls
        calls += 1
        return real_solve(solver, model) if calls == 1 else cp_model.UNKNOWN

    monkeypatch.setattr(cp_model.CpSolver, "solve", timeout_on_second_stage)
    result = optimize(scenario())
    assert result.status == "FEASIBLE"
    assert result.validation.valid
    assert [stage.status for stage in result.metadata.stages] == ["OPTIMAL", "UNKNOWN"]


@pytest.mark.parametrize("acclimatized,expected", [(True, "OPTIMAL"), (False, "INFEASIBLE")])
def test_unacclimatized_uninterrupted_allowance(acclimatized, expected):
    original = scenario(workers=1, task_count=1, heat=True)
    task = original.tasks[0].model_copy(update={"duration_minutes": 45})
    worker = original.workers[0].model_copy(update={"acclimatized": acclimatized})
    result = optimize(original.model_copy(update={"tasks": (task,), "workers": (worker,)}))
    assert result.status == expected


def test_unknown_readings_block_mandatory_heat_task():
    original = scenario(workers=1, task_count=1, heat=True)
    result = optimize(original.model_copy(update={"observations": ()}))
    assert result.status == "INFEASIBLE"


def test_observation_union_covers_task_and_uses_conservative_peak():
    original = scenario(workers=1, task_count=1, heat=True)
    task = original.tasks[0].model_copy(
        update={"deadline": original.starts_at + timedelta(minutes=30)}
    )
    readings = tuple(
        Observation(
            id=f"segment{i}",
            zone_id="z",
            observed_at=original.starts_at + timedelta(minutes=i * 15),
            valid_from=original.starts_at + timedelta(minutes=i * 15),
            valid_until=original.starts_at + timedelta(minutes=(i + 1) * 15),
            wbgt_c=25 + i,
        )
        for i in range(2)
    )
    result = optimize(original.model_copy(update={"tasks": (task,), "observations": readings}))
    assert result.status == "OPTIMAL"
    assert result.validation.valid
    assert result.validation.exposure_proxy == 260 * 30


def test_model_size_guard_rejects_without_partial_schedule(monkeypatch):
    from heatshield import policy

    monkeypatch.setattr(policy, "MAX_CANDIDATES", 1)
    result = optimize(scenario())
    assert result.status == "MODEL_INVALID"
    assert result.schedule is None
    assert "reduce scenario size" in result.diagnostics[0]


def test_independent_validator_can_reject_solver_proposal(monkeypatch):
    from heatshield import validation
    from heatshield.models import Finding, ValidationReport

    monkeypatch.setattr(
        validation,
        "validate_schedule",
        lambda scenario, schedule: ValidationReport(
            valid=False,
            violations=(Finding(code="TAMPERED", message="An independent check failed."),),
        ),
    )
    result = optimize(scenario())
    assert result.status == "MODEL_INVALID"
    assert result.schedule is None
    assert result.validation.valid is False
    assert "TAMPERED" in result.diagnostics


def test_uncovered_horizon_rejected_before_generating_deferred_plan():
    original = scenario(workers=1, task_count=1)
    shift = timedelta(days=365)
    worker = original.workers[0].model_copy(
        update={
            "available_from": original.workers[0].available_from + shift,
            "available_until": original.workers[0].available_until + shift,
        }
    )
    task = original.tasks[0].model_copy(
        update={
            "release_at": original.tasks[0].release_at + shift,
            "deadline": original.tasks[0].deadline + shift,
            "deferrable": True,
        }
    )
    outside = Scenario.model_validate(
        original.model_copy(
            update={
                "starts_at": original.starts_at + shift,
                "ends_at": original.ends_at + shift,
                "workers": (worker,),
                "tasks": (task,),
                "observations": (),
            }
        ).model_dump()
    )
    result = optimize(outside)
    assert result.status == "MODEL_INVALID"
    assert result.schedule is None
    assert result.metadata.candidate_count == 0
    assert "complete planning horizon" in result.diagnostics[0]
    assert greedy_schedule(outside) is None


def test_greedy_output_passes_independent_validation_gate(monkeypatch):
    from heatshield import validation
    from heatshield.models import ValidationReport

    monkeypatch.setattr(
        validation, "validate_schedule", lambda *args: ValidationReport(valid=False)
    )
    assert greedy_schedule(scenario()) is None


def test_irrelevant_future_observation_is_skipped_before_expiry_arithmetic():
    from heatshield.policy import evidence_exposure

    original = scenario(workers=1, task_count=1, heat=True)
    # Direct policy-call regression: bypass public timestamp bounds to exercise
    # the defensive interval filter before an overflowing freshness addition.
    remote = original.observations[0].model_copy(
        update={
            "observed_at": datetime(9999, 12, 31, 23, 59, tzinfo=UTC),
            "valid_from": datetime(9999, 12, 31, 23, 59, tzinfo=UTC),
            "valid_until": datetime.max.replace(tzinfo=UTC),
        }
    )
    altered = original.model_copy(update={"observations": (*original.observations, remote)})
    assert (
        evidence_exposure(
            altered, altered.tasks[0], altered.starts_at, altered.starts_at + timedelta(minutes=30)
        )
        == 260 * 30
    )
