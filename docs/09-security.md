# 09 · Security & compliance

This is a proof-of-concept architecture with sound foundations and several deliberate shortcuts. This page lists both, so
a security reviewer can see what is safe today and what must change before production.

## Identity and secrets

| Asset | Mechanism | Assessment |
|---|---|---|
| VoiceIntake → Foundry, Dataverse | System-assigned managed identity | Good. No secrets |
| Foundry agents → Dataverse MCP | Project managed identity connection | Good. No per-user tokens |
| ACS connection string, Speech key | Container Apps secrets | Acceptable; prefer Entra auth for ACS/Speech where supported |
| Rep Console and `/api/rep/*` | One shared `REP_KEY` | **Weak.** No per-user identity, passed in a URL |
| `/events/{callId}` SSE | None | **Weak.** Anyone with a call id can read transcripts |
| `/api/incoming` Event Grid webhook | Handshake only | Validate the Event Grid source/signature before production |
| Local `.env` files | Git-ignored | Keep out of source control; never share them |

## Authorization

| Item | Today | Target |
|---|---|---|
| Dataverse role for both identities | **System Administrator** | Custom role limited to the six `bdm_` tables: read all; create/write on drafts; create on tickets (app only); read-only for the Foundry identity |
| Who can create tickets | Anyone with the rep key | Authenticated reps only, with auditing |
| What agents can write | Instructions forbid writes; the note-taker's only write is through the app's draft save | Enforce with a read-only Dataverse role for the Foundry identity, so a prompt change cannot create records |

The strongest control is already in the design: **only the app's Submit endpoint creates tickets.** Back it with a
read-only role for the Foundry identity so the rule does not depend on prompt text.

## Data handling

| Data | Where it lives | Notes |
|---|---|---|
| Call audio | In flight only; not stored by this app | ACS may record only if you enable it. This app does not |
| Transcript | Container logs, Dataverse `bdm_transcript`, sent to Foundry agents | May include names, phone numbers, addresses |
| Caller phone | Dataverse, logs | Personal data |
| Draft | Dataverse | Retain per policy; add a cleanup job |

Recommendations:
- Play a **recording and AI disclosure** at the start of the call, per local law. This is not implemented ([roadmap](10-roadmap.md)).
- Set a retention policy for `bdm_servicerequestdraft` and for the Log Analytics workspace.
- Limit who can read transcripts in Dataverse (field or row security).
- Avoid logging full transcripts at information level in production.
- Confirm Foundry and Speech data-residency settings match your obligations.

## Network

- The app is exposed publicly because ACS and Event Grid must reach it. Restrict `/rep` and `/api/rep` further with Entra
  sign-in (Container Apps authentication) or a private route plus IP allow-list.
- ACS media streams over `wss://` to `/ws/{callId}`. Treat the call id as unguessable (it is an 8-character random hex) and
  consider adding a signed token.
- Put secrets in Key Vault references instead of inline Container Apps secrets if your standards require it.

## Hardening checklist (before real customers)

- [ ] Replace System Administrator with least-privilege Dataverse roles
- [ ] Sign reps in with Entra (Container Apps auth) and record `submittedBy` from the identity
- [ ] Remove or protect `/events/{callId}`
- [ ] Verify Event Grid deliveries and restrict `/api/incoming`
- [ ] Add recording and AI-disclosure prompt
- [ ] Define transcript retention and deletion
- [ ] Move secrets to Key Vault; rotate the current keys
- [ ] Turn on Container Apps and Log Analytics diagnostics and alerts
- [ ] Review the prompts for prompt-injection exposure: callers speak untrusted text to the agents
- [ ] Penetration-test the console and webhook endpoints

## Prompt-injection note

Everything a caller says reaches the agents. The design limits the blast radius: agents cannot write tickets, the
note-taker output is parsed as data, and only a human submits. Keep it that way, and never give either agent a write tool.
