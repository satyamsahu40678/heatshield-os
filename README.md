# HeatShield OS

Turn heat evidence into a workable plan, then track protective action through
worker acknowledgement and supervisor verification.

HeatShield OS is a complete **synthetic demonstration application** with a React
workspace, authenticated FastAPI service, PostgreSQL persistence and a separate
durable optimization worker. Scheduling uses OR-Tools CP-SAT and an independently
implemented validator. Policies and worker records are illustrative; demo review
and publication cannot authorize real work.

## Start the complete app

Requires Docker with Compose.

```sh
cp -n .env.example .env
docker compose up --build
```

Open <http://localhost:8000>. Compose starts PostgreSQL, applies migrations,
creates demo identities and a populated sample workspace, and starts the API
and worker. Data survives normal container restarts in a named PostgreSQL volume.

Use `safety@example.test`, `supervisor@example.test`, or `worker@example.test`
with the public demo password **`HeatShieldDemo2026!`**. These fixture identities
exist only in demo setup. The safety officer opens a populated operations
dashboard. Inspect any sample shift, then switch to the worker to acknowledge
recovery or the supervisor to verify a received instruction. You can also
create your own scenario and use the complete planning/review/publication flow.

For managed environments with an HTTPS inspection proxy, use
`scripts/build-image.sh` to pass optional CA build secrets, followed by
`docker compose up`. See [deployment instructions](docs/deployment.md) for
configuration, backups, migrations, non-demo provisioning and cleanup.

## Explore the populated demo

Demo startup adds six walkthroughs: draft inputs, an optimized proposal,
a reviewed shift, a published plan, a missing-evidence/cooling case requiring
review, and a withdrawn historical plan. Results are computed by the actual
optimizer and checked independently. The dashboard derives its metrics, WBGT
curve, follow-up queue, team roster and activity from those stored records.

[Desktop preview](docs/evidence/operations-dashboard.png) ·
[Worker mobile preview](docs/evidence/worker-mobile-v2.png)

The worker account opens published recovery instructions at pending,
acknowledged and verified stages, with English/Hindi/Arabic text. A separate
`worker-07@example.test` account demonstrates an active stop-work hold without
blocking the six normal planning-fixture workers. It uses the same public demo
password. Supervisors see a generic alert, not private symptom details.

Seeding is additive: rerunning startup or `heatshield seed-demo` preserves your
scenario edits, queued jobs, acknowledgements, withdrawals, hold releases and
credentials. It never resets the database. Set `HEATSHIELD_DEMO_CONTENT=false`
to disable automatic sample loading; existing samples remain. An authenticated
safety officer or administrator can explicitly add missing samples through
`POST /api/v1/demo/load` with an empty JSON body. Non-demo deployments cannot
load the showcase.

## What works

- Versioned scenario creation/import/editing, immutable input/policy snapshots,
  provenance, exact site-local restriction checks and explicit task deferrals.
- Skills, availability, acclimatization allowances, per-horizon heat-work caps,
  full-interval WBGT evidence, protected recovery and cooling capacity.
- Lexicographic CP-SAT planning, independent validation, fixed/greedy comparisons,
  solver status, objective bounds, reproducibility hashes and audit findings.
- Durable queued runs with leases, crash recovery and fencing against late
  workers. Database state survives API/worker restarts.
- Tenant-scoped identities, administrator/safety/supervisor/worker roles,
  expiring bearer sessions, logout revocation and idempotent workflow commands.
- Fresh revision/hash-bound demo review and publication. Changed or tampered
  plans cannot reuse an earlier review. Publication also rejects conflicting
  worker assignments across active plans; audited withdrawal permits replacement.
- Responsive supervisor dashboard and worker PWA. Worker views contain only
  their own published assignments; acknowledgement and verification are distinct.
- English/Hindi/Arabic recovery instructions, minimized private symptom reports,
  generic staff stop-work alerts and an explicitly demonstrative hold-release
  workflow. The application does not dispatch emergency services.
- Optional Open-Meteo forecast context, clearly separated from admissible WBGT
  measurements. Forecast context never admits a heat-work assignment.

## Local development

Requires Python 3.12+, Node 22+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync --extra dev --frozen
cd web
npm ci
npm run build
cd ..
uv run heatshield migrate
uv run heatshield seed-demo
uv run heatshield serve
```

In a second terminal from the repository:

```sh
uv run heatshield worker
```

The default development database is SQLite in `data/`. Set
`HEATSHIELD_DATABASE_URL` for PostgreSQL. `cd web && npm run dev` starts Vite with
an API proxy to port 8000. The built client is served directly by FastAPI.
Interactive API documentation is at `/docs`.

## Verify

```sh
uv run --extra dev pytest
uv run --extra dev ruff check .
uv run --extra dev ruff format --check .
cd web && npm run build
```

Set `HEATSHIELD_TEST_DATABASE_URL` to an isolated PostgreSQL database to include
real migration, concurrent-claim, lease-recovery and publication-race tests.
Those tests use disposable schemas and skip when the variable is absent.

With the API and worker running, execute browser tests against the built client:

```sh
cd web
npx playwright install chromium
PLAYWRIGHT_BASE_URL=http://127.0.0.1:8000 npm run test:e2e
```

The test configuration also accepts `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` for an
existing Chromium installation. GitHub Actions runs backend/PostgreSQL tests,
frontend build/browser checks and a container build.

```sh
uv run heatshield benchmark --suite robustness --count 100 --seed 7 --output benchmark-results.json
```

The robustness suite cycles ten declared synthetic families, including missing
cooling, skill shortages, missing/forecast-only evidence, tight deadlines and
conflicting readings. The retained [benchmark summary](docs/evidence/benchmark-summary.json)
and [verification record](docs/verification.md) contain computed results. These
are software-policy checks and exposure-ordering proxies, not health outcomes.

## Technical documentation

- [Architecture and domain semantics](docs/design.md)
- [Implementation plan and release gates](docs/implementation-plan.md)
- [Product scope and future field-pilot boundaries](docs/product-scope.md)
- [Security model and configuration](docs/security-notes.md)
- [Deployment and operations](docs/deployment.md)
- [Three-minute demo script](docs/demo-script.md)
- [Verification evidence](docs/verification.md)

Crews, payroll guarantees, reviewed rolling work/rest rules, certified sensors
and clinical field validation remain explicit research/integration extensions.
HeatShield OS remains a working title pending public-brand review.
