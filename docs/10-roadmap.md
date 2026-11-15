# 10 - Roadmap and known limits

The detailed source of work items is the [engineering review/backlog](19-intake-gateway-engineering-review.md).
Its original evidence is historical; implementation updates distinguish local fixes from outstanding acceptance.
The [configuration-agent design](20-config-agent-design.md) is a proposal, not a shipped provisioning feature.

## Implemented foundation

The Python gateway/React UI replace the retired C# gateway. Local implementations include protected rep edits/clears,
ETag concurrency, atomic/idempotent Submit, Dataverse transcript/extraction checkpoints, typed AI/API contracts,
bounded retry/state/delivery behavior, extraction recovery and Speech lifecycle/token-renewal handling.

Both PowerShell and azd target Python. Journaling is part of the Dataverse provisioner, and new deployment settings use
managed-identity Speech. These changes do not eliminate the need for real-service acceptance or production hardening.

## Remaining priorities

| Priority area | Work |
|---|---|
| Live acceptance | Real gateway identity, schema profiles, agent outputs, Speech renewal, throttling/outages, restart and load scenarios |
| Call lifecycle | Admission/watchdog enforcement, reconnect/handoff races, unavailable-rep feedback and full bounded draining |
| Deployment safety | Automated maintenance/no-overlap cutover and compatible rollback; single-revision mode alone is insufficient |
| Authorization | Entra rep sign-in, per-call/tenant access, trusted submit identity and least-privilege Dataverse roles |
| Delivery | Durable outbox/consumer idempotency where notifications or function side effects require stronger guarantees |
| Metadata compatibility | Field-length/relationship coverage and customer-approved mapping/provisioning for existing business tables |
| Operations | Metrics/alerts, agreed retention/deletion, recovery objectives, disclosure and runbooks |
| Engineering | Expand targeted regressions/CI gates and real-service acceptance without replacing the proven workflow |

## Known boundaries

| Area | Current limit |
|---|---|
| Scaling | One process/replica per dataset; live sockets/state do not have distributed ownership |
| Restart | Recover committed notes and unfinished extraction, not live calls or deferred functions |
| Durability | Uncheckpointed speech can be lost when Dataverse and the process fail together |
| Shared keys | No authenticated per-rep/tenant authorization; UI credentials persist in localStorage |
| Retention | Memory/event queues are bounded; Dataverse journal/draft cleanup remains an operator policy |
| Webhooks/functions | Best-effort notification delivery and process-local invocation deduplication; not cross-restart exactly-once side effects |
| Matching | Phone-based auto-linking plus ranked/search suggestions; humans must review record ownership and mismatches |
| Product scope | Intake ends at human ticket submission; scheduling, outbound callbacks and multilingual experience remain separate work |

## Extension directions

Speech phrase hints, richer rep routing, handoff summaries, analytics and alternate front ends can build on this
foundation. Preserve the core invariants: **read-only agents, rep edits win, draft first, human Submit**, explicit
failures and the documented Dataverse checkpoint boundary.
