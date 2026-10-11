# FloorWorks Live Intake

**An AI receptionist that answers your service line, a note-taker that fills in the ticket while the customer talks,
and a human rep who reviews and submits it.**

A customer dials one number. An AI voice agent answers instantly, finds the equipment in Dataverse, and brings in a human
rep on request, all on the same call. Behind the scenes a second agent keeps a **draft service request** up to date. The
rep sees it fill in live, fixes anything, and clicks **Submit**. Only then is a real service ticket created.

```
Caller ─► AI receptionist ─(on request)─► Human rep joins the same call
              │
              └─ note-taker ─► Draft in Dataverse ─► Rep Console ─ Submit ─► Service Ticket
```

## Why it is different

- **Draft, then human submit.** Agents never create tickets. Trust is built into the design.
- **One call, one app.** No bot VMs; the app owns the call end to end.
- **Native Microsoft Foundry agents** with business data from the **Dataverse MCP server**.
- **Rep edits always win.** Corrections are never overwritten by the AI.
- **Nothing is lost.** The draft and transcript persist even if the call drops.

## Documentation

| Start with | |
|---|---|
| [Solution overview](docs/01-overview.md) | What it does and why |
| [Architecture](docs/02-architecture.md) | Diagrams and call lifecycle |
| [Deployment guide](docs/03-deployment.md) | Stand it up in your tenant |
| [Demo guide](docs/11-demo-guide.md) | Run a clean demo |

Everything else is indexed in **[docs/README.md](docs/README.md)**: configuration, data model, AI agents, rep console,
operations and troubleshooting, security, roadmap and glossary.

## Repository layout

| Path | Purpose |
|---|---|
| [`src/VoiceIntake`](src/README.md) | **The product**: call handling, speech, agents, draft store, Rep Console |
| `src/CallController`, `src/StreamProcessor`, `src/Simulator` | Earlier two-service pipeline and a WAV replay simulator |
| [`dataverse/`](dataverse/README.md) | Schema and seed script for the manufacturing data |
| [`foundry/`](foundry/README.md) | Agent instructions, publishing and Foundry-to-Dataverse wiring |
| [`infra/`](infra/README.md) | Deployment and Azure telephony setup |
| [`teams/`](teams/README.md) | Teams number and rep enablement |
| [`experiments/`](experiments/README.md) | `AudioProbe` diagnostics |
| [`docs/`](docs/README.md) | All documentation |

## Quick start

```powershell
# 1. Data
dataverse/provision-dataverse.ps1 -OrgUrl https://<org>.crm.dynamics.com
# 2. Foundry <-> Dataverse, agents, toolbox     -> foundry/README.md
# 3. Telephony (bot, Event Grid, Teams number)  -> infra/README.md, teams/README.md
# 4. App
az acr build -r <acr> -t voice-intake:v1 src/VoiceIntake       # then create the Container App: docs/03-deployment.md
```

Then open `https://<app>/rep?k=<rep-key>` and call your number.

## Status

Proof of concept, running in one tenant. Core flow works end to end: inbound call, AI receptionist, Dataverse lookups,
rep transfer, live draft and form, rep Submit. Not yet production-ready: see the
[security checklist](docs/09-security.md) and the [roadmap](docs/10-roadmap.md) (least-privilege roles, Entra sign-in for
reps, recording disclosure, durable state, infrastructure as code).

## Prerequisites at a glance

Azure subscription with Container Apps, ACR, ACS, Speech and a Foundry project · Dataverse environment · Teams with a
phone number, a resource-account license and Enterprise Voice reps · PowerShell 7, Azure CLI, .NET 8 SDK.
Details in the [deployment guide](docs/03-deployment.md#1--prerequisites).

## License and sharing

No license has been chosen. Do not publish or share this repository until you decide on one and have removed any
environment-specific values (see [configuration](docs/04-configuration.md#reference-deployment-values)).
