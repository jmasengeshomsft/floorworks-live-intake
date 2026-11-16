# Infrastructure and deployment

**PowerShell and azd both deploy the Python Intake Gateway.** The React UI is hosted separately.
Choose one owner for each Container App; do not alternate tools against the same app.
Start with the [deployment guide](../docs/03-deployment.md) for schema, identity, agent and telephony ordering.

| File | Purpose |
|---|---|
| `deploy-gateway.ps1` | Build/update Python on existing infrastructure; secrets, access, Event Grid and verification |
| `init-gateway-params.ps1` | Discover resources and produce a private gateway parameter file |
| `gateway.parameters.example.json` | Tracked parameter template; keep customer copies out of Git |
| `verify-gateway.ps1` | Read-only configuration, scale, health, Speech and Event Grid checks |
| `speech-auth.ps1` | Shared Speech authentication/resource validation |
| `after-hours.ps1` | Shared explicit office-hours validation and environment mapping; preserves legacy omissions |
| `main.bicep`, `main.parameters.json` | azd infrastructure and current Python environment/secret contract |
| `acs.bicep`, `foundry.bicep` | Optional resource creation for fresh azd environments |
| `foundry-roles.bicep`, `search-model-role.bicep` | Gateway Foundry/Speech and optional KB model access |
| `setup-tpe-azure.ps1`, `rebind-tpe-azure.ps1` | Telephony bootstrap/rebinding with authenticated Event Grid setup |
| `verify-bot.ps1`, `verify-teams-side.ps1` | Specialist telephony diagnostics |

## PowerShell path

```powershell
# From the repository root; edit the private copy before deployment.
Copy-Item .\infra\gateway.parameters.example.json .\infra\gateway.customer.json
.\infra\deploy-gateway.ps1 -ParametersFile .\infra\gateway.customer.json -Plan
.\infra\deploy-gateway.ps1 -ParametersFile .\infra\gateway.customer.json -Rebuild
.\infra\verify-gateway.ps1 -ResourceGroup <rg> -AppName <app>
```

The deployer reuses/generates secrets, grants the gateway's Azure access and registers its Dataverse application user.
Its demo Dataverse role is broad; replace it for customer use. It does **not** provision the required Dataverse
schema/journal. Run the [Dataverse provisioner](../dataverse/README.md) first.

New gateway files default to managed-identity Speech. Preflight checks resource kind, region and custom subdomain;
deployment grants **Cognitive Services Speech User** and clears stale `SPEECH_KEY` environment references.
Legacy key settings remain supported. See [Speech migration](../docs/16-intake-gateway-deploy.md#migrate-live-notes-to-managed-identity).
New files also select `afterHours=auto`, `serviceTz=America/Chicago`, `serviceDays=Mon-Fri`, and
`serviceHours=07:00-18:00`. Review these before deploying. The initializer supports corresponding
`-AfterHours`, `-ServiceTz`, `-ServiceDays`, and `-ServiceHours` arguments. Legacy omissions leave live settings
unchanged. Timezone validation needs PowerShell 7.2+ with ICU; use IANA names, not Windows-only timezone names.
See [automatic office hours](../docs/04-configuration.md#automatic-office-hours) for boundaries and overrides.

## azd path

`azure.yaml` retains service key `voice-intake` but builds `src\intake-gateway\Dockerfile`.
Bicep provisions ACR, logging, a Container Apps environment/app and identity roles; it can also create ACS/Foundry
and optional Search. It sends `dataverse\config.json`, API/HMAC secret references, current agent/transfer settings and
keyless Speech using the Foundry account's resource ID/region.

Use the [staged azd instructions](../docs/03-deployment.md#3b---azd-deployment):
**provision -> configure both Dataverse identities and publish agents -> deploy -> authenticate Event Grid -> verify**.
The public bootstrap image is not the gateway. Every reprovision resets the app image, so redeploy after provisioning.
azd does not create Dataverse schema/application users, publish agents or bind the Teams number.

The separate .NET CLI in `foundry` remains available for optional KB construction. Search/model RBAC is retained;
`RUNNER_PRINCIPAL_ID` optionally grants the KB builder's Search roles.

## Telephony integration

`setup-tpe-azure.ps1` can prepare bot/ACS prerequisites before gateway deployment. Without `-EventGridSecret`, it warns
and skips subscription creation. Once the gateway is running, supply the matching `EVENTGRID_SECRET` to create
authenticated delivery, or let `deploy-gateway.ps1` wire it automatically.

Event Grid validates the endpoint during creation, so a bootstrap image is insufficient. Check for competing
`IncomingCall` subscriptions; when sharing ACS, filter by the intended `data.to.rawId`.
Teams resource accounts may arrive as `28:orgid:<object-id>`, not an E.164 number.

## Deployment constraint

Both paths set min/max replicas to **one**. This does not prevent revision overlap or drain active calls.
Pause routing, finish calls and stop old revisions before recovery/startup of the new gateway.
See [safe upgrades](../docs/08-operations.md#safe-upgrades-and-rollback); local checks do not certify live acceptance.

## Offline checks

```powershell
.\infra\tests\after-hours.Tests.ps1
.\infra\tests\speech-auth.Tests.ps1
.\infra\tests\setup-tpe-azure.Tests.ps1
.\dataverse\tests\provision-journal.Tests.ps1
az bicep build --file .\infra\main.bicep --stdout > $null
```

The PowerShell regressions use mocked resources/CLI/Web API calls; Bicep compilation does not deploy anything.
