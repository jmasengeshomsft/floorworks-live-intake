# 04 · Configuration reference

## VoiceIntake environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ACS_CONNECTION_STRING` | Yes | — | Connection string of the ACS resource that owns the phone number |
| `PUBLIC_BASE_URL` | Yes | — | External `https://` URL of the app. Used for ACS callbacks and the `wss://` media socket |
| `FOUNDRY_PROJECT_ENDPOINT` | Yes | — | `https://<account>.services.ai.azure.com/api/projects/<project>` |
| `SPEECH_KEY` | Yes | — | Azure Speech key used for per-speaker transcription |
| `SPEECH_REGION` | No | `eastus` | Region of the Speech resource |
| `DATAVERSE_URL` | No | `https://floorworks.crm.dynamics.com` | Dataverse environment URL. **Set this explicitly** in your deployment |
| `VOICE_AGENT_NAME` | No | `floorworks-voice-agent-app` | Foundry voice agent to open on each call |
| `NOTETAKER_AGENT` | No | `floorworks-notetaker` | Foundry prompt agent used for extraction |
| `REP_TEAMS_OBJECT_IDS` | No | empty | Comma-separated Entra object ids of reps the AI can add to a call |
| `REP_KEY` | No | empty | Shared secret for the console and `/api/rep/*`. **Empty disables auth** |
| `TRANSCRIPTION_MODE` | No | `acs-stt` | `acs-stt` runs Azure Speech per speaker. `voice-agent` uses the voice agent's own transcription for the caller and starts customer STT only once the rep joins |
| `VOICE_SESSION_TUNING` | No | off | `on` sends noise-reduction, echo-cancellation and semantic-VAD `session.update`s to the voice agent (experimental; the agent may reject them, logged as `VA ERROR`) |
| `LOCAL_DEV` | No | unset | `1` uses `AzureCliCredential` instead of managed identity. Local runs only |

Store `ACS_CONNECTION_STRING`, `SPEECH_KEY` and `REP_KEY` as Container Apps **secrets** and reference them with
`secretref:`. Never commit them.

`src/VoiceIntake/.env.example` lists the same variables for local runs.

## Script parameter files

Each script folder has a `.env.example` listing the values its scripts expect. Copy to `.env` (git-ignored).

| File | Keys |
|---|---|
| `foundry/.env.example` | `SubscriptionId`, `ResourceGroup`, `FoundryAccount`, `FoundryProject`, `OrgUrl`, `AgentName`, `ConnectionName` |
| `infra/.env.example` | `ResourceGroup`, `ContainerAppsEnv`, `AcrName`, `AcsName`, `FoundryAccount`, `FoundryProject`, `AgentName`, `RepObjectIds`, `CallControllerUrl`, `BotName`, `EventSubscriptionName` |
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

## Reference deployment values

These belong to the proof of concept and are shown so you can map the docs to a running system. Replace with your own.

| Item | PoC value |
|---|---|
| Resource group | `sharepointdemo` |
| Container Apps environment | `cae-sharepointdemo` |
| App | `floorworks-voice-intake` |
| Registry | `acrspdemohscljr` |
| ACS | `acs-jmdeere-demo` |
| Foundry project | `ai-foundry-demo-jm` / `firstProject` |
| Dataverse | `https://floorworks.crm.dynamics.com` |
