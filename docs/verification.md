# Verification record

Updated November 10, 2026 for the populated operations workspace and responsive
UI refresh. Earlier benchmark and deployment evidence is retained and identified
separately below.

## Executed checks

| Check | Result |
| --- | --- |
| Full Python suite, including a real PostgreSQL target | **223 passed** |
| PostgreSQL-specific tests within that suite | **12 passed** |
| Ruff lint and formatting | Passed across 48 Python files |
| TypeScript/Vite production client | Built successfully |
| Chromium tests against the refreshed production client and actual API/worker | **5 passed** in 15.3 seconds |
| Operations dashboard and worker mobile layouts | 390-pixel viewport; no horizontal overflow |
| Compose configuration and whitespace check | `docker compose config` and `git diff --check` passed |
| Retained PWA privacy/offline baseline | 51 public assets cached; zero API responses cached; offline shell renders without cached private data |
| Retained robustness benchmark | 100 synthetic scenarios across ten declared families |
| Retained Docker image and full Compose rehearsal | Passed for the earlier release baseline; detailed record below |
| Retained optional Open-Meteo adapter check | Live request returned 48 context hours; no WBGT/admission claim |

The preceding release baseline had 200 passing Python tests, including eleven
PostgreSQL tests, and four browser tests. The refresh adds additive computed
showcase data, protected operations aggregation and responsive presentation.
The retained benchmark/deployment/PWA/adapter results were not remeasured for
this visual refresh; successful Compose configuration validation is not a new
full-stack deployment rehearsal.

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

The new showcase and workspace API checks verify six computed synthetic stories,
fresh validation and approval-bound protection totals, additive restart/loading,
tenant and private-data boundaries, and preservation of custom credentials,
token versions and revisions. An existing queued user job remains queued with
zero attempts when sample content is enabled. The seeded hold uses the separate
`showcase-worker-07` identity; the normal six-worker fixture can still plan and
publish. Failed, unknown or tampered published candidates are excluded from
protection metrics and appear as integrity follow-up items.

## Browser journey and visual evidence

The production bundle was served by FastAPI, with a separate durable worker.
The browser tests exercised:

1. A populated operations dashboard with metrics matching the authenticated API,
   a retained WBGT curve, six workflow states, follow-up actions and responsive
   desktop/mobile layouts. Selecting missing-evidence inputs shows the actual
   no-proposal state, while an optimized shift opens its valid comparison.
2. Safety login, scenario import, queued planning, comparison, fresh demo review
   and publication, worker acknowledgement, supervisor verification and audit.
   The completed journey also withdraws the plan and confirms worker removal.
3. Worker role isolation and a narrow mobile layout.
4. Recoverable sign-in failure.
5. A private severe-report simulation, worker stop state, generic staff alert,
   supervisor access restrictions and safety-role demo hold release.

[Refreshed operations dashboard](evidence/operations-dashboard.png) ·
[Mobile operations dashboard](evidence/operations-dashboard-mobile.png) ·
[Refreshed mobile worker view](evidence/worker-mobile-v2.png)

The earlier [supervisor workspace](evidence/supervisor-dashboard.png) and
[mobile worker view](evidence/worker-mobile.png) remain baseline artifacts.

Only public static assets/HTML enter the PWA cache. Protected API responses are
excluded. Offline private-data fetching produces a visible unavailable state;
the application does not manufacture a cached current schedule.

## Retained deployment and persistence baseline

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
records the image and check details for that baseline. Its rebuilt image also
passed the admission/withdrawal delta:
a second overlapping publication returned 409; withdrawing the first plan then
allowed the replacement to publish. The tagged application image is a
local build artifact; no public hosting or real-work deployment is claimed.

## Retained computed robustness benchmark

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

The source and CI configuration are tracked through
[pull request #1](https://github.com/satyamsahu40678/heatshield-os/pull/1).
The workflow runs backend/PostgreSQL checks, production browser tests and a
container build. Earlier remote application test/build steps passed; the first
browser job exposed a cleanup failure from long-lived `uv run` processes
retaining the dependency-cache lock, and CI was adjusted to use installed
executables and explicit teardown.

The populated-workspace UI refresh passed the local checks above. Consult the
PR checks for remote CI on the exact published commit; earlier green steps or a
source push alone do not establish a passing remote result for this refresh.

All measurements, identities, symptoms and policy thresholds used above are
synthetic. Crews, payroll guarantees, reviewed rolling occupational policy,
certified live measurements and clinical field evaluation remain explicit
extensions in the [product scope](product-scope.md).
