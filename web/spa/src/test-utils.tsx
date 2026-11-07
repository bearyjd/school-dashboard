/* eslint-disable react-refresh/only-export-components -- test helpers, never part of the app bundle */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import type { ReactElement } from 'react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { vi } from 'vitest'
import type { Freshness, Overview, OverviewEvent, OverviewItem } from './api/types'

/** Saturday 2026-11-07, 10:00 in New York. */
export const NOW = new Date('2026-11-07T15:00:00Z')

export function item(over: Partial<OverviewItem> & Pick<OverviewItem, 'id'>): OverviewItem {
  return {
    kid: 'Alpha', title: 'Task', course: 'Math', due: '2026-11-04', status: 'overdue', source: 'schoology',
    category: null, score: null, url: '', readonly: false, ...over,
  }
}

export function event(over: Partial<OverviewEvent> & Pick<OverviewEvent, 'title'>): OverviewEvent {
  return {
    kids: ['Alpha'], unassigned: false, date: '2026-11-08', time: '16:00', type: 'practice', location: '',
    source: 'gc', scheduleSource: 'api', team: null, ...over,
  }
}

export function fresh(over: Partial<Freshness> = {}): Freshness {
  return { last_ok: '2026-11-07T14:00:00Z', last_run: '2026-11-07T14:00:00Z', result: 'ok', issue: null,
    stale: false, failing: false, age_hours: 1, ...over }
}

export const FIXTURE: Overview = {
  generated_at: '2026-11-07T14:55:00Z',
  kids: [{ name: 'Alpha', colorIndex: 0 }, { name: 'Beta', colorIndex: 1 }],
  items: [
    item({ id: 'o1', title: 'Essay draft' }),
    item({ id: 'o2', kid: 'Beta', title: 'Poster', source: 'manual', course: '', itemId: 5 }),
    item({ id: 'm1', title: 'Quiz 3', status: 'missing', source: 'powerschool', readonly: true }),
    item({ id: 'm2', kid: 'Beta', title: 'Lab report', status: 'missing', source: 'powerschool', readonly: true }),
    item({ id: 'l1', title: 'Worksheet', status: 'late', source: 'powerschool', readonly: true }),
    item({ id: 't1', title: 'IXL: 3 remaining', status: 'tonight', source: 'ixl', due: null, course: 'IXL', readonly: true }),
    item({ id: 't2', kid: 'Beta', title: 'Read chapter', status: 'tonight', due: '2026-11-09' }),
    item({ id: 'u1', title: 'Project outline', status: 'upcoming', due: '2026-11-12' }),
  ],
  events: [
    event({ title: 'Practice' }),
    event({ title: 'Joint game', kids: ['Alpha', 'Beta'], date: '2026-11-09' }),
    event({ title: 'No school', kids: [], unassigned: true, date: '2026-11-10', time: '', source: 'school', type: 'holiday' }),
    event({ title: 'Later match', kids: ['Beta'], date: '2026-11-20' }),
  ],
  freshness: {
    ixl: fresh(), sgy: fresh(), gc: fresh(), powerschool: fresh(),
  },
  warnings: [],
}

function Where() {
  const loc = useLocation()
  return <div data-testid="where">{loc.pathname + loc.search}</div>
}

export interface FakeResponse { ok: boolean; status?: number; body?: unknown }
/** May return a promise (e.g. one that never settles, to model a slow refetch). */
export type Handler = (url: string, init?: RequestInit) => FakeResponse | Promise<FakeResponse>

/** Renders `ui` with a QueryClient, a router at `route`, and fetch stubbed by `handler`. */
export function renderAt(ui: ReactElement, route: string, handler: Handler) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const out = await handler(url, init)
    return { ok: out.ok, status: out.status ?? (out.ok ? 200 : 500), statusText: 'err', json: async () => out.body }
  })
  vi.stubGlobal('fetch', fetchMock)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const utils = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>{ui}<Where /></MemoryRouter>
    </QueryClientProvider>,
  )
  return { ...utils, client, fetchMock }
}

export const okOverview = (data: Overview): Handler => () => ({ ok: true, body: data })
