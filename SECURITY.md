# Security

This is a demo/starter project, not production-hardened.

- Never commit secrets. Use `azd env`, Key Vault or Container Apps secrets; `.env` files are git-ignored.
- `REP_KEY` is a shared secret for the rep console. For production, replace it with Entra ID sign-in.
- The app uses managed identity for Foundry, Dataverse and Azure AI Search. Grant least privilege.
- Synthetic data only: the seed data and Foundry IQ corpus are fictional.
- Report vulnerabilities privately to the repository owner (GitHub Security Advisories).
