import { useEffect, useRef, useState } from 'react'
import {
  EVENT_TYPES,
  type CallDetail,
  type Enrichment,
  type Fields,
  type GatewayClient,
  type GatewayEvent,
  type TranscriptLine,
} from './api'

export interface PendingFn {
  functionCallId: string
  name: string
  arguments: unknown
  done?: boolean
}

export interface CallView {
  detail: CallDetail | null
  status: string
  transcript: TranscriptLine[]
  fields: Fields
  enrichment: Enrichment | null
  events: GatewayEvent[]
  functions: PendingFn[]
  connected: boolean
  error: string | null
}

const empty: CallView = {
  detail: null,
  status: 'ringing',
  transcript: [],
  fields: {},
  enrichment: null,
  events: [],
  functions: [],
  connected: false,
  error: null,
}

const lineKey = (l: TranscriptLine) => `${l.ts}|${l.speaker}|${l.text}`

// Loads the call snapshot, then follows its SSE stream (which replays buffered events).
export function useCall(client: GatewayClient | null, id: string | null): CallView {
  const [view, setView] = useState<CallView>(empty)
  const seen = useRef(new Set<string>())

  useEffect(() => {
    setView(empty)
    seen.current = new Set()
    if (!client || !id) return
    let es: EventSource | null = null
    let cancelled = false

    const addLine = (prev: CallView, l: TranscriptLine): CallView => {
      const k = lineKey(l)
      if (seen.current.has(k)) return prev
      seen.current.add(k)
      return { ...prev, transcript: [...prev.transcript, l] }
    }

    ;(async () => {
      try {
        const [detail, lines] = await Promise.all([client.call(id), client.transcript(id)])
        if (cancelled) return
        setView((p) => {
          let v: CallView = {
            ...p,
            detail,
            status: detail.status,
            fields: detail.fields ?? {},
            enrichment: detail.enrichment,
          }
          for (const l of lines) v = addLine(v, l)
          return v
        })
      } catch (e) {
        if (!cancelled) setView((p) => ({ ...p, error: String(e) }))
        return
      }
      if (cancelled) return

      es = new EventSource(client.eventsUrl(id))
      es.onopen = () => setView((p) => ({ ...p, connected: true, error: null }))
      es.onerror = () => setView((p) => ({ ...p, connected: false }))
      const handle = (m: MessageEvent) => {
        const ev: GatewayEvent = JSON.parse(m.data)
        setView((p) => {
          let v: CallView = { ...p, events: [...p.events, ev].slice(-300) }
          switch (ev.type) {
            case 'call.incoming':
              v.status = 'ringing'
              break
            case 'call.connected':
              v.status = 'active'
              break
            case 'call.ended':
              v.status = 'ended'
              if (ev.data?.fields) v.fields = ev.data.fields
              if (ev.data?.enrichment) v.enrichment = ev.data.enrichment
              break
            case 'transcript':
              v = addLine(v, ev.data)
              break
            case 'extraction.updated':
            case 'extraction.final':
              v.fields = ev.data.fields ?? v.fields
              if (ev.data.enrichment) v.enrichment = ev.data.enrichment
              break
            case 'enrichment.updated':
              v.enrichment = ev.data
              break
            case 'function.called':
              v.functions = [...v.functions, ev.data]
              break
          }
          return v
        })
      }
      for (const t of EVENT_TYPES) es.addEventListener(t, handle as EventListener)
    })()

    return () => {
      cancelled = true
      es?.close()
    }
  }, [client, id])

  return view
}
