// @vitest-environment node
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import { expect, test, vi } from 'vitest'

const source = readFileSync(new URL('../public/sw.js', import.meta.url), 'utf8')

type Handler = (e: Record<string, unknown>) => void

function load(cacheKeys: string[]) {
  const handlers: Record<string, Handler> = {}
  const deleted: string[] = []
  const caches = {
    keys: async () => cacheKeys,
    delete: async (k: string) => { deleted.push(k); return true },
    open: async () => ({ addAll: async () => undefined, put: async () => undefined }),
    match: async () => undefined,
  }
  const fetchFn = vi.fn()
  const self = {
    addEventListener: (t: string, h: Handler) => { handlers[t] = h },
    skipWaiting: async () => undefined,
    clients: { claim: async () => undefined },
  }
  vm.runInNewContext(source, { self, caches, fetch: fetchFn, URL, Response })
  return { handlers, deleted, fetchFn }
}

test('cache name is school-v2', () => {
  expect(source).toMatch(/const CACHE = 'school-v2';/)
})

test('activate deletes every other cache and keeps the current one', async () => {
  const { handlers, deleted } = load(['school-v1', 'school-v2', 'other'])
  let pending: Promise<unknown> = Promise.resolve()
  handlers.activate({ waitUntil: (p: Promise<unknown>) => { pending = p } })
  await pending
  expect(deleted.sort()).toEqual(['other', 'school-v1'])
})

test('navigations go to the network first so a deploy is not masked by a stale shell', async () => {
  const { handlers, fetchFn } = load([])
  fetchFn.mockResolvedValue(new Response('fresh'))
  let out: Promise<Response> | undefined
  handlers.fetch({
    request: { url: 'https://x.test/app/', mode: 'navigate' },
    respondWith: (p: Promise<Response>) => { out = p },
  })
  expect(await (await out!).text()).toBe('fresh')
  expect(fetchFn).toHaveBeenCalled()
})
