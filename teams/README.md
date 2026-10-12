# teams — phone number and rep enablement

`setup-teams.ps1` prepares the Teams side of telephony. It uses the `MicrosoftTeams` module and prompts for a device-code
sign-in as a Teams administrator.

It handles:
1. the **resource account** that owns the business phone number,
2. assigning your phone number and the *Teams Phone Resource Account* license,
3. binding the resource account to the ACS identity / bot app (run `infra/setup-tpe-azure.ps1` first),
4. enabling reps for **Enterprise Voice** so the AI can add them to a call.

Inputs come from `.env.example`: `TenantId`, `BotAppId`, `AcsImmutableId`, `AcsHost`, `ResourceAccountUpn`, `PhoneNumber`, `RepObjectIds`.

## Rep checklist

- Teams Phone license and Enterprise Voice enabled
- Signed in to Teams and **Available** (not Do Not Disturb) during calls
- Entra object id listed in the app's `REP_TEAMS_OBJECT_IDS`

Failure to add a rep shows as `AddParticipant FAILED` with 480 or 603; see [troubleshooting](../docs/08-operations.md).

> Routing the number to the resource account bypasses any Auto Attendant greeting; the AI receptionist replaces it.
