import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import type { Overview } from '../api/types'
import { FIXTURE, NOW, fresh, item, okOverview, renderAt } from '../test-utils'
import { Today } from './Today'

beforeEach(() => vi.useFakeTimers({ toFake: ['Date'], now: NOW }))
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

const tile = (name: RegExp) =>
  within(screen.getByRole('region', { name: 'Summary' })).getByRole('button', { name })

async function show(data: Overview = FIXTURE, route = '/') {
  const utils = renderAt(<Today />, route, okOverview(data))
  await screen.findByRole('heading', { level: 1, name: 'Today' })
  await screen.findByRole('group', { name: /filter by child/i })
  return utils
}

test('tile counts and link targets', async () => {
  await show()
  const cases: [RegExp, string, string][] = [
    [/overdue/i, '2', '/overdue'],
    [/missing/i, '2', '/missing'],
    [/due tonight/i, '2', '/tonight'],
    [/events this week/i, '3', '/events'],
  ]
  for (const [name, count, target] of cases) {
    const t = tile(name)
    expect(within(t).getByText(count)).toBeTruthy()
    fireEvent.click(t)
    expect(screen.getByTestId('where').textContent).toBe(target)
    // navigating away unmounts nothing here (no routes), so the tiles stay available
  }
})

test('kid filter narrows everything and updates ?kid=', async () => {
  await show()
  fireEvent.click(screen.getByRole('button', { name: /Beta/ }))
  expect(screen.getByTestId('where').textContent).toBe('/?kid=Beta')
  expect(screen.queryByText('Essay draft')).toBeNull()
  expect(screen.getByText('Poster')).toBeTruthy()
  expect(within(tile(/overdue/i)).getByText('1')).toBeTruthy()
  // Beta: joint game + school-wide event this week; Alpha-only practice is hidden.
  expect(within(tile(/events this week/i)).getByText('2')).toBeTruthy()
  const week = screen.getByRole('region', { name: /this week/i })
  expect(within(week).getByText('Joint game')).toBeTruthy()
  expect(within(week).getByText('No school')).toBeTruthy()
  expect(within(week).queryByText('Practice')).toBeNull()
})

test('?kid= from the URL is honoured and junk is ignored', async () => {
  const { unmount } = await show(FIXTURE, '/?kid=Alpha')
  expect(screen.queryByText('Poster')).toBeNull()
  unmount()
  await show(FIXTURE, '/?kid=Nobody')
  expect(screen.getByText('Poster')).toBeTruthy()
})

test('missing is collapsed by default and expands', async () => {
  await show()
  const toggle = screen.getByRole('button', { name: /missing \(2\)/i })
  expect(toggle.getAttribute('aria-expanded')).toBe('false')
  expect(screen.queryByText('Quiz 3')).toBeNull()
  fireEvent.click(toggle)
  expect(toggle.getAttribute('aria-expanded')).toBe('true')
  expect(screen.getByText('Quiz 3')).toBeTruthy()
})

test('stale source shows a banner naming it with age, tags its rows, and the page is not blank', async () => {
  const data: Overview = {
    ...FIXTURE,
    freshness: { ...FIXTURE.freshness, ixl: fresh({ stale: true, age_hours: 5 }) },
  }
  await show(data)
  const banner = screen.getAllByRole('status').find(el => /IXL/.test(el.textContent ?? ''))
  expect(banner?.textContent).toMatch(/5h ago/)
  const row = screen.getByText('IXL: 3 remaining').closest('.row') as HTMLElement
  expect(within(row).getByText(/as of 5h ago/)).toBeTruthy()
  expect(screen.getByText('Essay draft')).toBeTruthy()
})

test('failing source uses an error banner with text, not just colour', async () => {
  const data: Overview = {
    ...FIXTURE,
    freshness: { ...FIXTURE.freshness, powerschool: fresh({ failing: true, age_hours: 30 }) },
  }
  await show(data)
  const alert = screen.getByRole('alert')
  expect(alert.textContent).toMatch(/PowerSchool/)
  expect(alert.textContent).toMatch(/Failing/)
})

test('empty tonight names the next upcoming item', async () => {
  const data: Overview = {
    ...FIXTURE,
    items: FIXTURE.items.filter(i => i.status !== 'tonight'),
  }
  await show(data)
  expect(screen.getByText(/nothing due tonight/i).textContent).toMatch(/Next: Project outline, due Nov 12/)
})

test('error without data shows a retry button', async () => {
  renderAt(<Today />, '/', () => ({ ok: false, body: { error: 'down' } }))
  const retry = await screen.findByRole('button', { name: /retry/i })
  expect(screen.getByRole('heading', { level: 1, name: 'Today' })).toBeTruthy()
  expect(retry).toBeTruthy()
})

test('error with cached data shows the data and its age', async () => {
  const { client, fetchMock } = renderAt(<Today />, '/', okOverview(FIXTURE))
  await screen.findByText('Essay draft')
  fetchMock.mockResolvedValue({ ok: false, statusText: 'x', json: async () => ({ error: 'down' }) })
  await client.refetchQueries({ queryKey: ['overview'] })
  await waitFor(() => expect(screen.getByText(/showing saved data/i)).toBeTruthy())
  expect(screen.getByText('Essay draft')).toBeTruthy()
  expect(screen.getByText(/updated just now/i)).toBeTruthy()
  expect(screen.getByRole('button', { name: /retry/i })).toBeTruthy()
})

test.each([1, 4])('%i kid(s) render that many filter pills plus Everyone', async n => {
  const kids = Array.from({ length: n }, (_, i) => ({ name: `Kid${i}`, colorIndex: i }))
  await show({ ...FIXTURE, kids, items: [], events: [] })
  const group = screen.getByRole('group', { name: /filter by child/i })
  expect(within(group).getAllByRole('button')).toHaveLength(n + 1)
})

test('long titles use the wrapping row title and html is plain text', async () => {
  const title = '<b>x</b>' + 'y'.repeat(152)
  const data: Overview = { ...FIXTURE, items: [item({ id: 'long', title })] }
  await show(data)
  const el = screen.getByText(title)
  expect(el.className).toContain('row__title')
  expect(document.querySelector('b')).toBeNull()
})
