# 10 · Roadmap & known limits

## Known limits today

| Area | Limit | Impact |
|---|---|---|
| Rep availability | If the rep is offline or in Do Not Disturb, `AddParticipant` fails (480 / 603) | The AI resumes, but never tells the caller a rep is unavailable |
| Reconnect | ACS can drop and reopen the media socket; the voice agent restarts and re-greets | Caller hears the greeting twice |
| Speech accuracy | 24 kHz to 16 kHz resample, no phrase list | "CNC Mill" heard as "CNC Mail" |
| Console auth | Shared key | No per-rep identity or audit |
| Front end | Web UI, not Power Apps | Customers expecting a Power Platform app must rebuild the screen |
| Draft discard | Schema has *Discarded*; nothing sets it | Test drafts pile up |
| Customer match | By exact phone only | Unknown numbers need manual linking |
| Submit rule | Requires customer **and** equipment links | A new customer or unknown asset blocks Submit |
| Scaling | One replica, in-memory call state | One call stack; restart loses live state |
| Roles | System Administrator for both identities | Over-broad access |
| Consent | No recording or AI disclosure | Compliance gap in many jurisdictions |
| Languages | `en-US` only | |
| Scheduling | Not implemented | The JDDO scenario includes scheduling; this build stops at ticket intake |

## Proposed next steps

Ordered by value for a customer pilot.

### Now (pilot readiness)
1. **Say something when no rep is available.** After a failed `AddParticipant`, have the receptionist tell the caller and offer a
   callback; capture the preferred time on the draft.
2. **Audio consent line.** Opening disclosure for AI and recording.
3. **Fix the greeting repeat.** Skip the greeting when the same call reconnects, for example by sending prior transcript as
   context to the voice agent.
4. **Phrase list for Speech.** Seed from `bdm_equipment` names and asset numbers.
5. **Least-privilege Dataverse roles.** Read-only for the Foundry identity.
6. **Entra sign-in for the console.** Remove the shared key; record `submittedBy` automatically.
7. **Discard and clean-up.** A Discard button and a scheduled job for stale drafts.

### Next (product depth)
8. **Allow Submit with no equipment match**, creating a ticket flagged for triage.
9. **Fuzzy customer match** by name, company and address, with confidence shown to the rep.
10. **Scheduling.** Offer technician time slots from Dataverse and book them on the ticket.
11. **Call summary and handoff note** posted to the rep's Teams chat when a transfer begins.
12. **Multiple reps and queues** with skills-based routing instead of a fixed list.
13. **Analytics.** Dashboard of call volume, transfer rate, time to submit, field-edit rate (a proxy for AI accuracy).

### Later (scale and platform)
14. **Power Apps front end** on the draft table (see [Rep console](07-rep-console.md)).
15. **Durable state.** Move call state to a store so the app can run multiple replicas and survive restarts.
16. **Infrastructure as code.** Bicep or azd for the whole stack; today only the legacy services have a deploy script.
17. **CI/CD and tests.** Unit tests for `Drafts.cs` merge rules; a recorded-call regression suite using the Simulator.
18. **Multilingual** receptionist and STT.
19. **Outbound callbacks** and SMS confirmations.

## Design decisions to revisit

| Decision | Why it was taken | Revisit when |
|---|---|---|
| App owns the call | One call, no handoff gaps, no bot VM | You need multi-tenant isolation |
| Receptionist and note-taker are separate agents | Voice latency stays low; extraction can think longer | Voice agents gain structured-output reliability |
| Draft first | Human accountability, trust building | You have measured field accuracy high enough for auto-create on low-risk cases |
| Fixed form shape | Keeps schema, prompt and UI aligned | Customers need per-tenant forms |

## Change log of this build

| Stage | Outcome |
|---|---|
| Dataverse schema, MCP connection, Entra app | Manufacturing data and agent access in place |
| Teams Phone Extensibility path | Phone number routes into ACS |
| Two-service pipeline | Rep-answered call with live extraction |
| Unified `VoiceIntake` app | AI answers first, adds a rep on request |
| Draft-then-submit + Rep Console | Agents never create tickets; rep Submit does |
| Reconnect handling, managed-identity toolbox, explicit-only transfer | Fixes from live test calls |
| Fixed-shape live intake form | Rep sees fields fill during the call |
