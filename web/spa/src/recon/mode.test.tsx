import { screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { CONFIG_CACHE_KEY } from './config'
import { CONFIG, fail, fakeServer, ok, renderRecon } from './fixtures'
import { useReconMode } from './mode'

function Probe() {
  return <div data-testid="mode">{useReconMode()}</div>
}

beforeEach(() => sessionStorage.clear())
afterEach(() => vi.unstubAllGlobals())

const mode = () => screen.getByTestId('mode').textContent

test('loading until config answers, then recon when enabled and migrated', async () => {
  renderRecon(<Probe />, '/', fakeServer())
  expect(mode()).toBe('loading')
  await waitFor(() => expect(mode()).toBe('recon'))
})

test.each([
  ['404', { config: fail('not_found', 404) }],
  ['network error', { config: { reject: true } }],
  ['500', { config: fail('internal', 500) }],
  ['enabled false', { config: { ...CONFIG, enabled: false } }],
  ['not migrated', { config: { ...CONFIG, migrated: false } }],
  ['malformed body', { config: ok({ nonsense: true }) }],
])('%s means legacy', async (_name, opts) => {
  renderRecon(<Probe />, '/', fakeServer(opts))
  await waitFor(() => expect(mode()).toBe('legacy'))
})

test('a cached answer shows recon on the first render, with no loading state', () => {
  sessionStorage.setItem(CONFIG_CACHE_KEY, JSON.stringify(CONFIG))
  renderRecon(<Probe />, '/', fakeServer({ config: { status: 200, body: new Promise(() => undefined) } }))
  expect(mode()).toBe('recon')
})

test('a fresh answer beats the cache and updates it', async () => {
  sessionStorage.setItem(CONFIG_CACHE_KEY, JSON.stringify(CONFIG))
  renderRecon(<Probe />, '/', fakeServer({ config: { ...CONFIG, enabled: false } }))
  expect(mode()).toBe('recon')
  await waitFor(() => expect(mode()).toBe('legacy'))
  expect(JSON.parse(sessionStorage.getItem(CONFIG_CACHE_KEY) ?? 'null')).toMatchObject({ enabled: false })
})

test('a failing refetch drops a stale recon cache', async () => {
  sessionStorage.setItem(CONFIG_CACHE_KEY, JSON.stringify(CONFIG))
  renderRecon(<Probe />, '/', fakeServer({ config: fail('not_found', 404) }))
  await waitFor(() => expect(mode()).toBe('legacy'))
  expect(sessionStorage.getItem(CONFIG_CACHE_KEY)).toBeNull()
})

test('a corrupt cache is ignored', () => {
  sessionStorage.setItem(CONFIG_CACHE_KEY, '{not json')
  renderRecon(<Probe />, '/', fakeServer())
  expect(mode()).toBe('loading')
})
