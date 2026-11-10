# Verification record

Recorded November 10, 2026 for the complete synthetic planning MVP.

## Executed checks

| Check | Result |
| --- | --- |
| Full Python suite, including a real PostgreSQL target | **200 passed** in 16.32 seconds |
| PostgreSQL-specific tests within that suite | **11 passed** |
| Ruff lint and formatting | Passed across 44 Python files |
| TypeScript/Vite production client | Built successfully |
| Chromium tests against the built client and actual API/worker | **4 passed** |
| Mobile worker layout | 390-pixel viewport; no horizontal overflow |
| PWA privacy/offline check | 51 public assets cached; zero API responses cached; offline shell renders without cached private data |
| Robustness benchmark | 100 synthetic scenarios across ten declared families |
| Docker image and full Compose rehearsal | Passed; detailed record below |
| Optional Open-Meteo adapter | Live request returned 48 context hours; no WBGT/admission claim |

The Python run used an actual PostgreSQL 17 container. Tests cover migration
upgrade/downgrade and drift, rollback/restart durability, `SKIP LOCKED`, two
workers claiming one job, reclaimed-lease fencing, long worker identifiers,
publication/revision and hold/publication races, and concurrent publication of
conflicting plans without existing worker login rows. Exactly one of the two
conflicting publications succeeds.

The HTTP and product suites exercise real authentication, SQLAlchemy sessions,
the optimizer and validator. They check tenant/role isolation, actor-bound
idempotency, stale/tampered candidates, worker-only views, private-health
boundaries, distinct acknowledgement/verification and audited withdrawal.
The managed command sandbox stalls TestClient local event-loop wakeups;
integration runs used the approved outer executor. Domain and benchmark checks
also ran inside the ordinary sandbox.

## Browser journey and visual evidence

The production bundle was served by FastAPI, with a separate durable worker.
The browser tests exercised:

1. Safety login, scenario import, queued planning, comparison, fresh demo review
   and publication, worker acknowledgement, supervisor verification and audit.
   The completed journey also withdraws the plan and confirms worker removal.
2. Worker role isolation and a narrow mobile layout.
3. Recoverable sign-in failure.
4. A private severe-report simulation, worker stop state, generic staff alert,
   supervisor access restrictions and safety-role demo hold release.

[Supervisor workspace](evidence/supervisor-dashboard.png) ·
[Populated mobile worker view](evidence/worker-mobile.png)

Only public static assets/HTML enter the PWA cache. Protected API responses are
excluded. Offline private-data fetching produces a visible unavailable state;
the application does not manufacture a cached current schedule.

## Deployment and persistence

A real multi-stage image build installed locked dependencies with TLS
verification and ran the API as UID **10001** with a read-only root filesystem.
The isolated Compose rehearsal used PostgreSQL, API and a separate worker.
It completed authenticated planning, review/publication, multilingual
instructions, acknowledgement, distinct verification and severe-report holds.

Published records, the stop-work hold and eight linked audit events survived
restarting PostgreSQL/API/worker. Separately, with the worker stopped, a queued
job survived API/database restart and completed after worker restart with one
attempt and independent validation passing.

The retained [deployment evidence](evidence/deployment-verification.json)
records the image and check details. The rebuilt final-source image also passed the admission/withdrawal delta:
a second overlapping publication returned 409; withdrawing the first plan then
allowed the replacement to publish. The tagged application image is a
local build artifact; no public hosting or real-work deployment is claimed.

## Computed robustness benchmark

[Summary](evidence/benchmark-summary.json) ·
[Compressed complete output](evidence/benchmark-robustness-100.json.gz)

The run cycles ten families with ten seeded instances each: nominal, hotter,
missing cooling, sensor outage, skill shortage, limited availability, new
workers, tight deadlines, forecast-only data and conflicting readings.

| Outcome | Count |
| --- | ---: |
| Validated proposals | 55 |
| Proven `OPTIMAL` across all stages | 22 |
| `FEASIBLE`, optimality not established across all stages | 33 |
| `INFEASIBLE` under the encoded rules/evidence | 45 |
| Invalid returned proposals | **0** |

Median CP-SAT wall time was **0.346 seconds** and p95 **2.032 seconds**, including
model construction and independent checking on this machine. The solve-stage
limit is separate from total request/runtime cost.

For the 36 cases where both greedy and CP-SAT returned valid plans with equal
completed-task counts, mean CP-minus-greedy exposure-proxy difference was
**−1877.08**. The seeded bootstrap interval was [−2481.25, −1331.25]. These are
computed software ordering scores across repeated synthetic families, not an
independent population sample, a medical dose, a fairness validation or an
injury-reduction estimate. Runtime and timeout incumbents vary with the machine.
The unsafe fixed roster is a reference, never an operational fallback.

The earlier [three-case nominal smoke output](evidence/benchmark-smoke.json) is
retained separately. It is not the 100-case robustness result.

## Source and CI

The source and CI configuration are in
[pull request #1](https://github.com/satyamsahu40678/heatshield-os/pull/1).
The workflow runs backend/PostgreSQL checks, production browser tests and a
container build. Consult the PR checks for the current commit; a source push
alone is not a passing remote check.

All measurements, identities, symptoms and policy thresholds used above are
synthetic. Crews, payroll guarantees, reviewed rolling occupational policy,
certified live measurements and clinical field evaluation remain explicit
extensions in the [product scope](product-scope.md).
