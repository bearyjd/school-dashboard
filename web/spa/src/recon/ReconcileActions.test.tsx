import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { clearReconToken } from './identity'
import { CONFIG, countsOf, fail, fakeServer, link, ok, renderRecon, ritem, sampleItems, signIn, TOKEN, type Reply } from './fixtures'
import { Reconcile } from './Reconcile'
import type { ReconItem } from './types'

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'], now: new Date('2026-11-18T15:00:00Z') })
  localStorage.clear()
  sessionStorage.clear()
  clearReconToken()
  signIn()
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

async function show(server = fakeServer(), route = '/') {
  const utils = renderRecon(<Reconcile />, route, server)
  await screen.findByRole('heading', { level: 2, name: 'Alpha' })
  return utils
}
const row = (title: string) => screen.getByRole('group', { name: title })
const btn = (name: RegExp | string) => screen.getByRole('button', { name })
const stage = (id: number) => `POST /api/v2/assignments/${id}/stage`
const waiting = (id: number): ReconItem => ({ ...sampleItems()[id - 1], stage: 2, stage_label: 'Done at home', flag: 'waiting_on_teacher', bucket: 'waiting_on_teacher' })
const written = (item: ReconItem): Reply => ok({ item, counts: countsOf([item]) })
const tileText = (name: RegExp) => within(screen.getByRole('region', { name: 'Summary' })).getByRole('button', { name }).textContent

test('buttons are real <button>s whose names include the assignment title', async () => {
  await show()
  const r = within(row('Essay draft'))
  expect(r.getByRole('button', { name: 'Mark Essay draft done' })).toBeTruthy()
  expect(r.getByRole('button', { name: 'Mark Essay draft turned in' })).toBeTruthy()
  expect(r.getByRole('button', { name: 'More for Essay draft' })).toBeTruthy()
  expect(r.getByRole('link', { name: 'Open Essay draft in portal' }).getAttribute('href')).toBe('https://portal.example.org/a/1')
})

test('row action sets follow stage, kind and bucket', async () => {
  await show()
  const names = (t: string) => within(row(t)).getAllByRole('button').map(b => b.textContent)
  expect(names('Essay draft')).toEqual(['Done', 'Turned in', 'More'])
  expect(names('Lab sheet')).toEqual(['Turned in', 'Undo', 'More'])
  expect(names('Poster')).toEqual(['Draft note', 'Undo', 'More'])
  expect(names('Reading log')).toEqual(['Draft note', 'Done', 'Turned in', 'More'])
  expect(names('Map quiz')).toEqual(['Draft note', 'Turned in', 'Undo', 'More'])
  expect(names('Pack gym bag')).toEqual(['Done'])
})

test('kid mode hides Draft note', async () => {
  localStorage.setItem('school-actor', JSON.stringify({ name: 'Sam', role: 'kid' }))
  await show()
  expect(screen.queryByRole('button', { name: /draft note/i })).toBeNull()
})

test('optimistic: a Turned in tap moves the row and updates the counts before the server answers', async () => {
  const server = fakeServer()
  let release: (r: Reply) => void = () => undefined
  server.on(stage(1), () => new Promise<Reply>(res => { release = res }))
  await show(server)
  expect(tileText(/waiting on teacher/i)).toBe('1Waiting on teacher')
  fireEvent.click(btn('Mark Essay draft turned in'))
  await waitFor(() => expect(tileText(/waiting on teacher/i)).toBe('2Waiting on teacher'))
  expect(tileText(/do tonight/i)).toBe('2Do tonight')
  const wait = screen.getByRole('heading', { level: 3, name: /^waiting on teacher \(2\)/i }).closest('section') as HTMLElement
  expect(within(wait).getByRole('group', { name: 'Essay draft' })).toBeTruthy()
  release(written({ ...waiting(1), stage: 3, stage_label: 'Turned in' }))
  await waitFor(() => expect(within(row('Essay draft')).getByText('Turned in')).toBeTruthy())
  expect(server.writes()).toHaveLength(1)
  expect(server.writes()[0].body).toEqual({ action: 'turned_in', actor: { role: 'parent', name: 'Dana' } })
  expect(server.writes()[0].headers['X-Recon-Token']).toBe(TOKEN)
})

test('the server answer replaces the optimistic row', async () => {
  const server = fakeServer()
  server.on(stage(1), () => written({ ...waiting(1), stage: 3, stage_label: 'Turned in (unconfirmed)', info: ['unconfirmed_paper'] }))
  await show(server)
  fireEvent.click(btn('Mark Essay draft turned in'))
  expect(await within(row('Essay draft')).findByText('Turned in (unconfirmed)')).toBeTruthy()
})

test('a success toast offers Undo, which sends the undo action', async () => {
  const server = fakeServer()
  server.on(stage(1), () => written(waiting(1)))
  await show(server)
  fireEvent.click(btn('Mark Essay draft done'))
  expect(await screen.findByText(/marked essay draft done/i)).toBeTruthy()
  server.on(stage(1), () => written(sampleItems()[0]))
  fireEvent.click(screen.getByRole('button', { name: /^undo$/i }))
  await waitFor(() => expect(server.writes()).toHaveLength(2))
  expect(server.writes()[1].body).toMatchObject({ action: 'undo' })
})

test('a task Done leaves the buckets; the server item (no bucket) is honoured', async () => {
  const server = fakeServer()
  server.on(stage(7), () => written({ ...sampleItems()[6], stage: 2, flag: null, bucket: null }))
  await show(server)
  fireEvent.click(btn('Mark Pack gym bag done'))
  await waitFor(() => expect(screen.queryByRole('group', { name: 'Pack gym bag' })).toBeNull())
})

const FAILURES: [string, Reply, string][] = [
  ['401', fail('bad_token', 401), 'Token not accepted - check Settings'],
  ['403', fail('forbidden_role', 403), 'Parents only'],
  ['409', fail('invalid_transition', 409), 'not allowed'],
  ['429', fail('too_many_attempts', 429, { 'Retry-After': '30' }), 'Too many attempts - wait 30 s'],
  ['501', fail('write_token_not_configured', 501), 'Writes are not set up on the server'],
  ['503 maintenance', fail('maintenance', 503), 'Paused for maintenance'],
  ['network', { reject: true }, 'Offline - not saved'],
]

test.each(FAILURES)('rollback and one inline message on %s', async (_n, reply, text) => {
  const server = fakeServer()
  server.on(stage(1), () => reply)
  await show(server)
  fireEvent.click(btn('Mark Essay draft turned in'))
  const msg = await within(row('Essay draft')).findByText(new RegExp(text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
  expect(msg.closest('[aria-live="polite"]')).not.toBeNull()
  // rolled back: still in Do tonight, counts restored
  const tonight = screen.getByRole('heading', { level: 3, name: /^do tonight \(1\)/i }).closest('section') as HTMLElement
  expect(within(tonight).getByRole('group', { name: 'Essay draft' })).toBeTruthy()
  expect(tileText(/waiting on teacher/i)).toBe('1Waiting on teacher')
  expect(server.writes()).toHaveLength(1)
})

test('503 busy retries once after Retry-After and then succeeds', async () => {
  const server = fakeServer()
  const replies = [fail('busy', 503, { 'Retry-After': '0' }), written(waiting(1))]
  server.on(stage(1), () => replies.shift())
  await show(server)
  fireEvent.click(btn('Mark Essay draft done'))
  await waitFor(() => expect(server.writes()).toHaveLength(2))
  expect(await screen.findByText(/marked essay draft done/i)).toBeTruthy()
  expect(screen.queryByText(/busy, try again/i)).toBeNull()
})

test('503 busy twice rolls back with "Busy, try again" after exactly one retry', async () => {
  const server = fakeServer()
  server.on(stage(1), () => fail('busy', 503, { 'Retry-After': '0' }))
  await show(server)
  fireEvent.click(btn('Mark Essay draft done'))
  expect(await within(row('Essay draft')).findByText('Busy, try again')).toBeTruthy()
  expect(server.writes()).toHaveLength(2)
})

test('no stored token with write_auth=token opens the setup sheet and calls nothing', async () => {
  clearReconToken()
  const server = fakeServer()
  await show(server)
  fireEvent.click(btn('Mark Essay draft done'))
  const dialog = await screen.findByRole('dialog', { name: /set up this device/i })
  expect(within(dialog).getByText(/write token/i)).toBeTruthy()
  expect(within(dialog).getByRole('link', { name: /settings/i }).getAttribute('href')).toBe('/settings')
  expect(server.writes()).toHaveLength(0)
})

test('no actor opens the setup sheet and calls nothing', async () => {
  localStorage.removeItem('school-actor')
  const server = fakeServer()
  await show(server)
  fireEvent.click(btn('Mark Essay draft done'))
  const dialog = await screen.findByRole('dialog', { name: /set up this device/i })
  expect(within(dialog).getByText(/your name/i)).toBeTruthy()
  expect(server.writes()).toHaveLength(0)
})

test('write_auth=unconfigured disables the actions and says why', async () => {
  const server = fakeServer({ config: { ...CONFIG, write_auth: 'unconfigured' } })
  await show(server)
  expect(screen.getByText(/writes are not set up on the server/i)).toBeTruthy()
  for (const b of within(row('Essay draft')).getAllByRole('button')) expect((b as HTMLButtonElement).disabled).toBe(true)
  expect(server.writes()).toHaveLength(0)
})

test('write_auth=open needs no token (and sends none) but still needs a name', async () => {
  clearReconToken()
  const server = fakeServer({ config: { ...CONFIG, write_auth: 'open' } })
  server.on(stage(1), () => written(waiting(1)))
  await show(server)
  fireEvent.click(btn('Mark Essay draft done'))
  await waitFor(() => expect(server.writes()).toHaveLength(1))
  expect('X-Recon-Token' in server.writes()[0].headers).toBe(false)
})

test('rows in Needs action: Draft note creates a draft and opens the note sheet', async () => {
  const server = fakeServer()
  server.on('POST /api/v2/assignments/3/note', () => ok({ id: 7, flag: 'disputed', subject: 'Alpha - Poster (English)', body: 'Hello,\nPlease check.', teacher_email: 't@example.org', mailto: 'mailto:t@example.org?subject=x', portal_url: 'https://portal.example.org/a/3' }, 201))
  await show(server)
  fireEvent.click(btn('Draft note for Poster'))
  const dialog = await screen.findByRole('dialog', { name: /draft note/i })
  expect(within(dialog).getByText('Nothing is sent automatically.')).toBeTruthy()
  expect(server.writes()[0].body).toEqual({ actor: { role: 'parent', name: 'Dana' } })
})

test('Draft note errors are mapped (409 no note for this flag)', async () => {
  const server = fakeServer()
  server.on('POST /api/v2/assignments/3/note', () => fail('no_note_for_flag', 409))
  await show(server)
  fireEvent.click(btn('Draft note for Poster'))
  expect(await within(row('Poster')).findByText(/no note/i)).toBeTruthy()
})

test('items with several links render one badge per link', async () => {
  const items = [ritem({ id: 1, links: [link({ id: 1, source: 'schoology', status: 'missing' }), link({ id: 2, source: 'powerschool', status: 'submitted', url: null })] })]
  await show(fakeServer({ items }))
  const r = within(row('Essay draft'))
  expect(r.getByText(/Schoology.*missing/i)).toBeTruthy()
  expect(r.getByText(/PowerSchool.*submitted/i)).toBeTruthy()
})
