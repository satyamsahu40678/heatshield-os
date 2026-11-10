# Product scope and release gates

HeatShield OS is an authenticated, local synthetic occupational heat planning
MVP. Its purpose is to demonstrate the complete planning and intervention
workflow using fabricated data and an illustrative policy. It is not a
clinically validated heat-risk classifier or an employer's operational safety
authorization system.

## Usable MVP boundary

P0 provides strict scenario contracts, a deterministic independent validator,
CP-SAT scheduling and comparison baselines. P1 completes the product workflow
around that foundation:

| Capability | Required behavior for P1 acceptance |
| --- | --- |
| Identity | Authenticated administrator, safety officer, supervisor and worker roles; tenant-scoped records |
| Persistence | Immutable scenario revisions and policy snapshots, durable run state, migrations and retained audit events |
| Optimization jobs | API accepts a job; a separate worker claims and completes it; restart/retry does not lose work or duplicate effects |
| Review and publication | Review is bound to the exact input/policy/schedule revision; stale, invalid or conflicting worker assignments cannot publish; safety officers can withdraw a replaced plan |
| Supervisor UI | Create a scenario, inspect findings/provenance, request a plan and compare actual results |
| Worker UI | View only authorized own instructions, choose fixed English/Hindi/Arabic text and acknowledge receipt |
| Intervention verification | Supervisor verification remains a separate recorded action from worker receipt |
| Severe symptom simulation | Record a minimized private report, show immediate stop-work/human-response instructions, and create a worker hold |
| Delivery package | Reproducible local startup, separate worker, PostgreSQL Compose services, health/readiness checks and demo script |

P1 can be described as complete only after these paths have implementation and
verification evidence. The [verification record](verification.md) is the source
for what was actually exercised. Checkboxes in a plan or a green process health
check are not evidence that a product journey or occupational policy is valid.

## Safety and evidence boundaries

- Every current planning fixture is synthetic. Historical weather, simulated
  WBGT and manual observations retain their provenance; none is silently
  promoted to a certified live measurement.
- The policy demonstrates local restriction and scheduling checks. It is
  versioned, illustrative and not independently clinically reviewed.
- A mathematically feasible plan must pass the separate validator. A passed
  validator establishes the encoded rules only, with the same input snapshot.
- Acknowledgement records receipt. Verification records the supervisor's
  implementation check. Neither establishes that an injury was prevented.
- The static multilingual templates are application copy for a demonstration,
  not a claim of clinical translation review. The application does not diagnose
  illness, dispatch responders or send external messages.
- Access controls minimize worker/private-report exposure. Real personal or
  health data requires additional organizational governance and field review.

The [Abu Dhabi Municipality's July 2026 notice](https://dmt.gov.ae/en/adm/Media-Centre/News/02Jul2026)
supports the local summer restriction context. [OSHA's hazard-recognition guidance](https://www.osha.gov/heat-exposure/hazards)
explains why on-site environmental and workload assessment cannot be replaced
by remote weather. These are source references for the design; their presence
does not approve the fixture thresholds or a real deployment.

## P2 extensions and field-pilot work

The following remain outside the usable synthetic MVP boundary:

- Multi-worker crews, buddy coverage, task precedence and travel constraints.
- Payroll integration, enforceable paid-attendance/recovery guarantees and
  historical fairness across comparable eligible workers.
- Reviewed rolling exposure/work-rest rules and richer clothing/metabolic
  models beyond the declared demonstration constraints.
- Certified live sensors, real employer integrations and clinical field
  evaluation with appropriate outcome data.
- Wearables, biometric inference, custom medical classifiers, optional LLM/RAG
  explanations and external messaging providers.

These are planned research or integration extensions, not hidden prerequisites
for navigating the synthetic MVP. A real-work field pilot requires qualified
policy review, measurement validation, applicable legal review, privacy/security
governance and prospective evaluation before any operational claims.
