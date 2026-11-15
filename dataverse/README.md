# dataverse — schema and seed data

> **`config.json` is the one file that names the tables** the provisioner, the gateway and the voice agent use. Change tables there, then re-provision, redeploy the gateway and republish the agents. See [docs/18-table-configuration.md](../docs/18-table-configuration.md).

`provision-dataverse.ps1` provisions the manufacturing data model and required gateway journal storage through
the Dataverse Web API. It is **idempotent**: each run adds only what is missing. Sample data is opt-in.

```powershell
.\provision-dataverse.ps1 -OrgUrl https://<org>.crm.dynamics.com
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
| `bdm_calljournal` | Internal gateway transcript/extraction checkpoints; not exposed as an agent tool |

Plus lookups `CustomerId` (to account) on equipment, ticket and draft, `ContactId` (to contact, the caller) on ticket and draft, `EquipmentId` on ticket and draft, and `TicketId` on draft. Demo rows require `-SeedDemoData` or `seed-demo-data.ps1`. The tables the gateway uses are named in `config.json`. Full column reference and diagrams: [docs/05-data-model.md](../docs/05-data-model.md).

## Gateway journaling is part of normal provisioning

The same command handles fresh setup and upgrades, in both standard-table and custom-table modes. No separate
journaling flag or script is required. Before the script reports gateway journaling ready, it:

- Creates or fills in `bdm_calljournal` with `bdm_callid`, `bdm_checkpointjson`, `bdm_version` and `bdm_complete`.
- Adds `bdm_transcriptversion` to existing drafts.
- Creates the draft's single-column `bdm_callid` uniqueness key if missing and waits for it to become Active.
- Verifies optimistic concurrency on the draft and journal tables.

The names use `-Prefix` when supplied instead of the default `bdm`. Existing call data is not deleted or seeded.
Duplicate call IDs can prevent index creation: provisioning stops with instructions to reconcile them rather
than deleting duplicates. A pending/failed key or missing concurrency support prevents a ready result.
Drain/stop an existing gateway before upgrading, then verify its startup after provisioning completes.
The gateway application user needs read/create/write access to the journal and draft tables; provisioning
the schema does not by itself grant those runtime permissions.

Offline regression checks (mocked Web API, no Azure or Dataverse requests), from the repository root:

```powershell
.\dataverse\tests\provision-journal.Tests.ps1
```

## Change the model

- Add columns in the script with helpers `StrAttr`, `MemoAttr`, `IntAttr`, `DecAttr`, `DateAttr`, `PickAttr`; add lookups
  with `EnsureLookup`. Re-run the script.
- Keep the Python gateway's schema/API/extraction models, `DraftStore`, React form and note-taker prompt consistent
  ([checklist](../docs/05-data-model.md#extending-the-model)).

## Related admin steps

Enable *Allow MCP clients to interact with Dataverse MCP server* in the Power Platform admin center, and create application
users for the Foundry project identity and the Python gateway identity ([deployment guide](../docs/03-deployment.md)).

`.env.example` shows the one setting the script reads.
