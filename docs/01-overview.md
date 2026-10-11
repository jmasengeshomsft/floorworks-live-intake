# 01 · Solution overview

## The problem

Equipment manufacturers and service organizations take service requests by phone. A rep has to listen, ask the right
questions, look up the customer and the machine, and key everything into a ticket form while the customer waits.
Typical pain points:

- **Slow, manual intake.** Reps retype what was said and look up records by hand.
- **Inconsistent data.** Free-form notes, misspelled customers, missing asset numbers.
- **Brittle telephony.** Bot-based call recording often needs a Windows VM per call stack and adds latency.
- **Live-only AI.** Tools that only extract while the call is happening lose work when the call drops.

## What FloorWorks Live Intake does

A customer dials a phone number. An **AI receptionist** answers immediately, greets them, and collects the basics.
If the customer asks for a person, the receptionist **adds a human rep to the same call**. Throughout the call a
**note-taker agent** listens to the transcript and keeps a **draft service request** up to date in Dataverse.

The rep opens the **Rep Console**, sees the draft fill in live, corrects anything, links the customer and the equipment,
and clicks **Submit**. Only that click creates the real **Service Ticket**.

```
Customer ──► AI receptionist ──(only if asked)──► Human rep joins the same call
                  │
                  └── note-taker agent ──► Draft (Dataverse) ──► Rep Console ──Submit──► Service Ticket
```

## Design principles

| Principle | What it means in practice |
|---|---|
| **Draft, then human submit** | Agents never create tickets. They write a persisted draft; the rep is the only path to a real ticket. |
| **One call, one app** | A single application answers the call, streams the audio and hosts the console. No bot-per-call VMs. |
| **Native Foundry agents** | The receptionist is a Microsoft Foundry *voice agent*; the note-taker is a Foundry *prompt agent*. Prompts and tools are managed in Foundry, not hard-coded. |
| **Business data through MCP** | Agents read equipment, quality alerts and tickets through the **Dataverse MCP server**, not custom APIs. |
| **Rep edits always win** | Once a rep edits a field, the agents stop overwriting it. |
| **Persist early** | The draft, transcript and summary are saved as the call progresses, so a dropped call still leaves a reviewable draft. |

## The customer experience

1. The caller dials the FloorWorks number and hears a greeting within about a second.
2. The receptionist asks what they need. For an equipment problem it asks for the machine (by name or model; no asset
   number needed) and what is wrong.
3. The receptionist can look up equipment and open quality alerts for the caller. It never promises a ticket.
4. If the caller says they want a person, the receptionist transfers: the rep is added to the call and the AI goes quiet.
5. The rep already sees everything captured so far, so the caller does not repeat themselves.

## The rep experience

1. Open the console before the call (or pick the live call from the list).
2. Watch transcript and the **Service Intake Form** fill in. Fields highlight when the agents change them.
3. Fix anything wrong; link the customer and equipment from suggested matches.
4. Click **Submit** to create the Service Ticket. The draft is marked *Submitted* and locked.

## What is in the box

| Capability | Status |
|---|---|
| Inbound PSTN call into Teams and ACS | Working |
| AI voice receptionist (Foundry voice agent) with Dataverse lookups | Working |
| Add a Teams rep to the live call | Working (requires the rep to be Available in Teams) |
| Per-speaker transcription (caller, AI, rep) | Working |
| Note-taker draft with customer and equipment matching | Working |
| Rep Console with fixed-shape intake form, live updates, submit | Working |
| Dataverse manufacturing data model with seed data | Working |
| Power Apps front end | Not built. See [roadmap](10-roadmap.md) for the path |

## Reference deployment

The proof of concept runs in a single Azure subscription with an Azure Container Apps host, Azure Communication Services,
a Microsoft Foundry project and a Dataverse developer environment. See [Architecture](02-architecture.md) for details.
