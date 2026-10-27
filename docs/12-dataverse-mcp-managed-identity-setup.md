# Manual setup: Foundry agent -> Dataverse MCP via project managed identity

Manual, step-by-step replication of `foundry\setup-foundry.ps1`.

## Prerequisites

- A Foundry account and project.
- A Dataverse environment, `https://<org>.crm.dynamics.com`.
- Admin rights on both (Azure/Foundry and Power Platform).

## Step 1: Get the project managed identity's client ID

1. Azure portal > Foundry project > **Identity** > turn **System assigned** ON. Copy the **Object ID**.
2. Microsoft Entra ID > **Enterprise applications**. Clear the default "Application type" filter.
3. Search for the Object ID. Open the result and copy its **Application (client) ID**.

## Step 2: Allow the client in Dataverse MCP

1. Power Platform admin center > your environment > **Settings** > **Product** > **Features**.
2. Enable **Allow MCP clients to interact with Dataverse MCP server**.
3. Add the client ID to **Active Allowed MCP Clients**.

The script does this by creating an `allowedmcpclients` record:

| Column | Value |
|---|---|
| name | `Foundry Project MI` |
| uniquename | `fw_foundryprojectmi` |
| applicationid | client ID from Step 1 |
| isenabled | `true` |

## Step 3: Create the application user

1. Admin center > environment > **Settings** > **Users + permissions** > **Application users**.
2. **New app user** > add the app by client ID, business unit = root.
3. Assign a security role:
   - Demo: System Administrator.
   - Production: a custom least-privilege role. Read on the tables needed; create/write only where required.

## Step 4: Create the Foundry project connection

ARM PUT:

```
PUT https://management.azure.com/<projectResourceId>/connections/dataverse-mfg-mi?api-version=2025-04-01-preview
```

```json
{
  "properties": {
    "category": "RemoteTool",
    "target": "https://<org>.crm.dynamics.com/api/mcp",
    "authType": "ProjectManagedIdentity",
    "audience": "https://<org>.crm.dynamics.com",
    "isSharedToAll": true,
    "metadata": { "type": "custom_MCP" }
  }
}
```

Portal alternative: Foundry **Management center** > **Connected resources**. Labels not verified; the ARM call is the tested path.

## Step 5: Add the MCP tool to the agent

```json
{
  "type": "mcp",
  "server_label": "dataverse-mfg-mi",
  "server_url": "https://<org>.crm.dynamics.com/api/mcp",
  "require_approval": "never",
  "project_connection_id": "dataverse-mfg-mi"
}
```

> **Pre-approval.** Steps 1-4 do not pre-approve anything. Approval is a property of the agent's tool entry, and an MCP tool defaults to **approval required**, which hangs an unattended call ("pending user approval"). `"require_approval": "never"` above is what prevents it. If you use a toolbox, the toolbox's inner tool needs `never` as well, and `allowed_tools` limits what is exposed. `foundry\setup-agents.ps1` does all of this; see [17](17-agent-setup.md).

## Step 6: Test

Open the agent in the playground and ask a Dataverse question. Expect results with **no sign-in prompt**.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| 401 | Application user missing (Step 3) or wrong `audience` (must be the org URL) |
| 403 | Security role lacks permission on the table |
| "MCP client not allowed" | Step 2 missing or client ID wrong |
| Works in playground, not at runtime | Connection not shared (`isSharedToAll`), or the agent runs an old version without the tool |

## Comparison with the OAuth-passthrough approach

The Microsoft Community Hub blog approach uses an app registration, client secret and OAuth identity passthrough.

| | Blog (OAuth passthrough) | This solution (managed identity) |
|---|---|---|
| Identity | App registration + client secret, `user_impersonation` | Project system-assigned managed identity, no secret |
| Setup | Redirect URI, interactive consent | Application user + allowed MCP client |
| Runs unattended | No; needs an interactive user. Not workable on a phone call | Yes |
| Audit | Per-user | Service identity (individual callers not distinguished) |
| Main risk | Secret handling, consent prompts | Over-privileged role on the application user |

## Notes for Deere

- Use the managed-identity connection for unattended agents such as voice.
- Replace System Administrator with a least-privilege role before production.
- Names (`dataverse-mfg-mi`, `dataverse-mfg`, `fw_foundryprojectmi`) are arbitrary; keep them consistent across the connection, tool and script. The tool name/label equals the connection name (`dataverse-mfg-mi`), so tools show as `dataverse-mfg-mi___<tool>`.
