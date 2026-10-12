# 11 · Demo guide

A tight 8–10 minute walkthrough, plus preparation and a recovery plan.

## Preparation (15 minutes before)

- [ ] `GET /healthz` returns `ok`; confirm the latest revision
- [ ] Presenter signed in to Teams as the rep, status **Available**, Do Not Disturb off
- [ ] Rep Console open in a browser window you can share: `https://<fqdn>/rep?k=<key>&rep=<your name>`
- [ ] A second phone to dial in from (speaker off, quiet room)
- [ ] Seed a customer whose `bdm_primaryphone` is that phone's E.164 number (so the customer auto-links)
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

**3 · Show the data lookup (1 min).** Ask the receptionist: "Are there any open quality alerts on that furnace?" It answers
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

The console is a thin client over the Dataverse draft table; a Power Apps canvas app can sit on the same tables with no
backend change ([Rep console](07-rep-console.md#building-the-same-experience-in-power-apps)).

## Recovery plan

| If this happens | Do this |
|---|---|
| Rep does not ring | Check Teams status. Say "I'll bring someone in manually"; show the AI continuing and the draft |
| AI says it cannot look up records | Skip the lookup step; continue with the intake; fix later ([toolbox](06-agents.md#toolbox-floorworks-dataverse)) |
| Greeting plays twice | Carry on; it is a known reconnect quirk |
| Equipment misheard | Use it: edit the field in the console and show that rep edits win |
| No draft appears | Open the *recent* list; check `draft save error` in logs |
| Whole call fails | Show a pre-recorded call result: keep a previous submitted draft and ticket to walk through |

## After the demo

- Delete the demo ticket and draft rows if the environment is shared.
- Note any misheard terms and add them to the Speech phrase list backlog.
- Capture the call id for the logs if something looked off.
