# 16. Intake gateway: package, deploy, troubleshoot

The intake gateway (`src\intake-gateway`, Python 3.12, FastAPI) is the **Option A** call path:

```
Teams number -> resource account -> ACS -> Event Grid (IncomingCall)
   -> POST /api/incoming?s=<EVENTGRID_SECRET> -> gateway -> Foundry voice agent -> Dataverse
```

Option B (Foundry direct binding) does not use the gateway. See [14](14-telephony-binding-comparison.md) and [15](15-telephony-binding-runbook.md).

## Quick start (no Azure experience needed)

You need: a Windows, macOS or Linux machine with [PowerShell 7](https://aka.ms/powershell) and the [Azure CLI](https://aka.ms/installazurecli), and an Azure account with **Contributor** on the resource group you will use. Teams, the bot and Entra are assumed done.

1. **Sign in:** `az login`
2. **Get the repo** and open a terminal in its root.
3. **Create your settings file (no typing of IDs):** `.\infra\init-gateway-params.ps1 -Name mycompany`. It reads your Azure account and shows numbered lists of your ACS resources, Container Apps environments, registries, Foundry projects and agents. It derives the resource group from the ACS resource, reads the Speech key, checks the Teams consent, and writes `infra\gateway.mycompany.json`. Only the Teams resource account Object ID is asked for (`(Get-CsOnlineApplicationInstance -Identity <upn>).ObjectId`); press Enter to skip. For unattended use: `-NonInteractive -AcsResourceName <n> -FoundryProject <p> -VoiceAgent <a>`.
   Secrets (`EVENTGRID_SECRET`, `GATEWAY_SECRET`, `API_KEYS`) are never typed: the deploy script generates them and stores them as Container App secrets.
4. **Check before deploying (changes nothing):**
   `.\infra\deploy-gateway.ps1 -ParametersFile infra\gateway.mycompany.json -Plan`
   Every line should be `[ OK ]`. A `[FAIL]` line prints how to fix it.
5. **Deploy:** `.\infra\deploy-gateway.ps1 -ParametersFile infra\gateway.mycompany.json`
   Takes about 10 minutes. It ends with `Result: 0 failure(s)`.
6. **Call the Teams number.** Watch: `az containerapp logs show -g <rg> -n <app> --tail 100 --format text`. You should see `event received type=Microsoft.Communication.IncomingCall`.
7. **Something wrong?** Run `.\infra\verify-gateway.ps1 -ResourceGroup <rg> -AppName <app>` and follow section 7.

Safe to re-run any time: it keeps existing secrets, so Event Grid stays connected. Never share or commit your settings file.

## 1. Prerequisites

Assumes the Teams side (number, resource account, ACS Teams extension consent) is already done and verified. See `infra\verify-bot.ps1` and `infra\grant-acs-consent.ps1` (they hold tenant specific defaults; do not commit real IDs).

| Need | Notes |
|---|---|
| Azure CLI, signed in | `az login`; Contributor on the resource group |
| Container Apps environment | Existing; the script does not create it |
| ACR | Image build via `az acr build` (no local Docker needed) |
| ACS resource | In the same resource group as the Event Grid system topic |
| Foundry project + voice agent | `FOUNDRY_PROJECT_ENDPOINT`, `VOICE_AGENT_NAME` |
| PowerShell 7 (`pwsh`) | For the scripts |

## 2. Package

The image is built from `src\intake-gateway\Dockerfile` (`python:3.12-slim`, uvicorn on `PORT` 8080). Speech SDK dependencies are optional via `--build-arg WITH_SPEECH=1` (live notes).

```
az acr build -r <acr> -t intake-gateway:<tag> src\intake-gateway --no-logs
```

Tag every build (`vN` or a timestamp). Never deploy `latest`; a stable tag hides which revision is running.

## 3. Deploy (one command)

```
Copy-Item infra\gateway.parameters.example.json infra\gateway.<customer>.json   # edit, keep out of git
.\infra\deploy-gateway.ps1 -ParametersFile infra\gateway.<customer>.json
```

Check first without changing anything: add `-Plan`. It validates login, subscription, resource group, ACR, environment, ACS and Foundry account, and prints how to fix anything missing.

The script is idempotent. It:

1. Reuses secrets from the running app (so the Event Grid URL stays valid) or generates `GATEWAY_SECRET`, `EVENTGRID_SECRET`, `API_KEYS` (cryptographic random).
2. Reads the ACS connection string and immutable resource id from the ACS resource.
3. Builds and pushes the image (skip with `-SkipBuild -ImageTag <existing>`).
4. Creates or updates the Container App: external ingress, port 8080, `minReplicas=1`, secrets as `secretref:`, system managed identity.
5. Grants that identity `AcrPull` on the registry and `Azure AI User` on the Foundry account (needed to call the voice agent).
6. Derives `PUBLIC_BASE_URL` from the app FQDN and adds `28:orgid:<resourceAccountObjectId>` to `LINE_NUMBERS`.
7. Waits for `/healthz` = `ok` **before** touching Event Grid (Event Grid validates the endpoint on creation).
8. Creates or updates the `gateway-incoming` subscription to `https://<fqdn>/api/incoming?s=<EVENTGRID_SECRET>`, filtered to `Microsoft.Communication.IncomingCall`. Warns if another subscription on the same topic also takes IncomingCall (two consumers would both try to answer).
9. Smoke-tests: a wrong secret must return 401, and the right secret must pass the validation handshake.
10. Runs the verifier.

Status: tested twice against a real subscription (a from-scratch create of a new app, then an idempotent re-run), both with 0 failures. A real phone call is the final proof. Each run took 10+ minutes on a slow `az` connection; most of that is the CLI, not the app.

### Parameters

| Field | Required | Meaning |
|---|---|---|
| `subscriptionId`, `resourceGroup`, `appName` | yes | Target |
| `acrName`, `environmentName` | yes | Registry and Container Apps environment |
| `acsResourceName` | yes | ACS resource that receives the calls |
| `foundryProjectEndpoint`, `voiceAgentName` | yes | Foundry voice agent |
| `extractorAgentName` | no | Structured extraction agent |
| `resourceAccountObjectId` | no | Teams resource account object id; added to `LINE_NUMBERS` as `28:orgid:<id>` |
| `lineNumbers` | no | Allow-list; see pitfall below |
| `transferTeamsObjectIds` | no | Entra object ids of transfer targets |
| `corsOrigins`, `liveNotes`, `enrichment` | no | UI and feature toggles |
| `speechRegion`, `speechKey` | no | Live notes (STT) |
| `apiKeys` | no | Generated if blank |

### Environment variables

Required (startup fails, the revision crash-loops, and no event is ever delivered if any is missing): `PUBLIC_BASE_URL`, `FOUNDRY_PROJECT_ENDPOINT`, `VOICE_AGENT_NAME`, `API_KEYS`, `GATEWAY_SECRET`, `EVENTGRID_SECRET`, `ACS_RESOURCE_ID`, and `ACS_JWT_MODE=enforce`. Strongly recommended: `ACS_CONNECTION_STRING`. The full list with defaults is `src\intake-gateway\.env.example`; the loader is `app\config.py`, validation is `app\validate.py`.

## 4. Verify

```
.\infra\verify-gateway.ps1 -ResourceGroup <rg> -AppName <app>
```

Read-only. Prints `[ OK ]`, `[WARN]`, `[FAIL]` and exits non-zero on failure. It checks, in order:

- Container App found, ingress external, target port 8080, `minReplicas`, running status.
- Required env var **names** are present (values are never printed) and `PUBLIC_BASE_URL` equals the FQDN.
- `ACS_JWT_MODE` and `LOCAL_DEV` are safe for production.
- `LINE_NUMBERS` set (warns: drops are silent).
- `GET /healthz` returns `ok`.
- Every Event Grid subscription targeting this FQDN: provisioning state, `IncomingCall` filter, `/api/incoming` path, `?s=` equals `EVENTGRID_SECRET`, dead-letter configured.
- Recent logs for `incoming rejected`, `incoming call ignored`, `Traceback`.

A real end to end check is a test call, then `az containerapp logs show -g <rg> -n <app> --tail 100` and look for the IncomingCall event followed by the answer.

## 5. Troubleshooting: "Event Grid shows calls, the container is never invoked"

Work top to bottom; stop at the first hit.

| # | Symptom / evidence | Cause | Fix |
|---|---|---|---|
| 1 | `/healthz` is `degraded` or unreachable; revision restarting | Missing required env var; startup raises `RuntimeError` | Set the variable (`az containerapp update --set-env-vars`), check `az containerapp logs show` for the config error |
| 2 | Event Grid metrics: delivery failures, HTTP 401 | `?s=` missing or different from `EVENTGRID_SECRET`; log says `incoming rejected: bad event grid secret` | Recreate the subscription with the current secret (rerun `deploy-gateway.ps1`) |
| 3 | Subscription `provisioningState` is `Failed` / `AwaitingManualAction` | Endpoint was not healthy when validation ran, or wrong URL | Fix the app, then recreate the subscription |
| 4 | Event Grid delivers, HTTP 200, but no call is answered. Log says `incoming call ignored: called=... is not in LINE_NUMBERS` | Allow-list drops the call silently. Teams resource accounts arrive as `28:orgid:<oid>`, **not** a phone number | Add `28:orgid:<oid>` to `LINE_NUMBERS` or clear it |
| 5 | Calls hit an old host | Stale subscription endpoint, or `PUBLIC_BASE_URL` differs from the FQDN (callbacks and `/ws` go elsewhere) | Rerun deploy; verifier flags the mismatch |
| 6 | First call after idle fails | Cold start exceeds Event Grid timeout (`minReplicas=0`) | `minReplicas=1` |
| 7 | Event Grid shows no calls at all | The subscription is on the wrong system topic (different ACS resource or resource group), or the number is not routed to this ACS | Verifier lists subscriptions that target this app; if none, check the ACS side and [15](15-telephony-binding-runbook.md) |
| 8 | Call answered then drops; ACS callbacks rejected | `ACS_RESOURCE_ID` is not the ACS **immutable** resource id (JWT audience), or JWT mode not `enforce` | Use `az communication show ... --query immutableResourceId` |
| 9 | Calls to the resource account never reach ACS | Teams side: consent not granted | `infra\grant-acs-consent.ps1`; the assignment must return 200 on GET |

Useful commands:

```
az containerapp show -g <rg> -n <app> --query "properties.{rev:latestRevisionName,state:runningStatus}"
az containerapp revision list -g <rg> -n <app> -o table
az containerapp logs show -g <rg> -n <app> --tail 100 --format text
az eventgrid system-topic event-subscription list -g <rg> --system-topic-name <topic> -o table
az eventgrid system-topic event-subscription show -g <rg> --system-topic-name <topic> -n gateway-incoming --include-full-endpoint-url
```

Production logs are also in Log Analytics (`ContainerAppConsoleLogs_CL`, UTC).

## 6. Authentication and secrets

Five separate credentials are involved. Most Option A failures are one of these not lining up.

| Credential | Protects | Who presents it | Who checks it | Where it lives |
|---|---|---|---|---|
| `EVENTGRID_SECRET` | `POST /api/incoming` | Event Grid, as `?s=<secret>` in the subscription URL (or an `aeg-sas-key` header) | Gateway, constant-time compare (`security.check_shared_secret`) | Container App secret `eventgrid-secret`; **also embedded in the Event Grid subscription URL** |
| `GATEWAY_SECRET` | `POST /api/callbacks/{callId}` and `WS /ws/{callId}` | The gateway itself: it signs a per-call HMAC token (`call_token`) and puts it in the callback and media URLs it hands to ACS | Gateway, when ACS calls back | Container App secret `gateway-secret` |
| ACS JWT (`ACS_RESOURCE_ID`, `ACS_JWT_MODE=enforce`) | Callbacks and the media WebSocket | ACS Call Automation, as `Authorization: Bearer <jwt>` (issuer `https://acscallautomation.communication.azure.com`, keys at `/calling/keys`) | Gateway; audience must equal the ACS **immutable resource id** | Env var `ACS_RESOURCE_ID` |
| `API_KEYS` | Consumer API `/v1/*` | Rep console / integrations: `X-Api-Key`, `Bearer`, or `?key=` | Gateway | Container App secret `api-keys` |
| `ACS_CONNECTION_STRING` | Gateway to ACS (answer, transfer) | Gateway | ACS | Container App secret `acs-connection-string` |

Foundry access uses the app's managed identity (or `az login` when `LOCAL_DEV=1`), not a secret.

### The Event Grid handshake, step by step

1. Creating the subscription makes Event Grid POST a `SubscriptionValidationEvent` to `/api/incoming?s=...`. The gateway must be **up and healthy** and the secret must match, or creation fails or is stuck `AwaitingManualAction`. The gateway echoes `validationResponse`.
2. For each incoming call Event Grid POSTs an `IncomingCall` event to the same URL. The gateway checks the secret, de-duplicates by event id, and answers via ACS using `incomingCallContext`.
3. The gateway answers ACS with callback and media URLs that carry the HMAC call token. ACS then calls back with its JWT.

Consequences:

- **The secret exists in two places** (the app and the subscription URL). If either is rotated alone, every delivery is rejected. Always rotate by rerunning `deploy-gateway.ps1`, which rewrites both.
- Auth failures on `/api/incoming` return **401 and are logged only as `incoming rejected: bad event grid secret`**. Event Grid then retries with backoff and eventually drops the event (or dead-letters it). The app otherwise looks idle.
- Unlisted callers are different: `LINE_NUMBERS` drops return **200**, so Event Grid considers them delivered. Look for `incoming call ignored`.
- Event Grid only reaches an externally reachable HTTPS endpoint. Internal ingress, IP restrictions, or a custom domain without a valid certificate all look like "Event Grid failed, container saw nothing".

Compare the two copies without printing them in full:

```
$app = az containerapp secret list -g <rg> -n <app> --show-values --query "[?name=='eventgrid-secret'].value | [0]" -o tsv
$url = az eventgrid system-topic event-subscription show -g <rg> --system-topic-name <topic> -n gateway-incoming --include-full-endpoint-url --query destination.endpointUrl -o tsv
"match: " + ($url -match [regex]::Escape("s=$app"))
```

`verify-gateway.ps1` does this check for you.

## 7. Troubleshooting approach: "events on the grid, nothing in the container"

The key question is **where the event stops**. Walk the chain and use the evidence at each hop to decide where to look. Do not change settings before you know which hop fails.

```mermaid
flowchart LR
  A[ACS IncomingCall] --> B[Event Grid topic]
  B -->|1 delivered?| C[Subscription]
  C -->|2 HTTP status| D[Container App ingress]
  D -->|3 request reaches app?| E[/api/incoming]
  E -->|4 auth + filters| F[Answer call]
  F -->|5 callbacks + JWT| G[Media WebSocket]
```

**Step 0. Run the verifier.** `.\infra\verify-gateway.ps1 -ResourceGroup <rg> -AppName <app>`. It covers hops 2 to 4 for the common causes in one pass. Continue below only if it is clean or the failure is unclear.

**Step 1. Is Event Grid delivering, and with what result?** Azure portal: Event Grid system topic, then the subscription, then Metrics. Compare *Published events*, *Delivery Succeeded*, *Delivery Failed*, and *Dropped Events* (filter by `DeliveryFailure` and `HttpStatusCode`/`DropReason`). CLI:

```
az monitor metrics list --resource <system-topic-id> --metric DeliveryAttemptFailCount,DeliverySuccessCount,DroppedEventCount --interval PT5M --offset 1h -o table
```

| Event Grid shows | Meaning | Go to |
|---|---|---|
| Delivery Succeeded, but no `event received` log | You are looking at the wrong app, revision or log stream, or the app answered 200 and filtered the call | Step 3 and the `LINE_NUMBERS` row below |
| Failed with 401 | Secret mismatch | Compare the two copies (section 6); redeploy |
| Failed with 404 | Wrong path (not `/api/incoming`) or wrong host | Fix the subscription URL |
| Failed with 5xx, timeouts, connection errors | App unhealthy, cold, crash-looping, or ingress not reachable | Step 2 |
| Dropped events (max attempts / TTL) | Failures persisted long enough to exhaust retries | Fix the cause, then place a new call; add a dead-letter destination to keep payloads |
| Nothing published | Not an app problem: the call never reached ACS or this topic | Check ACS routing and consent (docs 15) |

**Step 2. Is the app healthy and reachable from outside?**

```
curl https://<fqdn>/healthz                 # ok / degraded (+errors) / no answer
az containerapp revision list -g <rg> -n <app> -o table     # active, healthy, replicas
az containerapp logs show -g <rg> -n <app> --type system --tail 50   # crash loops, probe failures, image pull errors
```

`degraded` or a crash loop means a missing required variable (the app refuses to start). The message names the variable; set it and redeploy. An image pull error means the app identity lacks `AcrPull` on the registry.

**Step 3. Did the request reach the app?** Read the console log around the time of the test call (UTC):

```
az containerapp logs show -g <rg> -n <app> --tail 200 --format text
```

or in Log Analytics:

```
ContainerAppConsoleLogs_CL
| where ContainerAppName_s == "<app>" and TimeGenerated > ago(30m)
| where Log_s has_any ("incoming", "event received", "rejected", "ignored", "Traceback", "ACS JWT")
| project TimeGenerated, Log_s | order by TimeGenerated desc
```

Also look at the ingress side (`ContainerAppSystemLogs_CL`) and the revision's replica count. If console logs are empty yet Event Grid says 200, check you are reading the right app, revision and time window; a sibling app, an old revision, or a different environment will make a healthy gateway look silent.

| Log line | Meaning | Fix |
|---|---|---|
| nothing at all for the test time | Request never arrived (hop 2 failure) | Step 1 and 2 |
| `incoming rejected: bad event grid secret` | Missing or different `?s=` | Redeploy to resync |
| `event received type=...IncomingCall` then `incoming call ignored: called=...` | `LINE_NUMBERS` does not contain the dialed identity. Teams resource accounts arrive as `28:orgid:<oid>` | Add it or clear `LINE_NUMBERS` |
| `incoming call but ACS not configured` | `ACS_CONNECTION_STRING` missing | Set it |
| `incoming call rejected: at capacity` | `MAX_CONCURRENT_CALLS` reached | Raise it or find stuck calls via `/v1/calls` |
| `incoming event without incomingCallContext` | Event is not a real IncomingCall (test event) | Ignore |
| `ACS JWT invalid on ... (InvalidAudienceError)` | `ACS_RESOURCE_ID` is not the immutable resource id | Use `az communication show ... --query immutableResourceId` |
| `ACS JWT invalid ... (missing bearer token)` | Callback not from ACS, or a proxy strips `Authorization` | Check ingress path; do not downgrade to `off` |
| Event received, answer fails with an ACS error | Connection string belongs to a different ACS resource than the one that raised the event, or Teams consent is missing | Verify ACS name in the connection string vs the topic source; run `grant-acs-consent.ps1` |

**Step 4. Reproduce on demand without a phone.** Send a synthetic request to isolate auth from the rest. Use a throwaway body (it will be ignored as not a real call):

```
curl -i -X POST "https://<fqdn>/api/incoming?s=WRONG" -H "Content-Type: application/json" -d "[]"   # expect 401
curl -i -X POST "https://<fqdn>/api/incoming?s=<real>" -H "Content-Type: application/json" -d "[]"  # expect 200
```

A 401 then a 2xx (the 401 was confirmed against the live gateway; the empty-list 2xx is expected from the handler but not run) proves ingress, routing and the secret check. A timeout on both proves a reachability or health problem. Use the `?s=` value from the app secret, and avoid pasting it into shared logs or tickets.

**Step 5. Still nothing?** Capture and send: verifier output, the Event Grid metrics screenshot with HTTP codes, 30 minutes of console and system logs around a test call, the revision list, and the gateway version tag. Do not send secret values.

### Decision summary for "events on the grid, nothing in the container"

1. Verifier clean and `/healthz` ok: the app is fine; the problem is the event (filtered, wrong topic) or you are reading the wrong log. Check the Event Grid HTTP status first.
2. 401s: the two secret copies differ. Redeploy.
3. 5xx or timeouts: health, cold start, or ingress. Check system logs and `minReplicas`.
4. 200 with no answer: `LINE_NUMBERS`, capacity, or an ACS answer error. Check console logs.
5. No attempts at all: the subscription is on the wrong topic or the call never reached ACS.

## 8. Security notes

- Secrets live in Container App secrets, referenced with `secretref:`. Never put them in the parameters example or git.
- `infra\gateway.<customer>.json`, `files\gateway-secrets*`, and any script containing real tenant, object or subscription ids must stay uncommitted.
- Event Grid authenticates to the gateway with a shared secret in the query string. Entra-authenticated delivery is a possible hardening step. See [09](09-security.md).
- No dead-letter destination means failed deliveries vanish after retries; configure one on production subscriptions.

## 9. Known gaps

- No `/readyz` or startup config banner; use `/healthz` and the verifier.
- The ACS Teams extension consent is a separate script and is not part of the deploy.

