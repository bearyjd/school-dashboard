// @vitest-environment node
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import { expect, test, vi } from 'vitest'
import { ERROR_OFFLINE } from './api/errors'

const source = readFileSync(new URL('../public/sw.js', import.meta.url), 'utf8')
const ORIGIN = 'https://x.test'

type Handler = (e: Record<string, unknown>) => void

function load(opts: { keys?: string[]; cached?: Record<string, Response> } = {}) {
  const handlers: Record<string, Handler> = {}
  const deleted: string[] = []
  const put = vi.fn(async () => undefined)
  const cached = opts.cached ?? {}
  const caches = {
    keys: async () => opts.keys ?? [],
    delete: async (k: string) => { deleted.push(k); return true },
    open: async () => ({ addAll: async () => undefined, put }),
    match: async (req: string | { url: string }) => {
      const key = typeof req === 'string' ? req : new URL(req.url).pathname
      return cached[key]
    },
  }
  const fetchFn = vi.fn()
  const self = {
    location: { origin: ORIGIN },
    addEventListener: (t: string, h: Handler) => { handlers[t] = h },
    skipWaiting: async () => undefined,
    clients: { claim: async () => undefined },
  }
  vm.runInNewContext(source, { self, caches, fetch: fetchFn, URL, Response })
  return { handlers, deleted, fetchFn, put }
}

async function request(
  handlers: Record<string, Handler>,
  path: string,
  init: { mode?: string; method?: string; origin?: string } = {},
) {
  let response: Promise<Response> | undefined
  const waits: Promise<unknown>[] = []
  handlers.fetch({
    request: { url: (init.origin ?? ORIGIN) + path, mode: init.mode ?? 'cors', method: init.method ?? 'GET' },
    respondWith: (p: Promise<Response>) => { response = p },
    waitUntil: (p: Promise<unknown>) => { waits.push(p) },
  })
  const res = await response!
  await Promise.all(waits)
  return res
}

test('activate deletes every other cache and keeps the current one', async () => {
  const { handlers, deleted } = load({ keys: ['school-v1', 'school-v2', 'other'] })
  let pending: Promise<unknown> = Promise.resolve()
  handlers.activate({ waitUntil: (p: Promise<unknown>) => { pending = p } })
  await pending
  expect(deleted.sort()).toEqual(['other', 'school-v1'])
})

test('install precaches into the school-v2 cache', async () => {
  const opened: string[] = []
  const handlers: Record<string, Handler> = {}
  const caches = { open: async (n: string) => { opened.push(n); return { addAll: async () => undefined } } }
  vm.runInNewContext(source, {
    self: { addEventListener: (t: string, h: Handler) => { handlers[t] = h }, skipWaiting: async () => undefined },
    caches, fetch: vi.fn(), URL, Response,
  })
  let pending: Promise<unknown> = Promise.resolve()
  handlers.install({ waitUntil: (p: Promise<unknown>) => { pending = p } })
  await pending
  expect(opened).toEqual(['school-v2'])
})

test('a successful navigation is served fresh and stored as the shell', async () => {
  const { handlers, fetchFn, put } = load()
  fetchFn.mockResolvedValue(new Response('fresh'))
  const res = await request(handlers, '/app/calendar', { mode: 'navigate' })
  expect(await res.text()).toBe('fresh')
  expect(put).toHaveBeenCalledTimes(1)
  expect((put.mock.calls[0] as unknown[])[0]).toBe('/app/index.html')
})

test('a failed navigation response is not stored', async () => {
  const { handlers, fetchFn, put } = load()
  fetchFn.mockResolvedValue(new Response('boom', { status: 500 }))
  await request(handlers, '/app/', { mode: 'navigate' })
  expect(put).not.toHaveBeenCalled()
})

test('offline navigation falls back to the cached shell', async () => {
  const { handlers, fetchFn } = load({ cached: { '/app/index.html': new Response('shell') } })
  fetchFn.mockRejectedValue(new Error('offline'))
  const res = await request(handlers, '/app/tonight', { mode: 'navigate' })
  expect(await res.text()).toBe('shell')
})

test('offline navigation with no cached shell returns 503', async () => {
  const { handlers, fetchFn } = load()
  fetchFn.mockRejectedValue(new Error('offline'))
  expect((await request(handlers, '/app/', { mode: 'navigate' })).status).toBe(503)
})

test('API responses are never cached', async () => {
  const { handlers, fetchFn, put } = load()
  fetchFn.mockResolvedValue(new Response('{"ok":true}'))
  await request(handlers, '/api/overview')
  await request(handlers, '/api/items', { method: 'POST' })
  expect(put).not.toHaveBeenCalled()
})

test('offline API call returns a 503 JSON error', async () => {
  const { handlers, fetchFn } = load()
  fetchFn.mockRejectedValue(new Error('offline'))
  const res = await request(handlers, '/api/overview')
  expect(res.status).toBe(503)
  expect(await res.json()).toEqual({ error: ERROR_OFFLINE }) // sw.js hard-codes this string
})

test('successful same-origin assets under /app/assets/ are cached', async () => {
  const { handlers, fetchFn, put } = load()
  fetchFn.mockResolvedValue(new Response('js'))
  await request(handlers, '/app/assets/index-abc.js')
  expect(put).toHaveBeenCalledTimes(1)
})

test('cached assets are served without touching the network', async () => {
  const { handlers, fetchFn } = load({ cached: { '/app/assets/a.js': new Response('cached-js') } })
  const res = await request(handlers, '/app/assets/a.js')
  expect(await res.text()).toBe('cached-js')
  expect(fetchFn).not.toHaveBeenCalled()
})

test.each([
  ['a failed asset response', '/app/assets/a.js', {}, new Response('x', { status: 404 })],
  ['a non-asset path', '/app/manifest.json', {}, new Response('{}')],
  ['a non-GET request', '/app/assets/a.js', { method: 'POST' }, new Response('x')],
  ['a cross-origin asset', '/app/assets/a.js', { origin: 'https://evil.test' }, new Response('x')],
])('%s is not cached', async (_n, path, init, response) => {
  const { handlers, fetchFn, put } = load()
  fetchFn.mockResolvedValue(response)
  await request(handlers, path, init)
  expect(put).not.toHaveBeenCalled()
})

test('offline asset miss returns 503', async () => {
  const { handlers, fetchFn } = load()
  fetchFn.mockRejectedValue(new Error('offline'))
  expect((await request(handlers, '/app/assets/a.js')).status).toBe(503)
})
