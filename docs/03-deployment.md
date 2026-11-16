# 03 - Deployment guide

Both deployment paths target `src\intake-gateway` (Python). The React UI is built/hosted separately.
Choose **PowerShell** for existing Azure infrastructure or **azd** to provision the Bicep-managed stack.
Do not let both tools manage the same Container App: reprovisioning can replace its configuration/image.

## Prerequisites

| Area | Requirement |
|---|---|
| Azure | Subscription, resource group and permission to deploy resources and assign roles |
| Voice | ACS Call Automation, Foundry account/project and supported voice/chat model deployments |
| Speech | Speech-capable Foundry/AI Services account with a custom subdomain for Entra authentication |
| Dataverse | Environment, customization rights for provisioning, and an admin to register application users |
| Teams | Admin, business phone number, licensed resource account and voice-enabled reps |
| Tools | PowerShell 7, Azure CLI; azd for the azd path |
| Local development | Python 3.12+; Node.js supported by Vite 8 for the UI; .NET 8 SDK only for optional Foundry IQ tooling |

Existing ACS must be in the gateway resource group for these templates/scripts. Foundry can be in another resource
group. Resource/model availability, licensing, regional support and tenant consent must be checked in your environment.
Creating resources does not publish agents or establish Teams routing.

## 1 - Dataverse schema and journal

Review `dataverse\config.json` before running:

```powershell
.\dataverse\provision-dataverse.ps1 -OrgUrl https://<org>.crm.dynamics.com
```

This is **schema-only by default**. It provisions the supported business-table profile plus draft/journal integrity
requirements: transcript version, active draft call-ID alternate key and optimistic concurrency. Demo data is optional
(`dataverse\seed-demo-data.ps1`). See [Dataverse provisioning](../dataverse/README.md).

For an existing gateway, first pause inbound routing, finish calls and stop old revisions. Missing journal columns,
inactive keys or missing runtime permissions cause startup failure; do not disable integrity mappings to bypass them.
The provisioner is not a general-purpose migration tool for arbitrary customer tables:
[table mapping](18-table-configuration.md) and customer-approved schema changes still require review.

## 2 - Foundry agents and telephony prerequisites

For existing resources, prepare the agents before deploying the application:

```powershell
.\foundry\setup-agents.ps1 -ProjectEndpoint https://<account>.services.ai.azure.com/api/projects/<project> `
  -OrgUrl https://<org>.crm.dynamics.com -GrantDataverseAccess
```

Review the prompts first. The opt-in access step needs a Dataverse admin and defaults to a broad demo role; replace it
with least privilege for a customer environment. Record the printed endpoint and agent names.
See [agent setup](17-agent-setup.md).

If azd will create Foundry, run **azd provision first**, then this agent setup, then **azd deploy**.
Optional Foundry IQ enrichment needs its knowledge base before enabling it ([Foundry guide](../foundry/README.md)).

Prepare the Azure bot/ACS tenant prerequisites and Teams resource-account binding using
[infra](../infra/README.md), [Teams](../teams/README.md) and the [telephony runbook](15-telephony-binding-runbook.md).
Without `-EventGridSecret`, `setup-tpe-azure.ps1` skips Event Grid with a warning; wire deliveries only after the actual
gateway is healthy. Avoid multiple subscriptions trying to answer the same call.

## 3A - PowerShell deployment

This path reuses an existing ACS resource, Foundry project, Container Apps environment and ACR.
From the repository root:

```powershell
Copy-Item .\infra\gateway.parameters.example.json .\infra\gateway.customer.json
# Edit the private JSON: resources, agents, Dataverse URL, CORS, transfers and office hours.
.\infra\deploy-gateway.ps1 -ParametersFile .\infra\gateway.customer.json -Plan
.\infra\deploy-gateway.ps1 -ParametersFile .\infra\gateway.customer.json -Rebuild
```

The script builds/deploys Python, manages secrets, grants Foundry/Speech access, registers the gateway's Dataverse
application user, and wires authenticated Event Grid delivery. Its automatic Dataverse role is **System Administrator**
for the demo; review/replace it before customer use. It does not provision the Dataverse schema.

New parameter files use keyless Speech. Existing key-based files remain supported; see
[deployment and Speech migration details](16-intake-gateway-deploy.md).
New files also explicitly enable [automatic office hours](04-configuration.md#automatic-office-hours):
review the timezone, service days and opening/closing times before deployment.
Legacy files without office-hours fields leave the existing app schedule unchanged.

## 3B - azd deployment

The service key remains `voice-intake` for compatibility, but its project is the **Python gateway**.
From the repository root, configure an environment (values in `.azure` are private and git-ignored):

```powershell
azd auth login
azd env new <environment-name>
azd env set AZURE_LOCATION eastus
azd env set AZURE_RESOURCE_GROUP <gateway-resource-group>
azd env set ACS_NAME <existing-acs>
azd env set FOUNDRY_ACCOUNT_NAME <existing-account>
azd env set FOUNDRY_RESOURCE_GROUP <foundry-resource-group>
azd env set FOUNDRY_PROJECT_NAME <project>
azd env set DATAVERSE_URL https://<org>.crm.dynamics.com
azd env set VOICE_AGENT_NAME intake-voice-agent
azd env set EXTRACTOR_AGENT_NAME intake-note-taker
azd env set TRANSFER_TEAMS_OBJECT_IDS <rep-object-id>
azd env set CORS_ORIGINS http://localhost:5173
azd env set AFTER_HOURS auto
azd env set SERVICE_TZ America/Chicago
azd env set SERVICE_DAYS Mon-Fri
azd env set SERVICE_HOURS 07:00-18:00
azd env set DEPLOY_SEARCH false

function New-DeploymentSecret {
  [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
}
azd env set API_KEYS (New-DeploymentSecret)
azd env set GATEWAY_SECRET (New-DeploymentSecret)
azd env set EVENTGRID_SECRET (New-DeploymentSecret)
azd provision
```

Generate these secrets **once** for a new environment; do not rotate them on every deployment.
API/HMAC secrets are secure Bicep parameters and Container Apps secrets, not plaintext container environment values.
Protect the local azd environment file too.
The office-hours values above are new-environment examples. For existing azd environments, retain/import their
actual `AFTER_HOURS` and `SERVICE_*` settings instead of overwriting them with these defaults. If unset, the azd
mode remains `off`; adopting `auto` is explicit. Daily schedule transitions do not create new revisions.

To create ACS/Foundry instead of reusing them, set `CREATE_ACS=true` and/or `CREATE_FOUNDRY=true` before provision.
New Foundry model deployments require regional availability/quota. Set `DEPLOY_SEARCH=true` only when provisioning
the optional knowledge-base service; `RUNNER_PRINCIPAL_ID` optionally grants the KB builder's Search access.

Before deploying the application:

1. Complete schema provisioning and agent setup using the provisioned Foundry outputs.
2. Register **the gateway identity separately** in Dataverse. Get its principal with
   `azd env get-value APP_PRINCIPAL_ID`; resolve its application/client ID using
   `az ad sp show --id <principal-id> --query appId -o tsv`. In Power Platform admin center, add that application user
   and assign the required draft/journal, lookup, ticket, customer and annotation permissions.
3. Ensure the Foundry account supports Speech and already has a custom subdomain. Bicep configures Speech AAD,
   uses that account's actual region/resource ID and grants **Cognitive Services Speech User**.

```powershell
azd deploy voice-intake
```

`azd provision` uses a public bootstrap image to establish the app/identity. **It is not a running gateway**,
and reprovisioning resets that image: always redeploy afterward, during maintenance.
For a fresh setup, prefer the staged commands above over `azd up` so identities/agents are ready before startup.

After the gateway is healthy, create its authenticated Event Grid subscription with the same secret:

```powershell
$eventGridSecret = azd env get-value EVENTGRID_SECRET
$gatewayUrl = azd env get-value PUBLIC_BASE_URL
.\infra\setup-tpe-azure.ps1 -ResourceGroup <gateway-resource-group> -AcsName <acs> `
  -AppUrl $gatewayUrl -EventGridSecret $eventGridSecret
```

The gateway application user is **not** the Foundry project application user. Both require their own permissions.
Neither Bicep nor agent setup automatically grants the gateway's Dataverse access.

## 4 - UI and acceptance

Build/host `src\intake-ui` as a static site, or run `npm ci` and `npm run dev` from that folder for local use.
Allow the UI's exact origin in `CORS_ORIGINS`. Enter the gateway URL and an `API_KEYS` value in the UI.
There is no `/rep?k=` page. See [Intake UI](07-rep-console.md).

Run the [read-only verifier](../infra/README.md) and the [live acceptance checklist](08-operations.md#live-acceptance).
A healthy endpoint or successful image build alone does not prove Speech, agent tools, recovery or Submit.

**Both paths require one process/replica and no overlapping old/new gateways during journal recovery.**
Single-revision mode can still overlap replicas briefly; neither path implements automatic call draining.
Use the [maintenance procedure](08-operations.md#safe-upgrades-and-rollback), not a rolling deployment during calls.
