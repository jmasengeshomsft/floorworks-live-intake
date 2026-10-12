# foundry — agents, toolbox and Dataverse wiring

| File | Purpose |
|---|---|
| `setup-foundry.ps1` | Grants the Foundry **project managed identity** access to Dataverse (application user, role, allowed MCP client), creates the `dataverse-mfg-mi` connection, publishes the original extractor agent |
| `notetaker-instructions.md` | Current prompt for the `floorworks-notetaker` agent (read-only, structured JSON) |
| `instructions.md` | Prompt of the **legacy** extractor agent (`floorworks-intake-extractor`) from the first pipeline |
| `publish-draft-flow-agents.ps1` | Publishes new versions of the note-taker and the receptionist voice agent for the draft-then-submit flow |
| `toolbox.sample.json` | Example toolbox definition exposing the Dataverse MCP server to the voice agent |
| `FloorWorks.FoundryIq.csproj`, `Program.cs`, `Corpus.cs` | .NET CLI that builds the Foundry IQ knowledge base (`dotnet run -- preflight\|all\|ask "..."`) |
| `notetaker-enrichment.md` | Extra note-taker instructions appended by the publish script when `$env:ENRICHMENT='on'` |

## Foundry IQ CLI

Builds the synthetic FloorWorks corpus (66 chunks from the Dataverse seed data) into index `floorworks-kb-index`, knowledge source `floorworks-kb-source`, KB `floorworks-kb`, and project connection `floorworks-kb` on your Azure AI Search service (`azd` provisions one; read it with `azd env get-value SEARCH_SERVICE`). Required env vars: `SEARCH_SERVICE`, `AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP`, `FOUNDRY_ACCOUNT`, `FOUNDRY_PROJECT`. The Foundry account needs `text-embedding-3-small` and `gpt-4.1` deployments. Run from this folder after `az login`: `dotnet run -- preflight`, `dotnet run -- all`, `dotnet run -- ask "Hydraulic Press H1 valve leaking"`. RBAC: project MI needs *Search Index Data Reader* (azd grants it); runner needs *Search Service Contributor* + *Search Index Data Contributor* (azd grants them if `RUNNER_PRINCIPAL_ID` is set). The search service's managed identity also needs *Cognitive Services OpenAI User* on the AI account (embedding vectorizer and KB model). The KB uses a chat model (`KB_LLM_DEPLOYMENT`, default `gpt-4.1`) because the Foundry MCP tool requests a reasoning effort above minimal. Verified against the live service (preview APIs).

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
