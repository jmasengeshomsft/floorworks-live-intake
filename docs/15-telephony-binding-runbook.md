# 15 - Telephony binding runbook (Teams number -> bot app -> ACS -> Event Grid -> gateway)

Step-by-step to recreate the link in a new tenant (e.g. Deere). Scripts: `teams\rebind-tpe.ps1` (Teams side, PowerShell only) and `infra\rebind-tpe-azure.ps1` (Azure side).
Background: `13-telephony-wiring.md`, `14-telephony-binding-comparison.md`.

```
Caller -> Teams number -> Resource account (app instance)
            ApplicationId = Entra app (tpe-live-intake-bot)
            AcsResourceId = ACS immutable id
       -> ACS (teamsExtension assignment for the resource account OID)
       -> IncomingCall -> Event Grid system topic -> webhook -> gateway /api/incoming
```

## What you need and where to find it

| Item | Requirement | Where to find it |
|---|---|---|
| Entra app registration | single-tenant, with a service principal; its AppId = `BotAppId` | Entra admin center > App registrations > Overview |
| Azure Bot | `msaAppId` = the AppId; Teams channel with calling enabled; calling webhook = gateway | Azure portal > Azure Bot > Channels |
| ACS resource | same tenant; ACS first-party app `1fd5118e-2576-4263-8130-9503064c837a` present | Azure portal > ACS |
| `AcsImmutableId` | ACS "immutable resource ID" | ACS > Overview/Properties, or `immutableResourceId` via `az rest` |
| `AcsHost` | `<name>.communication.azure.com` | ACS > Keys/Endpoint |
| `TenantId` | directory id | Entra > Overview |
| Resource account | application instance: ApplicationId = AppId, AcsResourceId = immutable id | `Get-CsOnlineApplicationInstance`; Entra > Users (`TPE_RESOURCE_ACCOUNT_OID`) |
| Resource account license | Teams Phone Resource Account (`PHONESYSTEM_VIRTUALUSER`, free); usage location set first | M365 admin center > Billing > Licenses |
| Rep license | Teams Phone Standard or a SKU with `MCOEV`; Enterprise Voice on | M365 admin center > Licenses |
| `RepObjectIds` | user object id (not app/client id) | Entra > Users > Object ID |
| Phone number | Calling Plan, Operator Connect or Direct Routing; must be a service (voice application) number | Teams admin center > Voice > Phone numbers |
| Event Grid | `IncomingCall` webhook on the ACS system topic -> gateway `/api/incoming` | Azure portal > ACS > Events |

## Step 0 - Preflight (run before anything else)

`teams\preflight-teams.ps1` is read-only. It signs in once (device code as Teams admin, plus `az login` in the same tenant) and prints PASS / WARN / FAIL with a fix hint per check. It exits 1 on any FAIL, so you can gate on it.

```powershell
.\teams\preflight-teams.ps1 -AcsName <acs-name> -ResourceGroup <rg>   # ACS args optional
```

| Area | Checks | FAIL means |
|---|---|---|
| Config/tooling | `teams\.env` keys, E.164 number, MicrosoftTeams module, `az` in the right tenant | fix before continuing |
| License | `PHONESYSTEM_VIRTUALUSER` SKU exists (free units); a SKU with `MCOEV` for reps | buy/assign the license |
| Number | exists, type CallingPlan/OperatorConnect/DirectRouting, supports voice applications (service number), activated, current assignment | wrong number type, not acquired |
| Resource account | exists, ApplicationId vs `BotAppId`, AcsResourceId vs `AcsImmutableId`, usage location, `PHONESYSTEM_VIRTUALUSER` license | number assignment will fail without the license |
| Reps | in Entra, `MCOEV` license, Enterprise Voice | EV cannot be enabled without the license |
| ACS | immutable id and host match `.env`; ACS first-party app exists in the tenant | wrong values in `.env` |

WARN items (account missing, stale ApplicationId, number on another target, EV off) are fixed by the steps below. Re-run after each Teams change; a new license can take up to an hour to reach Teams.

## What the runbook does, step by step (illustration)

Each step adds one link (five links in total: resource account, Entra app, Azure Bot, ACS assignment, Event Grid). After step 6 a call flows end to end.

**Step 1 - Create the Entra app and the Azure Bot (`CreateApp`).** The app gives Teams a valid third-party ApplicationId; the bot with the same `msaAppId` lets Teams resolve it. Nothing is connected yet.
```
[Entra app: tpe-live-intake-bot] <--msaAppId-- [Azure Bot, Teams calling on]   (no secrets, no code)
```
```powershell
$appId = az ad app create --display-name tpe-live-intake-bot --sign-in-audience AzureADMyOrg --query appId -o tsv
az ad sp create --id $appId          # service principal so the tenant can use the app
az bot create -g $rg -n tpe-live-intake-bot --appid $appId --app-type SingleTenant `  --tenant-id $tenant --sku F0 --location global
az bot msteams create -g $rg -n tpe-live-intake-bot --enable-calling true --calling-web-hook $callingWebhook
```

**Step 2 - Create the resource account** (first time only). Teams-side identity that will own the number, tied to the app.
```
[Resource account: TPE Live Intake] --ApplicationId--> [Entra app]
```
```powershell
Connect-MicrosoftTeams -UseDeviceAuthentication -TenantId $tenant
New-CsOnlineApplicationInstance -UserPrincipalName tpeintake@contoso.com `
  -DisplayName "TPE Live Intake" -ApplicationId $appId
# Then (Entra/M365 admin): set usage location + assign the PHONESYSTEM_VIRTUALUSER license
```

**Step 3 - Teams bind (`Rebind`).** Tells Teams which ACS resource receives calls for this account. This is half of the link.
```
[Resource account] --ApplicationId--> [Entra app]
        |
        +--AcsResourceId--> [ACS resource]      (Teams-side record)
```
```powershell
$oid = (Get-CsOnlineApplicationInstance | ? UserPrincipalName -eq $upn).ObjectId
# AcsResourceId = the ACS "immutable resource id" (ACS portal > Overview / Properties)
Set-CsOnlineApplicationInstance -Identity $oid -ApplicationId $appId -AcsResourceId $acsImmutableId
Sync-CsOnlineApplicationInstance -ObjectId $oid -ApplicationId $appId
Get-CsOnlineApplicationInstance -Identity $oid | fl ApplicationId, AcsResourceId   # verify
```

**Step 4 - Assign the phone number** (first time only). Calls to the number now route to the resource account.
```
+1 630 735 1758 ---> [Resource account] ---> [ACS resource]
```
```powershell
Set-CsPhoneNumberAssignment -Identity $oid -PhoneNumber "+16307351758" -PhoneNumberType CallingPlan
# Other types: OperatorConnect, DirectRouting
Get-CsPhoneNumberAssignment -TelephoneNumber "+16307351758"                       # verify
```

**Step 5 - ACS assignment (`Assign`).** ACS records that it accepts calls for this resource account. This is the other half of the link, and what the ACS portal does not show.
```
[ACS resource] <--teamsExtension assignment (oid, teamsResourceAccount)-- [Resource account]
```
```powershell
# ACS first-party app must exist in the tenant
az ad sp create --id 1fd5118e-2576-4263-8130-9503064c837a 2>$null
$tok = az account get-access-token --resource https://communication.azure.com --query accessToken -o tsv
$uri = "https://$acsHost/access/teamsExtension/tenants/$tenant/assignments/$oid`?api-version=2025-06-30"
Invoke-RestMethod -Method Put -Uri $uri -Headers @{ Authorization = "Bearer $tok" } `
  -ContentType application/json -Body '{"principalType":"teamsResourceAccount"}'
Invoke-RestMethod -Uri $uri -Headers @{ Authorization = "Bearer $tok" }            # verify (GET)
```
Both halves now exist, so ACS can raise `IncomingCall` for the number.

**Step 6 - Event Grid (`Subscribe`).** Delivers ACS's `IncomingCall` event to your gateway.
```
[ACS] --IncomingCall--> [Event Grid system topic] --webhook--> [Gateway /api/incoming]
```
```powershell
az eventgrid system-topic event-subscription create -g $rg --system-topic-name $topic -n audio-probe-incoming `
  --endpoint "https://$gatewayFqdn/api/incoming" --endpoint-type webhook `
  --included-event-types Microsoft.Communication.IncomingCall
# Event Grid validates the webhook handshake; the gateway must be up and public first.
```

**Securing the endpoints (recommended).** Set these gateway env vars (values from your secret store; never commit them):

| Var | Effect |
|---|---|
| `EVENTGRID_SECRET` | `/api/incoming` requires `?s=<secret>` (or `aeg-sas-key` header). Append it to the subscription endpoint: `--endpoint "https://$gatewayFqdn/api/incoming?s=<EVENTGRID_SECRET>"`. Set the secret and update the subscription together, or deliveries get 401. |
| `GATEWAY_SECRET` | Per-call HMAC token (`?t=`) is added to the ACS callback and media WebSocket URLs and required on `/api/callbacks/{id}` and `/ws/{id}`. |
| `LINE_NUMBERS` | Comma-separated E.164 numbers this gateway serves; calls to other numbers are ignored. |
| `ACS_JWT_MODE` / `ACS_RESOURCE_ID` | Validates the ACS-signed JWT (issuer `https://acscallautomation.communication.azure.com`, audience = ACS immutable resource ID from `az communication show`) on `/api/callbacks` and `/ws`. `off` (default) → `warn` (log failures) → `enforce` (401 / close 1008). Roll out in `warn`, place a test call on each line, confirm no `ACS JWT invalid` log lines, then switch to `enforce`. Outside `LOCAL_DEV` the verifier is always `enforce` and startup fails otherwise; after any deploy check the logs for "configuration validated" (a crashed revision leaves the old one serving `/healthz`). |
| `LINE_NUMBERS` (Teams-routed calls) | Teams-routed calls arrive with `to=28:orgid:<resource-account-guid>`, so include those IDs as well as the E.164 number. |
| Timeline logs | Each call logs `timeline:` lines (connected, first agent audio, transfer started, rep joined, ended, final-extraction duration) as seconds since call start. v9 also extends ringback to 120 s so there is no dead air while waiting for the rep. |
| `WEBHOOK_SECRET` | Signs outbound webhooks (`WEBHOOK_URLS`, function webhooks). Webhook URLs must be `https` and resolve to public addresses (SSRF guard); redirects are not followed and function responses over 256 KB are rejected. `LOCAL_DEV=1` relaxes the URL rules. |

Unset = not enforced (a startup warning is logged). `/ws` never creates call state, call ids are 128-bit random, and WebSocket frames are capped at 1 MiB.

**Result - one call, end to end**
```
Caller
  |  dials +1 630 735 1758
  v
Teams (number -> resource account; ApplicationId + AcsResourceId)
  |
  v
ACS (teamsExtension assignment matches the account)
  |  IncomingCall
  v
Event Grid  --webhook-->  Gateway /api/incoming  --> answer call, audio -> agent
```

**What each failure looks like**
| Missing | Symptom |
|---|---|
| Step 1 (Azure Bot missing or bound to an old AppId) | Caller hears "temporarily unavailable" (SIP 480); no IncomingCall, no gateway log line |
| Step 3 (Teams bind) | Call never reaches ACS; caller hears failure |
| Step 5 (ACS assignment) | ACS rejects/ignores the call; no `IncomingCall` |
| Step 6 (Event Grid) | ACS gets the call but the gateway sees nothing |

## Where the link lives
The ACS portal does not show it. It is two records plus the bot that makes Teams resolve the app (invariant: ApplicationId == Entra AppId == bot `msaAppId`):
1. Teams: the resource account's `ApplicationId` + `AcsResourceId` (`Set-CsOnlineApplicationInstance`).
2. ACS: the Teams-extension assignment (REST, verify with GET below).

## Prerequisites
- Teams Admin rights, a Teams phone number (Calling Plan / Operator Connect / Direct Routing), `PHONESYSTEM_VIRTUALUSER` license.
- ACS resource in the same tenant; Azure CLI logged in; modules `MicrosoftTeams`, `Microsoft.Graph.Applications`.
- Gateway deployed (public HTTPS `/api/incoming`).
- Fill `teams\.env`: TenantId, AcsImmutableId, AcsHost, ResourceAccountUpn, PhoneNumber.

## Steps
1. **Entra app + Azure Bot** (no secrets, no code; supplies a valid ApplicationId; the bot is required)
   `.\infra\rebind-tpe-azure.ps1 -Action CreateApp` -> prints `NEW_APP_ID`.
2. **Resource account** (first time only)
   `New-CsOnlineApplicationInstance -UserPrincipalName <upn> -DisplayName "TPE Live Intake" -ApplicationId <appId>`; assign license + usage location.
3. **Teams bind** (device-code sign-in at https://login.microsoft.com/device, valid ~15 min; run under `pwsh`, Windows PowerShell 5 lacks the module)
   `pwsh -NoProfile -File .\teams\rebind-tpe.ps1 -Action Rebind -BotAppId <appId>`
   = `Set-CsOnlineApplicationInstance -AcsResourceId <acs immutable id>` + `Sync-CsOnlineApplicationInstance`.
4. **Phone number** (first time only)
   `Set-CsPhoneNumberAssignment -Identity <oid> -PhoneNumber <+E164> -PhoneNumberType CallingPlan`.
5. **ACS assignment**
   `.\infra\rebind-tpe-azure.ps1 -Action Assign` (ensures ACS first-party SP `1fd5118e-2576-4263-8130-9503064c837a` exists, then
   `PUT https://<AcsHost>/access/teamsExtension/tenants/<tenant>/assignments/<oid>?api-version=2025-06-30` body `{"principalType":"teamsResourceAccount"}`, token audience `https://communication.azure.com`).
6. **Event Grid**
   `.\infra\rebind-tpe-azure.ps1 -Action Subscribe` (IncomingCall -> `<gateway>/api/incoming`).

## Verify each link
| Link | Check |
|---|---|
| Teams | `pwsh -NoProfile -File .\teams\rebind-tpe.ps1 -Action Snapshot` (ApplicationId, AcsResourceId, number; may lag minutes after a rebind) |
| Entra app + Azure Bot | `.\infra\rebind-tpe-azure.ps1 -Action Snapshot` -> bot `msaAppId` equals the app AppId, Teams calling `true` |
| ACS | GET the assignment URL above -> `principalType: teamsResourceAccount` |
| Event Grid | `az eventgrid system-topic event-subscription show` -> provisioningState Succeeded |
| End to end | call the number; gateway log shows `IncomingCall to=... from=...` |

## Baseline (FloorWorks demo, current)
App + bot `tpe-live-intake-bot` AppId `<current-app-id>` (previous: `<original-app-id>` original, `<first-rebuild-app-id>` first rebuild); resource account OID `<resource-account-object-id>`; ACS `acs-jmdeere-demo` (`<acs-immutable-resource-id>`); number `+16307351758`.

## Gotchas
- Teams PowerShell cannot take az tokens; use device-code sign-in; sessions do not persist.
- Changing the app means a new AppId: re-run steps 3 and 5.
- **The Azure Bot is REQUIRED.** `infra\rebind-tpe-azure.ps1 -Action CreateApp` creates it (same name as the Entra app, Teams channel with calling enabled). Rebuild test proved it: after deleting and recreating the Entra app, the bot still pointed at the deleted AppId and callers heard Verizon's "temporarily unavailable" (SIP 480) with no `IncomingCall` reaching the gateway. Recreating the bot with the new AppId is part of CreateApp.
- Allow a few minutes for Teams propagation after Sync.
- `Teardown` prints "Teardown done." even if an individual delete failed; run `-Action Snapshot` to confirm.
- The bot calling webhook points at the old `tpe-call-controller` URL; the gateway path does not use it (unverified for other designs).
- Rollback: recreate the app, then `Rebind` with its new AppId.

## Lessons from the rebuild (verified: call connects)

**What happened:** the first rebuild recreated the Entra app, ACS assignment, Event Grid subscription and Teams Set+Sync, but not the Azure Bot. The bot still pointed at the deleted AppId, so Teams returned SIP 480 ("temporarily unavailable") and ACS never saw the call. After recreating the bot with the new AppId, calls connected.

**Why it was missed:** the runbook was derived from the live wiring (Teams, ACS, Event Grid, gateway), and the bot is not visible in any of those. It only appears in `infra\setup-tpe-azure.ps1`, and an earlier note wrongly called it optional. The bot had no stale-looking error of its own. It just kept working with the old app id until the app was deleted.

**What this changes for the docs and runbook:**
1. The binding has FIVE links, not four: Teams resource account (ApplicationId), Entra app, **Azure Bot (msaAppId + Teams calling channel)**, ACS assignment, Event Grid subscription.
2. Invariant: resource account ApplicationId == Azure Bot msaAppId == Entra app AppId. Any change to the app means recreating the bot.
3. SIP 480 with no `IncomingCall` event and no gateway log line points to the Teams/bot layer, not ACS or the gateway.
4. Verify each link explicitly: `az bot show` (msaAppId) and `az bot msteams show` (calling true) belong in "Verify each link".
5. Teardown and CreateApp must always handle the bot (now in `infra\rebind-tpe-azure.ps1`).
6. Do not write a runbook from the running system alone. Cross-check it against the original provisioning script, and prove it with a destroy and rebuild test, as this one did.
7. For Deere, a bot in their tenant is a required deliverable. Its calling webhook value was kept from the original setup and is not used by our gateway path, which should be confirmed.

## Prerequisites checklist for a new tenant (not yet validated outside our tenant)

Confirm before starting; every run so far was in the FloorWorks demo tenant.

- [ ] A Teams phone number is assigned to a resource account (note its Object ID).
- [ ] The resource account has the required Teams Phone resource account licence.
- [ ] An ACS resource exists in the tenant, and you have its resource ID (`AcsResourceId`).
- [ ] The ACS first-party service principal `1fd5118e-2576-4263-8130-9503064c837a` exists in the tenant (create it if missing).
- [ ] Rights: Teams administrator (for the `MicrosoftTeams` module), permission to create Entra apps, Azure Bot resources and ACS/Event Grid resources.
- [ ] No tenant policy blocks app registration or bot creation.
- [ ] PowerShell 7 (`pwsh`) with the `MicrosoftTeams` module; Azure CLI logged in to the right subscription.
- [ ] The gateway is deployed and reachable, and its Foundry agents are published.
- [ ] Run order: Azure CreateApp (app + bot), Assign, Subscribe, then Teams Rebind. Allow minutes for propagation.
- [ ] Verify: place a test call and look for `IncomingCall` in the gateway logs. SIP 480 with no log line means the Teams or bot layer.

Open item: the bot's calling webhook still points at the old `tpe-call-controller` URL. It appears unused on the gateway path but has not been verified.

## Second rebuild test (verified)

Full teardown and rebuild using only the scripts (`rebind-tpe-azure.ps1` Teardown, CreateApp (app + bot), Assign, Subscribe, then `teams\rebind-tpe.ps1 -Action Rebind` under `pwsh`). New AppId `<current-app-id>`. The call at 22:04Z connected: IncomingCall, answered, media WebSocket accepted, live notes started, 211 s. Notes: Teams readback right after Rebind still showed the old ApplicationId (propagation lag), but the call worked; Teams scripts must run in `pwsh` (module `MicrosoftTeams`), not Windows PowerShell 5.

## Add another phone number (separate line with its own bot app)

Use this when you buy or port a new number and want it isolated from the working line (own Entra app, own Azure Bot, own resource account). One resource account carries one number. Nothing on the existing line is touched. No gateway or Event Grid change is needed: the gateway does not route on the called number, and all lines share the `IncomingCall` subscription.

1. Find the number: Teams admin center > Voice > Phone numbers. It must be **Activated**, capability **Voice application assignment**, **Unassigned**. Or: `Get-CsPhoneNumberAssignment -TelephoneNumber <+E164>` (check `NumberType`, `ActivationState`, `Capability`, `PstnAssignmentStatus`).
2. Create a new app and bot: `pwsh infra\rebind-tpe-azure.ps1 -Action CreateApp -AppName <new-app-name>`. Note the new AppId. **Always pass `-AppName` on every later action for this line** (the default targets the first line; never run Teardown without it).
3. Run `teams\setup-teams.ps1` with `-BotAppId <new-app-id>`, a **new** `-ResourceAccountUpn <intake2@contoso.com>`, `-DisplayName`, and `-PhoneNumber <+E164>`; keep `-AcsImmutableId` and `-AcsHost` the same. In one device-code session it creates the account, binds ApplicationId and AcsResourceId, registers the ACS teamsExtension assignment, sets usage location, assigns `PHONESYSTEM_VIRTUALUSER`, assigns the number and syncs.
4. License propagation: the number assignment can fail with "lacks appropriate licenses to assign a 'CallingPlan' number" right after the license is granted. Wait a few minutes and retry only the assignment: `Set-CsPhoneNumberAssignment -Identity <oid> -PhoneNumber <+E164> -PhoneNumberType CallingPlan`, then `Sync-CsOnlineApplicationInstance -ObjectId <oid> -ApplicationId <new-app-id>`. Then run the setup script's remaining steps (rep Enterprise Voice) if it aborted earlier.
5. Verify: `Get-CsOnlineApplicationInstance` shows ApplicationId = new app id and AcsResourceId set; `Get-CsPhoneNumberAssignment` shows `VoiceApplicationAssigned`; Azure: `$env:TPE_RESOURCE_ACCOUNT_OID=<oid>; pwsh infra\rebind-tpe-azure.ps1 -Action Snapshot -AppName <new-app-name>` shows bot `msaAppId` = AppId and an ACS assignment of type `teamsResourceAccount`.
6. Call the number and look for `IncomingCall` in the gateway logs. SIP 480 with no log line means the Teams or bot layer.

To move a number between resource accounts, run `Remove-CsPhoneNumberAssignment -Identity <oid> -PhoneNumber <+E164> -PhoneNumberType <type>` first.

### Fully isolated line (own ACS, Event Grid and gateway)

Teams routes a call through the ACS named in the resource account's `AcsResourceId`, and Event Grid `IncomingCall` fires on that ACS. So a line that shares nothing with the first one except the Foundry agents needs its own ACS, topic, subscription and gateway. Steps 1-2 above are unchanged; replace the shared-ACS parts of step 3 with:

1. Create an ACS resource (`az communication create`, data location matching the first). Note its host name and immutable resource id (`immutableResourceId`), and take its connection string.
2. Deploy a second gateway from the public image `ghcr.io/jmasengeshomsft/floorworks-intake-gateway:latest` as its own Container App (external ingress, port 8080, min replicas 1, system-assigned identity). Copy the first gateway's env vars (Foundry endpoint, agent names, speech key and region, feature toggles), then override `ACS_CONNECTION_STRING` (new ACS), `API_KEYS` (new key) and `PUBLIC_BASE_URL` (the new app's own FQDN). Pass the environment by name, not by resource id.
3. Grant the new gateway identity **Cognitive Services User** on the Foundry account (the only shared layer). No ACR role is needed for a public image.
4. Create an Event Grid system topic on the new ACS (`Microsoft.Communication.CommunicationServices`) and a subscription for `Microsoft.Communication.IncomingCall` with webhook `https://<new-gateway-fqdn>/api/incoming`.
5. Register the resource account on the **new** ACS: `PUT https://<new-acs-host>/access/teamsExtension/tenants/<tenant-id>/assignments/<resource-account-oid>?api-version=2025-06-30` with body `{}` and a token for `https://communication.azure.com`. Delete any earlier assignment for that account on the old ACS.
6. Point Teams at the new ACS (device-code session in `pwsh`): `Set-CsOnlineApplicationInstance -Identity <oid> -ApplicationId <new-app-id> -AcsResourceId <new-acs-immutable-id>`, then `Sync-CsOnlineApplicationInstance`. Read back both values.
7. Call the number. `IncomingCall` must appear in the new gateway's logs and not in the first gateway's.
## Scripts (inline copies)

Config read by both scripts, `teams\.env`: `TenantId`, `AcsImmutableId`, `AcsHost`, `ResourceAccountUpn`, `PhoneNumber`.

### `teams\rebind-tpe.ps1` (Teams side)

```powershell
<#
 Teams-side binding: resource account <-> Entra bot app <-> ACS resource.
 Teams PowerShell sessions do not persist, so each action signs in (device code) and runs in one session.

 -Action Snapshot : read-only dump of the resource account, number assignment and ACS assignment
 -Action Rebind   : point the existing resource account at -BotAppId + the ACS resource, sync, verify
 Reads defaults from teams\.env (TenantId, AcsImmutableId, AcsHost, ResourceAccountUpn, PhoneNumber).
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)][ValidateSet('Snapshot', 'Rebind')][string]$Action,
  [string]$BotAppId
)
$ErrorActionPreference = 'Stop'
$cfg = @{}
Get-Content (Join-Path $PSScriptRoot '.env') | Where-Object { $_ -match '^\w+=' } | ForEach-Object { $k, $v = $_ -split '=', 2; $cfg[$k] = $v }

Import-Module MicrosoftTeams
Connect-MicrosoftTeams -UseDeviceAuthentication -TenantId $cfg.TenantId | Out-Null

$acct = Get-CsOnlineApplicationInstance | Where-Object UserPrincipalName -eq $cfg.ResourceAccountUpn
if (-not $acct) { throw "Resource account $($cfg.ResourceAccountUpn) not found." }
$oid = $acct.ObjectId

if ($Action -eq 'Rebind') {
  if (-not $BotAppId) { throw '-BotAppId is required for Rebind.' }
  Set-CsOnlineApplicationInstance -Identity $oid -ApplicationId $BotAppId -AcsResourceId $cfg.AcsImmutableId | Out-Null
  Sync-CsOnlineApplicationInstance -ObjectId $oid -ApplicationId $BotAppId
  Start-Sleep 5
}

"--- Resource account ---"
Get-CsOnlineApplicationInstance -Identity $oid | Select-Object ObjectId, UserPrincipalName, DisplayName, ApplicationId, AcsResourceId, PhoneNumber | Format-List
"--- Number ---"
Get-CsPhoneNumberAssignment -TelephoneNumber $cfg.PhoneNumber | Select-Object TelephoneNumber, NumberType, PstnAssignmentStatus, AssignedPstnTargetId | Format-List
```

### `infra\rebind-tpe-azure.ps1` (Azure side)

```powershell
<#
 Azure-side binding steps (Entra app, Azure Bot, ACS Teams-extension assignment, Event Grid subscription).
 Pair with teams\rebind-tpe.ps1 (Teams side). Values come from teams\.env.

 -Action Snapshot : read-only (app, Azure Bot, ACS assignment, Event Grid subscription)
 -Action Teardown : delete Event Grid subscription, ACS assignment, the Entra app + SP and the Azure Bot
 -Action CreateApp: create the Entra app + SP and the Azure Bot with Teams calling (prints the new AppId)
 -Action Assign   : ACS access assignment for the resource account + ACS first-party SP check
 -Action Subscribe: Event Grid subscription IncomingCall -> $GatewayUrl/api/incoming
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)][ValidateSet('Snapshot', 'Teardown', 'CreateApp', 'Assign', 'Subscribe')][string]$Action,
  [string]$ResourceGroup = 'sharepointdemo',
  [string]$AcsName = 'acs-jmdeere-demo',
  [string]$AppName = 'tpe-live-intake-bot',
  [string]$ResourceAccountObjectId = $env:TPE_RESOURCE_ACCOUNT_OID,
  [string]$SystemTopic = 'acs-jmdeere-demo-topic',
  [string]$SubscriptionName = 'audio-probe-incoming',
  [string]$GatewayUrl = 'https://floorworks-intake-gateway.mangomushroom-8c85c1e1.eastus.azurecontainerapps.io'
)
$ErrorActionPreference = 'Stop'
$cfg = @{}
Get-Content (Join-Path $PSScriptRoot '..\teams\.env') | Where-Object { $_ -match '^\w+=' } | ForEach-Object { $k, $v = $_ -split '=', 2; $cfg[$k] = $v }
$tenant = $cfg.TenantId
$acsUri = "https://$($cfg.AcsHost)/access/teamsExtension/tenants/$tenant/assignments/$ResourceAccountObjectId`?api-version=2025-06-30"
$acsTok = { az account get-access-token --resource https://communication.azure.com --query accessToken -o tsv }

switch ($Action) {
  'Snapshot' {
    'App:'; az ad app list --display-name $AppName --query '[].{appId:appId,objectId:id,audience:signInAudience}' -o json
    'Azure Bot (msaAppId must equal the app AppId):'
    az bot show -g $ResourceGroup -n $AppName --query '{msaAppId:properties.msaAppId,endpoint:properties.endpoint}' -o json 2>$null
    az bot msteams show -g $ResourceGroup -n $AppName --query '{calling:properties.properties.enableCalling,webhook:properties.properties.callingWebhook}' -o json 2>$null
    'ACS assignment:'
    try { Invoke-RestMethod -Headers @{ Authorization = "Bearer $(& $acsTok)" } -Uri $acsUri | ConvertTo-Json } catch { "GET failed: $($_.Exception.Message)" }
    'Event Grid subscription:'
    az eventgrid system-topic event-subscription show -g $ResourceGroup --system-topic-name $SystemTopic -n $SubscriptionName --query '{url:destination.endpointBaseUrl,state:provisioningState,types:filter.includedEventTypes}' -o json
  }
  'Teardown' {
    az eventgrid system-topic event-subscription delete -g $ResourceGroup --system-topic-name $SystemTopic     -n $SubscriptionName --yes -o none
    Invoke-RestMethod -Method Delete -Headers @{ Authorization = "Bearer $(& $acsTok)" } -Uri $acsUri | Out-Null
    foreach ($id in (az ad app list --display-name $AppName --query '[].id' -o tsv)) { az ad app delete --id $id }
    az bot delete -g $ResourceGroup -n $AppName 2>$null
    'Teardown done.'
  }
  'CreateApp' {
    $appId = az ad app create --display-name $AppName --sign-in-audience AzureADMyOrg --query appId -o tsv
    az ad sp create --id $appId -o none
    # REQUIRED: Azure Bot (same name) bound to the new AppId with Teams calling; without it Teams returns SIP 480.
    az bot create -g $ResourceGroup -n $AppName --appid $appId --app-type SingleTenant --tenant-id (az account show --query tenantId -o tsv) --sku F0 --location global -o none
    az bot msteams create -g $ResourceGroup -n $AppName --enable-calling true --calling-web-hook 'https://tpe-call-controller.mangomushroom-8c85c1e1.eastus.azurecontainerapps.io/api/calling' -o none
    "NEW_APP_ID=$appId"
  }
  'Assign' {
    # ACS first-party app must exist in the tenant
    if (-not (az ad sp show --id 1fd5118e-2576-4263-8130-9503064c837a --query id -o tsv 2>$null)) { az ad sp create --id 1fd5118e-2576-4263-8130-9503064c837a -o none }
    Invoke-RestMethod -Method Put -ContentType 'application/json' -Body '{"principalType":"teamsResourceAccount"}' `
      -Headers @{ Authorization = "Bearer $(& $acsTok)" } -Uri $acsUri | ConvertTo-Json
  }
  'Subscribe' {
    az eventgrid system-topic event-subscription create -g $ResourceGroup --system-topic-name $SystemTopic -n $SubscriptionName `
      --endpoint "$GatewayUrl/api/incoming" --endpoint-type webhook --included-event-types Microsoft.Communication.IncomingCall --query provisioningState -o tsv
  }
}
```

For a new tenant, change the defaults (`ResourceGroup`, `AcsName`, `ResourceAccountObjectId`, `SystemTopic`, `GatewayUrl`) via parameters.

## Status
Teardown/rebuild executed. Teams-side Set+Sync worked against the existing resource account after the old app was deleted, so no resource-account recreation was needed.

Rebuild results:
- Deleted: Event Grid subscription, ACS assignment, Entra app `<original-app-id>`.
- First rebuild: created app `tpe-live-intake-bot` with AppId `<first-rebuild-app-id>`, then Teams Set+Sync, ACS assignment PUT and Event Grid subscription (all Succeeded). The Azure Bot was initially NOT recreated (it still referenced the old AppId, causing SIP 480); it was then deleted and recreated with the new AppId and Teams calling enabled.
- Teams propagation lag: a snapshot taken right after the rebind still showed the old ApplicationId; a later snapshot showed the new one. Wait and re-check before concluding failure.
- Gotcha fixed: `az eventgrid ... delete` needs `--yes` in non-interactive runs.
- Final state: AppId `<current-app-id>` after the second full teardown/rebuild from scripts; a test call connected (`call_id=c6eed937`, 211 s). Not yet validated: a fresh tenant, and rebuilding ACS/gateway/Foundry from scratch.

> Set TPE_RESOURCE_ACCOUNT_OID (resource account object id) in your environment before running infra\rebind-tpe-azure.ps1; IDs are intentionally not committed.
