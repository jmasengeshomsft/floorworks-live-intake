# Intake UI

React (Vite + TypeScript) client for the Intake Gateway `/v1` API: calls, live transcript, extracted fields, AI-suggested enrichment, function calls, event log, transfer/hang-up.

## Run

```
npm install
npm run dev      # http://localhost:5173
npm run build
```

Set the gateway URL and API key in the top bar (stored in localStorage).

## Gateway settings

- `CORS_ORIGINS=http://localhost:5173` (or the host serving this UI).
- `DEV_SIMULATOR=on` adds a "Simulate call" button that plays a fake call, no phone needed.
- SSE uses `?key=` because browser `EventSource` cannot send headers.
