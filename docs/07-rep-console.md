# 07 - Intake UI

The rep console is now the separate **React Intake UI** in `src\intake-ui`.
The gateway exposes `/v1` REST/SSE; it does not serve the retired `/rep?k=` page or `/api/rep/*` API.

## Open and connect

From `src\intake-ui`:

```powershell
npm ci
npm run dev
```

Open `http://localhost:5173`. Enter the gateway URL and an API key from `API_KEYS` in the connection bar.
Allow that exact browser origin in the gateway's `CORS_ORIGINS`.
For hosted use, run `npm run build` and serve `dist` from your approved static host over HTTPS.

Connection settings, including the API key, are stored in browser localStorage. Treat the browser profile as
credential-bearing: use trusted devices, clear settings after a shared demo, and do not put keys in screenshots.
Shared keys do not provide per-user authorization.

## Review a call

Select a call/draft to see the transcript, extracted fields, coaching, enrichment, relationship links and events.
The UI consumes authenticated SSE while a call is live. Replay gaps/slow-consumer resets reload snapshots and reconnect;
the browser's event history is not a durable audit record.

Draft fields include customer name, contact phone, equipment/asset text, location, preferred time, complaint,
priority and summary. Correct fields, select relationships, then press **Save**.
Saved rep edits, including explicit clears, are protected from later AI updates.
Do not assume unsaved browser edits have reached Dataverse.

## Customer, equipment and contact links

Customer search remains available on linked drafts. When changing a customer, replace or explicitly clear incompatible
equipment/contact links in the **same edit**. Use the clear-links checkbox when appropriate; the gateway rejects
mismatches instead of silently choosing a different relationship. Individual equipment/contact links can also be cleared.

Creating a customer requires clearing existing equipment/contact links first. The gateway validates relationships
again at Submit. Required equipment depends on the configured table profile; the current UI reflects that configuration.

## Submit

Review the draft and linked records before **Submit ticket**. The gateway commits ticket creation, any overflow note
and the Submitted draft link atomically. Retrying a completed submission returns the existing ticket rather than
creating another one. Concurrent business edits use ETags and can return a conflict requiring a refresh/review.

Submitted drafts are read-only for business edits and AI field updates. Transcript mirroring may continue until the
call/journal work finishes. The entered rep name is still caller-supplied metadata, not a verified signed-in identity.

## Failure states

Extraction failures retain the previous valid result and expose an explicit retry action. Check checkpoint/persistence
errors as well: a visible transcript can be newer than its last successful Dataverse checkpoint.
Completed, failed, expired or cancelled function invocations cannot accept late results.

If requests fail, check the browser network response, gateway key/URL, exact CORS origin and
[operations guide](08-operations.md). `/healthz` alone does not establish Dataverse write or Speech success.

## API and alternative front ends

The [gateway README](../src/intake-gateway/README.md#api)
lists the supported routes; `/v1/drafts/{callId}` is the review/edit endpoint and
`POST /v1/drafts/{callId}/submit` owns ticket creation.

A Power Apps/custom front end should use those mutation endpoints rather than reimplementing Submit or writing
draft fields directly: direct writes can bypass edited-field ownership, relationship checks, ETags and atomicity.
Preserve the draft-first/human-submit workflow.

Mocked relationship and resilience browser regressions are documented in the [UI README](../src/intake-ui/README.md).
