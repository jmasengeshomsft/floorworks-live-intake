# Documentation

The supported application is the **Python Intake Gateway plus React Intake UI**.
PowerShell and azd both deploy the Python gateway; the .NET project under `foundry` is separate knowledge-base tooling.

## Current guides

| Guide | Purpose |
|---|---|
| [01 - Overview](01-overview.md) | Customer experience and draft-first workflow |
| [02 - Architecture](02-architecture.md) | Components, call flow, identity and recovery boundaries |
| [03 - Deployment](03-deployment.md) | Setup order and both supported deployment paths |
| [04 - Configuration](04-configuration.md) | Current environment variables and shared table configuration |
| [05 - Data model](05-data-model.md) | Business tables, drafts, journal and model changes |
| [06 - AI agents](06-agents.md) | Voice/extraction contracts and read-only tools |
| [07 - Intake UI](07-rep-console.md) | Draft review, editing and Submit |
| [08 - Operations](08-operations.md) | Troubleshooting, safe upgrades and acceptance checks |
| [09 - Security](09-security.md) | Existing controls and remaining hardening |
| [10 - Roadmap](10-roadmap.md) | Known limits and priorities |
| [11 - Demo guide](11-demo-guide.md) | Preparation, walkthrough and recovery |
| [16 - Gateway deployment details](16-intake-gateway-deploy.md) | PowerShell packaging, verification and Speech migration |
| [17 - Agent setup](17-agent-setup.md) | Connection, shared toolbox, prompts and publishing |
| [18 - Table configuration](18-table-configuration.md) | `dataverse\config.json`, metadata discovery and overrides |
| [Glossary](glossary.md) | ACS, TPE, MCP and other terms |

## Specialist reference

| Reference | Purpose |
|---|---|
| [12 - Dataverse MCP managed identity](12-dataverse-mcp-managed-identity-setup.md) | Manual project-identity setup |
| [13 - Telephony wiring](13-telephony-wiring.md) | Resource-account and ACS wiring |
| [14 - Telephony binding comparison](14-telephony-binding-comparison.md) | Binding approaches and investigation context |
| [15 - Telephony binding runbook](15-telephony-binding-runbook.md) | Teams preflight and binding steps |

## Engineering and proposed work

| Document | Status |
|---|---|
| [19 - Engineering review/backlog](19-intake-gateway-engineering-review.md) | Historical review evidence plus scoped implementation updates; not a current deployment certificate |
| [20 - Configuration-agent design](20-config-agent-design.md) | **Proposal**, not an implemented setup feature |

Historical presentation diagrams may show earlier implementations; the maintained
[architecture guide](02-architecture.md) describes the current code.

## Repository guides

[Application development](../src/README.md) · [Dataverse](../dataverse/README.md) ·
[Foundry](../foundry/README.md) · [Infrastructure](../infra/README.md) · [Teams](../teams/README.md)

Commands use PowerShell unless stated otherwise. Replace `<placeholders>` with your own values; keep private
environment/configuration files and transcript data out of Git.
