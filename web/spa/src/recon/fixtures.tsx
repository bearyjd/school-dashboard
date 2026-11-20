import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import type { ReactElement } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { vi } from 'vitest'
import { Where } from './Where'
import type { Counts, Envelope, ReconConfig, ReconItem, ReconLink, ReconSummary } from './types'

/** Wednesday 2026-11-18, 10:00 in New York. */
export const NOW = new Date('2026-11-18T15:00:00Z')
export const TOKEN = 'T'.repeat(40)
export const ACTOR_KEY = 'school-actor'
export const TOKEN_KEY = 'school-recon-token'

export function link(over: Partial<ReconLink> = {}): ReconLink {
  return { id: 1, source: 'schoology', status: 'assigned', raw_status: null, score: null, live: true,
    url: 'https://portal.example.org/a/1', ...over }
}

export function ritem(over: Partial<ReconItem> & Pick<ReconItem, 'id'>): ReconItem {
  return {
    kid: 'Alpha', title: 'Essay draft', class: 'English', due: '2026-11-20', kind: 'assignment', stage: 1,
    stage_label: 'Assigned', flag: 'do_tonight', bucket: 'do_tonight', info: [], unverified: false,
    unverified_sources: [], portal_status: null, conflict: false, received_evidence: null,
    possible_duplicate: false, links: [link()], ...over,
  }
}

export const CONFIG: ReconConfig = {
  enabled: true, migrated: true, grace_default: 2, write_auth: 'token', write_token_header: 'X-Recon-Token',
  write_token_min_length: 32,
  kids: [{ name: 'Alpha', color: 'blue' }, { name: 'Beta', color: 'rose' }, { name: 'Gamma', color: 'teal' }],
}

export function countsOf(items: ReconItem[]): Counts {
  const n = (f: string) => items.filter(i => i.kind !== 'practice' && i.flag === f).length
  const detail = { done_not_received: n('done_not_received'), disputed: n('disputed'), missing: n('missing') }
  return { do_tonight: n('do_tonight'), waiting_on_teacher: n('waiting_on_teacher'),
    needs_action: detail.done_not_received + detail.disputed + detail.missing, needs_action_detail: detail }
}

export function summaryOf(items: ReconItem[], over: Partial<ReconSummary> = {}): ReconSummary {
  const base: ReconSummary = {
    kids: CONFIG.kids.map(k => {
      const mine = items.filter(i => i.kid === k.name)
      const rest = mine.filter(i => i.kind !== 'practice')
      const practice = mine.filter(i => i.kind === 'practice')
      const counts = countsOf(rest)
      const clear = counts.needs_action === 0 && counts.do_tonight === 0 && !practice.some(p => p.bucket === 'do_tonight')
      return { name: k.name, counts, practice, all_clear: clear, all_clear_reason: null }
    }),
    data_as_of: '2026-11-18T09:30:00-05:00', ingest_ok: true, unverified_sources: [], warnings: [],
    generated_at: '2026-11-18T10:00:00-05:00',
  }
  return { ...base, ...over }
}

/** One item for every bucket and sub-label, for Alpha and Beta; Gamma stays all clear. */
export function sampleItems(): ReconItem[] {
  return [
    ritem({ id: 1, title: 'Essay draft' }),
    ritem({ id: 2, title: 'Lab sheet', stage: 2, stage_label: 'Done at home', flag: 'waiting_on_teacher', bucket: 'waiting_on_teacher' }),
    ritem({ id: 3, title: 'Poster', stage: 3, stage_label: 'Turned in', flag: 'disputed', bucket: 'needs_action', portal_status: 'missing' }),
    ritem({ id: 4, title: 'Reading log', flag: 'missing', bucket: 'needs_action', due: '2026-11-10' }),
    ritem({ id: 5, title: 'Map quiz', stage: 2, stage_label: 'Done at home', flag: 'done_not_received', bucket: 'needs_action' }),
    ritem({ id: 6, kid: 'Beta', title: 'Fractions', class: 'Math', flag: 'do_tonight', bucket: 'do_tonight', due: null }),
    ritem({ id: 7, kid: 'Beta', title: 'Pack gym bag', kind: 'task', class: null, links: [] }),
    ritem({ id: 8, kid: 'Beta', title: 'Math', class: 'Math', kind: 'practice', links: [link({ source: 'ixl', raw_status: '4 remaining', url: null })], flag: 'do_tonight', bucket: 'do_tonight' }),
    ritem({ id: 9, kid: 'Beta', title: 'ELA', class: 'ELA', kind: 'practice', links: [link({ id: 2, source: 'ixl', raw_status: '2 remaining', url: null })], flag: 'do_tonight', bucket: 'do_tonight' }),
    ritem({ id: 10, kid: 'Beta', title: 'Later project', flag: 'upcoming', bucket: null, due: '2026-12-15' }),
  ]
}

export interface Reply {
  status?: number
  body?: unknown
  headers?: Record<string, string>
  /** Make fetch itself fail (offline). */
  reject?: boolean
}

export interface Req {
  method: string
  path: string
  url: string
  headers: Record<string, string>
  body: Record<string, unknown> | null
}

type Handler = (req: Req) => Reply | undefined | Promise<Reply | undefined>

export const ok = <T,>(data: T, status = 200): Reply => ({ status, body: { ok: true, data, error: null, meta: {} } as Envelope<T> })
export const fail = (error: string, status: number, headers?: Record<string, string>): Reply =>
  ({ status, headers, body: { ok: false, data: null, error, meta: {} } })

export interface ServerOpts {
  items?: ReconItem[]
  config?: ReconConfig | Reply
  summary?: Partial<ReconSummary>
}

/** A fake /api/v2 backend. `on('POST /api/v2/assignments/1/stage', handler)` overrides a route; `requests` records every call. */
export function fakeServer(opts: ServerOpts = {}) {
  const items = opts.items ?? sampleItems()
  const overrides = new Map<string, Handler>()
  const requests: Req[] = []
  const defaults = (req: Req): Reply => {
    if (req.method === 'GET' && req.path === '/api/v2/config') return opts.config && 'enabled' in opts.config ? ok(opts.config) : (opts.config as Reply | undefined) ?? ok(CONFIG)
    if (req.method === 'GET' && req.path === '/api/v2/summary') return ok(summaryOf(items, opts.summary))
    if (req.method === 'GET' && req.path === '/api/v2/assignments') {
      const open = items.filter(i => i.stage !== 4)
      return { status: 200, body: { ok: true, data: open, error: null, meta: { count: open.length, total: open.length, limit: 500, offset: 0 } } }
    }
    const detail = /^\/api\/v2\/assignments\/(\d+)$/.exec(req.path)
    if (req.method === 'GET' && detail) {
      const found = items.find(i => i.id === Number(detail[1]))
      return found ? ok({ ...found, events: [], notes: [] }) : fail('not_found', 404)
    }
    if (req.path.startsWith('/api/overview')) return fail('not_found', 404)
    return fail('not_found', 404)
  }
  const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
    const url = String(input)
    const path = url.split('?')[0]
    const method = (init?.method ?? 'GET').toUpperCase()
    const raw = init?.body
    const req: Req = { method, path, url, headers: { ...(init?.headers as Record<string, string> | undefined) },
      body: typeof raw === 'string' ? (JSON.parse(raw) as Record<string, unknown>) : null }
    requests.push(req)
    const key = `${method} ${path}`
    const handler = overrides.get(key)
    const reply = (handler ? await handler(req) : undefined) ?? defaults(req)
    if (reply.reject) throw new TypeError('Failed to fetch')
    const status = reply.status ?? 200
    return {
      ok: status >= 200 && status < 300, status, statusText: 'err',
      headers: { get: (n: string) => reply.headers?.[n] ?? null },
      json: async () => reply.body,
    }
  })
  return {
    fetchMock, requests,
    on(key: string, handler: Handler) { overrides.set(key, handler) },
    writes: () => requests.filter(r => r.method !== 'GET'),
  }
}

export function renderRecon(ui: ReactElement, route: string, server = fakeServer()) {
  vi.stubGlobal('fetch', server.fetchMock)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const utils = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>{ui}<Where /></MemoryRouter>
    </QueryClientProvider>,
  )
  return { ...utils, client, server }
}

export function signIn(name = 'Dana', role: 'parent' | 'kid' = 'parent') {
  localStorage.setItem(ACTOR_KEY, JSON.stringify({ name, role }))
  localStorage.setItem(TOKEN_KEY, TOKEN)
}
