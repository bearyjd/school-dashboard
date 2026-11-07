import { fireEvent, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { FIXTURE, NOW, okOverview, renderAt } from '../test-utils'
import { DATA_WINDOW_DAYS } from '../lib/week'
import { Calendar, MAX_WEEK_OFFSET } from './Calendar'

beforeEach(() => vi.useFakeTimers({ toFake: ['Date'], now: NOW }))
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

function mockWidth(wide: boolean) {
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: wide, media: q, addEventListener: () => {}, removeEventListener: () => {},
  }))
}

async function show(route = '/calendar', wide = true) {
  mockWidth(wide)
  const utils = renderAt(<Calendar />, route, okOverview(FIXTURE))
  await screen.findByRole('region', { name: /Sat, Nov 7|Sat, Nov 14/ })
  return utils
}

const day = (label: string) => screen.getByRole('region', { name: new RegExp(label) })

test('items and events land on their day', async () => {
  await show()
  const mon = day('Mon, Nov 9')
  expect(within(mon).getByText('Read chapter')).toBeTruthy()
  expect(within(mon).getByText('Joint game')).toBeTruthy()
  expect(within(day('Sun, Nov 8')).getByText('Practice')).toBeTruthy()
  expect(within(day('Sun, Nov 8')).queryByText('Read chapter')).toBeNull()
  expect(within(day('Tue, Nov 10')).getByText('No school')).toBeTruthy()
})

test('today is highlighted with text, not only colour', async () => {
  await show()
  const today = day('Sat, Nov 7')
  expect(today.getAttribute('aria-current')).toBe('date')
  expect(within(today).getByText('Today')).toBeTruthy()
  expect(day('Sun, Nov 8').getAttribute('aria-current')).toBeNull()
})

test('week navigation updates the url and shifts by seven days', async () => {
  await show()
  fireEvent.click(screen.getByRole('button', { name: /next week/i }))
  expect(screen.getByTestId('where').textContent).toBe('/calendar?week=1')
  expect(day('Sat, Nov 14')).toBeTruthy()
  expect(within(day('Fri, Nov 20')).getByText('Later match')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: /previous week/i }))
  expect(screen.getByTestId('where').textContent).toBe('/calendar')
  expect(day('Sat, Nov 7')).toBeTruthy()
})

test('week offset comes from the url and junk is ignored', async () => {
  await show('/calendar?week=1')
  expect(day('Sat, Nov 14')).toBeTruthy()
})

test('junk week param falls back to this week', async () => {
  await show('/calendar?week=abc')
  expect(day('Sat, Nov 7')).toBeTruthy()
})

test('agenda list on a narrow viewport, grid on a wide one', async () => {
  const { unmount } = await show('/calendar', false)
  expect(screen.getByTestId('calendar-layout').getAttribute('data-layout')).toBe('agenda')
  unmount()
  await show('/calendar', true)
  expect(screen.getByTestId('calendar-layout').getAttribute('data-layout')).toBe('grid')
})

test('multi-kid event shows both chips', async () => {
  await show()
  const mon = day('Mon, Nov 9')
  expect(within(mon).getAllByRole('img', { name: 'Alpha' }).length).toBeGreaterThan(0)
  expect(within(mon).getAllByRole('img', { name: 'Beta' }).length).toBeGreaterThan(0)
})

test('kid filter keeps school-wide events and hides other kids', async () => {
  await show('/calendar?kid=Beta')
  expect(within(day('Tue, Nov 10')).getByText('No school')).toBeTruthy()
  expect(within(day('Sun, Nov 8')).queryByText('Practice')).toBeNull()
})

test('weeks outside the data window are not offered: only this week and next', async () => {
  mockWidth(true)
  const { unmount } = renderAt(<Calendar />, '/calendar?week=1', okOverview(FIXTURE))
  await screen.findByRole('button', { name: /next week/i })
  expect((screen.getByRole('button', { name: /next week/i }) as HTMLButtonElement).disabled).toBe(true)
  expect((screen.getByRole('button', { name: /previous week/i }) as HTMLButtonElement).disabled).toBe(false)
  unmount()
  renderAt(<Calendar />, '/calendar', okOverview(FIXTURE))
  await screen.findByRole('button', { name: /previous week/i })
  expect((screen.getByRole('button', { name: /previous week/i }) as HTMLButtonElement).disabled).toBe(true)
})

test.each(['2', '-1', '52', '-52'])('a week=%s url is outside the window and shows this week', async raw => {
  await show(`/calendar?week=${raw}`)
  expect(day('Sat, Nov 7')).toBeTruthy()
})

test('backlog note hides the side that is zero', async () => {
  mockWidth(true)
  const data = { ...FIXTURE, items: FIXTURE.items.filter(i => i.status !== 'missing' && i.status !== 'late') }
  renderAt(<Calendar />, '/calendar', okOverview(data))
  await screen.findByRole('region', { name: /Sat, Nov 7/ })
  expect(screen.getByRole('link', { name: /2 overdue/i })).toBeTruthy()
  expect(screen.queryByRole('link', { name: /missing/i })).toBeNull()
})

test('banner links to the overdue and missing lists', async () => {
  await show()
  expect(screen.getByRole('link', { name: /2 overdue/i }).getAttribute('href')).toBe('/overdue')
  expect(screen.getByRole('link', { name: /3 missing/i }).getAttribute('href')).toBe('/missing')
})

test('unassigned events are labelled Unassigned and hidden under a kid filter', async () => {
  const data = { ...FIXTURE, events: [...FIXTURE.events,
    { ...FIXTURE.events[0], title: 'Mystery team game', kids: [], unassigned: true, date: '2026-11-08' }] }
  mockWidth(true)
  const { unmount } = renderAt(<Calendar />, '/calendar', okOverview(data))
  await screen.findByRole('region', { name: /Sat, Nov 7/ })
  expect(within(day('Sun, Nov 8')).getByText('Unassigned')).toBeTruthy()
  unmount()
  renderAt(<Calendar />, '/calendar?kid=Alpha', okOverview(data))
  await screen.findByRole('region', { name: /Sat, Nov 7/ })
  expect(screen.queryByText('Mystery team game')).toBeNull()
})

test('the week limit is derived from the 14-day data window', () => {
  expect(MAX_WEEK_OFFSET).toBe(DATA_WINDOW_DAYS / 7 - 1)
})
