# infra — deployment and telephony plumbing

| File | Purpose | Status |
|---|---|---|
| `setup-tpe-azure.ps1` | Azure side of Teams Phone Extensibility: Entra bot app, Azure Bot with Teams calling channel, ACS service principal, Event Grid subscription | Rewrite not run as-is |

The current product, **VoiceIntake**, deploys with the Azure Developer CLI: `main.bicep` (Log Analytics, ACR, Container Apps environment, the app with a managed identity, ACR pull and Foundry role assignments) and `foundry-roles.bicep`. See [docs/03-deployment.md](../docs/03-deployment.md#azd-quick-start).

### Self-contained deployment (validated)

`azd provision` can create everything in a new resource group: set `CREATE_ACS=true` and `CREATE_FOUNDRY=true` (via `azd env set`) to add ACS (`acs.bicep`) and a Foundry account, project and model deployments (`foundry.bicep`); an optional Search service is created for Foundry IQ. The Search service has a system identity that is granted *Cognitive Services OpenAI User* on the Foundry account (`search-model-role.bicep`); without it knowledge-base queries fail with HTTP 502.

Order of operations:

1. `azd provision`, then `azd deploy`. Provisioning resets the app to a placeholder image, so always redeploy after re-provisioning.
2. `foundry\setup-foundry.ps1`: Dataverse application user and the `dataverse-mfg-mi` connection for the project identity.
3. `cd foundry; dotnet run -- all`: builds the Foundry IQ index, knowledge base and project connection.
4. `foundry\create-agents.ps1 -Enrichment on|off`: creates the toolbox, note-taker and voice agent.
5. Create a Dataverse application user (System Administrator) for the app identity (`APP_PRINCIPAL_ID`), the same way `setup-foundry.ps1` does for the project identity.
6. Manual: the Event Grid `IncomingCall` subscription to `<PUBLIC_BASE_URL>/api/incoming`, and moving the phone number.

## Intake-gateway (Option A) deploy and verify

1. `Copy-Item infra\gateway.parameters.example.json infra\gateway.<customer>.json` and fill it in (keep it out of git).
2. `.\infra\deploy-gateway.ps1 -ParametersFile infra\gateway.<customer>.json` builds the image, creates or updates the app, reuses or generates secrets, derives `PUBLIC_BASE_URL`, creates the Event Grid subscription with the matching `?s=` and runs the verifier.
3. `.\infra\verify-gateway.ps1 -ResourceGroup <rg> -AppName <app>` is read-only. Use it whenever Event Grid shows calls but the app is not invoked. It checks required env var names, `PUBLIC_BASE_URL` vs the FQDN, `/healthz`, and that the subscription `?s=` equals `EVENTGRID_SECRET`. It also flags `LINE_NUMBERS` drops.

## Notes

- Create the Event Grid subscription only **after** the app is running: Event Grid validates the endpoint on creation.
- If several apps share one ACS resource, filter `IncomingCall` events by `data.to.rawId` so each subscription only receives its own number.
- Keep your `.env` (copied from `.env.example`) out of source control.
