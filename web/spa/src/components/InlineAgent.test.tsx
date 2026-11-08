import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { clearSyncToken, setSyncToken } from '../lib/syncToken'
import { InlineAgent } from './InlineAgent'

vi.mock('../hooks/useInlineChat', () => ({
  useInlineChat: () => ({
    loading: false, reply: 'Want me to sync?', error: null, ask: vi.fn(), reset: vi.fn(),
    action: { type: 'trigger_sync', payload: { source: 'ixl' } },
  }),
}))

function mount() {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter><InlineAgent contextType="sync_source" contextId="ixl" /></MemoryRouter>
    </QueryClientProvider>,
  )
  fireEvent.click(screen.getByRole('button', { name: /ask/i }))
}

beforeEach(() => { clearSyncToken(); localStorage.clear() })
afterEach(() => vi.unstubAllGlobals())

test('trigger_sync without a token shows an alert with a link to Settings, cleared on dismiss', () => {
  const f = vi.fn(); vi.stubGlobal('fetch', f)
  mount()
  fireEvent.click(screen.getByRole('button', { name: /sync now/i }))
  const alert = screen.getByRole('alert')
  expect(alert.textContent).toContain('Enter the sync token in Settings')
  expect(screen.getByRole('link', { name: 'Open Settings' }).getAttribute('href')).toBe('/settings')
  expect(f).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }))
  expect(screen.queryByRole('alert')).toBeNull()
})

test('trigger_sync rejected with 401 says Wrong token', async () => {
  setSyncToken('TESTTOKEN123')
  vi.stubGlobal('fetch', vi.fn(async () => ({
    ok: false, status: 401, statusText: 'x', headers: { get: () => null }, json: async () => ({ error: 'unauthorized' }),
  })))
  mount()
  fireEvent.click(screen.getByRole('button', { name: /sync now/i }))
  expect((await screen.findByRole('alert')).textContent).toContain('Wrong token')
})
