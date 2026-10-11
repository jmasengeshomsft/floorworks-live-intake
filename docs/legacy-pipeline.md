# Legacy two-service pipeline (archived README)

> Superseded by the unified VoiceIntake app. See [architecture](02-architecture.md). Paths below are relative to the repo root.

## Original overview

A customer calls a phone number; a service rep answers in **Microsoft Teams**; while they talk, the call is
transcribed live and a **Microsoft Foundry agent** fills in an intake form, checking equipment, quality alerts
and service tickets in **Dataverse** through the Dataverse **MCP server**.

```
PSTN caller ─► Teams number (resource account, TPE) ─► ACS Call Automation ─► Event Grid IncomingCall
                                                              │ answer + unmixed audio over WebSocket
                                  rep rings in Teams ◄────────┤
                                                              ▼
 call-controller (/api/incoming, /api/callbacks)      stream-processor (/ws/{callId}, /events/{callId} SSE)
                                                        Azure Speech STT per participant
                                                              │ transcript
                                                              ▼
                                       Foundry prompt agent ──MCP (project managed identity)──► Dataverse
```

SSE events on `/events/{callId}`: `partial`, `final` (transcripts with `speaker` = customer|rep), `fields`
(extracted form, confidence, matches, missing, next_question), `error`, `end`. Any UI (e.g. a Power Apps canvas app) can subscribe.

## Layout
| Path | Purpose |
|---|---|
| `src/CallController` | Answers incoming calls, starts media streaming, rings the rep(s) in Teams |
| `src/StreamProcessor` | WebSocket audio in, Speech STT, calls the Foundry agent, SSE out |
| `src/Simulator` | Replays WAV files as ACS-style frames to test without a phone call |
| `dataverse/` | Schema + seed data (Equipment, Part, Quality Alert, Service Ticket) |
| `foundry/` | Agent instructions and the script that wires Foundry to Dataverse |
| `infra/` | Container Apps deploy, bot / Event Grid setup |
| `teams/` | Teams resource account, number, rep voice enablement |

## Prerequisites
- Azure subscription, a Container Apps environment, ACR, an Azure Communication Services resource, a Foundry account + project
  (with a model deployment and an Azure Speech-capable key on the Foundry/AI Services account)
- A Power Platform environment with Dataverse (e.g. a free developer environment)
- Teams: a Teams admin, a phone number you own that can be assigned to a voice application,
  a *Teams Phone Resource Account* license, and reps with a Teams Phone license who stay signed in to Teams
- PowerShell 7, Azure CLI (+ `containerapp`, `communication` extensions), the `MicrosoftTeams` PowerShell module, .NET 8 SDK

## Setup
1. **Dataverse data** – `dataverse/provision-dataverse.ps1 -OrgUrl https://<org>.crm.dynamics.com`
2. **Services** – `infra/deploy.ps1 -ResourceGroup … -ContainerAppsEnv … -AcrName … -AcsName … -FoundryAccount … -FoundryProject … -RepObjectIds <rep-entra-object-id>`
3. **Foundry ↔ Dataverse** – `foundry/setup-foundry.ps1 …`: creates an application user for the **project managed identity**,
   adds it to Dataverse *Allowed MCP Clients*, creates a `ProjectManagedIdentity` connection, publishes the agent.
   (A per-user OAuth MCP connection returns 401 for unattended services; this avoids it.)
   Also enable *Allow MCP clients to interact with Dataverse MCP server* in Power Platform admin center → Environment → Settings → Features.
4. **TPE Azure side** – `infra/setup-tpe-azure.ps1 …` (bot with Teams calling channel, ACS service principal, Event Grid subscription)
5. **TPE Teams side** – `teams/setup-teams.ps1 …` (resource account, number, license, rep Enterprise Voice). Prompts for a device-code sign-in.
6. Call the number, answer in Teams, and watch `https://<stream-processor>/events/<callId>` (call ids appear in the controller logs).

> The scripts were consolidated from steps run manually during the proof of concept. `provision-dataverse.ps1`, the Foundry
> wiring and the two services were exercised end to end; `setup-tpe-azure.ps1`, `setup-teams.ps1` and `deploy.ps1` are
> parameterized rewrites that have not been run as-is. Expect small fixes.

## Local test without a phone call
```
$env:SPEECH_KEY=…; $env:SPEECH_REGION=eastus; $env:FOUNDRY_PROJECT_ENDPOINT=…; $env:LOCAL_DEV=1   # LOCAL_DEV uses your az login
dotnet run --project src/StreamProcessor
dotnet run --project src/Simulator -- ws://localhost:5000/ws/test customer=customer.wav rep=rep.wav   # 16 kHz/16-bit/mono WAVs
curl -N http://localhost:5000/events/test
```

## Known limits / gotchas
- Dataverse application user is granted **System Administrator** for the demo; create a least-privilege role for real use.
- Pointing the number at the resource account bypasses any Auto Attendant greeting; callers hear silence until the rep answers.
- Reps must be Enterprise Voice enabled and signed in to Teams, otherwise `AddParticipant` fails with 480 / 10037.
- Speaker roles are heuristic (first stream = customer, second = rep). Transcripts and extracted fields are kept in memory only.
- Speech may mis-spell names; consider a phrase list.
- `LOCAL_DEV=1` swaps in `AzureCliCredential`; deployed apps use managed identity.

