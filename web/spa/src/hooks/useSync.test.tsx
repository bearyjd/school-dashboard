import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { clearSyncToken, setSyncToken } from '../lib/syncToken'
import { useSync } from './useSync'

type Reply = { status: number; body: unknown; headers?: Record<string, string> }

function replies(...list: Reply[]) {
  let i = 0
  return vi.fn<(url: string, init?: RequestInit) => Promise<unknown>>(async () => {
    const r = list[Math.min(i++, list.length - 1)]
    return {
      ok: r.status < 400, status: r.status, statusText: 'x',
      headers: { get: (k: string) => r.headers?.[k] ?? null },
      json: async () => r.body,
    }
  })
}

beforeEach(() => { clearSyncToken(); localStorage.clear() })
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

async function run() {
  const hook = renderHook(() => useSync())
  await act(async () => { await hook.result.current.trigger('ixl') })
  return hook
}

test('no token: tells the user to enter it in Settings and sends nothing', async () => {
  const f = replies({ status: 202, body: {} }); vi.stubGlobal('fetch', f)
  const { result } = await run()
  expect(result.current.error).toEqual({ code: 'no_token', message: 'Enter the sync token in Settings' })
  expect(f).not.toHaveBeenCalled()
})

test.each([
  [401, { error: 'unauthorized' }, {}, 'Wrong token'],
  [429, { error: 'too many attempts' }, { 'Retry-After': '30' }, 'Too many attempts, wait 30 s'],
  [501, { error: 'SYNC_TOKEN not configured' }, {}, 'Syncing is not configured'],
])('status %i shows a clear message', async (status, body, headers, msg) => {
  setSyncToken('TESTTOKEN123')
  vi.stubGlobal('fetch', replies({ status, body, headers }))
  const { result } = await run()
  expect(result.current.error?.message).toBe(msg)
  expect(result.current.error?.message).not.toContain('TESTTOKEN123')
})

test('sends the saved token in the header only', async () => {
  setSyncToken('TESTTOKEN123')
  const f = replies({ status: 202, body: { started: true } }); vi.stubGlobal('fetch', f)
  vi.useFakeTimers()
  const { unmount } = await run()
  const [url, init] = f.mock.calls[0]
  expect(url).toBe('/api/sync')
  expect((init?.headers as Record<string, string>)['X-Sync-Token']).toBe('TESTTOKEN123')
  unmount()
})

test('409 already running is not an error: it starts following the running sync', async () => {
  setSyncToken('TESTTOKEN123')
  const f = replies(
    { status: 409, body: { error: 'sync already running' } },
    { status: 200, body: { running: false, last_result: 'ok' } },
  )
  vi.stubGlobal('fetch', f)
  vi.useFakeTimers()
  const { result, unmount } = await run()
  expect(result.current.error).toBeNull()
  await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
  expect(f.mock.calls[1][0]).toBe('/api/sync/status')
  expect(result.current.status?.running).toBe(false)
  unmount()
})

test('a failing status poll says it could not check status, and polling stops', async () => {
  setSyncToken('TESTTOKEN123')
  const f = replies({ status: 202, body: {} }, { status: 500, body: {} })
  vi.stubGlobal('fetch', f)
  vi.useFakeTimers()
  const { result, unmount } = await run()
  await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
  expect(result.current.error).toEqual({ code: 'status_failed', message: 'Could not check sync status' })
  const calls = f.mock.calls.length
  await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
  expect(f.mock.calls.length).toBe(calls)
  unmount()
})

test('unmount stops the polling interval', async () => {
  setSyncToken('TESTTOKEN123')
  const f = replies({ status: 202, body: {} }, { status: 200, body: { running: true } })
  vi.stubGlobal('fetch', f)
  vi.useFakeTimers()
  const { unmount } = await run()
  unmount()
  const calls = f.mock.calls.length
  await vi.advanceTimersByTimeAsync(10000)
  expect(f.mock.calls.length).toBe(calls)
})
