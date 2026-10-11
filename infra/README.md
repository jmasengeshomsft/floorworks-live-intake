# infra — deployment and telephony plumbing

| File | Purpose | Status |
|---|---|---|
| `deploy.ps1` | Builds and deploys the **legacy** `StreamProcessor` and `CallController` to Container Apps | Rewrite not run as-is |
| `setup-tpe-azure.ps1` | Azure side of Teams Phone Extensibility: Entra bot app, Azure Bot with Teams calling channel, ACS service principal, Event Grid subscription | Rewrite not run as-is |
| `backup-foundry-eventsub.json`, `backup-tpe-eventsub.json` | Saved Event Grid subscription definitions from the proof of concept, for restore or reference | Reference |

The current product, **VoiceIntake**, is deployed with the commands in [docs/03-deployment.md](../docs/03-deployment.md#5--build-and-deploy-voiceintake);
there is no script for it yet (roadmap item: infrastructure as code).

## Notes

- Create the Event Grid subscription only **after** the app is running: Event Grid validates the endpoint on creation.
- If several apps share one ACS resource, filter `IncomingCall` events by `data.to.rawId` so each subscription only receives its own number.
- Keep your `.env` (copied from `.env.example`) out of source control.
