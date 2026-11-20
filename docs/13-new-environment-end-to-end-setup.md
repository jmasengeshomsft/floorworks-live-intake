# New environment end-to-end setup (Dataverse + Foundry + phone line)

Ordered, copy-pasteable runbook for standing up the voice intake stack against a **new** Dataverse environment.
It was validated with the *Prairie Haul* environment (`https://orgd8793b40.crm3.dynamics.com`) using a private gateway parameters file (`infra\gateway.prairie1.json`, copied from `infra\gateway.parameters.example.json`).

> **The step that was missed: Phase 5.** The Foundry project managed identity needs its own **Dataverse application user**.
> Without it, the toolbox `tools/list` returns `HTTP_403 "Access denied when connecting to the MCP server"` even when the
> connection, endpoint, audience and allowed MCP client are all correct. The phone line worked only after this was done.

Placeholders: `<org>` = `https://<orgname>.crmN.dynamics.com` (no trailing slash). Never commit secrets or `EVENTGRID_SECRET` values; keep parameter files out of Git.

```powershell
az login
$org = 'https://orgd8793b40.crm3.dynamics.com'   # your org URL
$projectId = '/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<account>/projects/<project>'
$projectEndpoint = 'https://<account>.services.ai.azure.com/api/projects/<project>'
```

## Phase 1: Create the environment and Dataverse database

1. Power Platform admin center > **Manage** > **Environments** > **New**. Pick the type (Sandbox/Trial for demos) and region.
2. Turn on **Add a Dataverse data store** (creates the database), then **Create**.
3. Wait for state **Ready**; copy the **Environment URL** (`<org>`).
4. Confirm you can reach the org:

```powershell
$t = az account get-access-token --resource $org --query accessToken -o tsv
Invoke-RestMethod "$org/api/data/v9.2/WhoAmI" -Headers @{ Authorization = "Bearer $t" }
```

## Phase 2: Provision schema and seed data

The schema uses the `bdm_` prefix (defaults: `-Prefix bdm`, `-OptionPrefix 10573`, solution `BlueDotMfgQuality`). Requires Environment Maker or better.

```powershell
.\dataverse\provision-dataverse.ps1 -OrgUrl $org
.\dataverse\seed-demo-data.ps1      -OrgUrl $org
```

Notes:

- `provision-dataverse.ps1` is **idempotent**. On a fresh environment it once failed creating `bdm_checkpointjson` on `bdm_calljournal` with `0x80040216`. **Rerun the script**; it completes.
- Add `-StandardTables` (and `seed-demo-data.ps1 -Standard`) to use account/incident instead of `bdm_*`; the default comes from `dataverse\config.json` (see [18](18-table-configuration.md)).
- The **allowed MCP client unique name needs a customization prefix** (for example `bdm_foundryprojectmi`); an unprefixed name is rejected.

Verify:

```powershell
$h = @{ Authorization = "Bearer $(az account get-access-token --resource $org --query accessToken -o tsv)" }
(Invoke-RestMethod "$org/api/data/v9.2/EntityDefinitions?`$select=LogicalName&`$filter=startswith(LogicalName,'bdm_')" -Headers $h).value.LogicalName
```

## Phase 3: Foundry project managed identity

1. Azure portal > Foundry project > **Identity** > **System assigned: On**, or:

```powershell
$principalId = az rest --method get --url "https://management.azure.com$projectId`?api-version=2025-04-01-preview" --query identity.principalId -o tsv
```

2. Convert the principalId (object ID) to the **appId** (client ID). Dataverse uses the appId:

```powershell
$projectAppId = az ad sp show --id $principalId --query appId -o tsv
$projectAppId
```

## Phase 4: Allow the MCP client in Dataverse

1. Power Platform admin center > environment > **Settings** > **Product** > **Features** > enable **Allow MCP clients to interact with Dataverse MCP server**. Save.
2. Add the appId as an allowed MCP client (UI: **Active Allowed MCP Clients**, or API):

```powershell
$hj = @{ Authorization = "Bearer $(az account get-access-token --resource $org --query accessToken -o tsv)"; 'Content-Type' = 'application/json' }
$body = @{
  name          = 'Foundry Project MI'
  uniquename    = 'bdm_foundryprojectmi'     # needs a customization prefix
  applicationid = $projectAppId
  isenabled     = $true
} | ConvertTo-Json
Invoke-RestMethod "$org/api/data/v9.2/allowedmcpclients" -Method Post -Headers $hj -Body $body
```

## Phase 5: Create the Dataverse application user (the missed step)

Allowing the client is **not** the same as giving it an identity in Dataverse.

1. Power Platform admin center > **Manage** > **Environments** > your environment > **Settings** > **Users + permissions** > **Application users**.
2. **+ New app user** > **+ Add an app** > search by the **appId** from Phase 3. (Use the appId, not the object ID.)
3. Business unit: the root business unit.
4. Security role: **System Administrator** for the demo. In production use a least-privilege custom role.
5. **Create**.

**The gateway needs its own, separate application user** (here "prairie1"). Its Container App managed identity has a different appId from the Foundry project identity. Repeat steps 1-5 for it. Both are required:

| App user | Identity | Used for |
|---|---|---|
| Foundry project MI | project system-assigned MI appId (Phase 3) | Toolbox/agent calls to Dataverse MCP (`/api/mcp`) |
| Gateway (prairie1) | Container App identity appId | Gateway reads/writes drafts and journal via the Dataverse Web API |

```powershell
$gwPrincipal = az containerapp show -g <resource-group> -n <gateway-app-name> --query identity.principalId -o tsv
$gwAppId     = az ad sp show --id $gwPrincipal --query appId -o tsv
```

## Phase 6: Foundry connection

Name must be consistent everywhere (`dataverse-mi` is the `setup-agents.ps1` default; [12](12-dataverse-mcp-managed-identity-setup.md) uses `dataverse-mfg-mi`).

```powershell
$conn = 'dataverse-mi'
@{ properties = @{
    category      = 'RemoteTool'
    target        = "$org/api/mcp"
    authType      = 'ProjectManagedIdentity'
    audience      = $org            # org URL only
    isSharedToAll = $true           # required for runtime use
    metadata      = @{ type = 'custom_MCP' }
} } | ConvertTo-Json -Depth 5 | Set-Content "$env:TEMP\conn.json"
az rest --method put --url "https://management.azure.com$projectId/connections/$conn`?api-version=2025-04-01-preview" --body "@$env:TEMP\conn.json"
Remove-Item "$env:TEMP\conn.json"
```

## Phase 7: Toolbox and agent tools (`require_approval: never`)

Preferred: the idempotent script creates the connection, toolbox and agents. Run `-Plan` first.

```powershell
.\foundry\setup-agents.ps1 -ProjectEndpoint $projectEndpoint -OrgUrl $org -ConnectionName $conn -ToolboxName floorworks-dataverse -Plan
.\foundry\setup-agents.ps1 -ProjectEndpoint $projectEndpoint -OrgUrl $org -ConnectionName $conn -ToolboxName floorworks-dataverse
```

See [17](17-agent-setup.md). If configuring by hand, both the agent's tool and the toolbox's inner tool need approval `never`, or unattended calls hang as "pending user approval":

```json
{
  "type": "mcp",
  "server_label": "dataverse-mi",
  "server_url": "<org>/api/mcp",
  "require_approval": "never",
  "project_connection_id": "dataverse-mi"
}
```

## Phase 8: Gateway deploy and phone line

1. Create and edit the parameters file (keep it out of Git): `subscriptionId`, `resourceGroup`, `appName`, `acrName`, `environmentName`, `acsResourceName`, `foundryProjectEndpoint`, `voiceAgentName`, `dataverseUrl` (= `<org>`), `resourceAccountObjectId`, `lineNumbers`, `brandName`.

```powershell
Copy-Item infra\gateway.parameters.example.json infra\gateway.prairie1.json
.\infra\deploy-gateway.ps1 -ParametersFile infra\gateway.prairie1.json -Plan   # preflight only
.\infra\deploy-gateway.ps1 -ParametersFile infra\gateway.prairie1.json
```

The script builds the image, deploys the Container App, grants identity access, creates the Event Grid `IncomingCall` subscription with the matching shared secret and smoke-tests it. The gateway still needs its Dataverse application user (Phase 5).

2. Teams side and ACS assignment ([13 telephony wiring](13-telephony-wiring.md), [15](15-telephony-binding-runbook.md)):

```powershell
.\infra\rebind-tpe-azure.ps1 -Action Snapshot    # read-only: app, bot, ACS assignment, Event Grid subscription
.\infra\rebind-tpe-azure.ps1 -Action Assign      # ACS Teams-extension assignment for the resource account
.\infra\rebind-tpe-azure.ps1 -Action Subscribe -EventGridSecret '<must match the running gateway; do not commit>'
```

> **Do not delete the ACS Teams-extension assignment.** It breaks the phone line. Restore with
> `.\infra\rebind-tpe-azure.ps1 -Action Assign`. (`-Action Teardown` deletes it; avoid on a working line.)

## Phase 9: Verification checklist

1. **Both application users exist** (expect the project MI appId and the gateway appId, not disabled):

```powershell
$h = @{ Authorization = "Bearer $(az account get-access-token --resource $org --query accessToken -o tsv)" }
(Invoke-RestMethod "$org/api/data/v9.2/systemusers?`$select=fullname,applicationid,isdisabled&`$filter=applicationid ne null" -Headers $h).value
```

2. **Allowed MCP client** exists and is enabled:

```powershell
(Invoke-RestMethod "$org/api/data/v9.2/allowedmcpclients?`$select=name,uniquename,applicationid,isenabled" -Headers $h).value
```

3. **Connection** shows `ProjectManagedIdentity`, target `<org>/api/mcp`, audience `<org>`, `isSharedToAll` true:

```powershell
az rest --method get --url "https://management.azure.com$projectId/connections/$conn`?api-version=2025-04-01-preview" --query properties
```

4. **Toolbox `tools/list`** must return `describe`, `read_query`, `search` (possibly prefixed with the connection name):

```powershell
$tok = az account get-access-token --resource https://ai.azure.com --query accessToken -o tsv
$url = "$projectEndpoint/toolboxes/floorworks-dataverse/versions/<n>/mcp?api-version=v1"
$rpc = '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
Invoke-RestMethod $url -Method Post -ContentType 'application/json' -Headers @{ Authorization = "Bearer $tok"; Accept = 'application/json, text/event-stream' } -Body $rpc
```

A `403 Access denied when connecting to the MCP server` here means Phase 5 is missing for the project MI.

5. **Gateway**: `.\infra\verify-gateway.ps1` (see [16](16-intake-gateway-deploy.md)) and check Container App logs.
6. **Phone test**: call the line; the agent answers and draft/journal rows appear in Dataverse.

## Troubleshooting (symptom -> cause)

| Symptom | Cause / fix |
|---|---|
| 401 from Dataverse MCP | Wrong `audience` (must be only the org URL), wrong target, or token for the wrong identity |
| 403 `HTTP_403 Access denied when connecting to the MCP server` on toolbox `tools/list` | **Application user missing** for the project MI appId (Phase 5), or its role lacks table access. Connection, endpoint, audience and allowed client can all look correct |
| "MCP client not allowed" | Feature not enabled, or no enabled `allowedmcpclients` record for the project appId (Phase 4); object ID used instead of appId |
| Call hangs, "pending user approval" | `require_approval` is not `never` on the agent tool or toolbox inner tool (Phase 7) |
| Works in playground, not at runtime | Connection `isSharedToAll` is `false`, or the agent runs an old version without the tool |
| `extractor HTTPStatusError` in gateway logs | Check the status code: 403 = gateway app user missing or under-privileged (Phase 5); 401 = audience/identity; otherwise verify the extractor agent name and Dataverse URL in the parameters file |
| Phone line unanswered after changes | ACS Teams-extension assignment deleted; restore with `infra\rebind-tpe-azure.ps1 -Action Assign`; check Event Grid with `-Action Snapshot` |
| `0x80040216` on `bdm_checkpointjson` | Fresh-environment failure; rerun `provision-dataverse.ps1` (idempotent) |
