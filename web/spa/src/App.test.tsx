import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi } from 'vitest'
import App from './App'

vi.mock('./views/Chat', () => ({ Chat: () => <h1>Chat page</h1> }))
vi.mock('./views/Sync', () => ({ Sync: () => <h1>Sync page</h1> }))
vi.mock('./classic/Home', () => ({ Home: () => <h1>Classic home</h1> }))
vi.mock('./classic/Child', () => ({ Child: () => <h1>Classic child</h1> }))

function at(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')))
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}><App /></MemoryRouter>
    </QueryClientProvider>,
  )
}

test.each([
  ['/', 'Today'],
  ['/tonight', 'Tonight'],
  ['/overdue', 'Overdue'],
  ['/missing', 'Missing'],
  ['/events', 'Events'],
  ['/calendar', 'Calendar'],
  ['/chat', 'Chat page'],
  ['/sync', 'Sync page'],
  ['/settings', 'Settings'],
])('%s renders its page heading', async (path, heading) => {
  at(path)
  expect((await screen.findByRole('heading', { level: 1, name: heading })).textContent).toBe(heading)
})

test('/classic/home renders the old Home', async () => {
  at('/classic/home')
  expect(await screen.findByRole('heading', { name: 'Classic home' })).toBeTruthy()
})

test('/classic/child/:name renders the old Child', async () => {
  at('/classic/child/Zed')
  expect(await screen.findByRole('heading', { name: 'Classic child' })).toBeTruthy()
})

test('unknown path redirects to Today', async () => {
  at('/nope/deeper')
  expect(await screen.findByRole('heading', { level: 1, name: 'Today' })).toBeTruthy()
})

test('legacy /home redirects to the classic home', async () => {
  at('/home')
  expect(await screen.findByRole('heading', { name: 'Classic home' })).toBeTruthy()
})

test('legacy /child/:name redirects to the classic child view', async () => {
  at('/child/Zed')
  expect(await screen.findByRole('heading', { name: 'Classic child' })).toBeTruthy()
})

test('skip link targets the main content container', () => {
  at('/')
  const link = screen.getByRole('link', { name: /skip to main content/i })
  expect(link.getAttribute('href')).toBe('#main')
  expect(document.getElementById('main')).toBeTruthy()
})

test('both navs share the Main label', () => {
  at('/')
  expect(document.querySelectorAll('nav[aria-label="Main"]').length).toBe(2)
})

test('navigation exposes the main destinations', () => {
  at('/')
  for (const name of ['Today', 'Calendar', 'Chat', 'Settings']) {
    expect(screen.getAllByRole('link', { name: new RegExp(name) }).length).toBeGreaterThan(0)
  }
})
