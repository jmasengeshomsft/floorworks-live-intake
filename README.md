# FloorWorks Live Intake

**An AI receptionist answers the service line, a note-taker prepares a draft, and a human rep reviews and submits it.**

The receptionist looks up business records in Dataverse and brings a Teams rep into the same call on request.
The React Intake UI shows the transcript and draft, preserves rep corrections, and creates a ticket only on Submit.

```text
Caller -> ACS -> Python gateway <-> Foundry voice agent
                     |
                     +-> Note-taker -> Dataverse draft -> Intake UI -> Submit -> Ticket
                     +-> Dataverse transcript/extraction journal
```

[Historical architecture illustration](docs/images/architecture.png). See the
[current architecture](docs/02-architecture.md) for the Python gateway.

> **Disclaimer.** This is a sample, customized implementation of the
> [Azure-Samples/call-center-voice-agent-accelerator](https://github.com/Azure-Samples/call-center-voice-agent-accelerator).
> It also uses the [Dataverse MCP server in Microsoft Foundry](https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/consume-dataverse-mcp-server-in-microsoft-foundry-bringing-business-data-to-ai-a/4548218)
> to bring business data to the agents. It is provided as-is for demonstration, not as production-ready software.

### Key references

- [Call center voice agent accelerator](https://github.com/Azure-Samples/call-center-voice-agent-accelerator): the base this project customizes.
- [Consume the Dataverse MCP server in Microsoft Foundry](https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/consume-dataverse-mcp-server-in-microsoft-foundry-bringing-business-data-to-ai-a/4548218)
- [Voice agent telephony channels: configure transfer to a person](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/voice-agent-telephony-channels?tabs=portal#configure-transfer-to-a-person)

## Start here

| Guide | Purpose |
|---|---|
| [Deployment](docs/03-deployment.md) | Prerequisites and both supported deployment paths: PowerShell and azd |
| [Architecture](docs/02-architecture.md) | Components, call lifecycle and persistence boundaries |
| [Configuration](docs/04-configuration.md) | Current gateway settings, identities and secrets |
| [Intake UI](docs/07-rep-console.md) | Review, correct and submit drafts |
| [Operations](docs/08-operations.md) | Diagnose startup/call failures and deploy safely |
| [Documentation index](docs/README.md) | All guides, reference material and development backlog |

## Repository layout

| Path | Purpose |
|---|---|
| [`src/intake-gateway`](src/intake-gateway/README.md) | Python / FastAPI gateway, Dataverse persistence, REST/SSE and tests |
| [`src/intake-ui`](src/intake-ui/README.md) | React / TypeScript client, deployed separately from the gateway |
| [`dataverse`](dataverse/README.md) | Shared table configuration, schema/journal provisioning and optional demo data |
| [`foundry`](foundry/README.md) | Agent prompts/setup and the optional .NET Foundry IQ knowledge-base CLI |
| [`infra`](infra/README.md) | PowerShell deployment, azd/Bicep and Azure telephony setup |
| [`teams`](teams/README.md) | Teams number, resource account and rep enablement |

The retired C# gateway is removed. `FloorWorksLiveIntake.sln` remains for the **Foundry IQ CLI**, not the gateway.

## Setup sequence

1. Review `dataverse\config.json`; provision the schema, including the required journal. Demo data is opt-in.
2. Prepare Foundry/project access and publish the two agents with `foundry\setup-agents.ps1`.
3. Deploy the Python gateway with **either** PowerShell or azd; configure its Dataverse application user.
4. Configure authenticated Event Grid delivery and Teams routing, then connect the Intake UI.

For new Azure resources, azd provisioning comes before agent setup; application deployment follows it.
See the [deployment guide](docs/03-deployment.md) for the actual commands and ordering.
The UI connects to the gateway `/v1` API using an API key; there is no gateway-hosted `/rep` page.

## Status and limits

This is a proof of concept, not a production-hardened product. Use **one gateway process/replica** and avoid overlapping
revisions during recovery. Dataverse checkpoints recover committed notes and unfinished extraction, **not live calls**.
Speech not yet committed during a Dataverse outage can be lost if the process also fails.

Shared API keys, broad demo Dataverse roles and deployment draining still need hardening. Local regression checks
do not replace live Azure/Dataverse acceptance. See [security](docs/09-security.md),
[known limits](docs/10-roadmap.md) and the [engineering backlog](docs/19-intake-gateway-engineering-review.md).

Development tools: Python 3.12+, Node.js supported by Vite 8 (20.19+ or 22.12+), PowerShell 7 and Azure CLI.
Azure deployment also needs ACS, Foundry/Speech, Container Apps/ACR, Dataverse and the relevant Teams licensing.
azd is needed only for that deployment path; .NET 8 SDK is needed only for the optional Foundry IQ CLI.

## License

[MIT](LICENSE). Keep credentials, private configuration and customer data out of source control.
