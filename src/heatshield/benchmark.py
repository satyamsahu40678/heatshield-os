"""Paired computed comparisons and adversarial synthetic scenario families."""

import random
from collections import Counter
from datetime import timedelta
from statistics import mean, median, quantiles
from time import perf_counter

from .fixtures import demo_scenario, fixed_roster
from .models import Scenario, Schedule
from .optimization import greedy_schedule, optimize
from .provenance import content_hash
from .validation import validate_schedule

FAMILIES = (
    "nominal",
    "hotter",
    "no_cooling",
    "sensor_outage",
    "skill_shortage",
    "short_availability",
    "new_workers",
    "tight_deadlines",
    "forecast_only",
    "conflicting_readings",
)


def benchmark_scenario(seed: int, index: int, suite: str) -> tuple[Scenario, str]:
    scenario = demo_scenario(seed)
    if suite == "nominal":
        return scenario, "nominal"
    family = FAMILIES[index % len(FAMILIES)]
    body = scenario.model_dump(mode="json")
    body["id"] = f"synthetic-{family}-{seed}"
    if family == "hotter":
        for observation in body["observations"]:
            observation["wbgt_c"] += 1.0 + (seed % 5) * 0.25
    elif family == "no_cooling":
        body["cooling_capacity"] = 0
    elif family == "sensor_outage":
        body["observations"] = [
            item for i, item in enumerate(body["observations"]) if i not in {1, 2, 10, 11}
        ]
    elif family == "skill_shortage":
        for worker in body["workers"]:
            if "masonry" in worker["skills"]:
                worker["skills"] = ["general"]
    elif family == "short_availability":
        for i, worker in enumerate(body["workers"]):
            worker["available_until"] = (
                scenario.starts_at + timedelta(minutes=90 + ((seed + i) % 3) * 15)
            ).isoformat()
    elif family == "new_workers":
        for worker in body["workers"]:
            worker["acclimatized"] = False
    elif family == "tight_deadlines":
        for task in body["tasks"]:
            task["deadline"] = (scenario.starts_at + timedelta(hours=2)).isoformat()
    elif family == "forecast_only":
        for observation in body["observations"]:
            observation["source"] = "forecast"
    elif family == "conflicting_readings":
        conflicting = dict(body["observations"][0])
        conflicting.update(id="conflicting-hot-reading", wbgt_c=35.0)
        body["observations"].append(conflicting)
    return Scenario.model_validate(body), family


def _measure(scenario, schedule: Schedule | None, elapsed: float, status: str) -> dict:
    report = validate_schedule(scenario, schedule) if schedule is not None else None
    return {
        "status": status,
        "elapsed_seconds": elapsed,
        "validation": report.model_dump(mode="json") if report else None,
    }


def _paired_interval(differences: list[int], seed: int) -> dict:
    if not differences:
        return {"count": 0, "mean_cp_minus_greedy": None, "bootstrap_95_percent_interval": None}
    interval = None
    if len(differences) >= 5:
        randomizer = random.Random(seed)
        estimates = sorted(
            mean(randomizer.choices(differences, k=len(differences))) for _ in range(1000)
        )
        interval = [estimates[24], estimates[974]]
    return {
        "count": len(differences),
        "mean_cp_minus_greedy": mean(differences),
        "bootstrap_95_percent_interval": interval,
        "note": (
            "Paired software exposure-proxy differences only; lower is preferred. No health effect."
        ),
    }


def run_benchmark(count: int = 3, seed: int = 7, *, suite: str = "nominal") -> dict:
    if not 1 <= count <= 100:
        raise ValueError("benchmark count must be between 1 and 100")
    if seed < 0 or seed + count - 1 > 2_147_483_647:
        raise ValueError("benchmark seeds must fit the scenario seed range")
    if suite not in {"nominal", "robustness"}:
        raise ValueError("suite must be nominal or robustness")
    rows = []
    for index in range(count):
        scenario, family = benchmark_scenario(seed + index, index, suite)
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
                "family": family,
                "scenario_hash": content_hash(scenario),
                "policy_hash": content_hash(scenario.policy),
                "b0_fixed": b0,
                "b1_greedy": b1,
                "b2_cp_sat": b2,
            }
        )
    times = [row["b2_cp_sat"]["elapsed_seconds"] for row in rows]
    proposals = [row["b2_cp_sat"]["validation"] for row in rows if row["b2_cp_sat"]["validation"]]
    differences = []
    for row in rows:
        greedy, cp = row["b1_greedy"]["validation"], row["b2_cp_sat"]["validation"]
        if (
            greedy
            and cp
            and greedy["valid"]
            and cp["valid"]
            and (greedy["completed_tasks"] == cp["completed_tasks"])
        ):
            differences.append(cp["exposure_proxy"] - greedy["exposure_proxy"])
    return {
        "mode": "synthetic",
        "suite": suite,
        "operational_approval_allowed": False,
        "note": (
            "B0 is an unsafe reference, never a fallback. "
            "Metrics describe encoded demo constraints. "
            "Exposure and workload spread do not measure injuries, physiology, wages or validated "
            "fairness. The robustness suite cycles ten declared adversarial families; it is not "
            "an independent population sample. Only matched completed-task counts are paired."
        ),
        "count": count,
        "seed": seed,
        "family_counts": dict(Counter(row["family"] for row in rows)),
        "solver_status_counts": dict(Counter(row["b2_cp_sat"]["status"] for row in rows)),
        "validated_proposal_count": len(proposals),
        "invalid_returned_proposal_count": sum(not report["valid"] for report in proposals),
        "cp_sat_runtime_median_seconds": median(times),
        "cp_sat_runtime_p95_seconds": (
            quantiles(times, n=100, method="inclusive")[94] if len(times) >= 20 else None
        ),
        "p95_note": "Reported only for at least 20 samples; no statistical superiority claim.",
        "paired_exposure_proxy": _paired_interval(differences, seed),
        "scenarios": rows,
    }
