# 08 · Operations & troubleshooting

## Where to look

| Need | Tool |
|---|---|
| Is the app up? | `GET /healthz` |
| Recent app logs | `az containerapp logs show -g <rg> -n floorworks-voice-intake --tail 100` (max 300, noisy) |
| Full call history | Log Analytics table `ContainerAppConsoleLogs_CL` |
| What the draft holds | Dataverse table `bdm_servicerequestdraft` or `GET /api/rep/draft/{callId}` |
| Active revision | `az containerapp revision list -g <rg> -n floorworks-voice-intake -o table` |

### Log Analytics query for one call

```powershell
$ws = az containerapp env show -g <rg> -n <aca-env> --query properties.appLogsConfiguration.logAnalyticsConfiguration.customerId -o tsv
az monitor log-analytics query -w $ws --analytics-query "
ContainerAppConsoleLogs_CL
| where ContainerAppName_s == 'floorworks-voice-intake' and Log_s has '<callId>'
| project t=TimeGenerated, msg=substring(Log_s, 0, 300)
| order by t asc" -o table
```

KQL notes: use `substring()` (not `substr`); format time with `.ToString('HH:mm:ss')`.

### Log lines to know

| Line | Meaning |
|---|---|
| `[id] ANSWER OK caller=... conn=...` | Call answered; caller number captured |
| `[id] WS connected; rep UI: ...` | Media socket opened (can appear again on reconnect) |
| `[id] voice agent connected (name)` | Receptionist session is live |
| `[id] STT <role>: text` | Recognized speech and who said it |
| `[id] transfer_to_rep requested` | Receptionist asked for a rep |
| `[id] rep joined; AI muted` | Transfer worked |
| `[id] AddParticipant FAILED ...` | Rep did not join; AI resumes |
| `[id] draft save error` | Dataverse write failed |
| `[id] MediaStreaming FAILED` | ACS could not stream audio |
| `STT canceled` | Speech key, region or network problem |

## Call triage playbook

Work from the symptom to the cause.

| Symptom | Likely cause | Fix |
|---|---|---|
| Rings but nobody answers; no `ANSWER OK` | Event Grid not delivering, or app returned an error | Check the Event Grid subscription and `/api/incoming` logs; confirm the filter is not excluding the call |
| Call rejected by Teams, SIP 480 / code 10140 | Bot has no Teams calling channel | Re-run `infra/setup-tpe-azure.ps1`; check the bot channel |
| Answers, silence, no AI | `voice agent connect FAILED` | Check Foundry endpoint, app identity role on Foundry, preview header |
| AI hears nothing / no transcript | `STT canceled` or media streaming failed | Check `SPEECH_KEY`/`SPEECH_REGION`; confirm `PUBLIC_BASE_URL` is `https` and reachable as `wss` |
| Caller hangs up after a few seconds | Greeting too long or slow | Check latency; shorten the instructions' greeting |
| AI says it cannot look up equipment | Toolbox connection failed (`CONSENT_REQUIRED`) | Use the managed-identity connection and republish the toolbox and voice agent |
| Unexpected transfer | Voice agent instructions allow it | Confirm "only on explicit request" wording in the current agent version |
| `AddParticipant FAILED` 480 *Temporarily Unavailable* | Rep offline, in Do Not Disturb, or not Enterprise Voice enabled | Sign the rep into Teams, set Available, verify voice enablement |
| `AddParticipant FAILED` 603 *Decline* | Rep declined or did not answer | Retry; consider multiple reps in `REP_TEAMS_OBJECT_IDS` |
| AI greets again mid-call | ACS reconnected the media socket and the voice agent restarted | Known limit; see [roadmap](10-roadmap.md) |
| Draft has no customer or equipment | Caller phone not in `bdm_customer`; equipment not matched | Seed the customer; rep picks links in the console |
| Equipment mis-transcribed ("CNC Mail") | 24 kHz to 16 kHz resample and no phrase list | Add a Speech phrase list with equipment names |
| Draft empty after a short call | Caller hung up before speaking | Normal; the draft is created from the first transcript or at finalize |
| `/rep` returns 401 | Missing or wrong key | Pass `?k=<REP_KEY>` |
| Ticket not created after Submit | Customer or equipment not linked, draft already submitted, or Dataverse error | Link both in the console; read the `400` error text; check the app identity's table privileges |
| Dataverse 403 from the app | App identity lacks a role or application user is missing | Create the application user and assign a role |

## Routine tasks

| Task | How |
|---|---|
| Rotate the rep key | `az containerapp secret set ... rep-key=<new>` then restart the revision |
| Rotate the ACS or Speech key | Update the Container Apps secret, restart the revision |
| Add a rep | Add their Entra object id to `REP_TEAMS_OBJECT_IDS`; enable Enterprise Voice; restart |
| Roll back | `az containerapp ingress traffic set` to the previous revision, or redeploy the older image tag |
| Update the AI behavior | Publish a new agent version ([AI agents](06-agents.md)); no app redeploy needed |
| Clean up test data | Delete test rows in `bdm_servicerequestdraft` and `bdm_serviceticket` |

## Operational limits to remember

- **One replica.** Call state, SSE and sockets live in memory. A restart mid-call loses live state (the persisted draft survives).
- **Deploying during a call drops the call.** Do not deploy while testing.
- **Log retention** follows the Log Analytics workspace settings; transcripts in logs may contain personal data.

## Health checklist before a demo or go-live

- [ ] `/healthz` returns `ok` and the latest revision is the one you expect
- [ ] Rep signed in to Teams, status Available
- [ ] Rep Console open and shows the recent drafts
- [ ] Test call: greeting within about a second, equipment lookup works, draft appears
- [ ] No unexpected rows in `bdm_serviceticket`
