# Application code

| Project | Purpose |
|---|---|
| [`intake-gateway`](intake-gateway/README.md) | Python / FastAPI: call handling, agents, Speech, Dataverse drafts/journal and REST/SSE |
| [`intake-ui`](intake-ui/README.md) | React / TypeScript: live transcript, draft review, relationship selection and Submit |

The C# gateway has been removed. The root solution now contains only the optional
[Foundry IQ .NET CLI](../foundry/README.md), which builds and queries the knowledge base.

## Local development

From `src\intake-gateway`, follow its README to create a virtual environment, install dependencies and configure
`.env`. Use `LOCAL_DEV=1` only for local development. Azure callbacks require a publicly reachable HTTPS/WSS endpoint.

From `src\intake-ui`, run `npm ci` and `npm run dev`. Configure the gateway URL/API key in the UI and allow
`http://localhost:5173` in the gateway's `CORS_ORIGINS`.

## Contributor rules

1. **Agents never create tickets.** `DraftStore.submit`, reached through `POST /v1/drafts/{id}/submit`, owns ticket creation.
2. **Rep edits win**, including summaries and explicit clears. Preserve the edited-field ownership checks and ETag handling.
3. Keep extraction contracts, Dataverse mappings, API models and UI fields consistent. See [extending the model](../docs/05-data-model.md#extending-the-model).
4. Keep secrets in environment/deployment configuration, not code. Validate configuration and surface failures explicitly.
5. Keep live state process-local only within the documented **single-process, single-replica, no-overlap** deployment constraint.
6. Preserve Dataverse checkpoint/version semantics, atomic Submit and idempotent retry behavior.

## Checks

```powershell
# From src\intake-gateway, after installing requirements-dev.txt:
.\.venv\Scripts\python.exe -m pytest -q

# From src\intake-ui:
npm run build
npm run lint
```

The UI README documents the mocked browser regressions. Offline infrastructure checks are documented in
[infra](../infra/README.md) and [Dataverse](../dataverse/README.md).
