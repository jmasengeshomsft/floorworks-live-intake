# 04 · Configuration reference

## VoiceIntake environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ACS_CONNECTION_STRING` | Yes | — | Connection string of the ACS resource that owns the phone number |
| `PUBLIC_BASE_URL` | Yes | — | External `https://` URL of the app. Used for ACS callbacks and the `wss://` media socket |
| `FOUNDRY_PROJECT_ENDPOINT` | Yes | — | `https://<account>.services.ai.azure.com/api/projects/<project>` |
| `SPEECH_KEY` | Yes | — | Azure Speech key used for per-speaker transcription |
| `SPEECH_REGION` | No | `eastus` | Region of the Speech resource |
| `DATAVERSE_URL` | Yes | — | Dataverse environment URL, e.g. `https://<org>.crm.dynamics.com` |
| `VOICE_AGENT_NAME` | No | `floorworks-voice-agent-app` | Foundry voice agent to open on each call |
| `NOTETAKER_AGENT` | No | `floorworks-notetaker` | Foundry prompt agent used for extraction |
| `REP_TEAMS_OBJECT_IDS` | No | empty | Comma-separated Entra object ids of reps the AI can add to a call |
| `REP_KEY` | Yes | — | Shared secret for the console and `/api/rep/*`. **If empty, all rep access is denied** |
| `TRANSCRIPTION_MODE` | No | `acs-stt` | `acs-stt` runs Azure Speech per speaker. `voice-agent` uses the voice agent's own transcription for the caller and starts customer STT only once the rep joins |
| `VOICE_SESSION_TUNING` | No | off | `on` sends noise-reduction, echo-cancellation and semantic-VAD `session.update`s to the voice agent (experimental; the agent may reject them, logged as `VA ERROR`) |
| `RINGBACK` | No | `on` | `on` plays a US-style ringback tone to the caller while the rep is being connected (stops when the rep joins or the transfer fails); `off` disables it |
| `AFTER_HOURS` | No | `off` | `on` always runs after-hours mode (no transfer; the agent is told so and `transfer_to_rep` is declined). `auto` decides per call from `SERVICE_DAYS` (`Mon-Fri`), `SERVICE_HOURS` (`07:00-18:00`) and `SERVICE_TZ` (`America/Chicago`). `AFTER_HOURS_NOTE` overrides the instruction given to the agent |
| `LOCAL_DEV` | No | unset | `1` uses `AzureCliCredential` instead of managed identity. Local runs only |
| `ENRICHMENT` | No | `off` | `on` enables Foundry IQ enrichment: the note-taker (not the voice agent) queries the `floorworks-kb` knowledge base and returns an "AI suggested" block shown on the rep console and appended to the ticket notes. `off` leaves the flow unchanged. Requires the `bdm_enrichmentjson` column and the note-taker published with `$env:ENRICHMENT='on'` |

Store `ACS_CONNECTION_STRING`, `SPEECH_KEY` and `REP_KEY` as Container Apps **secrets** and reference them with
`secretref:`. Never commit them.

`src/VoiceIntake/.env.example` lists the same variables for local runs.

## Script parameter files

Each script folder has a `.env.example` listing the values its scripts expect. Copy to `.env` (git-ignored).

| File | Keys |
|---|---|
| `foundry/.env.example` | `SubscriptionId`, `ResourceGroup`, `FoundryAccount`, `FoundryProject`, `OrgUrl`, `ConnectionName` |
| `infra/.env.example` | `ResourceGroup`, `ContainerAppsEnv`, `AcrName`, `AcsName`, `FoundryAccount`, `FoundryProject`, `RepObjectIds`, `AppUrl`, `BotName`, `EventSubscriptionName` |
| `teams/.env.example` | `TenantId`, `BotAppId`, `AcsImmutableId`, `AcsHost`, `ResourceAccountUpn`, `PhoneNumber`, `RepObjectIds` |
| `dataverse/.env.example` | `OrgUrl` |

## Foundry settings

| Setting | Value |
|---|---|
| Voice agent name | `floorworks-voice-agent-app` |
| Note-taker name | `floorworks-notetaker` |
| Toolbox name | `floorworks-dataverse` |
| Dataverse connection | `dataverse-mfg-mi` (ProjectManagedIdentity). Do **not** use `dataverse-mfg` (OAuth2) for calls |
| Dataverse MCP URL | `https://<org>.crm.dynamics.com/api/mcp` |
| Preview feature headers | `Foundry-Features: VoiceAgents=V1Preview` for voice agents; `Foundry-Features: Toolboxes=V1Preview` for toolboxes |

## Dataverse settings

| Setting | Where |
|---|---|
| Allow MCP clients | Power Platform admin center → Environment → Settings → Features |
| Allowed MCP clients list | Includes the Foundry project's managed identity |
| Application users | Foundry project identity; VoiceIntake app identity |

## Teams and telephony

| Item | Notes |
|---|---|
| Number routing | Number is assigned to the **resource account**, which is bound to the ACS identity |
| Event Grid | `IncomingCall` events routed to `https://<fqdn>/api/incoming`. Add a filter if you share the ACS resource with other call apps (exclude other targets by `data.to.rawId`) |
| Rep readiness | Reps in `REP_TEAMS_OBJECT_IDS` must be signed in to Teams, Available, and Enterprise Voice enabled |

## Example values

Names below are placeholders; `azd` derives the real ones from your environment name.

| Item | Example |
|---|---|
| Resource group | `rg-floorworks` |
| Container Apps environment / registry | created by `azd provision` |
| ACS | `<your-acs>` |
| Foundry project | `<account>` / `<project>` |
| Dataverse | `https://<org>.crm.dynamics.com` |
