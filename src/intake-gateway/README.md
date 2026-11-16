# Intake Gateway (Python / FastAPI)

A UI-less, reusable gateway between a phone call (ACS) and Foundry agents.
It answers calls, bridges audio to a Foundry voice agent, runs a pluggable
extractor agent over the transcript, and exposes everything via REST, SSE and
signed webhooks. This is the supported gateway; the retired C# implementation has been removed.

```
Caller -> ACS -> Gateway <-> Voice agent (any Foundry agent)
                    |-> Extractor agent (any Foundry agent)
                    '-> REST / SSE / webhooks -> your app
```

## Run
```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
# Edit .env before running; use LOCAL_DEV=1 only for local development.
.\.venv\Scripts\python.exe -m uvicorn app.main:app --env-file .env --port 8080
.\.venv\Scripts\python.exe -m pytest -q
```
Event Grid `IncomingCall` subscription -> `POST {PUBLIC_BASE_URL}/api/incoming?s=<EVENTGRID_SECRET>`.
Auth: managed identity / `az login` (`LOCAL_DEV=1`) against `https://ai.azure.com/.default`.

## Azure clients and configuration

ACS uses the public asynchronous Call Automation SDK; identity uses native asynchronous Azure credentials.
`AZURE_CREDENTIAL_MODE=auto` chooses Azure CLI locally and `DefaultAzureCredential` without developer
credential fallbacks in production. Set `managed_identity` to require managed identity, optionally with
`AZURE_CLIENT_ID` for a user-assigned identity. Explicit `azure_cli` is local-only. Speech's synchronous SDK
uses the same credential-selection policy.

Configure either `ACS_CONNECTION_STRING` or `ACS_ENDPOINT`, never both. The endpoint option uses Entra
authentication; assign the selected identity the required ACS calling permissions separately. Configuration
does not grant Azure roles or Dataverse application-user permissions.

`GATEWAY_PROFILE=voice` is the default. Production requires voice/Foundry settings, ACS credentials, an
extractor agent and the existing authentication settings. Live notes retain their documented warning/degradation
policy. `GATEWAY_PROFILE=api` explicitly
omits voice requirements and rejects incoming calls; production API authentication remains required.
`LOCAL_DEV=1` permits missing voice configuration with warnings, not malformed configuration. Settings passed
directly to `create_app` undergo the same validation as environment settings.

Environment toggles require `on`/`off` (`LOCAL_DEV` requires `0`/`1`); malformed JSON, unknown function properties,
incorrect types, negative limits, invalid endpoints/timezones/schedules and duplicate/reserved function names
fail early. Zero disables a call limit. Correct previously tolerated configuration before upgrading.

The app reuses outbound HTTP clients and closes only resources it creates; injected SDK clients, credentials
and draft stores remain caller-owned. Background tasks are observed and drained for up to 10 seconds before
cancellation; transcript workers then get their separate checkpoint budget. Shutdown closes transcribers,
cancels deferred-function futures and closes owned clients even when another cleanup fails. This is not a
zero-drop call drain: call watchdogs and deployment cutover remain separate work.

Only explicitly retry-safe operations receive application retries: Dataverse reads and event-webhook delivery
(with stable event IDs). Extraction has a bounded retry budget for transient/parse failures. Permanent HTTP
failures are not retried; Dataverse writes retain their ETag/idempotency logic, and non-idempotent ACS/function
operations are not blindly retried. Retry waits and token acquisition have overall deadlines.

## Automatic office hours

New `.env` copies select this example schedule; review it for the service location:

```dotenv
AFTER_HOURS=auto
SERVICE_TZ=America/Chicago
SERVICE_DAYS=Mon-Fri
SERVICE_HOURS=07:00-18:00
```

`auto` evaluates the local clock when a call arrives: opening is inclusive, closing is exclusive, and other days
are after hours. IANA timezone rules handle daylight saving. After-hours calls collect details instead of transferring
to a rep. Each call retains its starting mode, even across closing. There is no timer job or daily restart.
`on` forces after hours; `off` disables it. The runtime fallback stays `off` for existing deployments without a mode.
Overnight windows, split shifts and holidays are not supported. `/v1/config` reports the mode, current status and
three effective schedule fields. Use `AFTER_HOURS`, not the ignored `AFTER_HOUR` typo.
Restart/redeploy only when changing configuration; observe the
[maintenance constraints](../../docs/08-operations.md#safe-upgrades-and-rollback).

## API

Authenticate with `x-api-key`, `Authorization: Bearer`, or `?key=` (SSE). Empty `API_KEYS` disables auth
only in local development; production startup rejects that configuration.
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

Draft persistence: when `DATAVERSE_URL` is set, transcript checkpoints create/update one `bdm_servicerequestdraft` per call
independently of extraction. AI results update agent-owned fields; rep-edited fields (including summary and explicit
clears) are preserved. Customer/equipment links are set only while empty. Agents stay read-only.

Rep mutation bodies reject unknown fields, wrong types, malformed UUID links and excessive field lengths with
HTTP 422. Omitted edit fields remain unchanged; explicit `null` clears them, except priority cannot be cleared.
When changing customers, replace or explicitly clear incompatible equipment/contact links in the same edit;
the gateway rejects mismatches rather than silently clearing them. It rechecks relationships on submission.
Explicit link clears are protected from later AI auto-linking. Creating a customer also requires clearing
existing equipment/contact links first. Deferred function results must be JSON objects no larger than 256 KiB
after serialization. These contracts do not add per-user authorization; `submittedBy` is still caller-supplied.

`DraftStore` (`app/drafts.py`) owns business-record writes; `app/journal.py` owns internal checkpoints:
- `save_notes` runs after each extraction: refreshes agent fields, coaching and `bdm_enrichmentjson`. Fields listed in `bdm_editedfields` are skipped; the customer is auto-linked by caller phone only while empty. `save_transcript` mirrors durable transcript checkpoints separately.
- `update` applies rep edits and marks them in `bdm_editedfields`; `create_customer` adds a customer and links it.
- `submit` (rep click only) creates the ticket, optional overflow note and Submitted draft link in one atomic Dataverse changeset. Retrying a completed submission returns the existing ticket. Rep edits/AI field updates are rejected/skipped after submission; later transcript checkpoints may still extend the draft's notes.
- Draft updates use Dataverse ETags with bounded conflict re-reads. Creation uses deterministic IDs and an Active call-ID uniqueness key. Ticket/customer/note identities are deterministic so a response lost after commit cannot create duplicates on retry. A conflict that cannot be safely resolved is HTTP 409.
- Customer suggestions rank by fuzzy name plus phone match; search accepts a name or phone digits.
- Auth: managed identity (`DefaultAzureCredential`, scope `<DATAVERSE_URL>/.default`; `AzureCliCredential` when `LOCAL_DEV=1`). It needs a Dataverse application user with a role on the tables.
- The table mapping is `dataverse/config.json` (sent as `DATAVERSE_SCHEMA`); edit its `roles` to point at other tables (for example `incident` as the ticket, or add/remove the optional `contact` role). See [docs/18-table-configuration.md](../../docs/18-table-configuration.md); the voice agent prompt is rendered from the same file, so republish the agents after a change. It names only the table roles (`roles`); the gateway derives columns, lookups and option values from the org metadata (`app/discover.py`), and explicit keys override that.

Custom Dataverse schema: table, column, lookup and option-set names default to FloorWorks `bdm_*`. Override only what
differs with one JSON (`DATAVERSE_SCHEMA` inline or `b64:<base64>`, or `DATAVERSE_SCHEMA_FILE`); see `schema.example.json`
and `app/schema.py`. Invalid JSON/unknown names fail startup validation. In `deploy-gateway.ps1` set `dataverseUrl` and
`dataverseSchema` (an object) in the parameters file. A canonical name maps to one real name across all tables.

## Dataverse-only journal and upgrade

Before deploying this gateway version, drain/stop the old gateway and rerun the provisioner:

```powershell
.\dataverse\provision-dataverse.ps1 -OrgUrl https://<org>.crm.dynamics.com
# From src\intake-gateway with DATAVERSE_* and the intended identity configured:
python -m app.preflight
```

The migration adds `bdm_transcriptversion` to drafts, the `bdm_calljournal` table and a unique key on draft
`bdm_callid`. It does not delete or merge existing data. If duplicates prevent key activation, reconcile them
explicitly and reactivate the key before proceeding. Custom draft mappings must provide the same columns,
optimistic concurrency and an Active single-column call-ID key. `bdm_calljournals` can be renamed through
`names`/`logicalNames`/`columns`; it is not an agent business-table role.

The gateway application user needs read/create/write access to the journal and draft tables, required metadata
reads and the existing customer/ticket/relationship/annotation permissions. Do not expose the journal as an
agent tool. Startup refuses missing integrity mappings, unsupported concurrency, an inactive uniqueness key or
an unreadable journal; no call is needed to validate these prerequisites. Run live acceptance against both the
custom-table and standard-table profiles before deploying to production.

`app/journal.py` checkpoints serializable call metadata and transcript JSON in Dataverse before emitting each
`transcript` event, then mirrors the transcript into the draft without waiting for any model. Call status includes
`checkpointVersion` (last durable transcript line count) and `persistenceError`; `error` events with stage
`checkpoint` identify failures. HTTP transcript reads may include newer, uncheckpointed lines: compare with the
call's `checkpointVersion`. Transient transport, HTTP 429 and server errors retry with backoff; permanent errors
are surfaced rather than acknowledged as success.

On startup, incomplete journal rows are restored as **ended/recovered** calls, without restoring audio sockets
or replaying function side effects. Committed transcript mirroring does not depend on a model. When extraction
is enabled, unfinished extraction then resumes automatically, including a final model request if needed.
Validated output is checkpointed before draft persistence: cached results retry persistence without another
model request. An incremental result is not mistaken for a final result. Completed historical transcripts remain
available by call ID through REST; live call control and SSE history are not restored.

Extraction tracks extracted and persisted versions separately and advances the persisted version only after
the draft write succeeds. Transient failures retry with backoff, with three worker attempts per target version;
invalid output and permanent/exhausted failures become visible `failed` states. Finalization waits at most
180 seconds. `GET /v1/calls/{id}/extraction` reports actual `status`, `error` and `final`, not inferred success
from call termination. Authenticated `POST /v1/calls/{id}/extraction/retry` retries a failed result; new speech
also reopens work. Failed terminal extraction is recorded as such, not retried indefinitely on every restart.
Without Dataverse, retry state remains process-local.

AI output must satisfy the typed fields, UUID/table matches and enrichment contracts before publication or
business writes. With Dataverse enabled, matched records and customer ownership are checked too. Complete
fenced JSON is accepted; truncated JSON is never repaired. Rejected output preserves the last valid result.
`EXTRACTION_SCHEMA` is a JSON Schema applied to the output's `fields` object, with local references only.
Existing custom agent output may need adjustment to the contract in `app/extraction_models.py`; output is
limited to 256 KiB. No agent deployment is performed by this upgrade.

Checkpoint failures do not suppress final extraction or the `call.ended` event. That event includes
`checkpointVersion` and `persistenceError`, so call termination is not mistaken for persistence success;
transient checkpoint failures continue retrying after the finalization flush times out. Azure identity
transport failures are retried too; permanent authentication failures are surfaced for operator action.

**Recovery limits:** With the selected Dataverse-only design, a successful checkpoint is the recovery point.
There is no fixed time-based RPO during a Dataverse outage: new speech stays in memory and can be lost if the
process also fails before a successful retry. Recovery time depends on Dataverse availability, backlog and
startup limits; measure it in the target environment. Shutdown gives pending checkpoint work up to 10 seconds;
an incoming-call/finalization flush waits up to 30 seconds and reports failure if it cannot complete. No local
disk or second Azure storage service is used.

Keep one gateway process/replica per journal/draft dataset, with no overlapping revisions during recovery.
ETags protect business writes, not distributed ownership of live calls. Journal retention is not automatic:
apply the agreed transcript retention/deletion policy to completed journal rows, draft transcripts and other
copies together; incomplete rows must be recovered/reconciled before deletion. The provisioned checkpoint
column supports up to 1,000,000 characters, and the draft transcript column supports 100,000; overflow raises
an observable persistence error, never silent truncation.

Infrastructure routes do not use consumer API keys: `/api/incoming` requires the Event Grid secret;
`/api/callbacks/{id}` and `/ws/{id}` require per-call HMAC tokens and ACS JWTs in production.
`GET /healthz` is public.

```
curl -H "x-api-key: $K" http://localhost:8080/v1/calls
curl -N -H "x-api-key: $K" http://localhost:8080/v1/calls/<id>/events
```

## Events
`call.incoming`, `call.connected`, `transcript`, `extraction.updated`, `extraction.status`,
`enrichment.updated`, `draft.updated`, `extraction.final`, `function.called`, `function.completed`,
`function.error`, `function.cancelled`, `transfer.*`, `call.ended`, `error`.

SSE accepts a nonnegative `Last-Event-ID` header or `afterEventId` query cursor. Slow subscribers, missing
replay history and reset/future cursors receive `resync.required` before disconnection. Reload the call and
transcript snapshots, then reconnect using that event's ID. The UI does this automatically; the call snapshot
also includes currently pending deferred functions.

## Webhooks
POST JSON with headers `X-Timestamp`, `X-Signature` (HMAC-SHA256 of `"{ts}.{body}"` with `WEBHOOK_SECRET`)
and an event id; up to 3 attempts for transient failures within the delivery deadline. Verify with
`webhooks.verify`. Delivery remains best-effort, not a durable outbox.

## Functions
Voice-agent function calls are routed to: built-ins (`transfer_to_rep`, `hangup`), or consumer
webhooks declared in `FUNCTIONS`. Sync functions return inline; async ones that exceed their
timeout return `{"status":"pending"}` to the model and the consumer later POSTs the result.
Deferred work expires after 120 seconds or call end and produces one terminal event. Results for expired,
ended or unknown invocations are rejected; callbacks never target an obsolete voice session. Function arguments
must be bounded JSON objects, not malformed JSON replaced with an empty object. Duplicate invocation IDs share
one outcome within the process; conflicting reuse is rejected. The ledger admits at most 4,096 invocations,
256 per call, and 128 callback reservations; capacity fails closed until call-end cleanup.
Webhook side effects are not retried after uncertain HTTP outcomes. The ledger is **not durable across
restart**; consumers must independently deduplicate their business operations.

## Assumptions / limits
- Live transport state is in-memory (single replica); Dataverse journals recover committed notes, not live calls.
  Scale-out requires explicit call ownership/routing as well as shared business state.
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
- `infra\init-gateway-params.ps1` now generates keyless Speech settings. For existing gateway files, set
  `speechAuth` to `aad`; `speechResourceId` can select a separate Speech account or default to the discovered
  Foundry account. Deployment checks the region/custom subdomain, grants the app's Speech role and clears the
  old key environment reference. See the [deployment migration guide](../../docs/16-intake-gateway-deploy.md#migrate-live-notes-to-managed-identity).
- Entra tokens refresh 120 seconds before expiry. Recognizer startup has a 15-second deadline; failed streams
  recover on subsequent audio with capped 0.5-30-second backoff. Cleanup attempts every stream and credential
  with a 5-second native-operation deadline and reports failures. A blocked native SDK thread cannot be
  forcibly stopped; it retains eventual cleanup ownership, and callbacks from stopped streams are suppressed.

## Browser UI
- `CORS_ORIGINS` (comma separated): allow browser origins, e.g. the React UI in `../intake-ui`.
- `DEV_SIMULATOR=on`: enables `POST /v1/dev/simulate`, which plays a scripted fake call (off by default; reported as `simulator` in `/v1/config`).

## Limits, stats and startup validation
- Ended in-memory calls expire after one hour or when the 500-call retention budget is exceeded. Active calls,
  running extraction and pending functions are not evicted. Journal history follows a separate retention policy.
- Event replay retains 200 events for at most 500 recently published call IDs. Each SSE queue holds 100 events;
  subscriber limits are 32 per call and 512 total. Eviction/overflow uses explicit resync, not silent event loss.
- Webhook delivery uses at most 32 workers and 1,024 queued deliveries. Overflow and failed/discarded deliveries
  are logged and counted as `droppedWebhookEvents` in stats; this is still not a durable outbox.
- Transcripts reject additions atomically at 256,000 UTF-8 text bytes or 2,000 lines; the voice bridge emits a
  limit error and requests hangup. Dataverse column limits can be lower and remain independently enforced.
- Voice WebSocket messages are capped at 1 MiB with a 16-message receive queue; the echo-reference buffer
  retains at most 192,000 bytes. Oversized ACS messages trigger an explicit error and close.
- `MAX_CONCURRENT_CALLS` (50): extra incoming calls are rejected. `MAX_CALL_DURATION_SECONDS` (3600) and `IDLE_TIMEOUT_SECONDS` (120): the gateway hangs up and emits an `error` event (stage `limits`).
- `GET /v1/stats` returns active/peak/rejected/expired counters; `/healthz` reports `ok` or `degraded`.
- On startup the config is validated against the explicit voice/API profile described above; softer issues show as `configWarnings` in `/v1/config`.
- Inspired by Azure-Samples/call-center-voice-agent-accelerator.
- Other engines: implement the `Transcriber` protocol in `app/transcribe.py`.
- Not tested against a live ACS call; unit tests cover API, auth, webhooks, hub, parsing.
  Verify `acs.py` Teams transfer on first real call.
