# 18 · Table configuration

**One file decides which Dataverse tables the whole solution uses: [`dataverse/config.json`](../dataverse/config.json).**
The deployment tools, gateway and prompt rendering share it. The runtime supports role mappings/metadata discovery,
but that does **not** make provisioning a general-purpose customer-schema migration engine.
The provisioner has supported standard/custom branches; review compatibility and approve additions for other existing tables.

## What the file looks like

```json
{
  "profile": "standard-account",
  "roles": {
    "customer":  "account",
    "contact":   "contact",
    "equipment": "bdm_equipment",
    "ticket":    "bdm_serviceticket",
    "draft":     "bdm_servicerequestdraft"
  },
  "describe": { "account": { "table": "Customers ...", "name": "Company name" } },
  "ticketDescriptionMax": 4000
}
```

| Key | Meaning |
|---|---|
| `roles` | Which Dataverse table plays each role. Values are logical table names. `contact` is optional: omit it and the solution matches callers on the account phone only. |
| `describe` | Plain-language table and column meanings. They are rendered into the voice agent prompt so the agent queries the right tables. |
| `ticketDescriptionMax` | Description length limit; derived from the org when omitted. |
| `profile` | A label only. |
| Anything else | Optional explicit overrides (`names`, `columns`, `options`, ...). Anything written here wins over what is derived. See [04](04-configuration.md). |

Columns, lookups, phone columns and priority option values are **not** listed. They are discovered from the org's
metadata, so a typical customer edits only `roles`.

## Default data model

```mermaid
erDiagram
    ACCOUNT ||--o{ CONTACT : "employs"
    ACCOUNT ||--o{ BDM_EQUIPMENT : "owns"
    ACCOUNT ||--o{ BDM_SERVICETICKET : "requests"
    CONTACT ||--o{ BDM_SERVICETICKET : "reported by"
    CONTACT ||--o{ BDM_SERVICEREQUESTDRAFT : "called in"
    ACCOUNT ||--o{ BDM_SERVICEREQUESTDRAFT : "linked to"
    BDM_EQUIPMENT ||--o{ BDM_SERVICETICKET : "work on"
    BDM_EQUIPMENT ||--o{ BDM_SERVICEREQUESTDRAFT : "linked to"
    BDM_SERVICEREQUESTDRAFT |o--o| BDM_SERVICETICKET : "becomes on submit"
```

| Role | Default table | Kind | Why |
|---|---|---|---|
| customer | `account` | standard | The company. Equipment and tickets belong to it. |
| contact | `contact` | standard, optional | The person who calls. Belongs to an account. |
| equipment | `bdm_equipment` | custom | Assets owned by an account. |
| ticket | `bdm_serviceticket` | custom (or `incident`) | Created only by a rep's Submit. |
| draft | `bdm_servicerequestdraft` | custom | Live draft built during the call. |

Both provisioning modes also create/upgrade the internal `bdm_calljournal` checkpoint table, the draft's
`bdm_transcriptversion` column and its call-ID uniqueness key. This is required gateway infrastructure, not an
agent business-table role; it is not exposed as an agent tool. See [Dataverse journaling setup](../dataverse/README.md#gateway-journaling-is-part-of-normal-provisioning).

Details and columns: [05](05-data-model.md).

## Who reads the file

```mermaid
flowchart LR
    C[dataverse/config.json]
    C --> P[provision-dataverse.ps1<br/>schema + journal; opt-in seed]
    C --> D[deploy-gateway.ps1<br/>sets DATAVERSE_SCHEMA env var]
    C --> B[azd / main.bicep<br/>loads config at compile time]
    C --> A[setup-agents.ps1<br/>renders the voice prompt]
    D --> G[Gateway<br/>queries, draft saves, Submit]
    B --> G
    A --> V[Voice agent<br/>Dataverse MCP queries]
```

| Consumer | How it uses the config | When it picks up a change |
|---|---|---|
| **Provisioner** | Idempotent supported-profile schema plus required draft/journal integrity. Arbitrary existing-table reuse needs review, not just a role-name change. | On the next approved run. |
| **Gateway** | Receives `DATAVERSE_SCHEMA` as JSON (base64 from PowerShell, plain JSON from Bicep). Reads metadata and maps canonical names to real names. | On deployment that updates the environment configuration. No image rebuild required. |
| **Voice agent** | `setup-agents.ps1` fills the prompt placeholders (table names, phone columns, contact step, schema notes) from the config. The prompt carries a `dataverse-config: <hash>` comment so you can tell which config an agent version was built from. | When agents are republished. |
| **Note-taker** | Uses published prompts/tools for read-only lookup; the gateway validates output and owns writes. | Republish agents and update gateway configuration together. |

The gateway and the voice agent are configured separately, so **a table change needs both to be updated**. If they
disagree, the agent queries one set of tables while the gateway saves to another.

## Caller matching

1. The caller's number is matched against **contact** phones (`mobilephone`, then `telephone1`-`telephone3`).
2. A unique contact match resolves to its **account**; the contact is stored on the draft as the caller.
3. Otherwise the account's own phones (`telephone1`-`telephone3`) are tried.
4. No match: the agent asks for name and company and never invents a customer.

The voice agent may state a matched person's name only as a confirming question ("Am I speaking with ... from ...?").
Numbers are compared on their last digits, so any stored format works (`(405) 602-9804`, `+1 405 602 9804`, `4056029804`).

## Changing the tables

1. Edit `roles` in `dataverse/config.json` (for example `"ticket": "incident"` to use Cases).
2. Review the supported profile/required additions, then run `dataverse\provision-dataverse.ps1 -OrgUrl <org>`. Use `-SeedDemoData` only for sample rows.
3. Validate: `cd src\intake-gateway; python -m app.preflight` with `DATAVERSE_SCHEMA_FILE=..\..\dataverse\config.json` and `DATAVERSE_URL` set. It reports missing tables, columns and lookups.
4. Deploy updated configuration with PowerShell or azd/Bicep. A previously built Python image is fine; the config is not baked into it.
5. Republish the agents: `foundry\setup-agents.ps1 -ParamsFile <params>`.

## Local runs and tests

From `src\intake-gateway`, set `DATAVERSE_SCHEMA_FILE=..\..\dataverse\config.json` for local runs/preflight.
Use a path relative to the process working directory, not the source file. There are no separate shipped per-profile files.

## Rules of thumb

- Keep account (and contact) phone numbers filled in. They drive caller matching.
- Equipment belongs to the account, not to a person.
- A table named in `roles` must exist in the org. Otherwise the gateway cannot read its metadata (`not found`) and preflight names what is missing, so run preflight before deploying.
- The gateway identity needs its own Dataverse application user. PowerShell attempts to create it; with azd, register it separately before application deployment. A 403 also warrants reviewing its table/relationship privileges.
- The params file's `dataverseSchema` overrides the file. Leave it empty unless you mean to.
