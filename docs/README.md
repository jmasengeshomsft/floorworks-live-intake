# FloorWorks Live Intake — Documentation

Start here. Each section stands alone; read them in order for the full story, or jump to what you need.

| # | Section | Read it if you want to… |
|---|---|---|
| 01 | [Solution overview](01-overview.md) | understand the problem, the customer experience and the value |
| 02 | [Architecture](02-architecture.md) | see how a call flows through every component, with diagrams ([voice-agent mode](architecture-voice-agent-mode.html), [step by step](architecture-voice-agent-steps.html)) |
| 03 | [Deployment guide](03-deployment.md) | stand the solution up in your own tenant, step by step |
| 04 | [Configuration reference](04-configuration.md) | look up every environment variable, connection and identifier |
| 05 | [Data model](05-data-model.md) | see the Dataverse tables, columns, relationships and option values |
| 06 | [AI agents](06-agents.md) | understand the voice agent, the note-taker, the toolbox and how to publish new versions |
| 07 | [Rep console](07-rep-console.md) | use or extend the web console reps use to review and submit |
| 08 | [Operations & troubleshooting](08-operations.md) | read logs, diagnose a bad call, fix common failures |
| 09 | [Security & compliance](09-security.md) | review identities, roles, data handling and hardening work |
| 10 | [Roadmap & known limits](10-roadmap.md) | know what is not done yet and what to build next |
| 11 | [Demo guide](11-demo-guide.md) | run a clean demo, including a script and a recovery plan |
| 16 | [Intake gateway: package, deploy, troubleshoot](16-intake-gateway-deploy.md) | deploy the Option A gateway and diagnose "Event Grid delivers but nothing happens" |
| 17 | [Agent setup](17-agent-setup.md) | `foundry/setup-agents.ps1`: connection, shared toolbox, note-taker and voice agent with pre-approved tools |
| — | [Glossary](glossary.md) | decode the acronyms (ACS, TPE, MCP, ...) |

## Repo map

| Folder | What lives there | Section README |
|---|---|---|
| `src/VoiceIntake` | **The product.** One app that owns the call, the speech-to-text, the agents and the rep console | [src/README.md](../src/README.md) |
| `dataverse/` | Idempotent schema and seed script for the manufacturing data | [dataverse/README.md](../dataverse/README.md) |
| `foundry/` | Agent instructions, publishing and Foundry-to-Dataverse wiring | [foundry/README.md](../foundry/README.md) |
| `infra/` | Container Apps deployment, Event Grid and bot setup | [infra/README.md](../infra/README.md) |
| `teams/` | Teams resource account, phone number and rep enablement | [teams/README.md](../teams/README.md) |

## Conventions used in these docs

- `<angle-brackets>` mark values you must supply.
- Commands are PowerShell unless stated.
- "Rep" means the human customer service representative. "Receptionist" means the AI voice agent that answers first.
- The shipped demo values (tenant, resource names) appear only in clearly marked *Reference deployment* boxes; replace them with your own.

