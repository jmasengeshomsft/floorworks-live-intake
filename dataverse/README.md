# dataverse — schema and seed data

> **`config.json` is the one file that names the tables** the provisioner, the gateway and the voice agent use. Change tables there, then re-provision, redeploy the gateway and republish the agents. See [docs/18-table-configuration.md](../docs/18-table-configuration.md).

`provision-dataverse.ps1` creates and seeds the manufacturing data model through the Dataverse Web API. It is
**idempotent**: each run adds only what is missing.

```powershell
./provision-dataverse.ps1 -OrgUrl https://<org>.crm.dynamics.com
```

Requires `az login` as a user who can customize the environment. `-OrgUrl` is mandatory.

## What it creates

| Table | Purpose |
|---|---|
| `account` (standard) | Customers; the caller is matched on `telephone1`-`telephone3` |
| `contact` (standard, optional) | The people who call; matched on `mobilephone`/`telephone1`-`telephone3`, each belongs to an account |
| `bdm_equipment` | Assets (name, asset number, plant, manufacturer, status), each owned by an account |
| `bdm_serviceticket` | Work orders, created only by rep Submit |
| `bdm_servicerequestdraft` | Live drafts built during calls |

Plus lookups `CustomerId` (to account) on equipment, ticket and draft, `ContactId` (to contact, the caller) on ticket and draft, `EquipmentId` on ticket and draft, `TicketId` on draft, and seed rows (7 accounts, 10 contacts, 6 equipment, 6 tickets). The tables the gateway uses are named in `config.json`. Full column reference and diagrams: [docs/05-data-model.md](../docs/05-data-model.md).

## Change the model

- Add columns in the script with helpers `StrAttr`, `MemoAttr`, `IntAttr`, `DecAttr`, `DateAttr`, `PickAttr`; add lookups
  with `EnsureLookup`. Re-run the script.
- Then update `src/VoiceIntake/Drafts.cs`, `RepUi.cs` and `foundry/prompts/notetaker-instructions.md` ([checklist](../docs/05-data-model.md#extending-the-model)).

## Related admin steps

Enable *Allow MCP clients to interact with Dataverse MCP server* in the Power Platform admin center, and create application
users for the Foundry project identity and the VoiceIntake identity ([deployment guide](../docs/03-deployment.md)).

`.env.example` shows the one setting the script reads.
