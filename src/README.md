# src — application code

| Project | Status | Purpose |
|---|---|---|
| [`VoiceIntake`](VoiceIntake) | **Current product** | One .NET 8 app: answers the ACS call, bridges audio to the Foundry voice agent, transcribes each speaker, runs the note-taker, persists the draft, serves the Rep Console and API |

`FloorWorksLiveIntake.sln` at the repo root references VoiceIntake and the Foundry IQ CLI.

## VoiceIntake files

| File | Responsibility |
|---|---|
| `Program.cs` | Endpoints, `/ws/{callId}` bridge, voice-agent WebSocket, per-speaker STT, transfer handling, reconnect logic, Rep API |
| `Extractor.cs` | `CallState` and the note-taker client (`RunAsync`, `FinalizeAsync`, `OnResult`) |
| `Drafts.cs` | Dataverse access: `SaveNotes`, `Update`, `Submit`, customer and equipment search |
| `RepUi.cs` | Embedded HTML and JS for `/rep` |
| `Dockerfile` | Multi-stage .NET 8 build, listens on port 8080 |

## Build and run

```powershell
dotnet build src/VoiceIntake
az acr build -r <acr> -t voice-intake:<tag> src/VoiceIntake        # build the image in the cloud
```

Run locally (no phone, limited use because ACS must reach a public URL; use a dev tunnel if you want a real call):

```powershell
$env:LOCAL_DEV = '1'      # use your az login instead of managed identity
# set the variables from .env.example, then:
dotnet run --project src/VoiceIntake
```

## Rules for contributors

1. **Agents never create tickets.** Only `DraftStore.Submit`, reached from `POST /api/rep/draft/{id}/submit`, may create a
   `bdm_serviceticket`.
2. **Rep edits win.** Any new agent-written field must go through `Put(...)` in `SaveNotes` so it honors `bdm_editedfields`.
3. **Keep the form shape in lockstep:** Dataverse column ↔ `DraftSelect`/`Update` ↔ `RepUi.cs` ↔ note-taker output schema.
4. **No secrets in code.** Read configuration from environment variables.
5. **Do not hold call state outside `CallState`** unless you also solve multi-replica behavior.
6. Prefer a new image tag per deployment.
