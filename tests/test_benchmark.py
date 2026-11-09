"""Benchmark outputs are computed, and unsafe references remain labeled."""

import pytest

from heatshield.benchmark import run_benchmark


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
