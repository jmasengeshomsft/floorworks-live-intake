# 07 · Rep console

A single-page web app served by VoiceIntake at `/rep`. It is the reps' view of a call and the only place a ticket is created.

## Opening it

```
https://<fqdn>/rep?k=<REP_KEY>                 # pick a call from the list
https://<fqdn>/rep?k=<REP_KEY>&call=<callId>   # open a specific call
https://<fqdn>/rep?k=<REP_KEY>&rep=<name>      # also records who submitted
```

- Open it **before** a call starts. The call list refreshes every 15 s and does not jump to a new call on its own; the open
  draft refreshes every 2 s.
- The call id appears in the app log line `WS connected; rep UI: <url>/rep?call=<id>`.
- If `REP_KEY` is empty the console is open to anyone who has the URL. Always set it.

## Screen layout

| Area | What it shows |
|---|---|
| **Call picker** | Live calls and recent drafts |
| **Transcript** | Labeled lines: customer, AI assistant, rep, streamed live |
| **Coaching** | The note-taker's suggested next question and what is missing |
| **Service Intake Form** | Fixed set of fields, filled live (see below) |
| **Customer card** | Linked customer, or suggested matches and a search box |
| **Equipment card** | Equipment for the customer; click a row to link |
| **Submit** | Creates the Service Ticket |

### Service Intake Form fields

| Field | Draft column | Type |
|---|---|---|
| Customer name | `bdm_customername` | Text |
| Contact phone | `bdm_contactphone` | Text (defaults to caller phone) |
| Equipment | `bdm_equipmenttext` | Text |
| Asset number | `bdm_assetnumber` | Text |
| Location | `bdm_location` | Text |
| Preferred time | `bdm_preferredtime` | Text |
| Complaint | `bdm_complaint` | Multiline |
| Priority | `bdm_priority` | Low / Normal / High / Urgent |
| Summary | `bdm_summary` | Multiline |

A line under the form shows whether a customer and equipment are linked. The form is a fixed shape on purpose: it keeps the
note-taker's output schema, the Dataverse columns and the UI in lockstep.

## How editing behaves

- A field you are typing in, or have changed and not yet saved, is **never overwritten** by a refresh.
- Edits save automatically after 700 ms of inactivity.
- Every saved edit adds the field name to `bdm_editedfields`; from then on agents will not overwrite that field.
- Fields highlight briefly when an agent changes them.
- After **Submit**, every field and the button are disabled and the line reads *Submitted by &lt;name&gt;*.

## Submit

1. Unsaved edits are flushed.
2. The app requires **both a linked customer and linked equipment**; otherwise Submit fails with *choose a customer and
   equipment first*.
3. It creates one `bdm_serviceticket` titled `FloorWorks-Voice-<account number>-<yyyyMMddHHmm>`, status Open, with the draft's
   priority and the linked customer and equipment. The description holds Call ID, Customer, Contact phone, Location,
   Preferred time, the complaint and the call summary.
4. The draft is set to *Submitted*, stamped with `submittedby` and `submittedon`, and linked via `bdm_ticketid`.
5. A second submit on the same draft is rejected (`draft already submitted`).

## API reference

All routes require the rep key as `?k=<key>` or header `x-rep-key`. Responses are JSON.

| Method & route | Body | Result |
|---|---|---|
| `GET /api/rep/calls` | — | `{ live: [callId...], recent: [draft...] }` |
| `GET /api/rep/draft/{callId}` | — | `{ callId, live, draft, customer, candidates, transcript }` |
| `POST /api/rep/draft/{callId}` | Any of `customername, contactphone, equipmenttext, assetnumber, location, preferredtime, complaint, summary, priority, customerId, equipmentId` | `200` or `400 { error }` |
| `POST /api/rep/draft/{callId}/submit` | `{ "submittedBy": "name" }` | `{ ticket: "<ticket title>" }` or `400 { error }` |
| `GET /api/rep/customers?q=` | — | Customers matching name, account number or phone |
| `GET /api/rep/equipment?customerId=` | — | Equipment, optionally for one customer |

`GET /events/{callId}` is an unauthenticated server-sent-event stream of `partial`, `final` (speaker + text) and `error` /
`end` events for custom UIs. Do not expose it publicly in production ([Security](09-security.md)).

## Building the same experience in Power Apps

The console is a thin client over Dataverse. A Power Apps canvas or model-driven app can replace it with no backend change:

1. Add `bdm_servicerequestdraft` as a data source; filter `bdm_status = Draft`, sort by `createdon` descending.
2. Bind form controls to the nine columns above; poll or use a timer control for live refresh.
3. Respect the rule: when a rep edits a field, append its name to `bdm_editedfields`.
4. On Submit, either call a Power Automate flow that mirrors the Submit logic in `Drafts.cs`, or call the app's
   `/api/rep/draft/{callId}/submit` endpoint through a custom connector.
5. Show the transcript from `bdm_transcript`.

Canvas apps must be authored in Power Apps Studio; they cannot be generated from this repo.
