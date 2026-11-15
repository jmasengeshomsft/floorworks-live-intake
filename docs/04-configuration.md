# 04 - Configuration reference

The Python gateway reads environment settings from
[`app/config.py`](../src/intake-gateway/app/config.py), with a copyable
[`.env.example`](../src/intake-gateway/.env.example). This page summarizes the deployment contract;
the [gateway README](../src/intake-gateway/README.md) covers API, functions, events and limits.

## Required production voice settings

| Variable | Purpose |
|---|---|
| `PUBLIC_BASE_URL` | Public HTTPS base for callbacks and the WSS media endpoint |
| `FOUNDRY_PROJECT_ENDPOINT` | `https://<account>.services.ai.azure.com/api/projects/<project>` |
| `VOICE_AGENT_NAME`, `EXTRACTOR_AGENT_NAME` | Names actually published by agent setup |
| `ACS_CONNECTION_STRING` **or** `ACS_ENDPOINT` | ACS authentication; configure exactly one. Endpoint mode requires Entra calling permissions |
| `API_KEYS` | Comma-separated consumer keys for all `/v1` routes |
| `GATEWAY_SECRET` | HMAC secret for per-call callback/media tokens |
| `EVENTGRID_SECRET` | Required on Event Grid deliveries as `?s=` or `aeg-sas-key` |
| `ACS_JWT_MODE=enforce` | Enforce ACS-signed JWT validation outside local development |
| `ACS_RESOURCE_ID` | ACS **immutable resource ID**, the JWT audience; not its ARM resource path |
| `DATAVERSE_URL` | Dataverse organization URL |
| `DATAVERSE_SCHEMA` or `DATAVERSE_SCHEMA_FILE` | Shared table configuration, required with Dataverse outside local development |

Use long cryptographically random API/HMAC secrets. Both deployment paths use Container Apps secret references.
Never commit `.env`, `.azure` or private `infra\gateway.<customer>.json` files.
The old C# settings (`REP_KEY`, `REP_TEAMS_OBJECT_IDS`, `NOTETAKER_AGENT`, `TRANSCRIPTION_MODE`) are not supported
gateway settings; use the current names below and connect the separate UI.

## Identity, Speech and optional behavior

| Variable | Default / behavior |
|---|---|
| `AZURE_CREDENTIAL_MODE` | `auto`: Azure CLI locally, production credential chain without developer fallbacks; `managed_identity` explicitly requires MI |
| `AZURE_CLIENT_ID` | Optional user-assigned managed identity client ID |
| `LOCAL_DEV` | `0`; `1` enables local-only relaxations and CLI identity. Never enable on a deployed app |
| `GATEWAY_PROFILE` | `voice`; `api` explicitly omits voice capabilities and rejects incoming calls. API/Dataverse requirements still apply |
| `SPEECH_AUTH` | `aad` for keyless Speech; if omitted, inferred from resource ID/key presence |
| `SPEECH_RESOURCE_ID` | Full ARM ID of the Speech-capable account; AAD needs a custom subdomain and **Cognitive Services Speech User** |
| `SPEECH_KEY` | Legacy key-based alternative. Not required with explicit AAD |
| `SPEECH_REGION`, `SPEECH_LANGUAGE` | `eastus`, `en-US`; region must match the selected Speech account |
| `LIVE_NOTES` | `on`; enables post-transfer Speech transcription. Missing Speech configuration degrades with warnings; verify `/v1/config` and a real call |
| `TRANSFER_TEAMS_OBJECT_IDS` | Comma-separated rep Entra IDs; empty means no rep transfer |
| `CORS_ORIGINS` | Comma-separated exact browser origins; include the separately hosted Intake UI |
| `BRAND_NAME` | `Intake`; prompt/title branding |
| `ENRICHMENT` | `off`; enable only after configuring the note-taker and Foundry IQ KB |
| `ENRICHMENT_INTERVAL_S` | `60`; extraction interval setting, not a durability guarantee |
| `RINGBACK`, `VOICE_SESSION_TUNING` | `on`, `off`; voice session tuning is optional/experimental |
| `AFTER_HOURS` | `off`, `on` or `auto` |
| `SERVICE_DAYS`, `SERVICE_HOURS`, `SERVICE_TZ` | `Mon-Fri`, `07:00-18:00`, `America/Chicago`; used by automatic after-hours mode |
| `MAX_CONCURRENT_CALLS`, `MAX_CALL_DURATION_SECONDS`, `IDLE_TIMEOUT_SECONDS` | `50`, `3600`, `120`; zero disables a limit. Lifecycle/admission enforcement still has open backlog work |
| `DEV_SIMULATOR` | `off`; `on` is allowed only with `LOCAL_DEV=1` |
| `EXTRACTION_SCHEMA` / `EXTRACTION_SCHEMA_FILE` | Optional JSON Schema for output `fields`; only local references allowed |
| `WEBHOOK_URLS`, `WEBHOOK_SECRET`, `FUNCTIONS` / `FUNCTIONS_FILE` | Consumer notifications and function integration; see gateway README |

Boolean environment switches use `on`/`off`, except `LOCAL_DEV` uses `0`/`1`. Malformed settings fail early rather than
silently taking defaults. The gateway's validation does not create role assignments or Dataverse application users.

## One Dataverse configuration

`dataverse\config.json` names the customer/contact/equipment/ticket/draft roles and optional mapping overrides.
The gateway derives metadata where supported; explicit overrides win. The journal is internal storage, **not an
agent business-table role**, and its integrity columns cannot be disabled.

PowerShell reads the file at deployment (`dataverseSchema` in private parameters overrides it).
azd/Bicep loads it when compiling the template. Both send `DATAVERSE_SCHEMA` to the gateway; it is not baked into
the image. Local runs can set `DATAVERSE_SCHEMA_FILE=..\..\dataverse\config.json` from `src\intake-gateway`.
Inline `DATAVERSE_SCHEMA` also accepts `b64:<base64 JSON>` and takes precedence over the file.

After a table change, review/provision the compatible schema, redeploy gateway configuration and republish the agents.
See [table configuration](18-table-configuration.md). Configurability does not mean the provisioner can automatically
migrate every existing customer table.

## Deployment and service configuration

| Location | Role |
|---|---|
| `infra\gateway.parameters.example.json` | PowerShell parameters; copy to a private per-environment JSON |
| `azure.yaml`, `infra\main.bicep`, `infra\main.parameters.json` | azd service, infrastructure and environment-to-parameter mappings |
| `foundry\setup-agents.ps1` | Publishes agents/toolbox and the managed-identity Dataverse connection |
| Power Platform admin center | Separate application users for gateway and Foundry project, appropriate roles and allowed MCP clients |
| Teams setup | Resource account, number binding, consent and rep voice enablement |
| Event Grid | Deliver only the intended `IncomingCall` events to the gateway, with matching `EVENTGRID_SECRET` |

Follow the [deployment guide](03-deployment.md) for ordering. Rotating Event Grid or HMAC secrets requires coordinated
consumer/callback changes; old in-flight callback URLs do not automatically acquire new tokens.
