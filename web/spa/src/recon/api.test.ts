import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { updateItem } from '../api'
import { clearReconToken, setReconToken } from './identity'
import { fakeServer, ok, TOKEN, ACTOR_KEY, ritem } from './fixtures'
import { getConfig, getList, getSummary, postStage, reconHeaders } from './api'

beforeEach(() => { clearReconToken(); localStorage.clear(); setReconToken(TOKEN) })
afterEach(() => vi.unstubAllGlobals())

test('token goes on /api/v2 non-GET requests only', () => {
  expect(reconHeaders('/api/v2/assignments/1/stage', 'POST')['X-Recon-Token']).toBe(TOKEN)
  expect(reconHeaders('/api/v2/assignments/1/note/3', 'PATCH')['X-Recon-Token']).toBe(TOKEN)
  expect(reconHeaders('/api/v2/summary', 'GET')['X-Recon-Token']).toBeUndefined()
  expect(reconHeaders('/api/v2/config', 'GET')['X-Recon-Token']).toBeUndefined()
  expect(reconHeaders('/api/items/1', 'PATCH')['X-Recon-Token']).toBeUndefined()
  expect(reconHeaders('/api/sync', 'POST')['X-Recon-Token']).toBeUndefined()
  expect(reconHeaders('https://elsewhere.example/api/v2/x', 'POST')['X-Recon-Token']).toBeUndefined()
  expect(reconHeaders('//elsewhere.example/api/v2/x', 'POST')['X-Recon-Token']).toBeUndefined()
})

test('no stored token means no header, not an empty one', () => {
  clearReconToken()
  expect('X-Recon-Token' in reconHeaders('/api/v2/assignments/1/stage', 'POST')).toBe(false)
})

test('real calls: GETs and v1 routes never carry the token; a v2 POST does, and never in the URL or body', async () => {
  const server = fakeServer({ items: [ritem({ id: 1 })] })
  server.on('POST /api/v2/assignments/1/stage', () => ok({ item: ritem({ id: 1, stage: 2 }), counts: {} }))
  server.on('PATCH /api/items/1', () => ({ status: 200, body: { ok: true } }))
  vi.stubGlobal('fetch', server.fetchMock)
  await getConfig()
  await getSummary()
  await getList()
  await updateItem(1, { title: 'x' })
  await postStage(1, 'done', { role: 'parent', name: 'Dana' })
  for (const r of server.requests) {
    const isV2Write = r.path.startsWith('/api/v2/') && r.method !== 'GET'
    expect(r.headers['X-Recon-Token']).toBe(isV2Write ? TOKEN : undefined)
    expect(r.url).not.toContain(TOKEN)
    expect(JSON.stringify(r.body)).not.toContain(TOKEN)
  }
  expect(server.writes()).toHaveLength(2)
})

test('stage body is {action, actor:{role,name}} as JSON', async () => {
  const server = fakeServer()
  server.on('POST /api/v2/assignments/5/stage', () => ok({ item: ritem({ id: 5 }), counts: {} }))
  vi.stubGlobal('fetch', server.fetchMock)
  await postStage(5, 'turned_in', { role: 'kid', name: 'Sam' })
  const w = server.writes()[0]
  expect(w.body).toEqual({ action: 'turned_in', actor: { role: 'kid', name: 'Sam' } })
  expect(w.headers['Content-Type']).toBe('application/json')
})

test('the token is never logged', async () => {
  const spies = (['log', 'warn', 'error', 'info', 'debug'] as const).map(m => vi.spyOn(console, m).mockImplementation(() => undefined))
  const server = fakeServer()
  server.on('POST /api/v2/assignments/5/stage', () => ({ status: 401, body: { ok: false, data: null, error: 'bad_token', meta: {} } }))
  vi.stubGlobal('fetch', server.fetchMock)
  await postStage(5, 'done', { role: 'parent', name: 'Dana' }).catch(() => undefined)
  for (const s of spies) expect(JSON.stringify(s.mock.calls)).not.toContain(TOKEN)
  expect(localStorage.getItem(ACTOR_KEY)).toBeNull()
})
