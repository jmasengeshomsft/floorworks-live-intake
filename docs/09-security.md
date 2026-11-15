# 09 - Security and compliance

This proof of concept has explicit integrity/authentication controls, but is not production-hardened.
Local regression coverage is not a penetration test or live-service certification.

## Current controls and limits

| Boundary | Current mechanism / limitation |
|---|---|
| Gateway to Foundry/Dataverse | Managed identity in Azure; separate Dataverse application user required |
| Gateway to Speech | Entra/managed identity with Speech resource ID, custom subdomain and Speech User role; legacy keys remain supported |
| Gateway to ACS | Connection string in supplied deployment paths; runtime also supports an Entra-authenticated ACS endpoint |
| Foundry to Dataverse | Project managed-identity connection and read-only tool allowlist; enforce matching privileges in Dataverse |
| Consumer `/v1` API, including SSE | Shared `API_KEYS`; no per-user, per-call or tenant authorization |
| Event Grid incoming calls | Required shared `EVENTGRID_SECRET`; intended number/identity filtering is separate |
| ACS callbacks/media | Per-call HMAC token plus enforced ACS JWT validation outside local development |
| Rep/AI writes | Typed contracts, relationship validation, rep-edit ownership, ETags and atomic/idempotent Submit |
| Outbound notifications | HMAC when configured, bounded best-effort retries; consumers need replay/idempotency controls |

Only the gateway Submit endpoint creates tickets in this workflow. Shared API keys still permit their holders to invoke
that endpoint; "human Submit" is not a substitute for identity-based authorization. `submittedBy` is caller-supplied.
The UI stores its API key in browser localStorage and SSE authentication can put it in a query string.
Protect browser profiles, URLs, proxy logs and screenshots accordingly.

## Permissions and secret handling

The gateway and Foundry project are **different principals**. Give the gateway only the required journal/draft
read/create/write, business lookup/customer/ticket/annotation and relationship privileges. Keep the project's business
access read-only and exclude the journal from agent tools. The demo scripts' **System Administrator** role is too broad
for customer use and must be replaced with reviewed roles.

Both deployment paths place API/HMAC/ACS credentials in Container Apps secrets. azd environments and private parameter
files still contain sensitive local values: protect them and never commit/share them. `LOCAL_DEV=1` is never appropriate
on a public deployment. Key Vault integration and coordinated secret rotation remain hardening work.

## Data handling

Audio is streamed, not recorded by this application. Transcripts/caller information can appear in memory, Dataverse
drafts, journal checkpoint JSON, model requests and logs. Tickets/overflow notes can hold further copies.
Define retention, deletion, access controls and data-residency obligations for **all** copies.

Dataverse-only durability preserves successful checkpoints, not speech still uncommitted during an outage/process crash.
Plan disclosure/consent for AI participation and transcription under applicable policy/law; do not imply this app records
audio or already implements a compliance workflow.

## Before customer production use

- Replace shared keys with authenticated, authorized rep/tenant access and identity-derived audit fields.
- Replace broad demo Dataverse roles and review all agent tool permissions.
- Restrict browser/API access without breaking authenticated ACS/Event Grid callbacks.
- Define disclosure, transcript/journal/log retention and deletion; reduce sensitive logging.
- Adopt approved secret storage/rotation, diagnostics, alerts and incident procedures.
- Validate callback/JWT/token rejection, prompt injection, endpoint access and dependency posture in the target environment.
- Complete live acceptance and a dedicated security review; track remaining work in the [backlog](19-intake-gateway-engineering-review.md).

Callers and model output are untrusted input. Keep strict output validation, read-only agent tools, protected rep edits
and the draft-first workflow when extending the system.
