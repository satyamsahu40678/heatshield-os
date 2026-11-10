"""Benchmark outputs are computed, and unsafe references remain labeled."""

import pytest

from heatshield.benchmark import benchmark_scenario, run_benchmark
from heatshield.optimization import optimize


def test_seeded_benchmark_validates_each_comparable_proposal():
    result = run_benchmark(count=1, seed=7)
    row = result["scenarios"][0]
    assert result["operational_approval_allowed"] is False
    assert result["cp_sat_runtime_p95_seconds"] is None
    assert row["b0_fixed"]["status"] == "UNSAFE_REFERENCE"
    assert row["b0_fixed"]["validation"]["valid"] is False
    assert row["b1_greedy"]["validation"]["valid"] is True
    assert row["b2_cp_sat"]["validation"]["valid"] is True
    assert row["b2_cp_sat"]["status"] in {"FEASIBLE", "OPTIMAL"}
    assert row["b2_cp_sat"]["validation"]["completed_tasks"] == 12
    assert row["scenario_hash"] and row["policy_hash"]


@pytest.mark.parametrize("count,seed", [(0, 7), (101, 7), (1, -1), (2, 2_147_483_647)])
def test_benchmark_rejects_out_of_bounds_runs(count, seed):
    with pytest.raises(ValueError):
        run_benchmark(count, seed)


@pytest.mark.parametrize("index", [2, 4, 6, 8])
def test_robustness_families_fail_closed_for_mandatory_work(index):
    scenario, _family = benchmark_scenario(7 + index, index, "robustness")
    result = optimize(scenario)
    assert result.status == "INFEASIBLE"
    assert result.schedule is None


def test_benchmark_rejects_unknown_suite():
    with pytest.raises(ValueError, match="suite"):
        run_benchmark(1, 7, suite="unreviewed")
