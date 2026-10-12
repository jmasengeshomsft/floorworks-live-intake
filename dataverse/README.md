# dataverse — schema and seed data

`provision-dataverse.ps1` creates and seeds the manufacturing data model through the Dataverse Web API. It is
**idempotent**: each run adds only what is missing.

```powershell
./provision-dataverse.ps1 -OrgUrl https://<org>.crm.dynamics.com
```

Requires `az login` as a user who can customize the environment. `-OrgUrl` is mandatory.

## What it creates

| Table | Purpose |
|---|---|
| `bdm_equipment` | Assets (name, asset number, plant, manufacturer, status, install date, customer) |
| `bdm_part` | Parts (part number, supplier, category, unit cost) |
| `bdm_qualityalert` | Quality issues on parts and equipment |
| `bdm_serviceticket` | Real work orders, created only by rep Submit |
| `bdm_customer` | Customer accounts |
| `bdm_servicerequestdraft` | Live drafts built during calls |

Plus nine lookups wiring them together and seed rows (6 equipment, 6 parts, alerts, tickets, customers).
Full column reference and diagrams: [docs/05-data-model.md](../docs/05-data-model.md).

## Change the model

- Add columns in the script with helpers `StrAttr`, `MemoAttr`, `IntAttr`, `DecAttr`, `DateAttr`, `PickAttr`; add lookups
  with `EnsureLookup`. Re-run the script.
- Then update `src/VoiceIntake/Drafts.cs`, `RepUi.cs` and `foundry/notetaker-instructions.md` ([checklist](../docs/05-data-model.md#extending-the-model)).

## Related admin steps

Enable *Allow MCP clients to interact with Dataverse MCP server* in the Power Platform admin center, and create application
users for the Foundry project identity and the VoiceIntake identity ([deployment guide](../docs/03-deployment.md)).

`.env.example` shows the one setting the script reads.
