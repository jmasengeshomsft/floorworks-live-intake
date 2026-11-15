# Intake UI

React (Vite + TypeScript) client for the Intake Gateway `/v1` API: calls, live transcript, extracted fields, AI-suggested enrichment, function calls, event log, transfer/hang-up.

## Run

```
npm install
npm run dev      # http://localhost:5173
npm run build
```

Set the gateway URL and API key in the top bar (stored in localStorage).

## Draft relationships

Equipment and contact links can be cleared explicitly. Customer search stays available for linked drafts;
check "Clear equipment and contact links with the next customer change" to submit those clears and the new
customer atomically. Otherwise existing links are preserved and the gateway rejects incompatible selections.
Creating a customer requires clearing existing equipment/contact links first. Submitted drafts remain read-only.

The browser regression uses mocked API responses only. After `npm run build`, serve the built UI on
`http://127.0.0.1:15173` (`npm run preview -- --host 127.0.0.1 --port 15173`), then run:

```powershell
playwright-cli -s=intake-ui-test open
playwright-cli -s=intake-ui-test run-code --filename=tests\relationships.playwright.js
playwright-cli -s=intake-ui-test run-code --filename=tests\resilience.playwright.js
playwright-cli -s=intake-ui-test close
```

## Gateway settings

Extraction failures retain the previous valid result and show an explicit retry action. SSE replay gaps reload
the call/transcript snapshots and reconnect with the server cursor. Completed, failed or cancelled functions
cannot accept more results; snapshot resync replaces stale pending-function state.

- `CORS_ORIGINS=http://localhost:5173` (or the host serving this UI).
- `DEV_SIMULATOR=on` adds a "Simulate call" button that plays a fake call, no phone needed.
- SSE uses `?key=` because browser `EventSource` cannot send headers.
