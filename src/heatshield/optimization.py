"""CP-SAT and greedy proposals with independent validation at the output boundary."""

from collections import defaultdict
from datetime import timedelta
from time import monotonic

import ortools
from ortools.sat.python import cp_model

from .models import (
    Assignment,
    ObjectiveStage,
    OptimizationResult,
    Recovery,
    Scenario,
    Schedule,
    SolverMetadata,
)
from .policy import Candidate, CandidateBudgetExceeded, candidate_options, heat_limit, policy_covers
from .provenance import content_hash


def _schedule(scenario: Scenario, selected: list[Candidate]) -> Schedule:
    chosen = {candidate.task_id for candidate in selected}
    ordered = sorted(selected, key=lambda item: (item.start, item.task_id, item.worker_id))

    def instant(slot: int):
        return scenario.starts_at + timedelta(minutes=slot * 15)

    return Schedule(
        scenario_hash=content_hash(scenario),
        policy_hash=content_hash(scenario.policy),
        assignments=tuple(
            Assignment(
                task_id=item.task_id,
                worker_id=item.worker_id,
                starts_at=instant(item.start),
                ends_at=instant(item.work_end),
            )
            for item in ordered
        ),
        recoveries=tuple(
            Recovery(
                task_id=item.task_id,
                worker_id=item.worker_id,
                starts_at=instant(item.work_end),
                ends_at=instant(item.end),
            )
            for item in ordered
            if item.end > item.work_end
        ),
        deferred_task_ids=tuple(
            sorted(task.id for task in scenario.tasks if task.id not in chosen)
        ),
    )


def greedy_schedule(scenario: Scenario) -> Schedule | None:
    """Earliest feasible atomic assignments; None means this heuristic failed.

    A greedy failure is never a proof of infeasibility. Mandatory tasks are
    attempted first; optional tasks are explicitly deferred when they cannot fit.
    """
    from .validation import validate_schedule

    if not policy_covers(scenario, scenario.starts_at, scenario.ends_at):
        return None
    try:
        options = candidate_options(scenario)
    except CandidateBudgetExceeded:
        return None
    selected: list[Candidate] = []
    occupied: dict[str, set[int]] = defaultdict(set)
    cooling: dict[int, int] = defaultdict(int)
    heat_minutes: dict[str, int] = defaultdict(int)
    workers = {worker.id: worker for worker in scenario.workers}
    for task in sorted(scenario.tasks, key=lambda item: (item.deferrable, item.deadline, item.id)):
        admitted = None
        for option in options[task.id]:
            if occupied[option.worker_id].intersection(range(option.start, option.end)):
                continue
            if any(
                cooling[slot] >= scenario.cooling_capacity
                for slot in range(option.work_end, option.end)
            ):
                continue
            if task.heat_exposed and (
                heat_minutes[option.worker_id] + task.duration_minutes
                > heat_limit(scenario, workers[option.worker_id])
            ):
                continue
            admitted = option
            break
        if admitted is None:
            if not task.deferrable:
                return None
            continue
        selected.append(admitted)
        occupied[admitted.worker_id].update(range(admitted.start, admitted.end))
        for slot in range(admitted.work_end, admitted.end):
            cooling[slot] += 1
        if task.heat_exposed:
            heat_minutes[admitted.worker_id] += task.duration_minutes
    schedule = _schedule(scenario, selected)
    return schedule if validate_schedule(scenario, schedule).valid else None


def optimize(scenario: Scenario) -> OptimizationResult:
    """Solve staged objectives and validate an incumbent independently before returning it."""
    from .validation import validate_schedule

    began = monotonic()
    stages: list[ObjectiveStage] = []
    diagnostics: list[str] = []
    count = 0

    def metadata() -> SolverMetadata:
        return SolverMetadata(
            scenario_hash=content_hash(scenario),
            policy_hash=content_hash(scenario.policy),
            schema_version=scenario.schema_version,
            solver_version=ortools.__version__,
            seed=scenario.seed,
            elapsed_seconds=round(monotonic() - began, 6),
            time_limit_seconds=scenario.solver_time_limit_seconds,
            candidate_count=count,
            stages=tuple(stages),
        )

    if not policy_covers(scenario, scenario.starts_at, scenario.ends_at):
        return OptimizationResult(
            status="MODEL_INVALID",
            metadata=metadata(),
            diagnostics=("Policy validity does not cover the complete planning horizon.",),
        )
    try:
        options = candidate_options(scenario)
    except CandidateBudgetExceeded as error:
        return OptimizationResult(
            status="MODEL_INVALID", metadata=metadata(), diagnostics=(str(error),)
        )
    count = sum(len(items) for items in options.values())
    model = cp_model.CpModel()
    candidates: list[tuple[Candidate, cp_model.IntVar]] = []
    worker_intervals: dict[str, list] = defaultdict(list)
    cooling_intervals = []
    task_values: dict[str, list] = defaultdict(list)
    worker_loads: dict[str, list] = defaultdict(list)
    worker_heat: dict[str, list] = defaultdict(list)
    deferred = []
    for task in scenario.tasks:
        for index, option in enumerate(options[task.id]):
            present = model.new_bool_var(f"x_{task.id}_{index}")
            candidates.append((option, present))
            task_values[task.id].append(present)
            worker_loads[option.worker_id].append(task.duration_minutes * present)
            interval = model.new_optional_fixed_size_interval_var(
                option.start, option.end - option.start, present, f"occupied_{task.id}_{index}"
            )
            worker_intervals[option.worker_id].append(interval)
            if task.heat_exposed:
                worker_heat[option.worker_id].append(task.duration_minutes * present)
                cooling_intervals.append(
                    model.new_optional_fixed_size_interval_var(
                        option.work_end,
                        option.end - option.work_end,
                        present,
                        f"recovery_{task.id}_{index}",
                    )
                )
        if task.deferrable:
            defer = model.new_bool_var(f"defer_{task.id}")
            deferred.append(defer)
            model.add(sum(task_values[task.id]) + defer == 1)
        else:
            model.add(sum(task_values[task.id]) == 1)
            if not task_values[task.id]:
                diagnostics.append(
                    f"Mandatory task {task.id} has no admitted worker/start under the current "
                    "policy, task window and environmental evidence."
                )
    for worker in scenario.workers:
        model.add_no_overlap(worker_intervals[worker.id])
        model.add(sum(worker_heat[worker.id]) <= heat_limit(scenario, worker))
    if cooling_intervals:
        model.add_cumulative(
            cooling_intervals, [1] * len(cooling_intervals), scenario.cooling_capacity
        )

    maximum_work = sum(task.duration_minutes for task in scenario.tasks)
    loads = []
    for worker in scenario.workers:
        load = model.new_int_var(0, maximum_work, f"load_{worker.id}")
        model.add(load == sum(worker_loads[worker.id]))
        loads.append(load)
    high = model.new_int_var(0, maximum_work, "max_load")
    low = model.new_int_var(0, maximum_work, "min_load")
    model.add_max_equality(high, loads)
    model.add_min_equality(low, loads)
    objectives = (
        ("deferred_tasks", sum(deferred)),
        ("exposure_proxy", sum(option.exposure * present for option, present in candidates)),
        ("workload_spread_minutes", high - low),
    )
    selected: list[Candidate] | None = None
    result_status = "UNKNOWN"
    # Model construction has a separate size guard; this budget bounds CP solving.
    deadline = monotonic() + scenario.solver_time_limit_seconds
    for name, expression in objectives:
        remaining = deadline - monotonic()
        if remaining <= 0:
            diagnostics.append("Shared solver budget expired before all objectives were proven.")
            if selected is not None:
                result_status = "FEASIBLE"
            break
        model.minimize(expression)
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = remaining
        solver.parameters.num_search_workers = 1
        solver.parameters.random_seed = scenario.seed
        status = solver.solve(model)
        label = solver.status_name(status)
        has_solution = status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
        stages.append(
            ObjectiveStage(
                name=name,
                status=label,
                value=round(solver.objective_value) if has_solution else None,
                best_bound=solver.best_objective_bound if has_solution else None,
            )
        )
        if has_solution:
            selected = [option for option, present in candidates if solver.boolean_value(present)]
            result_status = label
            if status == cp_model.FEASIBLE:
                diagnostics.append("Feasible incumbent; lexicographic optimality is not proven.")
                break
            model.add(expression == round(solver.objective_value))
        else:
            if selected is not None and status == cp_model.UNKNOWN:
                result_status = "FEASIBLE"
                diagnostics.append(
                    "Later objective timed out; retained earlier feasible incumbent."
                )
            else:
                result_status = label
            break
    if selected is None or result_status not in ("OPTIMAL", "FEASIBLE"):
        return OptimizationResult(
            status=result_status, metadata=metadata(), diagnostics=tuple(diagnostics)
        )
    schedule = _schedule(scenario, selected)
    validation = validate_schedule(scenario, schedule)
    if not validation.valid:
        return OptimizationResult(
            status="MODEL_INVALID",
            validation=validation,
            metadata=metadata(),
            diagnostics=(
                "Independent validation rejected the solver proposal.",
                *(finding.code for finding in validation.violations),
            ),
        )
    return OptimizationResult(
        status=result_status,
        schedule=schedule,
        validation=validation,
        metadata=metadata(),
        diagnostics=tuple(diagnostics),
    )
