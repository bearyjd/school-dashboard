import { fireEvent, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { FIXTURE, NOW, okOverview, renderAt } from '../test-utils'
import { Calendar } from './Calendar'

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
  fireEvent.click(screen.getByRole('button', { name: /previous week/i }))
  expect(screen.getByTestId('where').textContent).toBe('/calendar?week=-1')
  expect(day('Sat, Oct 31')).toBeTruthy()
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

test('banner links to the overdue and missing lists', async () => {
  await show()
  expect(screen.getByRole('link', { name: /2 overdue/i }).getAttribute('href')).toBe('/overdue')
  expect(screen.getByRole('link', { name: /2 missing/i }).getAttribute('href')).toBe('/missing')
})
