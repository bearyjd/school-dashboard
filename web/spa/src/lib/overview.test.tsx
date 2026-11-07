import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, expect, test, vi } from 'vitest'
import type { Overview } from '../api/types'
import { useOverview } from './overview'

const payload: Overview = {
  generated_at: '2026-01-01T00:00:00+00:00', kids: [], items: [], events: [], freshness: {}, warnings: [],
}

afterEach(() => vi.unstubAllGlobals())

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  return { ...renderHook(() => {
    const q = useOverview() // read in render so the observer tracks these fields
    return { data: q.data, isError: q.isError, refetch: q.refetch }
  }, { wrapper }) }
}

test('fetches /api/overview', async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => payload })
  vi.stubGlobal('fetch', fetchMock)
  const { result } = setup()
  await waitFor(() => expect(result.current.data).toEqual(payload))
  expect(fetchMock.mock.calls[0][0]).toBe('/api/overview')
})

test('keeps previous data and flags the error when a refetch fails', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => payload }))
  const { result } = setup()
  await waitFor(() => expect(result.current.data).toEqual(payload))
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
    ok: false, statusText: 'Bad', json: async () => ({ error: 'down' }),
  }))
  await act(async () => { await result.current.refetch() })
  await waitFor(() => expect(result.current.isError).toBe(true))
  expect(result.current.data).toEqual(payload)
})
