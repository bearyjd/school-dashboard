import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { clearSyncToken } from '../lib/syncToken'
import { Sync } from './Sync'

function mount() {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, json: async () => ({}) })))
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter><Sync /></MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => { clearSyncToken(); localStorage.clear() })
afterEach(() => vi.unstubAllGlobals())

test('sync view still lists all four sources', async () => {
  mount()
  for (const name of ['IXL', 'Schoology', 'PowerSchool', 'GameChanger']) {
    expect(await screen.findByText(name)).toBeTruthy()
  }
  expect(screen.getAllByRole('button', { name: 'Pull' })).toHaveLength(4)
})

test('pulling without a token alerts and links to Settings', async () => {
  mount()
  fireEvent.click((await screen.findAllByRole('button', { name: 'Pull' }))[0])
  const alert = await screen.findByRole('alert')
  expect(alert.textContent).toContain('Enter the sync token in Settings')
  expect(screen.getByRole('link', { name: 'Open Settings' }).getAttribute('href')).toBe('/settings')
})
