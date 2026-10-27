# 03 · Deployment guide

This guide stands up the whole solution in your own tenant. Expect a half day the first time, most of it waiting on
Teams and licensing.

> **Status of the scripts.** `dataverse/provision-dataverse.ps1` and the Foundry wiring were run end to end. Scripts
> under `infra/` and `teams/` were consolidated from manual steps and are parameterized rewrites: expect small fixes.

## 1 · Prerequisites

| Area | Requirement |
|---|---|
| Azure | Subscription, resource group, Container Apps environment, Azure Container Registry |
| ACS | Azure Communication Services resource with Call Automation |
| Foundry | Account and project with a model deployment, plus a Speech-capable key on the AI Services account |
| Power Platform | Environment with Dataverse (a free developer environment works) |
| Teams | Teams admin; a phone number you own; a *Teams Phone Resource Account* license; reps with a Teams Phone license, Enterprise Voice enabled |
| Tools | PowerShell 7, Azure CLI with `containerapp` and `communication` extensions, `MicrosoftTeams` PowerShell module, .NET 8 SDK |

You also need rights to: create app registrations, grant admin consent, administer the Dataverse environment, and
administer Teams voice.

## 2 · Dataverse data model

```powershell
cd dataverse
./provision-dataverse.ps1 -OrgUrl https://<org>.crm.dynamics.com
```

Creates the six tables, lookups and seed data (see [Data model](05-data-model.md)). It is idempotent: re-run it any time
to add columns or reseed.

Then, in the **Power Platform admin center → Environments → *your env* → Settings → Features**, turn on
*Allow MCP clients to interact with Dataverse MCP server*.

## 3 · Foundry ↔ Dataverse

```powershell
cd foundry
./setup-foundry.ps1 -SubscriptionId <sub> -ResourceGroup <rg> -FoundryAccount <account> -FoundryProject <project> -OrgUrl https://<org>.crm.dynamics.com
```

This:
1. creates a Dataverse *application user* for the **Foundry project's managed identity** and grants a role,
2. adds it to the Dataverse **Allowed MCP Clients** list,
3. creates the project connection `dataverse-mfg-mi` (type *ProjectManagedIdentity*),
4. publishes the original extractor prompt agent.

Then create the **note-taker** and **voice agent** and the **toolbox** as described in [AI agents](06-agents.md).
The default role is *System Administrator*, which is fine for a demo and **must** be replaced for production
([Security](09-security.md)).

## 4 · Telephony (Teams Phone Extensibility)

1. `infra/setup-tpe-azure.ps1`: Entra app for the bot identity, Azure Bot with the Teams calling channel, ACS service
   principal in your tenant, and the Event Grid subscription for `IncomingCall`.
2. `teams/setup-teams.ps1`: creates the Teams resource account, assigns your number and license, enables reps for
   Enterprise Voice. It prompts for a device-code sign-in.

Point the Event Grid subscription at `https://<voice-intake-fqdn>/api/incoming`. The app answers Event Grid's
subscription-validation handshake automatically, so the **VoiceIntake app must be running before you create the
subscription**.

> Without the bot's Teams calling channel Teams rejects the call with SIP 480 / code 10140. If you route the number
> straight to the resource account, no Auto Attendant greeting plays; the AI receptionist replaces it.

## azd quick start

Provisions the registry, Container Apps environment, app and role assignments, then builds and deploys VoiceIntake. It reuses an **existing** ACS resource and Foundry project.

```powershell
azd auth login
azd env new <name>
azd env set AZURE_LOCATION eastus
azd env set ACS_NAME <acs-resource-in-the-same-rg>
azd env set FOUNDRY_ACCOUNT_NAME <account>
azd env set FOUNDRY_PROJECT_NAME <project>
azd env set FOUNDRY_RESOURCE_GROUP <rg>          # if different from the target RG
azd env set DATAVERSE_URL https://<org>.crm.dynamics.com
azd env set REP_TEAMS_OBJECT_IDS <oid1,oid2>
azd env set REP_KEY <shared-key>
azd env set ENRICHMENT off                       # on after the Foundry IQ KB is built (foundry/README.md)
azd env set CREATE_ACS true                       # self-contained: also create ACS (omit ACS_NAME)
azd env set CREATE_FOUNDRY true                   # self-contained: also create Foundry account + project; then run foundry/setup-agents.ps1
azd env set DEPLOY_SEARCH true                   # Azure AI Search for Foundry IQ (false to reuse your own)
azd env set RUNNER_PRINCIPAL_ID <your-user-object-id>   # optional: grants you the search roles to build the KB
azd up
```

Manual steps azd does not cover: Dataverse schema (`dataverse/provision-dataverse.ps1`), a Dataverse **application user** for the app's managed identity (`azd env get-value APP_PRINCIPAL_ID`), publishing the agents (`foundry/`), the Teams number and the Event Grid `IncomingCall` subscription pointing at `<PUBLIC_BASE_URL>/api/incoming` (see steps 3 and 4). Set `FOUNDRY_PROJECT_ENDPOINT` when running `foundry\publish-draft-flow-agents.ps1`.
## 5 · Build and deploy VoiceIntake (manual alternative)

```powershell
cd src/VoiceIntake
az acr build -r <acr> -t voice-intake:v1 .
az containerapp create -g <rg> -n floorworks-voice-intake --environment <aca-env> `
  --image <acr>.azurecr.io/voice-intake:v1 --registry-server <acr>.azurecr.io `
  --target-port 8080 --ingress external --min-replicas 1 --max-replicas 1 `
  --system-assigned `
  --secrets acs-cs="<ACS connection string>" speech-key="<speech key>" rep-key="<long random string>" `
  --env-vars ACS_CONNECTION_STRING=secretref:acs-cs SPEECH_KEY=secretref:speech-key REP_KEY=secretref:rep-key `
             SPEECH_REGION=eastus PUBLIC_BASE_URL=https://<fqdn> `
             FOUNDRY_PROJECT_ENDPOINT=https://<account>.services.ai.azure.com/api/projects/<project> `
             DATAVERSE_URL=https://<org>.crm.dynamics.com `
             REP_TEAMS_OBJECT_IDS=<rep-entra-object-id>
```

Notes:
- **Keep one replica.** Call state, SSE hubs and sockets are in memory; scaling out splits a call across instances.
- `PUBLIC_BASE_URL` must equal the app's external `https://` URL; ACS uses it for callbacks and the `wss://` media socket.
  If you create the app before knowing the FQDN, update it afterward.
- Full variable list: [Configuration](04-configuration.md).

### Grant the app's identity access

The app's **system-assigned managed identity** needs:

| Target | Access |
|---|---|
| Foundry project | *Azure AI User* (or equivalent) so it can open voice sessions and call the note-taker |
| Dataverse | An *application user* for the identity's client id with a role that can read/write the six tables |

### Redeploy a new version

```powershell
az acr build -r <acr> -t voice-intake:v2 src/VoiceIntake
az containerapp update -g <rg> -n floorworks-voice-intake --image <acr>.azurecr.io/voice-intake:v2
```

Use a new tag each time so the revision changes.

## 6 · Smoke test

```powershell
curl https://<fqdn>/healthz                       # ok
curl "https://<fqdn>/rep?k=<rep-key>"             # Rep Console HTML
```

Then follow the [Demo guide](11-demo-guide.md): open the console, dial the number, speak, and confirm a draft appears
with **no** service ticket created until Submit.

## Tear-down

Delete the Container App, the Event Grid subscription, the Teams resource account and number assignment, the Foundry
agents and toolbox, and (optionally) the Dataverse tables. Draft transcripts can contain personal data; delete the
`bdm_servicerequestdraft` rows if you are retiring a demo.
