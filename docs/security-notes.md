# Security and data boundaries

HeatShield OS remains a synthetic planning demonstration. Authentication,
persistent workflow and human review do not convert its illustrative policy
into a reviewed occupational-health policy or authorize real work.

## Demo mode and deployment configuration

`HEATSHIELD_DEMO` defaults to `true` for the local demonstration. Demo mode
creates public synthetic accounts under `example.test` with the documented
password `HeatShieldDemo2026!` and uses a publicly known development signing
key unless configured otherwise. These credentials are conveniences for a demo,
not access controls for confidential information.

For a separately operated instance, set `HEATSHIELD_DEMO=false` and provide
`HEATSHIELD_JWT_SECRET` from a secret store or private environment. Non-demo
configuration rejects the built-in demo signing key and secrets shorter than
32 bytes. Generate independent random secret material; length validation cannot
establish randomness. Non-demo mode does not seed the public accounts. It still
accepts synthetic scenarios only.

Keep secrets and database credentials out of Git, images, screenshots and shared
benchmark artifacts. Terminate HTTPS at the deployment ingress and restrict
database access to the application and worker. The application does not encrypt
the SQLite database or individual PostgreSQL JSON fields; disk encryption,
backups, database transport and credential rotation belong to the deployment.

## Identity and sessions

Passwords use PBKDF2-HMAC-SHA256 with a random 16-byte salt and 600,000
iterations. Login failures have a common unauthorized response. Tokens use
HS256 with explicit issuer, audience, expiry, not-before, token identifier and
account token-version claims.

The API loads the account from the database on every authenticated request.
Roles, organization and worker bindings come from that account, not from
browser headers or role fields supplied with a command. Disabled accounts and
revoked token versions cannot authenticate. Logout increments the account's
token version and revokes every token issued before that increment.

`HEATSHIELD_TOKEN_LIFETIME_MINUTES` defaults to 60 and permits values from 1 to
1440. There is no refresh-token service; an expired session requires login.
Bearer credentials must be protected from browser script injection and from
being copied into logs or URLs. A production identity provider, account recovery
and MFA are outside this synthetic MVP.

The React client stores the current bearer session in per-tab `sessionStorage`
to survive a reload and clears it on logout or unauthorized responses. This
storage is readable by JavaScript running on the same origin; it is not an
HttpOnly cookie. The API uses bearer authorization rather than ambient cookie
authentication. The deployment must preserve the same-origin content boundary
and avoid injecting third-party scripts into the application.

## HTTP controls and their scope

The application limits POST/PUT/PATCH request bodies to 1 MiB. The legacy
synchronous optimizer requires a staff role and admits at most two concurrent
solver calls per API process; application clients should use durable planning
jobs. Worker and unauthenticated requests cannot bypass the product workflow
through those legacy planning routes.

Login is limited to 30 attempts per source address per 60 seconds per process;
excess attempts receive HTTP 429 with a `Retry-After` header. This in-memory
limiter is neither shared across replicas nor an account-lockout/MFA service.
Behind a reverse proxy, the source address is the connection peer seen by the
ASGI server. Configure trusted proxy forwarding at the ingress/server rather
than trusting arbitrary client-supplied forwarding headers.

Normal API responses use `Cache-Control: no-store`. Responses include
`nosniff`, frame denial and a restrictive same-origin content security policy;
interactive API documentation has its own script requirements and is excluded
from that CSP. The application does not set an HSTS policy itself: HTTPS and
HSTS belong to the deployment ingress. These controls reduce exposure but do
not establish resistance to every application attack.

## Tenant and workflow authority

Every persistent scenario, run, approval, intervention and audit lookup must be
scoped to the authenticated organization. Worker-facing responses additionally
filter the server-owned worker binding and publish state. A resource identifier
from another tenant must not disclose that resource through a read or mutation.

Scenario revisions retain the complete canonical input and policy snapshots.
Approval binds the current revision and scenario, policy and schedule hashes;
publishing performs a fresh independent validation and checks that binding.
An optimizer result or a successful validation is not itself a human approval.

Acknowledgement and implementation verification are distinct append-only
events. Raw symptom details belong to the private health-report record, not to
general audit payloads or supervisor schedule summaries. Urgent reports create
a stop-work hold and a human-response workflow; the prototype does not dispatch
emergency services.

The durable worker claims jobs with leases and fenced attempts. Reclaimed jobs
cannot be overwritten by an older attempt. Retries and command idempotency
must not create duplicate approvals, publications or intervention events.

## Integrity limits

Application-side append-only listeners protect revision, approval, intervention
event, health-report and audit rows from ordinary ORM updates/deletes. They are
not database-level protection against a privileged database administrator or
raw SQL. Hash checks and independent validation detect relevant snapshot or
schedule corruption before reviewed workflow transitions; a hash is not a
digital signature or a substitute for database authorization and backups.

The solver budget bounds CP-SAT solve stages, not total HTTP request time.
Typed input, scenario-size and candidate-count bounds remain necessary even
on authenticated routes. Deployment-wide connection/concurrency and abuse
controls must be considered separately from per-process API controls.

## Independent checks

[Product security tests](../tests/test_product_security.py) exercise the real
application, SQLAlchemy database, authentication and scheduling worker. Their
purpose is to verify tenant boundaries, server-owned identity, protected legacy
routes, workflow role checks, stale/tampered approval rejection, command
idempotency and private-data isolation. See the verification record for the
executed check results; the presence of a test file alone does not establish a
passing run.

SQLite tests provide fast application checks. PostgreSQL migrations and locking
require separate integration verification before claiming equivalent deployed
durability or concurrency behavior.
