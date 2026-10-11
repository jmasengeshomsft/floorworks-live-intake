# 06 · AI agents

Two Microsoft Foundry agents work every call. Neither can create a service ticket.

| | Receptionist | Note-taker |
|---|---|---|
| Name | `floorworks-voice-agent-app` | `floorworks-notetaker` |
| Foundry type | Voice agent (realtime, speech to speech) | Prompt agent (text, structured JSON out) |
| Talks to the caller | Yes | Never |
| Triggered by | App opens a realtime WebSocket per call | App sends the transcript after each utterance and once at call end |
| Dataverse access | Via **toolbox** `floorworks-dataverse` | Via project connection `dataverse-mfg-mi` |
| Can write Dataverse | No (instructions forbid it) | No (instructions forbid it) |
| Special tool | `transfer_to_rep` | none |
| Instructions | Managed in Foundry (versioned) | `foundry/notetaker-instructions.md` |

## Receptionist (voice agent)

**Job:** answer, collect the caller's name, the equipment and the problem, look equipment up in Dataverse, and hand
over to a human only when asked.

Behavior rules in its instructions:
- Look up equipment by name or model using the Dataverse tools. Do **not** ask for an asset number.
- Never create, update or delete records and never tell the caller a ticket was created.
- Call `transfer_to_rep` **only** when the caller explicitly asks for a person. An earlier version offered a transfer
  unprompted and customers were transferred unexpectedly.
- At the start of each call the app injects a system message with the Call ID and the caller's phone number.

**Transfer.** `transfer_to_rep` is handled by the app, not by Foundry. The app waits for the agent to finish speaking,
then calls ACS `AddParticipant` for each Teams user in `REP_TEAMS_OBJECT_IDS`. On `AddParticipantSucceeded` the AI is
muted. On `AddParticipantFailed` (for example 480 *Temporarily Unavailable* when the rep is offline or in Do Not Disturb, or
603 *Decline*) the AI is un-muted and the caller keeps being helped.

**Voice settings.** The agent connects over `wss://<account>.services.ai.azure.com/api/projects/<project>/agents/<name>/endpoint/protocols/voice?api-version=v1`
using the `realtime` subprotocol and the `foundry-features: VoiceAgents=V1Preview` header. Audio is 24 kHz PCM.

## Note-taker (prompt agent)

**Input** (JSON per message):

```json
{"mode":"extract|finalize","call_id":"...","caller_phone":"...",
 "transcript":[{"speaker":"customer|assistant|rep","text":"..."}],
 "current":{ "...notes so far..." }}
```

**Output** (exactly one JSON object):

```json
{"fields":{"customer_name":null,"phone":null,"equipment_model":null,"equipment_serial_or_asset":null,
           "location":null,"complaint":null,"priority":null,"preferred_time":null,"summary":null},
 "matches":[{"table":"","id":"","label":"","reason":""}],
 "missing":[],"next_question":null,"created":null,"action":"none","error":null}
```

- `matches` carries Dataverse record matches (equipment) that the app uses to link the draft.
- `missing` and `next_question` become the **coaching** prompt shown to the rep.
- `created` is always `null` and `action` is always `"none"`. This is a contract, and the app never acts on it.
- It treats part numbers carefully (e.g. "HV 2020" may be "HV-220") and verifies against Dataverse when it can.

When it runs: after each recognized utterance (extract) and once more after the call ends (finalize). Only one extraction runs per call at a time, so notes never regress from overlapping runs.

## Toolbox `floorworks-dataverse`

A Foundry **toolbox** bundles tools for voice agents. This one exposes the Dataverse MCP server:

```json
{ "tools": [ { "type": "mcp", "name": "dataverse-mfg", "server_label": "dataverse-mfg",
               "server_url": "https://<org>.crm.dynamics.com/api/mcp",
               "project_connection_id": "dataverse-mfg-mi" } ] }
```

(`foundry/toolbox.sample.json`). Important points:
- The connection **must** be the managed-identity one (`dataverse-mfg-mi`). The OAuth2 connection returns
  `CONSENT_REQUIRED` mid-call, which made the receptionist report it "had no Dataverse lookup tool".
- Prompt agents cannot use toolboxes; the note-taker references the project connection directly.
- A toolbox version number auto-increments on each POST. The voice agent definition pins `toolbox_version` and a
  `server_url` containing `/versions/<n>/`, so publish a new voice-agent version after you publish a new toolbox version.
- Verify a toolbox with an MCP `tools/list` call before going live.

## Publishing new versions

Agents are versioned; the app always uses the latest published version.

```powershell
foundry/publish-draft-flow-agents.ps1                # note-taker and voice agent
foundry/publish-draft-flow-agents.ps1 voice-only     # voice agent only
```

Notes for the REST API (`$ep` = project endpoint):

| Action | Call |
|---|---|
| Read agent | `GET $ep/agents/{name}?api-version=v1` (versions under `.versions.latest`) |
| Publish version | `POST $ep/agents/{name}/versions?api-version=v1` body `{ "description": "...", "definition": { ... } }` |
| Toolbox version | `POST $ep/toolboxes/{name}/versions?api-version=v1` with header `Foundry-Features: Toolboxes=V1Preview` |
| Voice agent calls | add header `Foundry-Features: VoiceAgents=V1Preview` |
| Token | `az account get-access-token --resource https://ai.azure.com` |

> The publish script's receptionist text edits are string replacements against the *current* instructions. If the
> instructions drift, review the script's output before relying on it, or edit the agent in the Foundry portal.

## Prompt-change checklist

1. Edit instructions (note-taker: `foundry/notetaker-instructions.md`; receptionist: Foundry portal or the script).
2. Publish a new version.
3. Place a short test call, and confirm no service ticket row appears.
4. Check the draft fields and `bdm_summary`.
5. Keep the *never create or change records* rule in both prompts.

## Evaluating quality

Track these across test calls:

| Metric | How to check |
|---|---|
| Greeting latency | Time between answer and first AI audio (logs) |
| Unprompted transfers | `transfer_to_rep requested` without an explicit ask in the transcript |
| Equipment match rate | Draft has `bdm_equipmentid` after the call |
| Field accuracy | Compare draft fields with what was said |
| Tool failures | `CONSENT_REQUIRED`, timeouts or "no tool" statements in the AI's speech |
