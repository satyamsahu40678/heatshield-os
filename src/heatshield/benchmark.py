"""Reproducible comparisons, including an explicitly unsafe reference roster."""

from statistics import median, quantiles
from time import perf_counter

from .fixtures import demo_scenario, fixed_roster
from .models import Schedule
from .optimization import greedy_schedule, optimize
from .provenance import content_hash
from .validation import validate_schedule


def _measure(scenario, schedule: Schedule | None, elapsed: float, status: str) -> dict:
    report = validate_schedule(scenario, schedule) if schedule is not None else None
    return {
        "status": status,
        "elapsed_seconds": elapsed,
        "validation": report.model_dump(mode="json") if report else None,
    }


def run_benchmark(count: int = 3, seed: int = 7) -> dict:
    if not 1 <= count <= 100:
        raise ValueError("benchmark count must be between 1 and 100")
    if seed < 0 or seed + count - 1 > 2_147_483_647:
        raise ValueError("benchmark seeds must fit the scenario seed range")
    rows = []
    for index in range(count):
        scenario = demo_scenario(seed + index)
        started = perf_counter()
        fixed = fixed_roster(scenario)
        b0 = _measure(scenario, fixed, perf_counter() - started, "UNSAFE_REFERENCE")
        started = perf_counter()
        greedy = greedy_schedule(scenario)
        b1 = _measure(
            scenario, greedy, perf_counter() - started, "FEASIBLE" if greedy else "NO_PLAN_FOUND"
        )
        started = perf_counter()
        result = optimize(scenario)
        b2 = _measure(scenario, result.schedule, perf_counter() - started, result.status)
        b2["solver_metadata"] = result.metadata.model_dump(mode="json")
        b2["diagnostics"] = list(result.diagnostics)
        rows.append(
            {
                "scenario_id": scenario.id,
                "scenario_hash": content_hash(scenario),
                "policy_hash": content_hash(scenario.policy),
                "b0_fixed": b0,
                "b1_greedy": b1,
                "b2_cp_sat": b2,
            }
        )
    times = [row["b2_cp_sat"]["elapsed_seconds"] for row in rows]
    return {
        "mode": "synthetic",
        "operational_approval_allowed": False,
        "note": (
            "B0 is an unsafe reference, never a fallback. Metrics apply only to encoded demo "
            "constraints. Exposure and workload spread are software-test proxies; they "
            "do not measure injuries, physiology, wages, or validated fairness. "
            "Scenarios vary fabricated heat conditions and cooling capacity."
        ),
        "count": count,
        "seed": seed,
        "cp_sat_runtime_median_seconds": median(times),
        "cp_sat_runtime_p95_seconds": (
            quantiles(times, n=100, method="inclusive")[94] if len(times) >= 20 else None
        ),
        "p95_note": "Reported only for at least 20 samples; no statistical superiority claim.",
        "scenarios": rows,
    }
