import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { GatewayClient, type CallSummary, type GatewayConfig, type Settings, type Stats } from './api'
import { useCall, type CallView } from './hooks'
import './App.css'

const STORE = 'intake-ui.settings'
const loadSettings = (): Settings => {
  try {
    const raw = localStorage.getItem(STORE)
    if (raw) return JSON.parse(raw)
  } catch {
    /* ignore */
  }
  return { baseUrl: import.meta.env.VITE_GATEWAY_URL ?? 'http://localhost:8080', apiKey: '' }
}

const label = (k: string) => k.replace(/([A-Z])/g, ' $1').replace(/[_-]+/g, ' ').replace(/^./, (c) => c.toUpperCase())
const fmtTime = (t: number) => new Date(t * 1000).toLocaleTimeString()

function Value({ v }: { v: unknown }) {
  if (v === null || v === undefined || v === '') return <span className="muted">—</span>
  if (Array.isArray(v)) {
    if (v.every((x) => typeof x !== 'object' || x === null))
      return (
        <ol className="list">
          {v.map((x, i) => (
            <li key={i}>{String(x)}</li>
          ))}
        </ol>
      )
    return (
      <ul className="list">
        {v.map((x, i) => (
          <li key={i}>
            <Value v={x} />
          </li>
        ))}
      </ul>
    )
  }
  if (typeof v === 'object')
    return (
      <span className="inline-obj">
        {Object.entries(v as Record<string, unknown>).map(([k, x]) => (
          <span key={k} className="chip">
            {label(k)}: {typeof x === 'object' ? JSON.stringify(x) : String(x)}
          </span>
        ))}
      </span>
    )
  return <>{String(v)}</>
}

function ConnectionBar({
  settings,
  onSave,
  online,
}: {
  settings: Settings
  onSave: (s: Settings) => void
  online: boolean | null
}) {
  const [draft, setDraft] = useState(settings)
  return (
    <form
      className="conn"
      onSubmit={(e) => {
        e.preventDefault()
        onSave(draft)
      }}
    >
      <span className={`dot ${online === null ? '' : online ? 'ok' : 'bad'}`} title={online ? 'connected' : 'offline'} />
      <input
        value={draft.baseUrl}
        onChange={(e) => setDraft({ ...draft, baseUrl: e.target.value })}
        placeholder="Gateway URL"
        aria-label="Gateway URL"
      />
      <input
        value={draft.apiKey}
        onChange={(e) => setDraft({ ...draft, apiKey: e.target.value })}
        placeholder="API key"
        type="password"
        aria-label="API key"
      />
      <button type="submit">Connect</button>
    </form>
  )
}

function Header({ config, stats }: { config: GatewayConfig | null; stats: Stats | null }) {
  if (!config) return null
  return (
    <div className="chips">
      <span className="chip">Voice: {config.voiceAgent}</span>
      <span className="chip">Extractor: {config.extractorAgent}</span>
      <span className="chip">Live notes: {config.liveNotes}</span>
      <span className="chip">Enrichment: {config.enrichment ? 'on' : 'off'}</span>
      {config.afterHoursNow && <span className="chip warn">After hours</span>}
      {stats && (
        <span className="chip">
          Active {stats.active}/{stats.maxConcurrent}
        </span>
      )}
      {config.configWarnings.map((w) => (
        <span key={w} className="chip warn" title={w}>
          ⚠ {w.length > 48 ? w.slice(0, 48) + '…' : w}
        </span>
      ))}
    </div>
  )
}

function CallList({
  calls,
  selected,
  onSelect,
  onSimulate,
}: {
  calls: CallSummary[]
  selected: string | null
  onSelect: (id: string) => void
  onSimulate: (() => void) | null
}) {
  return (
    <aside className="list-pane">
      <div className="pane-head">
        <h2>Calls</h2>
        {onSimulate && <button onClick={onSimulate}>Simulate call</button>}
      </div>
      {calls.length === 0 && <p className="muted pad">No calls yet. Waiting for the phone line…</p>}
      {calls.map((c) => (
        <button key={c.id} className={`call-row ${selected === c.id ? 'sel' : ''}`} onClick={() => onSelect(c.id)}>
          <span className={`status ${c.status}`}>{c.status}</span>
          <strong>{c.callerPhone || 'Unknown caller'}</strong>
          <span className="muted">
            {fmtTime(c.startedAt)} · {c.id}
            {c.afterHours ? ' · after hours' : ''}
          </span>
        </button>
      ))}
    </aside>
  )
}

function Transcript({ lines }: { lines: CallView['transcript'] }) {
  const end = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    end.current?.scrollIntoView({ block: 'end' })
  }, [lines.length])
  return (
    <section className="card transcript">
      <h3>Live transcript</h3>
      {lines.length === 0 && <p className="muted">Waiting for speech…</p>}
      {lines.map((l, i) => (
        <div key={i} className={`line ${l.speaker}`}>
          <span className="who">{l.speaker}</span>
          <span>{l.text}</span>
        </div>
      ))}
      <div ref={end} />
    </section>
  )
}

function FieldsCard({ fields, schema }: { fields: CallView['fields']; schema: Record<string, unknown> }) {
  const props = ((schema as { properties?: Record<string, unknown> })?.properties ?? {}) as Record<string, unknown>
  const keys = Array.from(new Set([...Object.keys(props), ...Object.keys(fields)]))
  return (
    <section className="card">
      <h3>Extracted fields</h3>
      {keys.length === 0 && <p className="muted">Nothing extracted yet.</p>}
      <dl>
        {keys.map((k) => (
          <div key={k} className="kv">
            <dt>{label(k)}</dt>
            <dd>
              <Value v={fields[k]} />
            </dd>
          </div>
        ))}
      </dl>
    </section>
  )
}

function EnrichmentCard({ data, on }: { data: CallView['enrichment']; on: boolean }) {
  if (!on && !data) return null
  return (
    <section className="card ai">
      <h3>
        <span className="badge">AI suggested</span> Enrichment
      </h3>
      {!data && <p className="muted">Enriching…</p>}
      {data && (
        <dl>
          {Object.entries(data).map(([k, v]) => (
            <div key={k} className="kv">
              <dt>{label(k)}</dt>
              <dd>
                <Value v={v} />
              </dd>
            </div>
          ))}
        </dl>
      )}
    </section>
  )
}

function FunctionsCard({
  fns,
  onResult,
}: {
  fns: CallView['functions']
  onResult: (fnId: string, body: unknown) => Promise<void>
}) {
  const [text, setText] = useState<Record<string, string>>({})
  const [msg, setMsg] = useState<Record<string, string>>({})
  if (fns.length === 0) return null
  return (
    <section className="card">
      <h3>Function calls</h3>
      {fns.map((f) => (
        <div key={f.functionCallId} className="fn">
          <strong>{f.name}</strong> <code>{JSON.stringify(f.arguments)}</code>
          <div className="row">
            <input
              placeholder='Result JSON, e.g. {"status":"ok"}'
              value={text[f.functionCallId] ?? ''}
              onChange={(e) => setText({ ...text, [f.functionCallId]: e.target.value })}
            />
            <button
              onClick={async () => {
                try {
                  await onResult(f.functionCallId, JSON.parse(text[f.functionCallId] || '{}'))
                  setMsg({ ...msg, [f.functionCallId]: 'sent' })
                } catch (e) {
                  setMsg({ ...msg, [f.functionCallId]: String(e) })
                }
              }}
            >
              Send result
            </button>
          </div>
          {msg[f.functionCallId] && <small className="muted">{msg[f.functionCallId]}</small>}
        </div>
      ))}
    </section>
  )
}

function EventLog({ events }: { events: CallView['events'] }) {
  return (
    <details className="card">
      <summary>Event log ({events.length})</summary>
      <div className="log">
        {events
          .slice()
          .reverse()
          .map((e) => (
            <div key={e.id}>
              <span className="muted">{fmtTime(e.ts)}</span> <strong>{e.type}</strong>{' '}
              <code>{e.type === 'transcript' ? e.data.text : JSON.stringify(e.data).slice(0, 160)}</code>
            </div>
          ))}
      </div>
    </details>
  )
}

function CallPanel({
  client,
  id,
  config,
  onChanged,
}: {
  client: GatewayClient
  id: string
  config: GatewayConfig | null
  onChanged: () => void
}) {
  const v = useCall(client, id)
  const [err, setErr] = useState<string | null>(null)
  const act = async (fn: () => Promise<unknown>) => {
    setErr(null)
    try {
      await fn()
      onChanged()
    } catch (e) {
      setErr(String(e))
    }
  }
  const ended = v.status === 'ended'
  return (
    <main className="detail">
      <div className="detail-head">
        <div>
          <h2>{v.detail?.callerPhone || 'Unknown caller'}</h2>
          <span className={`status ${v.status}`}>{v.status}</span>
          {v.detail?.afterHours && <span className="chip warn">After hours</span>}
          {v.detail?.repJoined && <span className="chip">Rep joined</span>}
          <span className={`dot ${v.connected ? 'ok' : 'bad'}`} title={v.connected ? 'streaming' : 'stream offline'} />
        </div>
        <div className="row">
          <button
            disabled={ended || !config?.transferTargets}
            title={config?.transferTargets ? '' : 'No transfer targets configured'}
            onClick={() => act(() => client.transfer(id))}
          >
            Transfer to rep
          </button>
          <button className="danger" disabled={ended} onClick={() => act(() => client.hangup(id))}>
            Hang up
          </button>
        </div>
      </div>
      {(err || v.error) && <div className="error">{err || v.error}</div>}
      <div className="grid">
        <Transcript lines={v.transcript} />
        <div className="stack">
          <FieldsCard fields={v.fields} schema={config?.schema ?? {}} />
          <EnrichmentCard data={v.enrichment} on={!!config?.enrichment || !!config?.simulator} />
          <FunctionsCard fns={v.functions} onResult={(fnId, body) => client.functionResult(id, fnId, body).then(() => undefined)} />
        </div>
      </div>
      <EventLog events={v.events} />
    </main>
  )
}

export default function App() {
  const [settings, setSettings] = useState<Settings>(loadSettings)
  const client = useMemo(() => (settings.baseUrl ? new GatewayClient(settings) : null), [settings])
  const [calls, setCalls] = useState<CallSummary[]>([])
  const [config, setConfig] = useState<GatewayConfig | null>(null)
  const [stats, setStats] = useState<Stats | null>(null)
  const [online, setOnline] = useState<boolean | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    if (!client) return
    try {
      const [c, s] = await Promise.all([client.calls(), client.stats()])
      setCalls(c)
      setStats(s)
      setOnline(true)
      setError(null)
    } catch (e) {
      setOnline(false)
      setError(String(e))
    }
  }, [client])

  useEffect(() => {
    setConfig(null)
    client?.config().then(setConfig).catch(() => undefined)
    refresh()
    const t = setInterval(refresh, 3000)
    return () => clearInterval(t)
  }, [client, refresh])

  const save = (s: Settings) => {
    localStorage.setItem(STORE, JSON.stringify(s))
    setSettings(s)
    setSelected(null)
  }

  const simulate = async () => {
    if (!client) return
    try {
      const c = await client.simulate()
      setSelected(c.id)
      refresh()
    } catch (e) {
      setError(String(e))
    }
  }

  return (
    <div className="app">
      <header>
        <h1>Intake Console</h1>
        <ConnectionBar settings={settings} onSave={save} online={online} />
      </header>
      <Header config={config} stats={stats} />
      {error && <div className="error">{error}</div>}
      <div className="layout">
        <CallList
          calls={calls}
          selected={selected}
          onSelect={setSelected}
          onSimulate={config?.simulator ? simulate : null}
        />
        {client && selected ? (
          <CallPanel key={selected} client={client} id={selected} config={config} onChanged={refresh} />
        ) : (
          <main className="detail empty">
            <p className="muted">Select a call to see the live transcript, extracted fields and AI suggestions.</p>
          </main>
        )}
      </div>
    </div>
  )
}
