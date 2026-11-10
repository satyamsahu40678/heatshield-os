# Three-minute synthetic demonstration

This script uses persisted application workflows and the seeded six-worker,
twelve-task Abu Dhabi scenario. All workers, observations and policy thresholds
are fabricated. The fixture's date is July 15, 2026, so its local summer
restriction is exercised even when the demonstration is run in November.

## Rehearsal setup

Follow [deployment](deployment.md), including a running separate worker. For a
direct Python setup, the preparation commands are:

```sh
uv run heatshield migrate
uv run heatshield seed-demo
uv run heatshield serve --host 127.0.0.1 --port 8000
```

Run `uv run heatshield worker` in another terminal. With Compose, startup does
these steps automatically. Open the local UI in three browser profiles or sign
out between roles. Use `supervisor@example.test`, `safety@example.test`, and
`worker@example.test`, all with `HeatShieldDemo2026!` in demo mode.

Prepare a fresh scenario for the recording rather than reusing a published
scenario with completed interventions. As safety officer, withdraw any prior
published demonstration plan that uses the same workers and times; overlapping
published work or recovery now blocks replacement publication. If an earlier
rehearsal created a simulated stop-work hold, review and explicitly release it
through the safety workflow before reusing that worker. This is a simulation
reset, not medical clearance. Check `/readyz`, then verify that the
worker is running and can claim a queued job. Rehearse on the actual machine;
solver elapsed time and incumbents vary. If a solve reaches its limit, show its
true `FEASIBLE`/`UNKNOWN` state instead of narrating an optimal result.

## Timed presentation

| Time | Action and narration | Visible evidence |
| --- | --- | --- |
| 0:00–0:20 | “A warning alone does not rearrange a shift or show that a protected break happened.” Sign in as supervisor. | Synthetic labels; separate worker, supervisor and safety roles |
| 0:20–0:45 | Load the demo fixture and create a persisted scenario. Inspect the original roster. | Local restriction overlap, work/recovery findings, source and policy labels |
| 0:45–1:10 | Request optimization. Show its persisted queued/running state while the separate worker computes it. | Run ID, scenario revision, eventual solver status and independent validator findings |
| 1:10–1:35 | Inspect the proposed assignments, protected recovery and explicit deferrals. Compare the fixed/greedy/proposed schedules. | Computed completion and exposure-proxy figures; zero encoded violations when validation passes |
| 1:35–1:55 | Sign in as safety officer. Review, approve and publish that exact revision. | Separate approval/publication actions and audit entries; no real-work authorization claim |
| 1:55–2:20 | Sign in as worker 01. Open the assigned recovery instruction, choose English/Hindi/Arabic, and acknowledge it. | The acknowledgement is recorded as receipt, with its own timestamp |
| 2:20–2:40 | Return as supervisor and verify that the protective action was implemented. | Verification is a separate transition from acknowledgement |
| 2:40–2:55 | As the worker, report severe symptoms in the simulation. | Stop-work message, trained-responder escalation and worker hold; no claimed emergency dispatch |
| 2:55–3:00 | “We connect constrained planning to independent checks and recorded action.” | Persisted audit trail and explicit prototype limits |

Use plain descriptions: “environmental exposure proxy,” “encoded constraints,”
and “demonstration approval.” Do not describe a proxy reduction as fewer
injuries, the demonstration policy as clinically validated, or acknowledgement
as proof that the action happened.

## Recovery and evidence

If a queued run remains pending, show the run state and inspect worker logs.
Restarting the worker should recover durable accepted work; do not invent a
result or substitute a fixed roster. If there is no feasible plan, show the
diagnostic and explicit deferrals rather than presenting unsafe assignments.

To generate reproducible offline comparison evidence:

```sh
uv run heatshield benchmark --count 100 --seed 7 --suite robustness --output benchmark-results.json
```

Retain the actual output with source revision and machine context. Report
feasibility, violations, completion, runtime distribution and solver status
together. Do not insert a hypothetical percentage into the demo. Saved
benchmark results are distinct from the current live scenario's persisted run.
