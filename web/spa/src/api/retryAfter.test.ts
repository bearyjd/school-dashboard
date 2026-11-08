import { afterEach, expect, test, vi } from 'vitest'
import { ApiError, triggerSync } from './index'

function reject429(retry: string | null) {
  vi.stubGlobal('fetch', vi.fn(async () => ({
    ok: false, status: 429, statusText: 'Too Many Requests',
    headers: { get: (k: string) => (k === 'Retry-After' ? retry : null) },
    json: async () => ({ error: 'too many attempts' }),
  })))
}

async function caught(): Promise<ApiError> {
  try {
    await triggerSync('ixl', 'none', 't')
  } catch (e) {
    return e as ApiError
  }
  throw new Error('expected a rejection')
}

afterEach(() => vi.unstubAllGlobals())

test('Retry-After integer is parsed', async () => {
  reject429('42')
  expect((await caught()).retryAfter).toBe(42)
})

test('absent Retry-After leaves retryAfter undefined', async () => {
  reject429(null)
  expect((await caught()).retryAfter).toBeUndefined()
})

test('HTTP-date Retry-After is ignored rather than mis-parsed', async () => {
  reject429('Wed, 21 Oct 2026 07:28:00 GMT')
  expect((await caught()).retryAfter).toBeUndefined()
})
