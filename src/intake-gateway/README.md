# Intake Gateway (Python / FastAPI)

A UI-less, reusable gateway between a phone call (ACS) and Foundry agents.
It answers calls, bridges audio to a Foundry voice agent, runs a pluggable
extractor agent over the transcript, and exposes everything via REST, SSE and
signed webhooks. Port of the FloorWorks C# bridge with business details moved to config.

```
Caller -> ACS -> Gateway <-> Voice agent (any Foundry agent)
                    |-> Extractor agent (any Foundry agent)
                    '-> REST / SSE / webhooks -> your app
```

## Run
```
python -m venv .venv && .venv\Scripts\pip install -r requirements.txt
copy .env.example .env   # set the env vars (see file)
uvicorn app.main:app --port 8080
pytest                   # requirements-dev.txt
```
Event Grid `IncomingCall` subscription -> `POST {PUBLIC_BASE_URL}/api/incoming`.
Auth: managed identity / `az login` (`LOCAL_DEV=1`) against `https://ai.azure.com/.default`.

## API (`x-api-key`, `Authorization: Bearer`, or `?key=`; disabled if `API_KEYS` empty)
| Method | Path | Purpose |
|---|---|---|
| GET | /v1/calls, /v1/calls/{id} | list / status |
| GET | /v1/calls/{id}/transcript | lines `{speaker: customer\|assistant, text}` |
| GET | /v1/calls/{id}/extraction | extracted fields + enrichment |
| GET | /v1/calls/{id}/events | SSE (supports `Last-Event-ID` replay) |
| POST | /v1/calls/{id}/transfer, /hangup | actions |
| POST | /v1/calls/{id}/functions/{fnCallId}/result | complete an async function call |
| GET | /v1/functions, /v1/config | introspection |
| GET | /v1/drafts, /v1/drafts/{id}, /v1/tickets, /v1/customers?q=, /v1/equipment?customerId= | Dataverse draft and lookups (503 unless `DATAVERSE_URL` is set) |
| POST | /v1/drafts/{id}, /v1/drafts/{id}/customer, /v1/drafts/{id}/submit | rep edit, create customer, Submit (the only ticket creator) |

Draft persistence: when `DATAVERSE_URL` is set, every extraction result upserts one `bdm_servicerequestdraft` per call
(rep-edited fields are never overwritten; customer/equipment links are set only while empty). Agents stay read-only.

`DraftStore` (`app/drafts.py`, the Python port of the C# draft store) is the only Dataverse writer:
- `save_notes` runs after each extraction: creates the draft on first use, then refreshes agent fields, transcript, coaching and `bdm_enrichmentjson`. Fields listed in `bdm_editedfields` are skipped; the customer is auto-linked by caller phone only while empty.
- `update` applies rep edits and marks them in `bdm_editedfields`; `create_customer` adds a customer and links it.
- `submit` (rep click only) creates the ticket with the call details, summary and enrichment text, marks the draft Submitted and links the ticket. Drafts already submitted are read-only.
- Customer suggestions rank by fuzzy name plus phone match; search accepts a name or phone digits.
- Auth: managed identity (`DefaultAzureCredential`, scope `<DATAVERSE_URL>/.default`; `AzureCliCredential` when `LOCAL_DEV=1`). It needs a Dataverse application user with a role on the tables.
- `DATAVERSE_PROFILE=cdm-account-case` writes to the standard `account` and `incident` tables (equipment stays free text).

Custom Dataverse schema: table, column, lookup and option-set names default to FloorWorks `bdm_*`. Override only what
differs with one JSON (`DATAVERSE_SCHEMA` inline or `b64:<base64>`, or `DATAVERSE_SCHEMA_FILE`); see `schema.example.json`
and `app/schema.py`. Invalid JSON/unknown names fail startup validation. In `deploy-gateway.ps1` set `dataverseUrl` and
`dataverseSchema` (an object) in the parameters file. A canonical name maps to one real name across all tables.

Infra (no key): `POST /api/incoming`, `POST /api/callbacks/{id}`, `WS /ws/{id}`, `GET /healthz`.

```
curl -H "x-api-key: $K" http://localhost:8080/v1/calls
curl -N -H "x-api-key: $K" http://localhost:8080/v1/calls/<id>/events
```

## Events
`call.incoming`, `call.connected`, `transcript.line`, `extraction.updated`,
`enrichment.updated`, `draft.updated`, `extraction.final`, `function.called`, `function.completed`,
`transfer.*`, `call.ended`, `error`.

## Webhooks
POST JSON with headers `X-Timestamp`, `X-Signature` (HMAC-SHA256 of `"{ts}.{body}"` with `WEBHOOK_SECRET`)
and an event id; 3 retries. Verify with `webhooks.verify`.

## Functions
Voice-agent function calls are routed to: built-ins (`transfer_to_rep`, `hangup`), or consumer
webhooks declared in `FUNCTIONS`. Sync functions return inline; async ones that exceed their
timeout return `{"status":"pending"}` to the model and the consumer later POSTs the result.

## Assumptions / limits
- State is in-memory (single replica); swap `CallStore` for Redis/Cosmos for scale-out.
- Live notes after transfer are pluggable (see "Live notes" below). When off, rep speech isn't transcribed.

## Live notes after transfer (on by default, omittable)

After a transfer the voice agent is muted. Azure Speech then transcribes the caller and the rep
(per-speaker push streams) and adds `customer`/`rep` lines to the transcript, which the extractor
and `transcript` events pick up.

- Runtime off: `LIVE_NOTES=off`.
- Deploy without it: `docker build --build-arg WITH_SPEECH=0 .` (no Speech SDK in the image).
- On but misconfigured (no `SPEECH_KEY` or SDK missing): logs a warning, degrades to off. `/v1/config` shows `liveNotes`.
- Env: `SPEECH_KEY`, `SPEECH_REGION` (default eastus), `SPEECH_LANGUAGE` (default en-US).
- Keyless: `SPEECH_AUTH=aad` + `SPEECH_RESOURCE_ID` (full ARM id of a custom-subdomain Speech resource); the app's managed identity needs **Cognitive Services Speech User**. Auto-selected when no `SPEECH_KEY` but a resource id is set.

## Browser UI
- `CORS_ORIGINS` (comma separated): allow browser origins, e.g. the React UI in `../intake-ui`.
- `DEV_SIMULATOR=on`: enables `POST /v1/dev/simulate`, which plays a scripted fake call (off by default; reported as `simulator` in `/v1/config`).

## Limits, stats and startup validation
- `MAX_CONCURRENT_CALLS` (50): extra incoming calls are rejected. `MAX_CALL_DURATION_SECONDS` (3600) and `IDLE_TIMEOUT_SECONDS` (120): the gateway hangs up and emits an `error` event (stage `limits`).
- `GET /v1/stats` returns active/peak/rejected/expired counters; `/healthz` reports `ok` or `degraded`.
- On startup the config is validated: missing `PUBLIC_BASE_URL`, `FOUNDRY_PROJECT_ENDPOINT` or `VOICE_AGENT_NAME` fails fast (unless `LOCAL_DEV`); softer issues show as `configWarnings` in `/v1/config`.
- Inspired by Azure-Samples/call-center-voice-agent-accelerator.
- Other engines: implement the `Transcriber` protocol in `app/transcribe.py`.
- Not tested against a live ACS call; unit tests cover API, auth, webhooks, hub, parsing.
  Verify `acs.py` Teams transfer on first real call.
