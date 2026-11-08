import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { clearSyncToken, setSyncToken } from '../lib/syncToken'
import { executeTool } from './tools'

function stubFetch(status: number, body: unknown = {}) {
  const f = vi.fn(async () => ({
    ok: status < 400, status, statusText: 'x', headers: { get: () => null }, json: async () => body,
  }))
  vi.stubGlobal('fetch', f)
  return f
}

beforeEach(() => { clearSyncToken(); localStorage.clear() })
afterEach(() => vi.unstubAllGlobals())

test('trigger_sync without a token returns the Settings hint and sends nothing', async () => {
  const f = stubFetch(202)
  expect(await executeTool({ name: 'trigger_sync', args: { sources: 'ixl' } })).toBe('Enter the sync token in Settings')
  expect(f).not.toHaveBeenCalled()
})

test('trigger_sync with a token starts the sync', async () => {
  setSyncToken('TESTTOKEN123')
  const f = stubFetch(202, { started: true })
  const out = await executeTool({ name: 'sync_source', args: { source: 'gc' } })
  expect(out).toContain('Sync started for: gc')
  const init = (f.mock.calls[0] as unknown as [string, RequestInit])[1]
  expect((init.headers as Record<string, string>)['X-Sync-Token']).toBe('TESTTOKEN123')
})

test.each([[401, 'Wrong token'], [501, 'Syncing is not configured'], [500, 'Sync failed']])(
  'trigger_sync failing with %i returns a short message, not the raw error',
  async (status, msg) => {
    setSyncToken('TESTTOKEN123')
    stubFetch(status, { error: 'raw server text TESTTOKEN123' })
    expect(await executeTool({ name: 'trigger_sync', args: {} })).toBe(msg)
  },
)
