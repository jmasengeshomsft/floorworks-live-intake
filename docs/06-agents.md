# 06 - AI agents

Two agents serve different roles. Neither is allowed to create tickets.

| Agent | Responsibility | Default setup name |
|---|---|---|
| Voice receptionist | Answers, collects details, reads business records, requests an explicit human handoff | `intake-voice-agent` |
| Note-taker | Extracts structured notes and optional enrichment from the transcript | `intake-note-taker` |

Use the names actually published in `VOICE_AGENT_NAME` and `EXTRACTOR_AGENT_NAME`.
The authoritative setup procedure is [17 - Agent setup](17-agent-setup.md), using
`foundry\setup-agents.ps1`. Older scripts/agent names are not required for this workflow.

## Publishing

Review `foundry\prompts\voice-instructions.md`, `foundry\prompts\notetaker-instructions.md` and
`dataverse\config.json` before setup. The script renders the shared table configuration into the prompts,
reuses/creates the managed-identity connection, and publishes versioned toolbox/agent definitions.

```powershell
.\foundry\setup-agents.ps1 -ProjectEndpoint https://<account>.services.ai.azure.com/api/projects/<project> `
  -OrgUrl https://<org>.crm.dynamics.com -Plan
# After reviewing the plan and project identity's Dataverse access:
.\foundry\setup-agents.ps1 -ProjectEndpoint https://<account>.services.ai.azure.com/api/projects/<project> `
  -OrgUrl https://<org>.crm.dynamics.com
```

`-GrantDataverseAccess` is opt-in and needs admin rights; its default demo role is broad.
It registers the **project identity**, not the gateway identity.

## Receptionist contract

The gateway opens the voice session and supplies call context. The receptionist uses read-only Dataverse tools
and requests `transfer_to_rep` only when the caller asks. The gateway, not the model, executes ACS call-control
operations and enforces after-hours behavior. Successful handoff mutes AI audio; failed transfer lets the AI continue.

Configure reps with `TRANSFER_TEAMS_OBJECT_IDS`. Teams voice readiness and the gateway's ACS permissions remain
deployment prerequisites. Reconnecting a voice session does not restore a live call after a process restart.

## Note-taker contract

Extraction consumes the transcript and current notes. A typical output is:

```json
{
  "fields": {
    "customer_name": null, "phone": null, "equipment_model": null,
    "equipment_serial_or_asset": null, "location": null, "complaint": null,
    "priority": null, "preferred_time": null, "summary": null
  },
  "matches": [],
  "missing": [],
  "next_question": null,
  "created": null,
  "action": "none",
  "error": null
}
```

The complete contract lives in
[`app/extraction_models.py`](../src/intake-gateway/app/extraction_models.py).
Matches must identify allowed tables and valid UUIDs; referenced records/customer relationships are checked.
Optional `EXTRACTION_SCHEMA` validates `fields` locally. Truncated or incompatible JSON is rejected, not repaired
into success; the last valid result remains available and failure/retry state is visible in the API/UI.

Extraction is serialized per call and may coalesce incoming speech. A final pass runs at call end; finalization
is bounded and can fail. Dataverse journaling can resume unfinished extraction after restart or retry persistence
of a checkpointed valid result without calling the model again. It does not replay function side effects.

## Tools and approval

The shared toolbox exposes `describe`, `read_query` and `search` by default, using the Foundry project's managed
identity. Both agent definitions pin the appropriate toolbox version/MCP endpoint; setup configures approval
as `never` so unattended calls do not stall awaiting a user click.

Keep the tool allowlist read-only and grant a corresponding least-privilege Dataverse role. Prompt instructions
alone are not an authorization boundary. Never expose the internal call journal as an agent tool.
If the connection/toolbox changes, republish both agents together.

## Optional Foundry IQ enrichment

Build the knowledge base using the separate [.NET Foundry IQ CLI](../foundry/README.md), then publish the agents
with `-Enrichment on` and enable `ENRICHMENT=on` on the gateway. Enrichment is a separate AI-suggested block,
not caller-said fields. A rep still reviews and submits the ticket.

## Change acceptance

After publishing, make a real call: verify tool access without approvals, explicit-only handoff, valid extraction,
preserved rep corrections and **no ticket before Submit**. Check failures and final extraction as well as the happy path.
Publishing an agent or passing local tests does not establish live acceptance.
