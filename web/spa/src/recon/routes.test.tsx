import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import App from '../App'
import { FIXTURE } from '../test-utils'
import { CONFIG, fail, fakeServer, type ServerOpts } from './fixtures'

vi.mock('../views/Chat', () => ({ Chat: () => <h1>Chat page</h1> }))
vi.mock('../views/Sync', () => ({ Sync: () => <h1>Sync page</h1> }))
vi.mock('../classic/Home', () => ({ Home: () => <h1>Classic home</h1> }))
vi.mock('../classic/Child', () => ({ Child: () => <h1>Classic child</h1> }))

beforeEach(() => sessionStorage.clear())
afterEach(() => vi.unstubAllGlobals())

function at(path: string, opts: ServerOpts = {}) {
  const server = fakeServer(opts)
  server.on('GET /api/overview', () => ({ status: 200, body: FIXTURE }))
  vi.stubGlobal('fetch', server.fetchMock)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}><App /></MemoryRouter>
    </QueryClientProvider>,
  )
}

const h1 = (name: string) => screen.findByRole('heading', { level: 1, name })

test('/ shows Reconcile when v2 is live', async () => {
  at('/')
  expect(await h1('Reconcile')).toBeTruthy()
  expect(screen.queryByRole('heading', { level: 1, name: 'Today' })).toBeNull()
})

test.each([
  ['404', { config: fail('not_found', 404) }],
  ['network error', { config: { reject: true } }],
  ['500', { config: fail('internal', 500) }],
  ['enabled false', { config: { ...CONFIG, enabled: false } }],
  ['not migrated', { config: { ...CONFIG, migrated: false } }],
])('/ shows Today with no error UI when config is %s', async (_n, opts) => {
  at('/', opts)
  expect(await h1('Today')).toBeTruthy()
  await screen.findByRole('region', { name: 'Summary' })
  expect(screen.queryByRole('alert')).toBeNull()
  expect(screen.queryByText(/not switched on/i)).toBeNull()
  expect(document.querySelector('.today__tiles')).not.toBeNull()
})

test('/ shows a loading skeleton, not Today or an error, while config is pending', async () => {
  at('/', { config: { status: 200, body: new Promise(() => undefined) } })
  expect(await screen.findByRole('status', { name: /loading/i })).toBeTruthy()
  expect(screen.queryByRole('heading', { level: 1, name: 'Today' })).toBeNull()
  expect(screen.queryByRole('alert')).toBeNull()
})

test('/today always renders Today, even when v2 is live', async () => {
  const server = at('/today')
  expect(await h1('Today')).toBeTruthy()
  server.unmount()
})

test('/recon renders Reconcile when live', async () => {
  at('/recon')
  expect(await h1('Reconcile')).toBeTruthy()
})

test('/recon in legacy mode explains that v2 is not switched on', async () => {
  at('/recon', { config: { ...CONFIG, enabled: false } })
  expect(await screen.findByText(/not switched on yet/i)).toBeTruthy()
  await waitFor(() => expect(screen.queryByRole('heading', { level: 2, name: /needs action/i })).toBeNull())
  expect(screen.getByRole('link', { name: /older view|today/i })).toBeTruthy()
})

test('Reconcile links back to the older Today view', async () => {
  at('/')
  await h1('Reconcile')
  const link = screen.getByRole('link', { name: /older view/i })
  expect(link.getAttribute('href')).toBe('/today')
})

test('other routes are unchanged', async () => {
  at('/calendar')
  expect(await h1('Calendar')).toBeTruthy()
})
