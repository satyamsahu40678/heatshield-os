# Verification record

Recorded November 9, 2026 for the first synthetic planning slice.

- Locked installation into a fresh virtual environment succeeded with `uv sync --extra dev --frozen --offline` using the populated dependency cache.
- Full domain, scheduler, validator, benchmark and HTTP suite: **95 passed** on Python 3.12.14.
- `ruff check .`, `ruff format --check .`, and `git diff --check` passed.
- The running Uvicorn server returned HTTP 200 for health, interactive API docs, optimization and a separate validation request. The live demo assigned all twelve tasks and had zero modeled-constraint violations.
- The source includes GitHub Actions verification; its remote result must be checked separately.

Remote publication is blocked: both the GitHub connector's tree-write endpoint
and a normal Git push returned HTTP 403 with the connected credentials. No
remote implementation branch or pull request was created, so remote CI has not
run. The implementation is committed on local branch `codex/planning-foundation`
and can be delivered as a source archive or Git bundle.

The managed command sandbox stalls local TestClient event-loop operations. API integration tests were run outside that sandbox; ordinary domain checks and benchmark execution succeeded inside it.

## Computed smoke benchmark

Exact generated output is retained in [benchmark-smoke.json](evidence/benchmark-smoke.json). All workers, tasks and readings are fabricated. Seeds vary synthetic heat conditions and cooling capacity; three scenarios are a smoke check, not a representative evaluation.

| Seed | Fixed violations | Greedy completed / deferred | CP-SAT completed / deferred | CP-SAT status | Greedy proxy | CP-SAT proxy |
| --- | --- | --- | --- | --- | --- | --- |
| 7 | 18 | 12 / 0 | 12 / 0 | OPTIMAL | 95625 | 95625 |
| 8 | 18 | 12 / 0 | 12 / 0 | FEASIBLE | 91950 | 91050 |
| 9 | 18 | 12 / 0 | 12 / 0 | OPTIMAL | 92625 | 92175 |

CP-SAT median wall time in this run: 0.461 seconds, including candidate construction and independent validation. Runtime and timeout incumbents depend on the machine. No p95 is reported for this three-sample run.

Greedy and optimized proposals had zero encoded hard-constraint violations in all three scenarios. The fixed roster deliberately violates the demo rules and is never an operational fallback. Proxy scores order environmental inputs; they do not quantify medical risk or injury reduction. A `FEASIBLE` result does not claim lexicographic optimality. Workload spread is an unadjusted demonstration metric and does not prove wage protection or fairness.

## Important exclusions

No real measurements, employer data, reviewed clinical policy, database, durable jobs, authentication, operational approvals, worker delivery, or dashboard are included. Their dependency order and acceptance gates remain open in the [implementation plan](implementation-plan.md).
