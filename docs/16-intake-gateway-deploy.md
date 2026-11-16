# 16 - Intake gateway: PowerShell deployment details

This guide covers the Python gateway's PowerShell path. For azd and shared setup ordering, use
[03 - Deployment](03-deployment.md); for diagnosis/recovery, use [08 - Operations](08-operations.md).

```text
Teams resource account -> ACS -> Event Grid IncomingCall
  -> /api/incoming?s=<EVENTGRID_SECRET> -> Python gateway -> Foundry
                                            |
                                            +-> Dataverse drafts/journal -> separate React UI
```

Foundry direct telephony binding is a different call path, not a second consumer for the same number.
See the [binding comparison](14-telephony-binding-comparison.md).

## Prerequisites

- PowerShell 7, Azure CLI signed in to the intended subscription.
- Existing ACS, Container Apps environment and ACR in the supported resource-group topology.
- Foundry project with **both** voice and note-taker agents published through [agent setup](17-agent-setup.md).
- Dataverse schema/journal provisioned, including draft transcript version, Active call-ID key and optimistic concurrency.
- Permission to create role assignments and register the gateway's Dataverse application user.
- For calls: configured Teams resource account/number, bot calling channel and ACS tenant consent.

The [Dataverse provisioner](../dataverse/README.md) handles schema/journal setup and upgrades. Gateway deployment
does not run it automatically. The deploying identity needs Dataverse admin access for automatic application-user
registration; the demo script assigns **System Administrator**, which must be reviewed/replaced for customer use.

## Initialize private parameters

From the repository root:

```powershell
az login
.\infra\init-gateway-params.ps1
```

The initializer discovers Azure resources and writes `infra\gateway.<name>.json`. Select the actual agents and supply
the Dataverse URL. If initialization leaves an agent/Dataverse field empty, complete it before deployment:
production voice mode requires `extractorAgentName` and `dataverseUrl`, not just the voice agent.

For unattended initialization:

```powershell
.\infra\init-gateway-params.ps1 -NonInteractive -Name my-gateway `
  -AcsResourceName <acs> -FoundryAccount <account> -FoundryProject <project> `
  -VoiceAgent <voice-agent> -ExtractorAgent <note-taker> `
  -DataverseUrl https://<org>.crm.dynamics.com `
  -CorsOrigins https://<ui-host> `
  -AfterHours auto -ServiceTz America/Chicago -ServiceDays Mon-Fri -ServiceHours 07:00-18:00 `
  -PhoneNumber +15551234567 -ResourceAccountObjectId <guid>
```

Alternatively copy `infra\gateway.parameters.example.json` and edit it. These files can contain credentials and are
git-ignored; never commit/share them. New Speech settings use managed identity, not a retrieved account key.
The initializer prompts for office-hours settings, or accepts the non-interactive arguments above, and writes
explicit `auto` defaults. Review them for the service location. Legacy parameter files without these fields retain
existing app settings. See [automatic office hours](04-configuration.md#automatic-office-hours) for overrides,
timezone/daylight-saving handling and the per-call behavior at closing.

## Check and deploy

For an existing deployment, first follow the [maintenance procedure](08-operations.md#safe-upgrades-and-rollback):
pause routing, finish calls and stop old gateways. Min/max replicas of one do not prevent revision overlap.

```powershell
.\infra\deploy-gateway.ps1 -ParametersFile .\infra\gateway.my-gateway.json -Plan
.\infra\deploy-gateway.ps1 -ParametersFile .\infra\gateway.my-gateway.json -Rebuild
```

`-Plan` performs parameter/resource/Speech preflight. It is not proof of Dataverse runtime permissions, schema readiness
or a working call. Startup and the verifier provide further gates.

The deployment:

1. Reuses existing API/HMAC secrets or generates missing ones; reads the ACS connection string/immutable ID.
2. Builds Python in ACR, or selects an existing image. Use `-Rebuild` for current source or `-UseImage <tag>` for an existing tag.
3. Creates/updates the app with managed identity, secret references, current environment names and min/max replicas of one.
4. Grants ACR/Foundry access and the Speech role when AAD live notes are enabled; registers the gateway Dataverse app user.
5. Sends the shared table config, derives `PUBLIC_BASE_URL`, and includes the Teams resource-account identity in `LINE_NUMBERS`.
6. Waits for health, wires authenticated Event Grid delivery, checks the handshake and runs the verifier.

The interactive image menu is bounded to recent tags; an older explicit tag may need separate registry verification
until the image-selection backlog is resolved. Record immutable image identity for releases.
`/healthz` can be served by an old revision, so independently confirm the intended new image/revision.
Neither script implements automatic call draining, comprehensive native-command failure gating or safe concurrent ownership.

### Parameters

| Field | Requirement / purpose |
|---|---|
| `subscriptionId`, `resourceGroup`, `appName` | Required deployment target |
| `acrName`, `environmentName`, `acsResourceName` | Required infrastructure names |
| `foundryProjectEndpoint`, `voiceAgentName`, `extractorAgentName` | Required actual project/agent settings |
| `dataverseUrl` | Required production organization URL |
| `dataverseSchema` | Optional object override; otherwise the deployer reads `dataverse\config.json` |
| `resourceAccountObjectId`, `lineNumbers` | Incoming identity allowlist; Teams may send `28:orgid:<guid>`, not E.164 |
| `transferTeamsObjectIds` | Rep Entra IDs |
| `corsOrigins` | Exact origins of separately hosted Intake UI clients |
| `liveNotes`, `enrichment`, `brandName` | Optional feature/branding settings |
| `afterHours` | `auto` in new files; `on`/`off` force a mode; omitted legacy settings are not deployed |
| `serviceTz`, `serviceDays`, `serviceHours` | Complete shared daily schedule required with explicit `auto`; defaults/examples are `America/Chicago`, `Mon-Fri`, `07:00-18:00` |
| `speechAuth` | `aad` for new files; legacy files with only `speechKey` retain key mode |
| `speechResourceId` | Full ARM ID; empty selects the discovered Foundry account |
| `speechRegion` | Derived/checked against the AAD account, or supplied for key mode |
| `speechKey` | Legacy key mode only; ignored with explicit AAD |
| `apiKeys` | Optional override; otherwise reuse/generate |

## Migrate live notes to managed identity

Set `speechAuth` to `aad`. Select `speechResourceId`, or leave it empty to use the account discovered from the Foundry
endpoint. It must support Speech and already have a custom subdomain. Creating a custom subdomain is irreversible;
preflight fails with instructions rather than changing an existing account automatically.

Run `-Plan`, then deploy in maintenance. The deploying identity must be allowed to grant
**Cognitive Services Speech User** on that account. Deployment sets `SPEECH_AUTH=aad`, `SPEECH_RESOURCE_ID`,
the actual `SPEECH_REGION`, and clears `SPEECH_KEY` in the new revision's environment.

An unused `speechKey` in an old private JSON can be removed; it is not uploaded in AAD mode. Old Container App secrets
are retained while older revisions may reference them. Delete the obsolete secret only after retiring those revisions
and deciding rollback requirements. Do not disable account-wide key authentication as part of this migration.

The verifier checks resource kind/region/custom subdomain, identity role and the running transcriber flag.
A real transferred call and long-running token-renewal scenario are still required to prove authentication/audio.

## Verify and connect the UI

```powershell
.\infra\verify-gateway.ps1 -ResourceGroup <rg> -AppName <app>
az containerapp revision list -g <rg> -n <app> -o table
az containerapp logs show -g <rg> -n <app> --tail 100 --format text
```

The verifier checks ingress, scale, required settings, health, Speech, office-hours configuration/runtime agreement,
the ignored `AFTER_HOUR` typo, Event Grid secret/filter and recent error signals.
It is read-only and exits nonzero on failures; a clean result is not a full acceptance test.
Build/host the [React UI](07-rep-console.md), allow its origin and connect using the gateway URL/API key.
There is no gateway-hosted `/rep` console.

## Authentication boundaries

| Credential | Used for |
|---|---|
| `API_KEYS` | All consumer `/v1` REST/SSE routes |
| `EVENTGRID_SECRET` | Incoming-call webhook; must match app and subscription URL/header |
| `GATEWAY_SECRET` | Per-call signed callback/media URLs |
| ACS JWT / immutable resource ID | ACS-signed callback/media authentication; `ACS_JWT_MODE=enforce` outside local dev |
| ACS connection string | Gateway-to-ACS operations in this deployer |
| Managed identity | Foundry, Dataverse and AAD Speech; each needs its own permissions |

Rerunning deployment **reuses** Event Grid/gateway secrets; it is not a rotation operation.
Coordinated rotation remains engineering work. Do not rotate a callback HMAC secret mid-call or update only one copy
of the Event Grid secret. URL query secrets must be kept out of logs/screenshots.

For failure triage and live acceptance, use [operations](08-operations.md#live-acceptance).
Offline checks: `infra\tests\after-hours.Tests.ps1`, `infra\tests\speech-auth.Tests.ps1`, `infra\tests\setup-tpe-azure.Tests.ps1` and
`dataverse\tests\provision-journal.Tests.ps1`.
