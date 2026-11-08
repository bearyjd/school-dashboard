import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { setSyncToken } from '../lib/syncToken'
import { useSync } from './useSync'

function reply(status: number, body: unknown, headers: Record<string, string> = {}) {
  return vi.fn<(url: string, init?: RequestInit) => Promise<unknown>>(async () => ({
    ok: status < 400, status, statusText: 'x',
    headers: { get: (k: string) => headers[k] ?? null },
    json: async () => body,
  }))
}

beforeEach(() => localStorage.clear())
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

async function run() {
  const { result } = renderHook(() => useSync())
  await act(async () => { await result.current.trigger('ixl') })
  return result.current
}

test('no token: tells the user to enter it in Settings and sends nothing', async () => {
  const f = reply(202, {}); vi.stubGlobal('fetch', f)
  expect((await run()).error).toBe('Enter the sync token in Settings')
  expect(f).not.toHaveBeenCalled()
})

test.each([
  [401, { error: 'unauthorized' }, {}, 'Wrong token'],
  [429, { error: 'too many attempts' }, { 'Retry-After': '30' }, 'Too many attempts, wait 30 s'],
  [501, { error: 'SYNC_TOKEN not configured' }, {}, 'Syncing is not configured'],
])('status %i shows a clear message', async (status, body, headers, msg) => {
  setSyncToken('TESTTOKEN123')
  vi.stubGlobal('fetch', reply(status, body, headers))
  const out = await run()
  expect(out.error).toBe(msg)
  expect(out.error).not.toContain('TESTTOKEN123')
})

test('sends the saved token in the header only', async () => {
  setSyncToken('TESTTOKEN123')
  const f = reply(202, { started: true }); vi.stubGlobal('fetch', f)
  vi.useFakeTimers()
  await run()
  const [url, init] = f.mock.calls[0]
  expect(url).toBe('/api/sync')
  expect((init?.headers as Record<string, string>)['X-Sync-Token']).toBe('TESTTOKEN123')
})
