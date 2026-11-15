# 11 · Demo guide

A tight 8–10 minute walkthrough, plus preparation and a recovery plan.

## Preparation (15 minutes before)

- [ ] `GET /healthz` returns `ok`; confirm the latest revision
- [ ] Presenter signed in to Teams as the rep, status **Available**, Do Not Disturb off
- [ ] Intake UI open on its separate host, connected to the gateway with an API key; exact UI origin allowed by CORS
- [ ] A second phone to dial in from (speaker off, quiet room)
- [ ] Use an account/contact whose configured phone column contains the caller's number (for the default profile, `account.telephone1` or `contact.mobilephone`)
- [ ] Confirm equipment names you will say exist (for example *Heat Treat Furnace F2*, *CNC Mill 3*)
- [ ] No deployments or restarts planned during the demo
- [ ] Dataverse `bdm_serviceticket` count noted, to prove nothing is created before Submit

## Talk track

**1 · Frame the problem (1 min).** "Today a rep listens, asks questions and types the ticket while the customer waits."

**2 · Place the call (3 min).** Dial the number and say, with natural pauses:
> "Hi, this is Maria from Acme Plastics. Our Heat Treat Furnace F2 isn't holding temperature. It's pretty urgent, and we
> can't run the next batch. We're in Brussels."

Narrate while the console updates: transcript lines appear, the Service Intake Form fills in, fields flash, the summary
grows, and the coaching box suggests the next question.

**3 · Show the data lookup (1 min).** Ask the receptionist: "Do we have any open tickets on that furnace?" It answers
from Dataverse through MCP.

**4 · Bring in a human (1 min).** Say: "Can I talk to a person?" The rep's Teams rings. Answer. The AI goes quiet.
Point out the rep never asked the caller to repeat anything.

**5 · Review and submit (2 min).** Hang up. In the console:
- Correct a field to show rep edits stick.
- Link the customer and equipment if needed.
- Click **Submit**. Show the ticket appear in Dataverse (Service Tickets table).

**6 · Close (1 min).** Recap the guardrail: *agents only write a draft; a person submits.* Then show the architecture
([02](02-architecture.md)) if the audience is technical.

## What to say about "why not Power Apps?"

The UI is a client of the gateway API. A Power Apps front end can use those endpoints while preserving protected edits
and atomic Submit ([alternative front ends](07-rep-console.md#api-and-alternative-front-ends)).

## Recovery plan

| If this happens | Do this |
|---|---|
| Rep does not ring | Check Teams status. Say "I'll bring someone in manually"; show the AI continuing and the draft |
| AI says it cannot look up records | Skip the lookup step; continue with the intake; fix later ([toolbox](06-agents.md#tools-and-approval)) |
| Greeting plays twice | Carry on; it is a known reconnect quirk |
| Equipment misheard | Use it: edit the field in the console and show that rep edits win |
| No draft appears | Refresh the draft list; inspect checkpoint/persistence errors and extraction status |
| Whole call fails | Show a pre-recorded call result: keep a previous submitted draft and ticket to walk through |

## After the demo

- Apply the agreed cleanup policy to demo tickets, drafts, completed journal rows and logs; reconcile incomplete journals first.
- Note any misheard terms and add them to the Speech phrase list backlog.
- Capture the call id for the logs if something looked off.
