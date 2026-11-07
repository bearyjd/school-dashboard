import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import type { Overview } from '../api/types'
import { FIXTURE, NOW, item, okOverview, renderAt, type Handler } from '../test-utils'
import { ListPage, type ListKind } from './ListPage'

beforeEach(() => vi.useFakeTimers({ toFake: ['Date'], now: NOW }))
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

async function show(kind: ListKind, data: Overview = FIXTURE, route = '/', handler: Handler = okOverview(data)) {
  const utils = renderAt(<ListPage kind={kind} />, route, handler)
  await screen.findByRole('table')
  return utils
}

const whatCol = () =>
  within(screen.getAllByRole('rowgroup')[1]).getAllByRole('row').map(r => r.querySelector('[data-col=what]')?.textContent)

test.each([
  ['tonight', ['IXL: 3 remaining', 'Read chapter']],
  ['overdue', ['Essay draft', 'Poster']],
  ['missing', ['Quiz 3', 'Lab report', 'Worksheet']],
] as const)('%s shows only its status', async (kind, titles) => {
  await show(kind)
  expect([...whatCol()].sort()).toEqual([...titles].sort())
})

test('events tab lists events with kids, and school-wide ones', async () => {
  await show('events')
  const titles = whatCol()
  expect(titles).toContain('Practice')
  expect(titles).toContain('No school')
  expect(titles).toContain('Later match')
})

test('sort by due and by kid, keyboard-operable buttons, aria-sort', async () => {
  const data: Overview = {
    ...FIXTURE,
    items: [
      item({ id: 'a', title: 'B-late', kid: 'Beta', due: '2026-11-05' }),
      item({ id: 'b', title: 'A-early', kid: 'Alpha', due: '2026-11-06' }),
      item({ id: 'c', title: 'C-none', kid: 'Alpha', due: null }),
    ],
  }
  await show('overdue', data)
  expect(whatCol()).toEqual(['B-late', 'A-early', 'C-none']) // due ascending, undated last
  fireEvent.click(screen.getByRole('button', { name: /sort by due/i }))
  expect(whatCol()).toEqual(['A-early', 'B-late', 'C-none']) // descending, undated still last
  fireEvent.click(screen.getByRole('button', { name: /sort by who/i }))
  expect(whatCol()[2]).toBe('B-late')
  expect(screen.getByRole('columnheader', { name: /who/i }).getAttribute('aria-sort')).toBe('ascending')
  expect(screen.getByTestId('where').textContent).toContain('sort=kid')
  fireEvent.click(screen.getByRole('button', { name: /sort by who/i }))
  expect(screen.getByTestId('where').textContent).toContain('sort=-kid')
})

test('late filter on missing', async () => {
  await show('missing')
  fireEvent.change(screen.getByLabelText(/show/i), { target: { value: 'late' } })
  expect(whatCol()).toEqual(['Worksheet'])
  fireEvent.change(screen.getByLabelText(/show/i), { target: { value: 'missing' } })
  expect(whatCol().sort()).toEqual(['Lab report', 'Quiz 3'])
})

test('kid and source filters narrow rows', async () => {
  await show('overdue')
  fireEvent.click(screen.getByRole('button', { name: /Beta/ }))
  expect(whatCol()).toEqual(['Poster'])
  fireEvent.click(screen.getByRole('button', { name: 'Everyone' }))
  fireEvent.change(screen.getByLabelText(/filter by source/i), { target: { value: 'schoology' } })
  expect(whatCol()).toEqual(['Essay draft'])
})

test('check-off is optimistic and sends PATCH /api/items/<id>', async () => {
  const calls: [string, RequestInit | undefined][] = []
  const { fetchMock } = await show('overdue', FIXTURE, '/', (url, init) => {
    calls.push([url, init])
    if (url !== '/api/overview') return { ok: true, body: { ok: true } }
    const patched = calls.some(c => c[0] === '/api/items/5')
    return { ok: true, body: { ...FIXTURE, items: FIXTURE.items.filter(i => !(patched && i.id === 'o2')) } }
  })
  fireEvent.click(screen.getByRole('checkbox', { name: /poster/i }))
  await waitFor(() => expect(whatCol()).toEqual(['Essay draft']))
  await waitFor(() => expect(fetchMock.mock.calls.some(c => c[0] === '/api/items/5')).toBe(true))
  const patch = calls.find(c => c[0] === '/api/items/5')!
  expect(patch[1]?.method).toBe('PATCH')
  expect(JSON.parse(patch[1]?.body as string)).toEqual({ completed: true })
})

test('check-off rolls back and shows an error when the PATCH fails', async () => {
  await show('overdue', FIXTURE, '/', url =>
    url === '/api/overview' ? { ok: true, body: FIXTURE } : { ok: false, body: { error: 'boom' } })
  fireEvent.click(screen.getByRole('checkbox', { name: /poster/i }))
  await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/poster/i))
  expect(whatCol().sort()).toEqual(['Essay draft', 'Poster'])
})

test('read-only rows (PowerSchool, IXL, schoology without an item id) have no checkbox', async () => {
  await show('missing')
  expect(screen.queryAllByRole('checkbox')).toHaveLength(0)
  expect(screen.queryByRole('columnheader', { name: /done/i })).toBeNull()
})

test('garbage rows do not break the table and it sits in a scroll container', async () => {
  const long = 'z'.repeat(160)
  const data: Overview = {
    ...FIXTURE,
    items: [item({ id: 'e', title: '', course: '' }), item({ id: 'f', title: long, course: long })],
  }
  await show('overdue', data)
  expect(whatCol()).toContain(long)
  expect(screen.getByRole('table').parentElement?.className).toContain('table-scroll')
  expect(screen.getAllByRole('row')).toHaveLength(3)
})

const TWO_MANUAL: Overview = {
  ...FIXTURE,
  items: [
    item({ id: 'a', title: 'Essay draft', due: '2026-11-04' }),
    item({ id: 'p', title: 'Poster', source: 'manual', course: '', due: '2026-11-01', itemId: 5 }),
    item({ id: 'b', title: 'Banner', source: 'manual', course: '', due: '2026-11-02', itemId: 6 }),
  ],
}

/** Overview handler that drops manual rows whose PATCH already succeeded, like the real server. */
function server(patch: (id: string) => { ok: boolean; status?: number; body?: unknown }): Handler {
  const done = new Set<string>()
  return (url, init) => {
    if (url === '/api/overview') {
      return { ok: true, body: { ...TWO_MANUAL, items: TWO_MANUAL.items.filter(i => !done.has(String(i.itemId))) } }
    }
    const id = url.split('/').pop() as string
    const out = patch(id)
    if (out.ok && init?.method === 'PATCH') done.add(id)
    return out
  }
}

test('overlapping check-offs: when the first fails only that row comes back', async () => {
  await show('overdue', TWO_MANUAL, '/', server(id => (id === '5' ? { ok: false, body: { error: 'boom' } } : { ok: true, body: { ok: true } })))
  fireEvent.click(screen.getByRole('checkbox', { name: /poster/i }))
  fireEvent.click(screen.getByRole('checkbox', { name: /banner/i }))
  await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/poster/i))
  await waitFor(() => expect(whatCol().sort()).toEqual(['Essay draft', 'Poster']))
})

test('maintenance 503 shows the paused-for-maintenance message', async () => {
  await show('overdue', TWO_MANUAL, '/', server(() => ({ ok: false, status: 503, body: { error: 'maintenance' } })))
  fireEvent.click(screen.getByRole('checkbox', { name: /poster/i }))
  await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/Updates are paused for maintenance\. Try again later\./))
  await waitFor(() => expect(whatCol()).toContain('Poster'))
})

test('after a successful check-off focus moves to the next checkbox and the result is announced', async () => {
  await show('overdue', TWO_MANUAL, '/', server(() => ({ ok: true, body: { ok: true } })))
  fireEvent.click(screen.getByRole('checkbox', { name: /poster/i }))
  await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('checkbox', { name: /banner/i })))
  expect(screen.getAllByRole('status').some(s => s.textContent === 'Marked Poster done')).toBe(true)
})

test('after checking off the last checkable row focus goes to the table', async () => {
  const one: Overview = { ...TWO_MANUAL, items: TWO_MANUAL.items.slice(0, 2) }
  const done = new Set<string>()
  await show('overdue', one, '/', (url, init) => {
    if (url === '/api/overview') return { ok: true, body: { ...one, items: one.items.filter(i => !done.has(String(i.itemId))) } }
    if (init?.method === 'PATCH') done.add(url.split('/').pop() as string)
    return { ok: true, body: { ok: true } }
  })
  fireEvent.click(screen.getByRole('checkbox', { name: /poster/i }))
  await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('table')))
})

test('sort keys are per table; an unknown one falls back to due', async () => {
  const { unmount } = await show('overdue', FIXTURE, '/?sort=type')
  expect(screen.getByRole('columnheader', { name: /due/i }).getAttribute('aria-sort')).toBe('ascending')
  expect(screen.queryByRole('columnheader', { name: /type/i })).toBeNull()
  unmount()
  await show('events', FIXTURE, '/?sort=course')
  expect(screen.getByRole('columnheader', { name: /when/i }).getAttribute('aria-sort')).toBe('ascending')
  unmount()
})

test('a failing source tags its rows with the failing icon', async () => {
  const data: Overview = {
    ...FIXTURE,
    freshness: { ...FIXTURE.freshness, powerschool: { ...FIXTURE.freshness.powerschool, failing: true, age_hours: 30 } },
  }
  await show('missing', data)
  const row = screen.getByText('Quiz 3').closest('tr') as HTMLElement
  expect(row.textContent).toMatch(/✖ as of 1d 6h ago/)
})

test('error without data offers retry', async () => {
  renderAt(<ListPage kind="overdue" />, '/', () => ({ ok: false, body: { error: 'x' } }))
  expect(await screen.findByRole('button', { name: /retry/i })).toBeTruthy()
})
