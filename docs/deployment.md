# Run the synthetic MVP

The application is a local, synthetic demonstration. It includes role-based
workflow, persisted records, a separate optimization worker, and a React UI.
Publishing a demonstration schedule does not authorize real workplace activity.
Use [product scope](product-scope.md) and the [verification record](verification.md)
to distinguish implemented behavior from evidence collected for a release.

## Docker Compose

Prerequisites: Docker Engine with Compose v2, enough disk for the Python/OR-Tools
image, and free local port 8000. From the repository root:

```sh
cp -n .env.example .env
docker compose up --build -d
docker compose ps
```

Open <http://127.0.0.1:8000>. API documentation is at
<http://127.0.0.1:8000/docs>. The published port binds only to the local machine.
PostgreSQL has no published host port. Set `HEATSHIELD_PORT` in `.env` if port
8000 is occupied.

The API migrates the database before serving requests, then creates the demo
accounts only when `HEATSHIELD_DEMO=true`. The separate worker starts after the
API and PostgreSQL are healthy and checks that migration heads are current.
Both application containers use the same immutable image and database-backed
job queue; Redis is not required. The runtime uses a non-root user and a
read-only application filesystem.

All accounts below are deliberately public synthetic fixtures. Their password
is **`HeatShieldDemo2026!`**. Never reuse these accounts with real data.

| Login | Role | Demonstration responsibility |
| --- | --- | --- |
| `supervisor@example.test` | Supervisor | Create scenarios, request plans, verify interventions |
| `safety@example.test` | Safety officer | Review, approve and publish the exact validated revision |
| `worker@example.test` | Worker 01 | View own instructions, acknowledge receipt, report a concern |
| `admin@example.test` | Administrator | Exercise organization-wide demonstration workflows |

Additional `worker-01@example.test` through `worker-06@example.test` identities
match the six workers in the seeded planning fixture.

```sh
docker compose logs --tail=100 api worker
curl --fail http://127.0.0.1:8000/healthz
curl --fail http://127.0.0.1:8000/readyz
docker compose stop
docker compose start
```

`/healthz` checks the process; `/readyz` checks database/schema access. A green
health check does not validate the occupational policy or certify work safety.
Normal `docker compose down` preserves the PostgreSQL volume. Adding `-v`
deletes that volume, including scenarios, jobs, accounts and audit records.

## Python and Node development

Use Python 3.12+, uv, and Node 22. Install the locked dependencies and build the UI:

```sh
uv sync --frozen --extra dev
npm --prefix web ci --strict-ssl=true
npm --prefix web run build
uv run heatshield migrate
uv run heatshield seed-demo
uv run heatshield serve --host 127.0.0.1 --port 8000
```

In a separate terminal with the same environment:

```sh
uv run heatshield worker
```

SQLite at `data/heatshield.db` is the default for lightweight local development.
Use PostgreSQL for concurrent deployment and queue-concurrency verification.
Both processes must receive the same `HEATSHIELD_DATABASE_URL`; setting it in
one terminal does not configure the other. The Python settings read environment
variables directly; Compose reads `.env` for interpolation.

For frontend development, `npm --prefix web run dev` starts Vite on local port
5173 and proxies `/api` to local port 8000. Set `API_PROXY_TARGET` when the API
runs elsewhere. For a single queued job, use `uv run heatshield worker --once`.

## TLS-inspecting development networks

Ordinary Docker builds need no custom CA. On a managed development network with
a supplied `CODEX_PROXY_CERT`, use the helper:

```sh
./scripts/build-image.sh
docker compose up --no-build -d
```

The helper mounts `proxy_ca` and the outer system `ca_bundle` as optional
BuildKit secrets. npm receives `NODE_EXTRA_CA_CERTS`; uv receives
`SSL_CERT_FILE`/`PIP_CERT` for the dependency-installing steps. Certificates are
not copied into image layers. HTTPS verification stays enabled. Do not disable
TLS verification to work around certificate failures.

Build-time CA secrets do not change running-container trust. If an outbound
connector also runs behind a TLS-inspecting proxy, mount the appropriate
combined trust bundle read-only into that container and configure its HTTP
client's `SSL_CERT_FILE` or equivalent setting separately.

The build helper uses the normal Docker client configuration. In a managed
cloud environment, verify the intended local Docker socket before running it;
do not switch to an unrelated remote daemon to bypass an access failure.

## Configuration and hosted demonstration

| Variable | Meaning |
| --- | --- |
| `HEATSHIELD_DATABASE_URL` | SQLAlchemy URL, normally `postgresql+psycopg://…` for deployment |
| `HEATSHIELD_DEMO` | Enables public synthetic accounts and demo fixtures; default `true` locally |
| `HEATSHIELD_JWT_SECRET` | Token-signing material; non-demo mode rejects the built-in demo key and requires at least 32 bytes |
| `HEATSHIELD_TOKEN_LIFETIME_MINUTES` | Access-token lifetime, 60 minutes by default |
| `HEATSHIELD_FRONTEND_DIST` | Compiled UI directory, `web/dist` by default |
| `HEATSHIELD_PORT` | Compose loopback port, 8000 by default |
| `POSTGRES_PASSWORD` | Compose database credential; example value is demonstration-only |

Before hosting an authenticated demonstration, generate independent random
database and JWT secrets outside source control, disable demo mode and seed
accounts, provision authorized users, configure HTTPS at the hosting boundary,
and restrict database/network access. For example, `openssl rand -hex 32`
generates a URL-safe secret; generate a separate value for each purpose. The
example `.env` values are public and provide no confidentiality.

Use a fresh deployment database or explicitly deactivate all previously seeded
public identities. Changing `HEATSHIELD_DEMO` does not delete accounts or data
that already exist in a persistent volume.

After migrations, provision each authorized identity through the private
password prompt. Worker identities also require their scenario worker binding:

```sh
docker compose exec api heatshield create-user --email owner@example.org --name Owner --role admin --organization demonstration
```

Keep passwords out of command arguments, shell history and checked-in files.
Use distinct identities and the minimum appropriate role for each participant.

Turning demo mode off changes account/security behavior. It does not convert
the synthetic safety policy into a reviewed clinical or legal policy and does
not make real-worker deployment supported. No hosting or external messaging
service is provisioned by these files.

## Backup and recovery

Keep database backups and configuration secrets in separate protected storage.
A local demonstration backup can be made without printing credentials:

```sh
docker compose exec -T db pg_dump -U heatshield -d heatshield -Fc > heatshield-backup.dump
```

Test restoration into a separate database before relying on a backup. Retain
the image/source revision and migration version with it. Do not restore over a
running database or delete a volume as a troubleshooting shortcut. For failed
jobs, inspect the persisted run result and worker/API logs; a process restart
must preserve accepted work and must not duplicate publication or intervention
events.
