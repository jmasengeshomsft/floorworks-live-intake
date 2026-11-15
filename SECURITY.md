# Security

This is a demo/starter project, not production-hardened.

- Never commit secrets. Use `azd env`, Key Vault or Container Apps secrets; `.env` files are git-ignored.
- `API_KEYS` protect the `/v1` API and SSE. The React UI stores its key in localStorage; production needs per-user authorization.
- `EVENTGRID_SECRET`, per-call HMAC tokens and ACS JWT validation protect infrastructure endpoints. Never enable `LOCAL_DEV` on a public deployment.
- The app uses managed identity for Foundry, Dataverse and Azure AI Search. Grant least privilege.
- Synthetic data only: the seed data and Foundry IQ corpus are fictional.
- Report vulnerabilities privately to the repository owner (GitHub Security Advisories).

See [security and compliance](docs/09-security.md) for permissions, journal/transcript retention and remaining hardening.
