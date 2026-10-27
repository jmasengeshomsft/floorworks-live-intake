# foundry — agents, toolbox and Dataverse wiring

| File | Purpose |
|---|---|
| `setup-agents.ps1` | **One-stop project setup.** Give it a Foundry project (`-ProjectEndpoint`, or pick one). Preflight, Dataverse grant for the project MI, `dataverse-mfg-mi` connection, toolbox, note-taker (`require_approval never`) and voice agent (toolbox + `transfer_to_rep`), then reads everything back. Optional: `-VoiceAgent`, `-NotetakerAgent`, `-ToolboxName`, `-Enrichment`, `-ParamsFile`, `-Plan` (preflight only) |
| `add-transfer-tool.ps1` | Adds the `transfer_to_rep` function tool to an existing voice agent |
| `setup-foundry.ps1` | Grants the Foundry **project managed identity** access to Dataverse (application user, role, allowed MCP client), creates the `dataverse-mfg-mi` connection, publishes the original extractor agent |
| `prompts\voice-instructions.md` | Prompt of the voice agent. Edit it, then re-run `setup-agents.ps1` (no script changes needed) |
| `prompts\notetaker-instructions.md` | Prompt of the note-taker agent (read-only, structured JSON). Its `ENRICHMENT:BEGIN/END` section is included only when enrichment is on. Edit, then re-run `setup-agents.ps1` |
| `instructions.md` | Prompt of the **legacy** extractor agent (`floorworks-intake-extractor`) from the first pipeline |
| `publish-draft-flow-agents.ps1` | Publishes new versions of the note-taker and the receptionist voice agent for the draft-then-submit flow |
| `toolbox.sample.json` | Example toolbox definition exposing the Dataverse MCP server to the voice agent |
| `FloorWorks.FoundryIq.csproj`, `Program.cs`, `Corpus.cs` | .NET CLI that builds the Foundry IQ knowledge base (`dotnet run -- preflight\|all\|ask "..."`) |

Full guide: [docs/17-agent-setup.md](../docs/17-agent-setup.md).

## Foundry IQ CLI

Builds the synthetic FloorWorks corpus (66 chunks from the Dataverse seed data) into index `floorworks-kb-index`, knowledge source `floorworks-kb-source`, KB `floorworks-kb`, and project connection `floorworks-kb` on your Azure AI Search service (`azd` provisions one; read it with `azd env get-value SEARCH_SERVICE`). Required env vars: `SEARCH_SERVICE`, `AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP`, `FOUNDRY_ACCOUNT`, `FOUNDRY_PROJECT`. The Foundry account needs `text-embedding-3-small` and `gpt-4.1` deployments. Run from this folder after `az login`: `dotnet run -- preflight`, `dotnet run -- all`, `dotnet run -- ask "Hydraulic Press H1 valve leaking"`. RBAC: project MI needs *Search Index Data Reader* (azd grants it); runner needs *Search Service Contributor* + *Search Index Data Contributor* (azd grants them if `RUNNER_PRINCIPAL_ID` is set). The search service's managed identity also needs *Cognitive Services OpenAI User* on the AI account (embedding vectorizer and KB model). The KB uses a chat model (`KB_LLM_DEPLOYMENT`, default `gpt-4.1`) because the Foundry MCP tool requests a reasoning effort above minimal. Verified against the live service (preview APIs).

## Typical flows

**First-time setup**
1. `.\setup-agents.ps1 -ProjectEndpoint <endpoint> -OrgUrl https://<org>.crm.dynamics.com -Plan` (check only), then re-run without `-Plan`.
2. Put the printed agent names and endpoint into the gateway settings (`-ParamsFile` does it for you) and run `infra\deploy-gateway.ps1`.

**Change the AI's behavior**
- Edit the instructions, run `publish-draft-flow-agents.ps1` (add `voice-only` to skip the note-taker), place a test call.

Details, API headers and the prompt-change checklist: [docs/06-agents.md](../docs/06-agents.md).

## Hard rules

- Use the **managed-identity** connection (`dataverse-mfg-mi`). The OAuth2 connection cannot work on phone calls.
- Neither agent may create, update or delete records.
- Publish a new voice-agent version after publishing a new toolbox version.
- Voice agents need header `Foundry-Features: VoiceAgents=V1Preview`; toolboxes need `Toolboxes=V1Preview`.
