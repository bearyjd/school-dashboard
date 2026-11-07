import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi } from 'vitest'
import { Sync } from './Sync'

test('sync view still lists all four sources', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, json: async () => ({}) })))
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter><Sync /></MemoryRouter>
    </QueryClientProvider>,
  )
  for (const name of ['IXL', 'Schoology', 'PowerSchool', 'GameChanger']) {
    expect(await screen.findByText(name)).toBeTruthy()
  }
  expect(screen.getAllByRole('button', { name: 'Pull' })).toHaveLength(4)
})
