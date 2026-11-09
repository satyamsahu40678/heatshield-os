# HeatShield OS

A synthetic planning prototype for explainable occupational heat prevention.
The first implementation proposes task schedules and checks them independently
against an **illustrative, unreviewed demo policy**. It does not approve real work.

Read the [refined technical design](docs/design.md) and the
[prioritized implementation plan](docs/implementation-plan.md).
The [verification record](docs/verification.md) contains test results and the
computed synthetic benchmark.

## Run locally

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync --extra dev --frozen
uv run heatshield demo
uv run uvicorn heatshield.api:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000/docs> for the interactive API. No external services,
API keys, database or weather connection are required for this milestone.

```sh
curl http://127.0.0.1:8000/api/v1/demo/scenario -o /tmp/scenario.json
curl -X POST http://127.0.0.1:8000/api/v1/scenarios/optimize \
  -H 'Content-Type: application/json' --data-binary @/tmp/scenario.json
```

The scenario is fabricated: six workers, twelve tasks, an Abu Dhabi summer shift
and simulated hourly WBGT observations. `heatshield fixture --seed 8` prints a
different reproducible input. `heatshield schema` exports the OpenAPI contract.

## Implemented foundation

- Strict versioned inputs, bounded scenario sizes, UTC timestamps and site-local
  half-open restriction intervals.
- Single-worker atomic tasks, skills and availability, explicit deferral,
  acclimatization allowances, per-horizon heat-work caps and full-interval WBGT
  evidence. All allowances and thresholds are demo configuration.
- Explicit recovery after each heat task, worker non-overlap and shared cooling
  capacity. Idle time is not silently counted as recovery.
- OR-Tools CP-SAT with lexicographic objectives: deferrals, environmental proxy,
  then workload spread. Solver statuses, stages, seed and version are retained.
- An independent validator that reconstructs constraints from the original
  inputs, checks hashes, and rejects tampered or incomplete schedules.
- Stateless FastAPI endpoints, a fixed reference roster, a greedy baseline and
  computed benchmark output.

Every result requires human review. `validation.valid` means the submitted plan
passes the encoded demo constraints; `operational_approval_allowed` is always
`false`. `UNKNOWN` means feasibility was not established, whereas `INFEASIBLE`
is a solver proof under the supplied model and evidence. An impossible mandatory
task is never silently deferred. The intentionally unsafe fixed roster is a
benchmark reference, never a fallback.

## API

| Method | Path | Body/result |
| --- | --- | --- |
| GET | `/healthz` | Process health and synthetic mode |
| GET | `/api/v1/demo/scenario` | Reproducible scenario |
| POST | `/api/v1/scenarios/optimize` | Scenario → solver result and validation |
| POST | `/api/v1/scenarios/validate` | `{ "scenario": ..., "schedule": ... }` → findings |

The synchronous CP solve-stage budget is at most five seconds. Model construction
and independent validation take additional time. Candidate generation is capped
at 50,000 admitted worker/task/start combinations. This local API has no users,
tenant isolation, storage, operational approval or publishing.

## Verify and compare

```sh
uv run --extra dev pytest
uv run --extra dev ruff check .
uv run --extra dev ruff format --check .
uv run heatshield benchmark --count 3 --seed 7 --output benchmark-results.json
```

The benchmark checks fixed, greedy and optimized schedules against identical
inputs and the same independent validator. It reports actual completion,
deferrals, violations, proxy scores and runtimes; there is no hardcoded
performance-improvement claim. The small smoke benchmark varies fabricated heat
and cooling capacity. It is not a clinical evaluation or a representative
workforce study. P95 is emitted only for at least twenty samples.

GitHub Actions runs these checks and retains a generated benchmark artifact.

## Boundaries and next milestone

The exposure score is an ordering proxy, not physiological dose. Workload spread
is a demonstration objective, not validated fairness or proof of preserved pay.
Forecast, simulated, manual and sensor labels remain distinct; forecast-only,
stale, suspect and uncalibrated sensor evidence cannot admit heat assignments.
Calendar rules alone do not establish heat safety.

Next: PostgreSQL snapshots and migrations, durable optimization jobs, tenant
permissions, revision-bound approvals, then the supervisor dashboard and worker
acknowledgement/verification workflow. Crews, buddy requirements, payroll,
rolling work/rest rules, live sensors, medical histories and emergency dispatch
are outside this first implementation. See the plan for their acceptance gates.

HeatShield OS remains a working title pending public-brand review.
