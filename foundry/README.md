# foundry — agents, toolbox and Dataverse wiring

| File | Purpose |
|---|---|
| `setup-foundry.ps1` | Grants the Foundry **project managed identity** access to Dataverse (application user, role, allowed MCP client), creates the `dataverse-mfg-mi` connection, publishes the original extractor agent |
| `notetaker-instructions.md` | Current prompt for the `floorworks-notetaker` agent (read-only, structured JSON) |
| `instructions.md` | Prompt of the **legacy** extractor agent (`floorworks-intake-extractor`) from the first pipeline |
| `publish-draft-flow-agents.ps1` | Publishes new versions of the note-taker and the receptionist voice agent for the draft-then-submit flow |
| `toolbox.sample.json` | Example toolbox definition exposing the Dataverse MCP server to the voice agent |

## Typical flows

**First-time setup**
1. `setup-foundry.ps1 …` ([parameters](../docs/04-configuration.md)).
2. Create the toolbox `floorworks-dataverse` from `toolbox.sample.json` (with your org URL) using the toolboxes API.
3. Create the voice agent `floorworks-voice-agent-app` in Foundry with the toolbox attached and the `transfer_to_rep` tool.
4. Create the prompt agent `floorworks-notetaker` with `notetaker-instructions.md` and the `dataverse-mfg-mi` MCP connection.

**Change the AI's behavior**
- Edit the instructions, run `publish-draft-flow-agents.ps1` (add `voice-only` to skip the note-taker), place a test call.

Details, API headers and the prompt-change checklist: [docs/06-agents.md](../docs/06-agents.md).

## Hard rules

- Use the **managed-identity** connection (`dataverse-mfg-mi`). The OAuth2 connection cannot work on phone calls.
- Neither agent may create, update or delete records.
- Publish a new voice-agent version after publishing a new toolbox version.
- Voice agents need header `Foundry-Features: VoiceAgents=V1Preview`; toolboxes need `Toolboxes=V1Preview`.
