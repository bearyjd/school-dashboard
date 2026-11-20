import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { FIXTURE, fresh } from '../test-utils'
import { clearReconToken } from './identity'
import { CONFIG, fakeServer, link, ok, renderRecon, ritem, sampleItems, signIn, summaryOf, TOKEN } from './fixtures'
import { Reconcile } from './Reconcile'

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'], now: new Date('2026-11-18T15:00:00Z') })
  localStorage.clear()
  sessionStorage.clear()
  clearReconToken()
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

async function show(server = fakeServer(), route = '/', first = 'Alpha') {
  const utils = renderRecon(<Reconcile />, route, server)
  await screen.findByRole('heading', { level: 2, name: first })
  return utils
}
const tileText = (name: RegExp) => within(screen.getByRole('region', { name: 'Summary' })).getByRole('button', { name }).textContent

test('F1: the kid empty state is the server verdict', async () => {
  const items = sampleItems()
  const s = summaryOf(items, { unverified_sources: ['schoology'] })
  const kids = s.kids.map(k => (k.name === 'Gamma' ? { ...k, all_clear: true, all_clear_reason: null } : k))
  await show(fakeServer({ items, summary: { kids } }))
  const gamma = screen.getByRole('heading', { level: 2, name: 'Gamma' }).closest('section') as HTMLElement
  expect(within(gamma).getByText('All clear')).toBeTruthy()
  expect(within(gamma).queryByText(/not verified/i)).toBeNull()
})

test('F2: tiles show the server counts, all kids and one kid', async () => {
  const items = sampleItems()
  const s = summaryOf(items)
  const kids = s.kids.map(k => (k.name === 'Beta' ? { ...k, counts: { ...k.counts, needs_action: 4, do_tonight: 6 } } : k))
  const { unmount } = await show(fakeServer({ items, summary: { kids } }))
  expect(tileText(/needs action/i)).toBe('7Needs action')
  unmount()
  await show(fakeServer({ items, summary: { kids } }), '/?kid=Beta', 'Beta')
  expect(tileText(/needs action/i)).toBe('4Needs action')
  expect(tileText(/do tonight/i)).toBe('6Do tonight')
})

test('F2: a truncated list says so; a complete one does not', async () => {
  const items = sampleItems()
  const server = fakeServer({ items })
  server.on('GET /api/v2/assignments', () => ({ status: 200, body: { ok: true, data: items, error: null, meta: { count: items.length, total: 600, limit: 500, offset: 0 } } }))
  await show(server)
  expect(screen.getByText(`Showing the first ${items.length} of 600 items`)).toBeTruthy()
})

test('F2: no truncation banner for a complete list', async () => {
  await show()
  expect(screen.queryByText(/showing the first/i)).toBeNull()
})

test('F3 and F4: one IXL prefix; placeholder (none) links are hidden unless alone', async () => {
  const items = [
    ritem({ id: 1, title: 'Two links', links: [link({ id: 1, source: 'schoology', status: 'assigned' }), link({ id: 2, source: 'powerschool', status: 'none', url: null })] }),
    ritem({ id: 2, title: 'Only link', links: [link({ id: 3, source: 'ixl', status: 'none', url: null })] }),
    ritem({ id: 3, title: 'All none', links: [link({ id: 4, source: 'gc', status: 'none', url: null }), link({ id: 5, source: 'ixl', status: 'none', url: null })] }),
  ]
  await show(fakeServer({ items }))
  const r = (t: string) => within(screen.getByRole('group', { name: t }))
  expect(r('Two links').queryByText(/PowerSchool/)).toBeNull()
  expect(r('Two links').getByText(/Schoology/)).toBeTruthy()
  expect(r('Only link').getByText(/IXL: none/)).toBeTruthy()
  expect(r('All none').queryByText(/: none/)).toBeNull()
})

test('F5: a stale source gets one merged banner, and "Data as of" stays', async () => {
  const server = fakeServer({ summary: { unverified_sources: ['ixl'] } })
  server.on('GET /api/overview', () => ({ status: 200, body: { ...FIXTURE, freshness: { ...FIXTURE.freshness, ixl: fresh({ stale: true, age_hours: 20 }) } } }))
  await show(server)
  expect(await screen.findByText('Not verified: IXL data is out of date (last good 20h ago)')).toBeTruthy()
  expect(screen.queryByText(/Stale, as of/)).toBeNull()
  expect(screen.getAllByText(/IXL/, { selector: 'span, strong' }).filter(e => e.closest('.banner'))).toHaveLength(1)
  expect(screen.getByText(/Data as of/)).toBeTruthy()
})

test('F7: action names are unique for same-titled rows (class, and kid in the all-kids view)', async () => {
  const items = [
    ritem({ id: 1, title: 'Reading log', class: 'English', flag: 'missing', bucket: 'needs_action' }),
    ritem({ id: 2, title: 'Reading log', class: 'Math', flag: 'missing', bucket: 'needs_action' }),
    ritem({ id: 3, kid: 'Beta', title: 'Reading log', class: 'English' }),
  ]
  const { unmount } = await show(fakeServer({ items }))
  expect(screen.getByRole('button', { name: 'Mark Reading log (English, Alpha) done' })).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Mark Reading log (Math, Alpha) done' })).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Mark Reading log (English, Beta) done' })).toBeTruthy()
  unmount()
  await show(fakeServer({ items }), '/?kid=Alpha')
  expect(screen.getByRole('button', { name: 'Mark Reading log (English) done' })).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Mark Reading log (Math) done' })).toBeTruthy()
})

test('F8: the write uses the configured token header name and redirect: error', async () => {
  signIn()
  const config = { ...CONFIG, write_token_header: 'X-Other-Token' }
  const server = fakeServer({ config })
  const item = sampleItems()[0]
  server.on('POST /api/v2/assignments/1/stage', () => ok({ item: { ...item, stage: 2, flag: 'waiting_on_teacher', bucket: 'waiting_on_teacher' }, counts: summaryOf(sampleItems()).kids[0].counts }))
  await show(server)
  fireEvent.click(screen.getByRole('button', { name: 'Mark Essay draft (English, Alpha) done' }))
  await waitFor(() => expect(server.writes()).toHaveLength(1))
  expect(server.writes()[0].headers['X-Other-Token']).toBe(TOKEN)
  expect(server.writes()[0].headers['X-Recon-Token']).toBeUndefined()
  expect(server.writes()[0].redirect).toBe('error')
})

test('F9: reopening a closed draft shows the same note and creates no second one', async () => {
  signIn()
  const draft = { id: 7, flag: 'disputed', subject: 'Alpha - Poster', body: 'Hello', teacher_email: null, mailto: null, portal_url: null }
  const server = fakeServer()
  const replies = [ok(draft, 201), ok(draft, 200)]
  server.on('POST /api/v2/assignments/3/note', () => replies.shift())
  await show(server)
  const open = () => fireEvent.click(screen.getByRole('button', { name: 'Draft note for Poster (English, Alpha)' }))
  open()
  let dialog = await screen.findByRole('dialog', { name: /draft note/i })
  const first = (within(dialog).getByLabelText('Draft note text') as HTMLTextAreaElement).value
  fireEvent.keyDown(dialog, { key: 'Escape' })
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  open()
  dialog = await screen.findByRole('dialog', { name: /draft note/i })
  expect((within(dialog).getByLabelText('Draft note text') as HTMLTextAreaElement).value).toBe(first)
  expect(server.writes().map(w => w.method)).toEqual(['POST', 'POST'])
})

test('F10: a failed tap puts the row back in its original bucket', async () => {
  signIn()
  const server = fakeServer()
  server.on('POST /api/v2/assignments/1/stage', () => ({ status: 401, body: { ok: false, data: null, error: 'bad_token', meta: {} } }))
  await show(server)
  // The refetch after the failure must not be what puts the row back: keep it pending.
  server.on('GET /api/v2/assignments', () => new Promise(() => undefined))
  fireEvent.click(screen.getByRole('button', { name: 'Mark Essay draft (English, Alpha) turned in' }))
  await within(screen.getByRole('group', { name: 'Essay draft' })).findByText('Token not accepted - check Settings')
  const section = screen.getByRole('group', { name: 'Essay draft' }).closest('[data-bucket]')
  expect(section?.getAttribute('data-bucket')).toBe('do_tonight')
})
