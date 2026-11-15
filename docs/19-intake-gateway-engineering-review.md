# Intake Gateway: engineering review and development backlog

**Reviewed:** 2026-11-12 (UTC)

**Baseline:** Git `cf8b8b3`, with existing local working-tree changes; source references describe that review snapshot.

**Scope:** `src\intake-gateway`, its tests, and directly related packaging, CI/CD and Azure deployment configuration.

**Purpose:** Turn the working PoC into a reliable, supportable application without changing the proven customer workflow.

**Status:** Original review plus local P0, code-quality and resilience updates below; rollout acceptance remains outstanding.

**Backlog:** 45 tasks: 4 P0, 31 P1 and 10 P2.

**Gateway retirement/documentation update:** The C# gateway has been removed; the .NET solution retains only the
independent Foundry IQ CLI. PowerShell and azd now target Python, with current API/HMAC secrets, shared table config
and keyless Speech settings. Both configure one replica. IG-045's C#/Python target mismatch is addressed locally,
but deployment ownership/cutover safeguards and live acceptance remain open. Original source references below,
including retired paths and line numbers, are historical evidence rather than current setup instructions.
IG-033's create/update scale settings and verifier now require min/max replicas of one; draining and live rollout
acceptance remain open. Telephony bootstrap/rebinding now require matching Event Grid credentials when subscribing.

**Implementation update (2026-11-12):** IG-001 through IG-004 now have local code and regression coverage.
IG-001 protects summaries and clears; IG-002 uses conditional ETag writes; IG-003 uses an atomic changeset and
deterministic identities; IG-004 uses the user-selected Dataverse-only checkpoint journal and startup replay.
These updates are **not deployed or live-service certified**. The Dataverse schema/key migration, runtime
permissions and custom/standard table acceptance checks remain rollout gates. Dataverse-only storage cannot
preserve uncommitted speech across a simultaneous Dataverse outage and process failure. See the gateway
[upgrade and recovery instructions](../src/intake-gateway/README.md#dataverse-only-journal-and-upgrade).

**Local code-quality cleanup:** The user-selected SDK, resource ownership, outbound HTTP, configuration and API
contract work is implemented locally. This is scoped progress on the following P1 items, **not blanket closure**
of their operational acceptance criteria:

| Item | Local implementation | Remaining acceptance / dependencies |
|---|---|---|
| IG-018 | Public async ACS API, native async identity, explicit credential selection, optional Entra ACS endpoint, owned credential cleanup. | Verify real-service calls and intended identity permissions; Speech renewal remains IG-016. |
| IG-010 | Supervised background work, bounded drain, awaited cancellation, transcriber/client cleanup, borrowed-resource ownership. | Full call admission/watchdog/drain behavior and deployment cutover remain IG-006/IG-041; Speech startup cancellation remains IG-016. |
| IG-013 | Reused HTTP clients, retry-safe opt-in, transient-only bounded retries, Retry-After/deadline handling; no blind retries of side effects. | Real-service throttling/timeout acceptance; durable delivery remains IG-027. |
| IG-021 | Strict typed settings, fail-closed injected production configuration, explicit API-only profile, endpoint/schedule/function validation. | Verify deployment environment migration and required capabilities in the target Azure environment. |
| IG-022 | Typed mutations, UUID links, bounded fields/results, explicit null/omission, relationship rejection and submit revalidation. | Real Dataverse relationship/profile acceptance; metadata-derived field lengths and caller authorization remain IG-023/IG-020. |

Real-SDK fake-transport regressions cover identity HTTP 503 wrapping; the real simulator and ACS bridge share
failure-safe finalization. Configuration/ownership/API regressions extend the existing integrity coverage.
These changes neither deploy resources nor certify live behavior.

**Local resilience update:** The following five P1 implementations now extend that foundation. The original
evidence below describes the review baseline, not the current behavior. No deployment or live data mutation
was performed.

| Item | Local implementation | Remaining acceptance / limits |
|---|---|---|
| IG-014 | Typed AI fields/enrichment/matches, local JSON Schema validation, referenced-record/ownership checks; no truncated-JSON repair or publication before successful persistence. | Verify actual agent outputs and custom field mappings; metadata-derived limits remain IG-023. |
| IG-005 | Durable extracted/persisted versions, cached validated output, bounded transient retries, automatic restart recovery, explicit failed/final status and authenticated retry. | Inject model/Dataverse outages and restart in Azure. Dataverse-only RPO still excludes uncommitted speech; recovery does not restore audio. |
| IG-012 | Once-only process-local invocation outcomes, 120-second deferred expiry, call-end/shutdown cancellation, bounded ledger, terminal UI state and no delivery to obsolete voice sessions. | No cross-restart deduplication or function replay; consumer side effects need independent idempotency. Validate callback/end races in Azure. |
| IG-011 | Actual ended-call retention; bounded replay, SSE queues/subscribers, webhook workers/backlog, transcript and audio buffers; explicit resync and delivery-drop counters. | Load-test the documented defaults. Active calls are not evicted; watchdog/admission behavior remains IG-006, durable webhook delivery IG-027. |
| IG-016 | Pre-expiry Entra renewal, owned/cancellation-safe startup and cleanup, bounded deadlines and recognizer recovery backoff. | Exercise real Speech renewal, native failures and post-transfer audio. Blocked native SDK threads retain eventual cleanup ownership, not forced termination. |

Defaults, migration behavior and remaining recovery limitations are documented in the
[gateway README](../src/intake-gateway/README.md#dataverse-only-journal-and-upgrade).
These are scoped local implementations, not production certification or closure of unrelated lifecycle,
handoff, authorization, deployment-drain and live-service acceptance tasks.

## Assessment

The PoC demonstrates the core integration successfully, but functionality alone does not establish production readiness. The most important work is **data integrity and call lifecycle reliability**, followed by identity, deployment safety, observability and scale. Do not start with a rewrite or a framework migration.

Preserve these useful foundations:

- Human submission is the only gateway path that creates a service ticket.
- Dataverse translation is concentrated at the data boundary rather than spread throughout business logic.
- ACS, extraction, transcription and event distribution already have identifiable boundaries and injectable dependencies.
- Production configuration checks, per-call HMACs, ACS JWT validation, constant-time comparisons and outbound URL checks are already present.
- Existing tests cover authentication, callback gating, extraction parsing, draft workflows and schema translation.

The strongest product promises need stronger implementation guarantees: **rep corrections always win**, **one call creates at most one ticket**, and **transcripts survive downstream failure**. Each currently has a concrete gap.

### Review limits and evidence

This is a repository-based engineering review, not a penetration test, compliance certification, dependency vulnerability assessment or live Azure environment audit. No cloud resources were changed, no real calls were made, and no production data was queried. Existing unrelated working-tree changes were left untouched.

Source references below are relative to `src\intake-gateway` unless explicitly prefixed with a repository folder such as `infra\`. Line numbers describe the reviewed snapshot and will move as work proceeds.

Evidence labels:

| Label | Meaning |
|---|---|
| Reproduced | Observed with local fakes or an existing test command; no external service was needed. |
| Observed | Directly supported by source inspection; the complete failure scenario was not executed. |
| Recommendation | A missing engineering control or design decision, not a claim that a live deployment has failed. |
| Verify in Azure | Requires deployment, service-contract or operational evidence before closing. |

### Local baseline

Command run from `src\intake-gateway`:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Result: **56 passed, 3 warnings**. Warnings included the installed Starlette/httpx test-client integration and ACS `CallInvite` deprecation. These are compatibility signals, not evidence of a particular security vulnerability. The installed virtual environment was used; a clean requirements-only installation was not performed.

Additional in-memory/HTTP-fake probes produced the following results:

| Scenario | Actual result | Related task |
|---|---|---|
| Rep edits summary; next AI extraction supplies another summary | Rep summary becomes the AI summary | IG-001 |
| Rep edits summary; next AI extraction omits summary | Stored summary becomes `None` | IG-001 |
| Ticket POST succeeds; draft PATCH fails; rep retries Submit | Two ticket POSTs | IG-003 |
| Extractor fails for the current transcript version | `processed == version` despite failure | IG-005 |
| Metadata response has description length information | Fetcher stores the length as `None` | IG-023 |
| Two simultaneous deliveries have the same Event Grid event ID | Both return 200 and invoke answer; two call states allocated | IG-007 |
| `MemoryStore(max_calls=1)` creates three ended calls through `get_or_create` | Three calls retained | IG-011 |
| `CallDisconnected` arrives for a connected call without a media-close path | Status remains active; capacity remains allocated | IG-006 |
| SSE request supplies a non-integer `Last-Event-ID` | HTTP 500 | IG-026 |

These probes are review evidence, not committed regression tests. Convert them into focused permanent tests as the corresponding fixes are implemented.

## Priorities and delivery policy

| Priority | Release meaning |
|---|---|
| P0 | Block production use until resolved: confirmed integrity gaps or missing guarantees essential to the product. |
| P1 | Complete before a broader/multi-user pilot or production rollout; explicitly accept any deferral with an owner and mitigation. |
| P2 | Required production engineering or a measured improvement; schedule against rollout requirements. |
| P3 | Optional optimization after operational evidence justifies it. |

These priorities are engineering sequencing, not CVSS/security severity. No P3 work is necessary to address the major risks found.

Unless qualified by the implementation update above, every item below is **open**. Assign an individual owner
and estimate during planning. The suggested owner is a discipline, not an assignment to an existing contributor.
Acceptance criteria are proposed verification requirements, not claims of live-service certification.

## A. Data integrity and recoverability

**Suggested owner: backend/data engineer.**

### IG-001 - Preserve rep-edited summaries (P0; Reproduced)

**Evidence:** `app\drafts.py:27-35,249-278`. `summary` is editable and tracked in `bdm_editedfields`, but `save_notes` assigns `bdm_summary` unconditionally before the helper that protects other edited fields.

**Task:** Apply the same ownership rule to summary as to other editable fields. Distinguish an omitted AI value from an intentional user clear; do not erase the existing summary merely because an extraction omits it.

**Acceptance:** A rep-edited summary survives subsequent AI saves with a replacement summary, a missing summary and a null summary. Other editable fields retain their existing behavior.

### IG-002 - Enforce concurrent-edit protection at Dataverse (P0; Observed)

**Evidence:** `app\drafts.py:145,249-319,397-431,453-525`. The process-local lock covers `save_notes` and `submit`, but not `update` or `create_customer`. All use read-then-write requests without ETag conditions. A save can read the old edited-field list, await another request, and overwrite a rep correction. Independent rep edits can also lose each other's edited-field markers.

**Task:** Use Dataverse optimistic concurrency where supported, with explicit conflict handling and a merge that honors rep ownership. A per-draft lock can reduce same-process contention, but is not the correctness boundary across processes or other Dataverse writers. Remove the single global lock bottleneck only after database-level correctness is established.

**Acceptance:** Deterministically interleave extraction, two rep edits and Submit. No correction or edited marker is lost; stale edits/submissions produce a documented conflict response or safe merge. Repeat against two gateway instances. Verify optimistic-concurrency support on the selected tables.

### IG-003 - Make ticket submission atomic and idempotent (P0; Reproduced)

**Evidence:** `app\drafts.py:453-525`. Ticket creation, optional note creation and marking/linking the draft are separate requests. A failed final PATCH leaves an open draft and an existing ticket; retry creates another ticket. The lock does not solve partial failure or cross-instance execution.

**Task:** Establish a durable unique submission identity tied to the call/draft. Use a Dataverse transactional changeset/custom API, or an equivalent recoverable protocol with uniqueness and reconciliation. Evaluate actual table constraints before choosing. Return the existing ticket on a repeated successful submission. Persist or retry overflow notes rather than treating a failed attachment as complete.

**Acceptance:** Concurrent double-clicks, two instances, response loss after ticket creation, note failure and draft-update failure produce at most one ticket. A retry resolves to the same ticket. Full notes are recoverable and incomplete submission is visible.

**Dependencies:** IG-002; coordinate with IG-004.

### IG-004 - Persist transcripts and call checkpoints independently of extraction (P0; Observed)

**Evidence:** `app\state.py:13-70,72-90`; `app\bridge.py:217-221`; `app\extractor.py:146-163,173-203`; `app\drafts.py:249-265`. Transcript lines initially exist only in process memory. Dataverse persistence is invoked after a successful extraction, not on transcript ingestion. A process failure or sustained model failure can lose notes before any draft save.

**Task:** Add a durable, incrementally written call/transcript journal with documented retention and replay. Use a unique call identity for draft upserts rather than relying solely on lookup-then-POST and a local lock. Persist serializable business state separately from live sockets, locks, recognizers and futures. Retain single-replica operation until routing/ownership is designed.

**Acceptance:** Restart the process mid-call and during a model/Dataverse outage; acknowledged transcript checkpoints are recoverable and replay does not duplicate drafts or lines. Declare and measure the recovery-point/recovery-time targets; do not promise seamless audio migration without proving it.

### IG-005 - Retry failed extraction/persistence without losing work (P1; Reproduced/Observed)

**Evidence:** `app\extractor.py:146-163,173-203`; `app\main.py:113-116`; `app\bridge.py:346-362`. `_loop` advances `processed` after errors. `apply` catches draft-save failures, so extraction can be considered processed without a durable draft update. Retry currently depends on another transcript version or finalization, and finalization can also fail. The API derives extraction `final` from ended call status, which is set before final extraction finishes.

**Task:** Track extraction completion and persistence completion separately. Retain dirty work until durable success; add bounded retry, replay/reconciliation and a visible terminal/dead-letter state. Define a finalization deadline rather than waiting indefinitely for prior work.

**Acceptance:** A transient failure with no further speech eventually updates the draft. A restart can resume pending work. Permanent failure is visible to the rep/operator and is not reported as durable completion. Call ended, extraction final and persistence complete are distinct states; clients cannot mistake an in-progress/failed finalization for success.

**Dependency:** IG-004 for restart-safe retries.

## B. Call lifecycle, concurrency and resource ownership

**Suggested owner: backend/realtime engineer.**

### IG-006 - Centralize terminal call handling and add an independent watchdog (P1; Reproduced/Observed)

**Evidence:** `app\main.py:288-330,333-383`; `app\bridge.py:247-362`; `app\limits.py:36-67`. Callback handling has no `CallDisconnected` transition. Capacity and duration/idle enforcement depend on reaching the media loop/finalizer. An answered call whose media socket never opens can retain capacity indefinitely. Answer failures also do not receive the normal terminal metadata/event sequence.

**Task:** Implement an idempotent lifecycle coordinator for answer, connect, transfer, disconnect, streaming failure, timeout and shutdown. Add deadlines for answer-to-connect/media startup and an independently scheduled watchdog. Clarify that current `touch` counts transport messages, including silence, rather than conversational activity.

**Acceptance:** No-media startup, early disconnect, duplicate/out-of-order callbacks, streaming failure and answer failure all release capacity exactly once and produce consistent terminal state. Transport-idle and caller-silence policies are separately documented and tested.

### IG-007 - Claim incoming events atomically and durably (P1; Reproduced)

**Evidence:** `app\main.py:247-285,313-329`. An event is entered into `seen_events` only after awaiting answer. Simultaneous duplicates both pass the check. The bounded in-memory cache also resets on restart.

**Task:** Claim each event/call attempt before the first awaited side effect, persist the claim where restart/cross-instance deduplication is required, and track in-flight/success/failure states with expiry and recovery. Handle uncertain answer outcomes deliberately; do not blindly retry a possibly completed answer.

**Acceptance:** Simultaneous duplicate deliveries, process restart, delayed replay and answer response loss cause one logical call attempt. A genuinely failed attempt remains retryable. Test partially processed event batches.

**Dependency:** IG-004 for durable identity/claims.

### IG-008 - Make transfer a bounded, participant-aware operation (P1; Observed)

**Evidence:** `app\acs.py:51-54`; `app\bridge.py:56-97`; `app\main.py:370-381`. Multiple targets are added sequentially, but any participant failure resets the entire transfer even if another rep has joined. Ringback stops after 120 seconds without a corresponding recovery transition; the agent can remain muted.

**Task:** Define first-answer/queue/parallel-ring semantics explicitly. Correlate ACS operation and participant outcomes, set a transfer deadline, cancel/settle outstanding attempts, and restore an audible recovery path when no rep joins. Prevent a late failure from undoing a successful join. Synchronize muting with the current utterance/response rather than a stale completion flag.

**Acceptance:** Test REST/agent transfer during speech, no answer, one success plus another failure, partial SDK failure, duplicate/late callbacks, caller disconnect during transfer and ringback expiry. The caller never remains indefinitely muted with no rep or fallback.

### IG-009 - Isolate human-call continuity from Voice Live failures (P1; Observed)

**Evidence:** `app\bridge.py:69-87,164-215,274-291,346-362`. After the rep joins, the Voice Live socket remains open. Exhausted Voice Live reconnects still trigger `_hangup_now`, which can terminate the human conversation.

**Task:** Define ownership of the voice-agent session after handoff. Stop or detach unnecessary model sessions and function execution; keep ACS and live notes independent of agent availability once the human owns the call. Specify recovery behavior before and during handoff.

**Acceptance:** Drop Voice Live before transfer, while ringing and after rep join. Apply the documented fallback without terminating a healthy rep/caller conversation solely because the agent service is unavailable.

### IG-010 - Add application lifespan, task supervision and graceful draining (P1; Observed)

**Evidence:** `app\main.py:29-76,89`; `app\bridge.py:66-111,215,235,333-362`; `app\extractor.py:171`; `app\drafts.py:139-148`; `app\transcribe.py:46-58,111-114`. Background work is launched without an owned task registry; clients/credentials have no coordinated application shutdown. The bridge catches `RuntimeError` as if it were always a normal disconnect.

**Task:** Own clients, credentials, per-call tasks and shutdown in FastAPI lifespan. Drain ingress, settle/checkpoint calls, await or cancel tasks, cancel pending functions, and close transports/recognizers within an explicit budget. Retrieve task exceptions and distinguish normal transport closure from programming errors. Clear call-held socket closures after teardown.

**Acceptance:** SIGTERM/revision replacement during extraction, transfer, ringback and deferred functions produces no unobserved task failures, leaked sessions or false completion. New calls are rejected/drained predictably and durable work survives.

**Dependencies:** IG-004 and IG-006; coordinate with deployment draining.

### IG-011 - Bound state, buffers, subscribers and background fan-out (P1; Reproduced/Observed)

**Evidence:** `app\state.py:72-125`; `app\bridge.py:133-141,190-193,324-325`. Eviction exists only in `MemoryStore.put`, but the normal lifecycle uses `get_or_create` and mutates state. Event buffers/sequences have no per-call expiry; subscriber queues and listener-created tasks are unbounded. Voice frames are accepted with `max_size=None`.

**Task:** Enforce call TTL/retention on the actual lifecycle, expire event-hub state, bound subscriber queues and outbound work, cap message/audio buffers, and define slow-consumer disconnect/resync behavior. Preserve active calls and required durable history during eviction.

**Acceptance:** A soak test substantially beyond the retention count, with slow/disconnected SSE clients and failing webhooks, keeps memory/task counts within an agreed bound. Active calls are not evicted. Replay gaps are explicit rather than silently losing events.

### IG-012 - Own deferred function calls through completion or expiry (P1; Observed)

**Evidence:** `app\functions.py:34-101`; `app\bridge.py:169-179,210-215`. Futures can remain pending after webhook errors or timeouts. They have no terminal deadline or call-end cleanup. Duplicate function IDs can replace pending work, and late completion can try to use a closed Voice Live session.

**Task:** Give each function invocation a validated identity, idempotency policy, argument/result contract, expiration and terminal state. Remove pending entries on every terminal path, supervise late callbacks, and decide which invocations require durable storage.

**Acceptance:** Duplicate IDs, webhook rejection, timeout without callback, callback after call end, repeated result delivery and shutdown leave no retained futures or duplicate side effects. Completion/error events follow one documented contract.

## C. Azure clients, AI processing and media

**Suggested owner: Azure/backend engineer; AI engineer for extraction quality.**

### IG-013 - Standardize outbound clients, retries and end-to-end deadlines (P1; Observed)

**Evidence:** `app\extractor.py:109-130`; `app\drafts.py:183-204`; `app\bridge.py:224-237`; `app\webhooks.py:74-101`. Extraction constructs a client per invocation and retries parse/transport errors, but not HTTP 429/5xx. Dataverse has no application-level retry classification. Voice retries use a fixed short schedule. Webhook retries do not distinguish permanent failures.

**Task:** Reuse managed client pools; define per-dependency connect/read/overall deadlines, retryable failures, bounded exponential backoff with jitter and `Retry-After` handling. Check native SDK policies before adding another retry layer. Never retry non-idempotent writes blindly.

**Acceptance:** Inject 429, 503, connection reset, slow response and permanent 400/403 for each dependency. Calls remain responsive, retry counts/deadlines are bounded, and writes stay duplicate-safe.

**Dependencies:** IG-003 for retry-safe submission; IG-010 for client ownership.

### IG-014 - Validate AI output before publishing or writing records (P1; Observed)

**Evidence:** `app\extractor.py:29-78,132-163`; `app\config.py:43-46`; `app\drafts.py:295-311`; `tests\test_extractor_parse.py:10-15`. The configured extraction schema is prompt input, not enforced validation. Truncated JSON is repaired and accepted as ordinary output; nested field/match/enrichment shapes and referenced equipment IDs are not validated.

**Task:** Define typed extraction/enrichment contracts with field limits, enum validation, provenance and explicit incomplete/refusal states. Use structured output where supported by the selected agent/API. Do not silently persist repaired output as verified business data. Verify referenced records and customer/equipment relationships at the write boundary.

**Acceptance:** Wrong types, truncated JSON, invalid priority, missing required structures, nonexistent IDs and mismatched equipment/customer results do not corrupt the draft. Preserve the last valid result and show an actionable validation state.

### IG-015 - Control extraction cost and model concurrency (P2; Observed/Recommendation)

**Evidence:** `app\extractor.py:132-144,165-203`. Each processed transcript version sends the entire transcript/current state. Serialization is per call, not across calls; there is no global model concurrency/rate budget or explicit transcript/context-size limit.

**Task:** Measure current latency/token cost, then add debouncing/coalescing, a global concurrency budget, fair scheduling and bounded context construction. Preserve final extraction and material corrections; use incremental context only if quality evaluation supports it.

**Acceptance:** Publish cost per call/minute and extraction-lag percentiles at the agreed concurrency. Long calls do not exceed context limits or starve other calls. Quality does not regress on the evaluation set.

### IG-016 - Make Speech authentication and recognizer lifecycle resilient (P1; Observed/Verify in Azure)

**Evidence:** `app\transcribe.py:60-114`. The AAD path acquires one token when constructing a recognizer; no refresh is scheduled. Cancellation is logged but does not replace the retained recognizer. Failed startup can be retried on subsequent audio frames without backoff. Stop/late callback behavior is not coordinated with final extraction.

**Task:** Prefer a supported credential-based Speech configuration when compatible with the selected SDK/endpoint, or renew each live recognizer's authorization token before expiry. Add recognizer cancellation/restart policy, per-call resource limits and a close/drain barrier for final recognized utterances. Report actual live-note degradation, not just configured capability.

**Acceptance:** Expiring-token, recognizer cancellation, startup failure and close-during-recognition tests pass. A long real transferred call continues transcription across credential renewal. Final words arrive before final extraction, and late callbacks cannot restart work on a finalized call.

### IG-017 - Define and verify the audio/protocol contract (P2; Observed/Recommendation)

**Evidence:** `app\audio.py:13-61`; `app\bridge.py:133-162,190-215,301-329`; `app\transcribe.py:71-76,99-109`. Audio resampling is frame-local linear interpolation; echo reference is a byte FIFO without explicit timestamp alignment. Silent frames use a fixed 20 ms substitute, while ringback uses 200 ms frames. Protocol messages are largely untyped.

**Task:** Record negotiated audio format/channel/frame assumptions for ACS and Voice Live; validate sizes, base64, sample alignment and supported message shapes. Measure resampler and echo-reference quality before replacing them. Bound any reference backlog and define reset behavior on reconnect/barge-in.

**Acceptance:** Fixture audio covers silence, odd/truncated samples, varied frame sizes, participant changes, reconnect and barge-in. Measured speech quality, audio latency and memory remain within agreed thresholds. Do not assume a frame-format defect without protocol evidence.

### IG-018 - Use supported Azure SDK surfaces and deliberate identity selection (P1; Observed/Recommendation)

**Evidence:** `app\acs.py:7-28,51-54`; `app\extractor.py:21-26`; `app\transcribe.py:52-67`. ACS uses a connection string, imports a Teams identifier from a private `_shared` module, and uses deprecated `CallInvite`. Sync SDK calls are wrapped in `to_thread`; identity selection is coupled to `LOCAL_DEV`.

**Task:** Move to public supported types and current call signatures. Evaluate native async ACS/Identity clients where supported by the chosen dependency set, rather than adding unbounded thread work. Support an explicit managed identity/client selection for Azure and keep developer credentials local. Plan ACS Entra/RBAC authentication where supported; keep a documented fallback only if needed.

**Acceptance:** SDK contract tests cover answer/add participant/reject/hangup with the pinned SDK version. Production uses the intended identity, local development still works, no private import is required, and credentials/transports are closed. Verify required Azure and Dataverse roles without granting broad contributor permissions.

Native async conversion is a maintainability/performance recommendation, not a claim that `to_thread` is inherently incorrect.

### IG-019 - Version the Foundry integration and evaluate agent behavior (P1; Observed/Recommendation)

**Evidence:** `app\bridge.py:133-162`; `app\extractor.py:109-121`; `app\render_prompts.py:49-76`. Voice uses a preview feature header and a hand-built protocol URL; extraction references an agent by name. Prompt/config hashes are not a complete deployed model/agent/API compatibility record.

**Task:** Maintain a tested compatibility matrix for Voice Live, Responses API, agent versions, model deployments and SDK packages. Validate actual regional/preview support for the deployment. Evaluate supported SDK clients against raw protocol requirements without forcing a migration. Add repeatable agent evaluations for extraction accuracy, tool use, handoff, ambiguous identification and unsafe/untrusted transcript instructions.

**Acceptance:** A release records tested agent/model/config versions and can roll them back together. The evaluation set covers noisy speech, unknown customers, conflicting equipment, long transcripts and prompt injection. Agent tool permissions enforce read-only behavior independently of prompt wording.

## D. Consumer APIs, access and events

**Suggested owner: backend engineer with identity/security and frontend integration support.**

### IG-020 - Replace shared rep keys with authenticated, authorized users (P1; Observed/Recommendation)

**Evidence:** `app\auth.py:10-23`; `app\main.py:98-239`. One API key grants the same list/read/edit/transfer/hangup/submit access. `submittedBy` is supplied by the request, defaulting to `"rep"`, rather than derived from a verified principal. Browser SSE permits a query-string key.

**Task:** Add Entra-based user authentication and scoped authorization for reps, operators and integrations; establish tenant/team/call access rules appropriate to the product. Derive submitter identity from verified claims and record audit events. Replace long-lived URL keys with a safe browser streaming authentication design.

**Acceptance:** Wrong tenant/audience, expired token, read-only user, unauthorized call/team and forged submitter tests fail appropriately. Authorized reps retain the workflow; credentials do not enter URLs or access logs. Machine-to-machine access is separately scoped.

### IG-021 - Enforce configuration invariants consistently (P1; Observed)

**Evidence:** `app\main.py:29-37`; `app\auth.py:11-13`; `app\config.py:91-163`; `app\validate.py:11-82`. Explicitly supplying `Settings` suppresses production startup failure. Validation is incomplete for numeric ranges, service hours/timezone, endpoint schemes, JSON object shapes and function uniqueness/timeouts. Missing ACS/extractor settings are warnings even for a deployment intended to provide those capabilities.

**Task:** Separate test construction from production validation; define required capabilities/profiles and validate their dependencies. Add typed/ranged settings, early schedule/timezone validation, safe HTTPS endpoint validation and actionable function/schema errors. Make simulator activation an explicit development-only control.

**Acceptance:** Both environment-loaded and explicitly supplied invalid production settings fail startup. Misconfigured hours/timezone fail before the first call. Negative limits/timeouts, malformed JSON types and duplicate function names produce actionable errors. Deliberate API-only/local profiles remain supported.

### IG-022 - Add typed API requests and business relationship validation (P1; Observed)

**Evidence:** `app\main.py:149-153,206-235,250-268,345-350`; `app\drafts.py:405-451`. Raw dictionaries accept unchecked edit values and record IDs; invalid relationships can be linked without checking ownership. Customer creation POST and draft-link PATCH are separate operations, and a time-based account number is the only generated account reference.

**Task:** Define request/response models with size limits, allowed fields, UUID/ID formats, enum rules and clear null/clear semantics. Verify contact/equipment belongs to the selected customer and define what happens when the customer changes. Make create-and-link retry-safe; do not create an orphan/duplicate customer after partial failure. Map validation/not-found/conflict/dependency errors consistently.

**Acceptance:** Invalid types, overlong fields, unknown edit keys, malformed callback bodies and foreign equipment/contact links yield controlled 4xx responses without writes. Changing customer cannot retain an incompatible link. Retrying failed create-and-link reuses the same customer.

### IG-023 - Fetch actual Dataverse field lengths (P1; Reproduced)

**Evidence:** `app\discover.py:145-153,184-201`; `app\drafts.py:485-517`; `tests\test_discover.py:15-20,39-53`. The resolver uses `attrs[*].max`, but the live metadata fetcher requests only name/type and explicitly sets every maximum to `None`. Tests supply a maximum directly, bypassing the broken fetcher path.

**Task:** Fetch typed string/memo metadata including `MaxLength`, propagate limits into the resolved schema and validate selected columns. Make truncation safe even when the allowed length is shorter than the truncation marker; ensure overflow note creation is recoverable.

**Acceptance:** HTTP metadata fixtures exercise `fetch_meta -> resolve_schema -> submit` with realistic length limits and boundary/tiny values. Ticket text never exceeds the actual limit, and complete overflow notes remain available.

### IG-024 - Validate required schema capabilities, not only table existence (P1; Observed)

**Evidence:** `app\discover.py:87-126`; `app\schema.py:100-195,241-300`; `app\preflight.py:37-94`; `app\drafts.py:158-168`. Discovery can drop unmapped columns/lookups, including fields the draft lifecycle requires. Resolution is lazy on first Dataverse request; preflight verifies existence for surviving mappings, not all required write/type/relationship guarantees.

**Task:** Classify columns and relationships as required or optional per capability. Validate types, writeability, nullability, unique call/submission identity, option values, annotations support and required lookup navigation properties before admitting calls. Report optional capability degradation explicitly.

**Acceptance:** Removing draft status/call ID/edited-field tracking or a required link fails preflight/readiness with a precise error. An absent optional equipment/contact feature degrades consistently in prompts, API and writes. Run against both custom and standard Dataverse table profiles.

### IG-025 - Make metadata overrides, failures and resolved configuration deterministic (P2; Observed)

**Evidence:** `app\discover.py:145-180,207-229`; `app\main.py:160-179`; `app\drafts.py:158-168`. English priority inference can fail before explicit `tableOptions` are merged. Failed option metadata requests are silently skipped. `/v1/config` can expose the unresolved schema/hash before first Dataverse use and a different resolved one later.

**Task:** Apply explicit overrides before inference that can fail, surface metadata request failures, and use deterministic/configured option mappings rather than relying solely on localized labels. Resolve once during readiness and publish a stable effective config/version; define invalidation on schema changes. Restrict path/body translation to intended identifier/bind locations rather than arbitrary text values.

**Acceptance:** Localized/custom priority labels plus explicit mappings succeed. Metadata 403/429/5xx cannot silently produce a misleading valid mapping. Hash/capabilities agree across startup, rendered prompts and API, and free text is not rewritten as identifiers.

### IG-026 - Stabilize pagination, SSE replay and public contracts (P2; Reproduced/Observed)

**Evidence:** `app\main.py:100-138,237-245`; `app\state.py:103-129`; `app\drafts.py:203-208,244-247,328-369`; `app\simulator.py:9-70`; gateway `README.md` API/events/webhook/stats sections. Calls/transcripts return whole collections; Dataverse queries use fixed `$top` values without continuation. Invalid `Last-Event-ID` raises an exception. Actual transcript event is `transcript`, not the documented `transcript.line`; webhook headers and advertised statistics also differ. Simulator speaker/field/enrichment shapes differ from real bridge/extractor output.

**Task:** Define versioned API/event models, pagination/cursors and ordering. Follow Dataverse continuation where completeness is required; make deliberately capped suggestions explicit and avoid claiming unique matches from a truncated candidate set. Validate SSE cursors, report replay gaps, define end-of-call stream behavior and align documentation with actual header/event/stat names. Make simulated calls obey the real public contract so frontend development does not validate an incompatible shape.

**Acceptance:** Clients can traverse more than one page without gaps/duplicates. Invalid/expired/future SSE cursors have defined behavior, reconnect replay is deterministic, and consumer contract tests use the published event/header names.

### IG-027 - Make webhook delivery durable and bounded (P1; Observed/Recommendation)

**Evidence:** `app\state.py:103-113`; `app\webhooks.py:64-101`; `app\main.py:57-63`. Outbound events are fire-and-forget tasks with best-effort retries; a restart loses them, and tasks for successive events can arrive out of order.

**Task:** If consumers depend on events, introduce a durable outbox/queue with stable event IDs, bounded workers, retry/dead-letter/replay and documented ordering guarantees. Keep HMAC authentication, require signing in production, and support secret rotation without exposing delivery URLs in logs. Make best-effort delivery an explicitly named option if retained.

**Acceptance:** Receiver outage and process restart do not lose committed events. Duplicate delivery is documented and deduplicable; ordering behavior is tested; poison messages are visible/replayable. Audio processing is not blocked by a slow receiver.

**Dependencies:** IG-004 and IG-010.

### IG-028 - Define privacy, retention and outbound trust boundaries (P1; Observed/Recommendation)

**Evidence:** `app\state.py:13-38`; `app\bridge.py:116-130,362`; `app\extractor.py:132-144`; `app\webhooks.py:22-51,74-82`; `app\drafts.py:183-197`. Caller phone, transcript and extracted details are copied into model inputs, drafts, event payloads and potentially exception text. Public-DNS checks and the later HTTP connection are separate operations.

**Task:** Document data flows, approved recipients/regions, retention/deletion policy and disclosure/consent requirements with the responsible product/legal owners. Minimize/redact sensitive log/error/event data, including query tokens and webhook URLs. Enforce approved outbound destinations and network policy; evaluate DNS-rebinding/redirect/proxy behavior rather than treating the current URL check as a complete egress boundary.

**Acceptance:** A data inventory identifies every transcript/phone copy and its retention owner. Automated checks prove secrets and transcript contents are not emitted in operational errors. Retention/deletion covers caches, journals, Dataverse and delivery queues; consent/disclosure behavior is agreed and verified before rollout.

## E. Verification and maintainability

**Suggested owner: backend/quality engineer; shared with each feature owner.**

### IG-029 - Convert known failures into meaningful regression tests (P1; Observed)

**Evidence:** `tests\test_api.py:114-120,123-129,321-364`; `tests\test_drafts.py:87-114`; `tests\test_discover.py:39-60`. Some assertions are vacuous (`... or True`, or conditional success when an expected ID is absent). Coverage is weighted toward happy paths/local helpers; critical races, partial writes and normal bridge/finalization behavior are not established by the current suite.

**Task:** Fix ineffective assertions and add focused tests with each behavior fix. Use dependency injection, deterministic clocks/barriers and HTTP/protocol fakes rather than sleeps. Test real metadata-fetch shapes, transfer callbacks, reconnect/finalization, task cancellation, deferred functions, webhook failures and SSE backpressure.

**Acceptance:** Tests fail on the reviewed defect before its fix and pass afterward. No arbitrary catch-all `pytest.raises(Exception)` is used where a precise outcome is known. Tests isolate optional Speech presence/absence rather than requiring it unconditionally in the base environment.

### IG-030 - Add real-service acceptance, resilience and load gates (P1; Recommendation/Verify in Azure)

**Evidence:** Current tests exercise fakes and constructor/API behavior, not a real ACS/Teams/Voice Live/Speech/Dataverse end-to-end call.

**Task:** Create an opt-in isolated test environment and documented acceptance suite. Cover inbound answer, greeting, extraction, human handoff, dual-speaker notes, rep correction, Submit, disconnect and persisted recovery. Inject dependency throttling/outage, token refresh, container restart and revision replacement. Establish realistic concurrency and latency/cost targets before tuning defaults.

**Acceptance:** A release retains reproducible results for the complete workflow, long calls, multiple reps and the selected region/API versions. No duplicate tickets, unbounded resources or silent transcript loss under the agreed fault scenarios. Tests never target production/customer records.

**Dependencies:** Correctness tasks first; coordinate with CI/CD and observability.

### IG-031 - Refactor along existing boundaries after regression coverage (P2; Recommendation)

**Evidence:** `app\main.py:29-396`; `app\bridge.py:29-362`; `app\drafts.py:139-525`. Routing, callback state transitions, scheduling and integration behavior are densely coupled; several injected clients/callbacks have implicit types.

**Task:** Introduce typed protocols for ACS/tokens/drafts/agent transport, typed states/results and focused routers/services only where they simplify ownership and tests. Separate Dataverse transport/query construction from draft business rules. Keep the existing adapters/schema boundary; avoid speculative plugin frameworks or a microservice split.

**Acceptance:** The same acceptance/regression suite passes; behavior and public contracts remain stable. New integrations can be faked without private monkeypatching, and production code does not need broad type ignores to represent optional dependencies.

**Dependency:** IG-029; perform incrementally alongside relevant fixes.

## F. Delivery and Azure operationalization

**Suggested owner: platform/DevOps engineer; backend engineer for health and telemetry.**

### IG-032 - Add dependency-aware readiness and actionable telemetry (P1; Observed/Recommendation)

**Evidence:** `app\main.py:20-26,237-245`; `app\limits.py:69-82`; `app\bridge.py:346-362`; `app\extractor.py:173-203`. Health reflects initial config errors, not dependency access or current draft/transcriber health; degraded health still returns 200. Timeline logs and active-call counts exist, but there is no application tracing/metrics instrumentation in the reviewed Python code.

**Task:** Separate process liveness, startup validation and readiness to admit new calls. Validate configured dependency capabilities during startup without making a billable call. Add structured correlation across ACS call/event IDs, gateway call ID, model request, draft and ticket. Instrument latency, throttling, reconnects, extraction lag, failed persistence, finalization, queue depth, resource use and delivery failures. Export to Azure Monitor/Application Insights through supported OpenTelemetry integration, with redaction and sampling.

**Acceptance:** An alive process with broken required credentials is not declared ready. Dependency outage does not cause a liveness restart storm. Dashboards/alerts distinguish audio, model, persistence and handoff failures and link a synthetic call end to end without revealing transcript/credentials. Set SLOs and alert thresholds from measured pilot behavior.

### IG-033 - Enforce single-replica deployment until ownership/routing is solved (P1; Observed/Verify in Azure)

**Evidence:** `infra\deploy-gateway.ps1:191-195,245-246` creates a gateway with `--max-replicas 3` and updates only the minimum. The gateway's documented/process-local call state cannot support arbitrary callback/media/API routing across replicas. The deployment verifier checks the minimum, not the incompatible maximum.

**Task:** Set and verify `minReplicas=1,maxReplicas=1` on both create and update for the current architecture. Ensure revision/traffic behavior does not accidentally expose two independent owners for the same calls. Design durable business state, per-call media ownership, event routing and admission coordination before enabling scale-out; a Redis replacement alone is insufficient.

**Acceptance:** Fresh deployment and redeployment converge to one replica and the verifier rejects an incompatible maximum. A revision cutover follows a tested draining/recovery plan. A later two-instance test proves callbacks, media, functions and SSE route coherently before lifting the restriction.

**Dependencies for future scale-out:** IG-002, IG-004, IG-006, IG-007, IG-010 and IG-011.

### IG-034 - Prevent unintentional Event Grid subscription takeover (P1; Observed)

**Evidence:** `infra\deploy-gateway.ps1:267-292`. The topic-wide name `gateway-incoming` is reused for every gateway and excluded from competing-subscription warnings. Deploying a different app against the same topic can update the existing subscription endpoint.

**Task:** Check endpoint/deployment ownership before mutation and require explicit cutover for a different owner. If sharing ACS is supported, use deployment-specific subscriptions with non-overlapping called-number/resource-account filters; otherwise reject the unsupported topology early.

**Acceptance:** Deploying app B cannot silently redirect app A's calls. An intentional cutover has a reversible procedure. Supported side-by-side deployments route each number exclusively to its intended gateway and do not depend on acknowledging then ignoring misrouted calls.

### IG-035 - Reconcile removal of script-owned settings (P1; Observed)

**Evidence:** `infra\deploy-gateway.ps1:170-189,245-246`. Blank values are omitted, while `--set-env-vars` merges rather than removes them. Clearing a transfer target, CORS origin, extractor or line allow-list in parameters can leave its old runtime value.

**Task:** Define empty/unset semantics and explicitly remove or replace script-owned environment variables during update. Preserve unrelated operator-managed settings; display a sanitized desired-versus-effective configuration summary.

**Acceptance:** Deploy nonempty values, clear them and redeploy. Removed rep targets and origins are no longer active; desired config and the effective revision agree. Test switches between mutually exclusive authentication/configuration options as well.

### IG-036 - Stop automatically granting Dataverse System Administrator (P1; Observed/Verify in Azure)

**Evidence:** `infra\deploy-gateway.ps1:231-238`. Deployment selects `System Administrator` for the gateway application user on every run, potentially undoing previous least-privilege hardening.

**Task:** Define/configure a dedicated application role with only the required metadata/table/relationship/annotation permissions for each supported schema profile. Separate provisioning permissions from runtime permissions. Plan removal of previously granted administrator membership and independently verify Foundry agent tool access is read-only.

**Acceptance:** All intake operations work with the scoped role; unrelated table writes and administrative operations are denied. Redeployment neither restores nor requires System Administrator. Verify existing live memberships explicitly rather than assuming IaC reflects them.

### IG-037 - Fail deployment immediately on required command failures (P1; Observed)

**Evidence:** `infra\deploy-gateway.ps1:241-246`. Secret update and ingress command failures are not checked immediately; a later successful native command can overwrite `$LASTEXITCODE`.

**Task:** Check every required native command result before proceeding, using consistent sanitized diagnostics. Treat role/configuration/secret mutation failure as deployment failure, and distinguish partial provisioning from a ready release.

**Acceptance:** Inject secret-write, ingress, role-assignment and revision-update failures. Deployment exits nonzero at the failed phase without reporting success or leaking secrets. A failed key rotation cannot appear successful merely because the previous revision is healthy.

### IG-038 - Make resource-group boundaries explicit in generated parameters (P2; Observed)

**Evidence:** `infra\init-gateway-params.ps1:115-122,240-242`; `infra\deploy-gateway.ps1:100-103`. Initialization permits selecting a Container Apps environment in another group and writes that environment's group as the single `resourceGroup`; deployment subsequently looks for ACS in that same group.

**Task:** Either reject unsupported cross-group selection with an actionable message, or model app/environment and ACS/Event Grid scopes separately throughout initialization, deployment and verification. Prefer explicit resource IDs where ownership spans groups.

**Acceptance:** ACS in group A and Container Apps environment in group B either produce a usable deployment configuration or fail before writing misleading parameters. Same-group deployments remain unchanged.

### IG-039 - Make explicit-image rollback independent of the recent-tag menu (P2; Observed)

**Evidence:** `infra\deploy-gateway.ps1:129-134`. An explicit `-UseImage` is checked against only the eight tags fetched for the interactive menu.

**Task:** Resolve an explicitly requested tag/digest independently, retain the bounded menu for interactive use, and record immutable image identity with the deployment/config/agent versions.

**Acceptance:** An existing tag older than the eight listed entries deploys successfully through the explicit path; a genuinely missing image fails clearly. Operators can identify and restore a known-good release without rebuilding it.

### IG-040 - Verify the intended revision, probes and requested features (P1; Observed/Verify in Azure)

**Evidence:** `infra\deploy-gateway.ps1:175-189,245-258`; `infra\verify-gateway.ps1:40-49,59-78`; `infra\gateway.parameters.example.json:15-21`. An app-wide health response does not prove that the intended revision/image is ready or receiving traffic. Revision mode/traffic/probes are not established by these checks. Verification also does not compare desired optional settings with effective runtime state; requested live notes can degrade to off.

**Task:** Establish the supported revision mode and explicit startup/liveness/readiness probes. Verify the desired immutable image, `latestReadyRevisionName`, revision-specific health and actual traffic routing. Compare desired capabilities and settings with `/v1/config` and effective platform configuration without exposing credentials. Distinguish an intentionally disabled feature from an enabled-but-unavailable feature.

**Acceptance:** An old healthy revision cannot mask a failed/unrouted new revision. A requested Speech feature with missing SDK/auth fails verification or yields an explicitly accepted degraded result. Verify scaling maximum, revision mode, probes and traffic independently of a successful `/healthz` request.

**Dependencies:** IG-021, IG-032, IG-033 and IG-043.

### IG-041 - Deliver termination signals and gate deployments on call draining (P1; Observed)

**Evidence:** Gateway `Dockerfile:15`; `infra\deploy-gateway.ps1:241-245`; `docs\08-operations.md:75-78`. Uvicorn starts behind `sh -c` without `exec`/a signal-forwarding init. Deployment updates the app without an active-call drain gate; operations documentation already warns about dropped calls during deployment.

**Task:** Make the application receive termination signals directly and configure a shutdown budget compatible with its drain/checkpoint behavior. Stop admission and wait for active calls to finish, or refuse a deployment while calls remain. Define an explicit forced-cutover procedure when waiting is not acceptable.

**Acceptance:** Container termination reaches application cleanup. Deploying during an active call is blocked or follows the documented drain policy. A forced override is explicit and observable; autoscale/revision events cannot bypass the supported lifecycle silently.

**Dependencies:** IG-006 and IG-010; coordinate with IG-033 and IG-040.

### IG-042 - Implement actual coordinated secret rotation (P1; Observed)

**Evidence:** `docs\16-intake-gateway-deploy.md:279-282`; `infra\deploy-gateway.ps1:114-125`. The runbook says rerunning deployment rotates the Event Grid credential, but the deployer preserves existing Event Grid/gateway secrets and generates them only when absent.

**Task:** Provide an explicit rotation operation and correct the runbook. Coordinate ingress credentials, Event Grid configuration, app revision/restart and active per-call callback tokens; distinguish API keys, Event Grid credentials, gateway HMAC secrets and webhook signing secrets. Use versioned secrets/Key Vault references where warranted by the deployment's controls, with narrowly scoped access; lack of Key Vault alone is not a demonstrated defect.

**Acceptance:** Rotation changes the intended credential, verifies new delivery/access and rejects the old credential after the agreed overlap. Ordinary redeploy remains non-rotating. Rotating the gateway HMAC secret has a tested active-call policy and does not accidentally invalidate every live callback/media URL.

**Dependencies:** IG-027, IG-037 and IG-041 as applicable.

### IG-043 - Produce and promote one reproducible release artifact (P2; Observed/Recommendation)

**Evidence:** `.github\workflows\publish-gateway.yml:5-7,14-33`; `infra\deploy-gateway.ps1:153-158`; gateway `Dockerfile:1-10`, `requirements.txt:1-7`, `requirements-speech.txt:1`. CI publishes to GHCR while normal deployment builds the local checkout independently into ACR. Dependency ranges and base image are mutable; rebuilds can change contents beneath a commit-derived tag. Multiple branches share `latest`.

**Task:** Resolve a reviewed, locked dependency graph and recorded base digest; use an explicit update process rather than freezing forever. Build once, produce an SBOM/provenance record and scan the artifact, then promote the exact approved digest to the deployment registry. Separate development channels from immutable release identity. Record package/base/agent/config compatibility together.

**Acceptance:** Deployment consumes the validated digest without a local rebuild. Re-running a commit cannot silently replace an approved release. Dependency/base updates are reproducible and reviewed, vulnerabilities have an explicit triage policy, and operators can identify source and resolved components for every deployed revision.

### IG-044 - Gate publication with clean validation and both Speech variants (P2; Observed/Recommendation)

**Evidence:** `.github\workflows\publish-gateway.yml:14-33`; gateway `Dockerfile:1-15`, `requirements-dev.txt`, `requirements-speech.txt:1`; `tests\test_api.py:123-129`. The publication workflow builds the default image but has no test, lint or type-check gate. The current passing virtual environment does not establish a clean install or the optional-Speech-off image.

**Task:** Add pull-request/release validation using the chosen supported Python/package set: clean dependency install, focused tests, an agreed formatter/linter and incremental type checks. Build/import-smoke both `WITH_SPEECH=1` and `WITH_SPEECH=0`, separating optional-Speech tests. Validate deployment scripts/Bicep and request/agent/schema contracts where they change. Add tools deliberately and configure them to the existing code style rather than introducing a wholesale formatting rewrite.

**Acceptance:** A deliberately failing test/check prevents publication or promotion. Both packaging variants start successfully with the expected capabilities, and test discovery does not accidentally use an existing local virtual environment. Infrastructure/config validation failures block release before cloud mutation.

**Dependencies:** IG-029 and IG-043 for the complete release gate.

### IG-045 - Establish Python gateway infrastructure ownership (P2; Observed conditional risk)

**Evidence:** `azure.yaml:5-12`; `infra\main.bicep:189-230`; `infra\README.md:13-20`. The azd service deploys C# `VoiceIntake`, and Bicep owns that app's image/secrets/environment, including a placeholder image. The Python script accepts an existing app without excluding that resource. This is a risk if both deployment paths target the same app, not evidence that the current live gateway does so.

**Task:** Give the Python gateway its own declarative resource/deployment mode or separate resource ownership from the C# product explicitly. Reject incompatible existing-app ownership before mutation. Reconcile image, environment, secrets, identity, roles, ingress, probes, scale, Event Grid and monitoring through a documented source of truth. Update the repository navigation/runbooks to distinguish the two implementations and their commands.

**Acceptance:** Provision/reprovision of a supported Python deployment cannot replace it with a placeholder or C# image/configuration. Python and C# can coexist without overwriting each other, and drift is detected before release. A new environment can be provisioned from documented inputs without undocumented portal fixes.

## Recommended implementation sequence

| Stage | Work | Exit gate |
|---|---|---|
| 1. Protect business data | IG-001 through IG-005, with IG-029 regression cases alongside each fix | Rep edits survive interleaving; partial failure/retry cannot duplicate tickets; acknowledged transcript data is recoverable. |
| 2. Make the current topology safe | IG-006 through IG-014, IG-018, IG-020 through IG-024, IG-033 through IG-037 | One effective call owner; reliable terminal cleanup; authenticated/scoped users and runtime identities; transfer and persistence failures have visible recovery. |
| 3. Establish an operable release | IG-016, IG-019, IG-027 through IG-030, IG-032 and IG-040 through IG-045 | Reproducible artifact/config deployment, proven rollback/draining, dependency-aware health and fault-injection evidence from an isolated Azure environment. |
| 4. Improve and scale on evidence | IG-015, IG-017, IG-025, IG-026, IG-031, IG-038, IG-039, then explicitly designed scale-out | Agreed quality/latency/cost targets are met; cross-instance routing/ownership and data integrity are proven before increasing replicas. |

Stages can overlap when ownership is independent, but do not enable retries, remove locking, or increase replicas ahead of their integrity prerequisites.

### Decisions to record before committing to infrastructure

| Decision | Responsible discipline | Required output |
|---|---|---|
| Supported users/tenants, caller-identification policy and integration consumers | Product + identity/security | Access matrix; whether caller phone matching is only a suggestion rather than identity verification. |
| Call volume, duration, availability, transcript RPO/RTO and retention | Product + operations | Measurable targets that drive storage, admission, drain and recovery design. |
| Durable journal, submission transaction and event delivery guarantees | Backend + data/platform | A minimal architecture decision record with failure/replay semantics; do not adopt Redis, Cosmos DB and Service Bus indiscriminately. |
| Multi-target transfer and after-hours/degraded-service behavior | Product + realtime | Explicit caller experience for no answer, partial answer, model outage and unavailable notes. |
| Region, data residency, disclosure/consent, deletion and approved destinations | Product + responsible compliance owners | Deployment and data-handling constraints verified before a production rollout. |
| Agent/API/package support and upgrade cadence | Azure + AI engineering | Tested compatibility matrix, release ownership and rollback procedure. |

### Production exit criteria

All P0 items are closed with regression evidence. P1 items relevant to the intended rollout are closed or have an explicit, time-bounded risk acceptance with owner and mitigation; an acknowledged open item is not a completed fix. The complete call-to-ticket workflow and agreed failure/recovery cases pass in a representative Azure environment. Release records identify the image, effective configuration, schema, agent/model versions and outstanding risks.

This backlog is a source-based development starting point, not a guarantee that every production issue has been discovered. Update it with live-service findings and remove completed items only with linked implementation and acceptance evidence.

## Reference guidance

The implementation must be checked against the exact supported package/API versions selected for a release; guidance does not prove the current tenant configuration.

- [Azure SDK for Python: Call Automation](https://github.com/Azure/azure-sdk-for-python/blob/main/sdk/communication/azure-communication-callautomation/README.md): supported authentication/client usage.
- [Azure Identity client lifecycle](https://github.com/Azure/azure-sdk-for-python/blob/main/sdk/identity/azure-identity/README.md): credential ownership and async cleanup.
- [Dataverse conditional operations and optimistic concurrency](https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/perform-conditional-operations-using-web-api): ETags, conditional updates and table capability checks.
- [Dataverse batch operations](https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/execute-batch-operations-using-web-api): evaluate transactional changesets for the submission protocol.
- [Speech SDK Microsoft Entra authentication](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/how-to-configure-azure-ad-auth): supported credential-based configuration and endpoint prerequisites.
- [SpeechRecognizer authorization token lifecycle](https://learn.microsoft.com/en-us/python/api/azure-cognitiveservices-speech/azure.cognitiveservices.speech.speechrecognizer?view=azure-python): callers supplying an authorization token must keep it valid.
