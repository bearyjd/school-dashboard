import { afterEach, expect, test, vi } from 'vitest'
import { ApiError } from '../api'
import { postNote, reconHeaders, setWriteTokenHeader } from './api'
import { messageFor } from './errors'
import { CONFIG, fakeServer, link, ok, ritem, sampleItems, summaryOf, TOKEN } from './fixtures'
import { buildKidViews, ixlLine } from './group'
import { clearReconToken, setReconToken } from './identity'
import { safeMailto } from './safeUrl'

afterEach(() => { vi.unstubAllGlobals(); clearReconToken(); setWriteTokenHeader('X-Recon-Token') })

const withGamma = (all_clear: boolean, reason: 'unverified' | null, over = {}) => {
  const items = sampleItems()
  const s = summaryOf(items, over)
  return { items, summary: { ...s, kids: s.kids.map(k => (k.name === 'Gamma' ? { ...k, all_clear, all_clear_reason: reason } : k)) } }
}
const gammaState = ({ items, summary }: ReturnType<typeof withGamma>) =>
  buildKidViews({ config: CONFIG, summary, items, kid: '' })[2].state

test('F1: the empty state follows the server verdict, not global stale sources', () => {
  expect(gammaState(withGamma(true, null, { unverified_sources: ['schoology'], ingest_ok: false }))).toBe('clear')
  expect(gammaState(withGamma(false, 'unverified'))).toBe('unverified_clear')
  expect(gammaState(withGamma(true, null))).toBe('clear')
})

const practice = (id: number, title: string, raw: string | null, over = {}) =>
  ritem({ id, kind: 'practice', title, class: null, flag: 'do_tonight', bucket: 'do_tonight',
    links: [link({ id, source: 'ixl', raw_status: raw, url: null })], ...over })

test('F3: no doubled prefix, alphabetical, zero rows omitted, unknown shown', () => {
  expect(ixlLine([practice(1, 'IXL Math', '3 remaining'), practice(2, 'IXL ELA', '2 remaining')]))
    .toBe('IXL: ELA 2 remaining, Math 3 remaining')
  expect(ixlLine([practice(1, 'IXL Math', '0 remaining'), practice(2, 'IXL ELA', '2 remaining')])).toBe('IXL: ELA 2 remaining')
  expect(ixlLine([practice(1, 'IXL Math', 'unknown'), practice(2, 'IXL ELA', '2 remaining')]))
    .toBe('IXL: ELA 2 remaining, Math status unknown')
  expect(ixlLine([practice(1, 'IXL Math', '0 remaining')])).toBe('IXL: nothing remaining')
  expect(ixlLine([practice(1, 'Math', null, { class: 'Math' })])).toBe('IXL: Math status unknown')
})

test('F2: with a complete list the bucket headings equal the server counts', () => {
  const items = sampleItems()
  const summary = summaryOf(items)
  for (const v of buildKidViews({ config: CONFIG, summary, items, kid: '' })) {
    const server = summary.kids.find(k => k.name === v.name)?.counts
    for (const b of v.buckets) expect(b.count).toBe(server?.[b.id])
  }
})

test('F2: kid counts are the server counts', () => {
  const items = sampleItems()
  const s = summaryOf(items)
  const summary = { ...s, kids: s.kids.map(k => (k.name === 'Alpha' ? { ...k, counts: { ...k.counts, needs_action: 9 } } : k)) }
  expect(buildKidViews({ config: CONFIG, summary, items, kid: '' })[0].counts.needs_action).toBe(9)
})

test('F8: the token header name comes from the config, with a safe fallback', () => {
  setReconToken(TOKEN)
  setWriteTokenHeader('X-Custom-Token')
  expect(reconHeaders('/api/v2/x/stage', 'POST')['X-Custom-Token']).toBe(TOKEN)
  expect(reconHeaders('/api/v2/x/stage', 'POST')['X-Recon-Token']).toBeUndefined()
  setWriteTokenHeader('bad name\r\nX: y')
  expect(reconHeaders('/api/v2/x/stage', 'POST')['X-Recon-Token']).toBe(TOKEN)
})

test('F8: v2 writes use redirect: error, reads do not need it', async () => {
  const server = fakeServer()
  server.on('POST /api/v2/assignments/3/note', () => ok({ id: 1 }, 200))
  vi.stubGlobal('fetch', server.fetchMock)
  await postNote(3, { role: 'parent', name: 'Dana' })
  expect(server.writes()[0].redirect).toBe('error')
})

test('F8: a note POST answering 200 (existing draft) is accepted like 201', async () => {
  const draft = { id: 7, flag: 'missing', subject: 's', body: 'b', teacher_email: null, mailto: null, portal_url: null }
  const server = fakeServer()
  server.on('POST /api/v2/assignments/3/note', () => ok(draft, 200))
  vi.stubGlobal('fetch', server.fetchMock)
  expect(await postNote(3, { role: 'parent', name: 'Dana' })).toEqual(draft)
})

test('F8: server codes map to plain words', () => {
  expect(messageFor(new ApiError('invalid_transition', 409))).toBe('Already handled or no longer possible')
  expect(messageFor(new ApiError('invalid_field', 400))).toBe('That did not look right')
  expect(messageFor(new ApiError('anything', 400))).toBe('That did not look right')
})

test('F10: safeMailto rejects control characters', () => {
  expect(safeMailto('mailto:a@b.c\r\nBcc: x')).toBeNull()
  expect(safeMailto('mailto:a@b.c\u0007')).toBeNull()
  expect(safeMailto('mailto:a@b.c?subject=x')).toBe('mailto:a@b.c?subject=x')
})

test('F2: an optimistic row move shifts the kid counts and a rollback restores them', async () => {
  const { shiftKidCounts } = await import('./optimistic')
  const items = sampleItems()
  const s = summaryOf(items)
  const from = items[0]
  const to = { ...from, flag: 'disputed' as const, bucket: 'needs_action' as const }
  const moved = shiftKidCounts(s, from, to)
  expect(moved.kids[0].counts).toMatchObject({ do_tonight: 0, needs_action: 4 })
  expect(moved.kids[0].counts.needs_action_detail.disputed).toBe(2)
  expect(shiftKidCounts(moved, to, from).kids[0].counts).toEqual(s.kids[0].counts)
  expect(s.kids[0].counts.needs_action).toBe(3)
})
