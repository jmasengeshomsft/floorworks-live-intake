# 08 - Operations and troubleshooting

## First checks

```powershell
.\infra\verify-gateway.ps1 -ResourceGroup <rg> -AppName <app>
az containerapp revision list -g <rg> -n <app> -o table
az containerapp logs show -g <rg> -n <app> --tail 100 --format text
```

The verifier is read-only. Check the intended image/revision, not just the public `/healthz` response:
an old healthy revision can mask a failed new one. Use the call ID to correlate logs, `/v1/calls/{id}`,
the Dataverse draft and journal. Logs/transcripts may contain personal data; restrict access.

## Triage

| Symptom | Check/action |
|---|---|
| New revision fails; `/healthz` times out | Read startup logs first. Configuration, schema and journal verification can stop startup before HTTP is available |
| `required integrity columns cannot be disabled` | Check draft transcript version and journal mappings. Run the approved Dataverse provisioner/migration; do not disable integrity fields |
| Missing journal/key or key not Active | Re-run schema provisioning; reconcile duplicate call IDs explicitly if needed. Wait for key activation before deployment |
| Dataverse 403 | Register the **gateway** application user and its table privileges. Foundry project access is separate |
| Event Grid delivers but no call starts | Matching `EVENTGRID_SECRET`, correct `/api/incoming` URL, intended event filter and `LINE_NUMBERS`; exclude competing subscriptions |
| Teams rejects with SIP 480 / code 10140 | Bot calling channel, resource-account/ACS binding and tenant consent; use the [telephony runbook](15-telephony-binding-runbook.md) |
| Answered but no AI audio | Foundry endpoint/agent name, managed-identity role and voice model availability; inspect session errors |
| No transcript after transfer | `/v1/config` live-notes status, actual Speech resource/region/custom subdomain, Speech role/token refresh and per-speaker audio |
| Tool approval or `CONSENT_REQUIRED` | Managed-identity Dataverse connection, project application user, read-only allowlist and approval settings; republish with [agent setup](17-agent-setup.md) |
| Rep does not ring | `TRANSFER_TEAMS_OBJECT_IDS`, Teams sign-in/availability, licensing and Enterprise Voice |
| UI 401 / browser CORS failure | Current API key, gateway URL and exact `CORS_ORIGINS`; the UI is separately hosted |
| Extraction `failed` | Inspect status/error and agent output contract. Fix permanent errors before retry; the prior valid result remains visible |
| `persistenceError` / checkpoint-stage error | Dataverse availability, identity, column limits and permissions. Compare transcript length with `checkpointVersion`; visible text is not proof of durability |
| Edit/Submit 409 or 422 | Refresh and review concurrent changes; supply valid field types and compatible customer/equipment/contact links |
| SSE disconnect/resync | Client must reload snapshots and reconnect with the server cursor; the shipped UI handles this |

For detailed PowerShell deployment/Speech migration diagnostics, see [16](16-intake-gateway-deploy.md).

## Safe upgrades and rollback

The gateway requires **one process/replica per journal/draft dataset**. PowerShell and Bicep configure min/max replicas
to one, but that is not distributed call ownership. Container Apps single-revision mode can briefly overlap revisions.
Neither deployment path implements automatic call draining.

1. Pause inbound routing upstream and finish active calls; allow final extraction/checkpoint work to complete.
2. Stop/deactivate all old gateway revisions and confirm no other gateway process uses the dataset.
3. Apply approved schema changes and runtime permissions. Reconcile failed/pending keys before proceeding.
4. Deploy the new immutable image/configuration. After any `azd provision`, redeploy the actual gateway image.
5. Confirm the intended revision is healthy, only the intended revision is active, and verifier/live smoke checks pass.
6. Resume routing and monitor checkpoint, extraction, Speech and delivery failures.

Rollback is also a maintenance operation: stop the new gateway before activating an older compatible revision/image.
Do not roll back to the removed C# gateway or code that cannot honor the current journal/schema invariants.
Keep old secrets only when an intentional compatible rollback still needs them; rotate/delete them under your policy.

## Recovery and retention

Dataverse holds transcript/extraction checkpoints and drafts. Restart restores incomplete journal work as
**ended/recovered** calls and resumes unfinished extraction/persistence. It does not restore live audio, Teams calls,
SSE replay history or deferred-function side effects.

The recovery point is the last successful Dataverse checkpoint. During a Dataverse outage, new speech remains in memory;
simultaneous process failure can lose it. Recovery time depends on service availability and backlog.
Check `checkpointVersion`, `persistenceError` and extraction status before treating a call as fully persisted/final.

Ended in-memory calls expire after one hour/500-call retention; event replay and delivery queues are bounded.
This is not Dataverse retention. Apply a coordinated policy to completed journal rows, draft transcripts, tickets/notes
and logs; recover/reconcile incomplete journals before deletion. Webhooks are best-effort, not a durable outbox.

## Live acceptance

Run in an approved test environment with the **actual gateway identity**, recording image, profile, call IDs and outcomes:

- Startup/schema/permissions verification, including both custom and standard table profiles where supported.
- Real call, read-only agent lookup, rep handoff, both speakers' notes, edit protection and human Submit.
- Repeat/concurrent Submit and response-loss retry: one ticket, no lost rep edits or incompatible links.
- Long-running Speech recognition/token renewal and recoverable stream failure.
- Model/Dataverse throttling/outage: visible failure/retry, durable checkpoint boundary and recovery after restart.
- Restart with unfinished extraction: committed notes recover without replaying functions or ticket/customer side effects.
- UI slow-consumer resync and bounded queue/load behavior.
- Maintenance deployment/rollback with no overlapping gateways and no active-call cutover.

A container build, successful deployment or `/healthz` response is **not** acceptance of those scenarios.
Track unresolved lifecycle, authorization, delivery and operational work in the [engineering backlog](19-intake-gateway-engineering-review.md).
