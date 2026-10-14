export interface Settings {
  baseUrl: string
  apiKey: string
}

export interface CallSummary {
  id: string
  callerPhone: string
  status: 'ringing' | 'active' | 'ended'
  afterHours: boolean
  repJoined: boolean
  startedAt: number
  endedAt: number | null
}

export interface TranscriptLine {
  speaker: string
  text: string
  ts: number
}

export interface GatewayEvent {
  id: number
  type: string
  callId: string
  ts: number
  data: any
}

export type Fields = Record<string, unknown>

export interface Enrichment {
  [key: string]: unknown
}

export interface CallDetail extends CallSummary {
  fields: Fields
  enrichment: Enrichment | null
}

export interface GatewayConfig {
  voiceAgent: string
  extractorAgent: string
  enrichment: boolean
  liveNotes: string
  afterHoursMode: string
  afterHoursNow: boolean
  transferTargets: number
  webhooks: number
  simulator?: boolean
  configWarnings: string[]
  schema: Record<string, unknown>
}

export interface Stats {
  active: number
  maxConcurrent: number
  maxDurationSeconds: number
  idleTimeoutSeconds: number
  tracked: number
  [key: string]: unknown
}

const norm = (u: string) => u.replace(/\/+$/, '')

export class GatewayClient {
  private s: Settings
  constructor(s: Settings) {
    this.s = s
  }

  private async req<T>(path: string, init: RequestInit = {}): Promise<T> {
    const res = await fetch(norm(this.s.baseUrl) + path, {
      ...init,
      headers: {
        'Content-Type': 'application/json',
        ...(this.s.apiKey ? { 'x-api-key': this.s.apiKey } : {}),
        ...init.headers,
      },
    })
    if (!res.ok) {
      let detail = ''
      try {
        detail = (await res.json()).detail ?? ''
      } catch {
        /* ignore */
      }
      throw new Error(`${res.status} ${detail || res.statusText}`)
    }
    return res.json() as Promise<T>
  }

  health = () => this.req<{ status: string; errors: string[]; activeCalls: number }>('/healthz')
  config = () => this.req<GatewayConfig>('/v1/config')
  stats = () => this.req<Stats>('/v1/stats')
  calls = () => this.req<CallSummary[]>('/v1/calls')
  call = (id: string) => this.req<CallDetail>(`/v1/calls/${id}`)
  transcript = (id: string) => this.req<TranscriptLine[]>(`/v1/calls/${id}/transcript`)
  transfer = (id: string) => this.req<unknown>(`/v1/calls/${id}/transfer`, { method: 'POST' })
  hangup = (id: string) => this.req<unknown>(`/v1/calls/${id}/hangup`, { method: 'POST' })
  simulate = () => this.req<CallSummary>('/v1/dev/simulate', { method: 'POST' })
  functionResult = (id: string, fnId: string, body: unknown) =>
    this.req<unknown>(`/v1/calls/${id}/functions/${fnId}/result`, {
      method: 'POST',
      body: JSON.stringify(body),
    })

  // EventSource cannot set headers, so the gateway accepts ?key=
  eventsUrl(id: string) {
    const k = this.s.apiKey ? `?key=${encodeURIComponent(this.s.apiKey)}` : ''
    return `${norm(this.s.baseUrl)}/v1/calls/${id}/events${k}`
  }
}

export const EVENT_TYPES = [
  'call.incoming',
  'call.connected',
  'call.ended',
  'transcript',
  'extraction.updated',
  'extraction.final',
  'enrichment.updated',
  'function.called',
  'transfer.started',
  'transfer.succeeded',
  'transfer.failed',
  'error',
]
