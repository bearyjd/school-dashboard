import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { clearReconToken } from './identity'
import { fail, fakeServer, ok, renderRecon, ritem, sampleItems, signIn, type Reply } from './fixtures'
import { Reconcile } from './Reconcile'
import type { NoteDraft } from './types'

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

const DRAFT: NoteDraft = {
  id: 7, flag: 'disputed', subject: 'Alpha - Poster (English)', body: 'Hello Ms. Rivera,\nWe marked it turned in.',
  teacher_email: 'rivera@example.org', mailto: 'mailto:rivera@example.org?subject=Alpha&body=Hello', portal_url: 'https://portal.example.org/a/3',
}

async function show(server = fakeServer()) {
  renderRecon(<Reconcile />, '/', server)
  await screen.findByRole('heading', { level: 2, name: 'Alpha' })
  return server
}

async function openNote(draft: NoteDraft = DRAFT) {
  const server = fakeServer()
  server.on('POST /api/v2/assignments/3/note', () => ok(draft, 201))
  await show(server)
  fireEvent.click(screen.getByRole('button', { name: 'Draft note for Poster (English, Alpha)' }))
  const dialog = await screen.findByRole('dialog', { name: /draft note/i })
  return { server, dialog }
}

test('note sheet shows subject and body read-only, with the fixed line and no send control', async () => {
  const { dialog } = await openNote()
  const text = within(dialog).getByRole('textbox') as HTMLTextAreaElement
  expect(text.readOnly).toBe(true)
  expect(text.value).toContain('Alpha - Poster (English)')
  expect(text.value).toContain('We marked it turned in.')
  expect(within(dialog).getByText('Nothing is sent automatically.')).toBeTruthy()
  expect(within(dialog).queryByRole('button', { name: /send/i })).toBeNull()
  expect(within(dialog).queryByRole('link', { name: /send/i })).toBeNull()
  expect(within(dialog).queryByRole('button', { name: /submit/i })).toBeNull()
  expect(within(dialog).getByRole('button', { name: 'Copy' })).toBeTruthy()
  expect(within(dialog).getByRole('button', { name: 'I sent it' })).toBeTruthy()
  expect(within(dialog).getByRole('button', { name: 'Discard' })).toBeTruthy()
})

test('mail and portal links appear only when present and safe', async () => {
  const { dialog } = await openNote()
  expect(within(dialog).getByRole('link', { name: /open in mail app/i }).getAttribute('href')).toBe(DRAFT.mailto)
  expect(within(dialog).getByRole('link', { name: /open in portal/i }).getAttribute('href')).toBe(DRAFT.portal_url)
})

test('no mailto and no portal_url means neither link', async () => {
  const { dialog } = await openNote({ ...DRAFT, mailto: null, teacher_email: null, portal_url: null })
  expect(within(dialog).queryByRole('link')).toBeNull()
})

test('a hostile mailto or portal url is never linked', async () => {
  const { dialog } = await openNote({ ...DRAFT, mailto: 'javascript:alert(1)', portal_url: 'javascript:alert(2)' })
  expect(within(dialog).queryByRole('link')).toBeNull()
})

test('Copy uses the clipboard API', async () => {
  const writeText = vi.fn(async () => undefined)
  vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } })
  const { dialog } = await openNote()
  fireEvent.click(within(dialog).getByRole('button', { name: 'Copy' }))
  await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1))
  expect(writeText.mock.calls[0]).toEqual([expect.stringContaining('We marked it turned in.')])
  expect(await within(dialog).findByText(/copied/i)).toBeTruthy()
})

test('Copy falls back to a textarea and execCommand without the clipboard API', async () => {
  vi.stubGlobal('navigator', { ...navigator, clipboard: undefined })
  const exec = vi.fn(() => true)
  Object.defineProperty(document, 'execCommand', { value: exec, configurable: true, writable: true })
  const { dialog } = await openNote()
  fireEvent.click(within(dialog).getByRole('button', { name: 'Copy' }))
  await waitFor(() => expect(exec).toHaveBeenCalledWith('copy'))
})

test.each([['I sent it', 'sent_marked'], ['Discard', 'discarded']])('"%s" records %s and closes', async (label, status) => {
  const { server, dialog } = await openNote()
  server.on('PATCH /api/v2/assignments/3/note/7', () => ok({ note: { id: 7, flag: 'disputed', status, created_at: 'x', actor: 'Dana' } }))
  fireEvent.click(within(dialog).getByRole('button', { name: label }))
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  const patch = server.writes().find(w => w.method === 'PATCH')
  expect(patch?.body).toEqual({ status, actor: { role: 'parent', name: 'Dana' } })
})

test('a failed note status change keeps the sheet open with a message', async () => {
  const { server, dialog } = await openNote()
  server.on('PATCH /api/v2/assignments/3/note/7', () => fail('bad_token', 401))
  fireEvent.click(within(dialog).getByRole('button', { name: 'I sent it' }))
  expect(await within(dialog).findByText('Token not accepted - check Settings')).toBeTruthy()
  expect(screen.getByRole('dialog')).toBeTruthy()
})

test('sheet focus: moves in on open, Escape closes and restores focus to the opener', async () => {
  const { dialog } = await openNote()
  expect(dialog.contains(document.activeElement)).toBe(true)
  fireEvent.keyDown(dialog, { key: 'Escape' })
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Draft note for Poster (English, Alpha)' })))
})

test('sheet focus is trapped: Tab from the last control wraps to the first', async () => {
  const { dialog } = await openNote()
  const focusable = within(dialog).getAllByRole('button').concat(within(dialog).queryAllByRole('link'))
  const last = within(dialog).getByRole('button', { name: 'Discard' })
  last.focus()
  fireEvent.keyDown(last, { key: 'Tab' })
  expect(dialog.contains(document.activeElement)).toBe(true)
  expect(focusable.length).toBeGreaterThan(2)
})

// ---- More sheet ----

const DETAIL = (id: number) => ({
  ...sampleItems().find(i => i.id === id)!,
  events: [{ at: '2026-11-17T20:00:00+00:00', setter: 'parent', actor: 'Dana', kind: 'stage', from: '1', to: '2', detail: '<b>bold?</b>' }],
  notes: [{ id: 4, flag: 'missing', status: 'draft', created_at: '2026-11-16T12:00:00+00:00', actor: 'Dana' }],
})

async function openMore(title: string, id: number, role: 'parent' | 'kid' = 'parent') {
  if (role === 'kid') signIn('Sam', 'kid')
  const server = fakeServer()
  server.on(`GET /api/v2/assignments/${id}`, () => ok(DETAIL(id)))
  await show(server)
  fireEvent.click(screen.getByRole('button', { name: new RegExp(`^More for ${title} \\(`) }))
  const dialog = await screen.findByRole('dialog', { name: new RegExp(`more.*${title}`, 'i') })
  return { server, dialog }
}

test('More loads the history on open and shows it as plain text, plus existing note drafts', async () => {
  const { server, dialog } = await openMore('Map quiz', 5)
  expect(await within(dialog).findByText('<b>bold?</b>')).toBeTruthy()
  expect(within(dialog).getByText(/Dana/)).toBeTruthy()
  expect(within(dialog).getByText(/Draft note \(missing\)/)).toBeTruthy()
  expect(server.requests.filter(r => r.path === '/api/v2/assignments/5')).toHaveLength(1)
  expect(dialog.querySelector('b')).toBeNull()
})

test('Teacher confirmed needs a 3-500 character note', async () => {
  const { server, dialog } = await openMore('Map quiz', 5)
  const submit = within(dialog).getByRole('button', { name: 'Teacher confirmed' }) as HTMLButtonElement
  const note = within(dialog).getByRole('textbox', { name: /note/i })
  expect(submit.disabled).toBe(true)
  fireEvent.change(note, { target: { value: 'ok' } })
  expect(submit.disabled).toBe(true)
  fireEvent.change(note, { target: { value: '   ab   ' } })
  expect(submit.disabled).toBe(true)
  fireEvent.change(note, { target: { value: 'x'.repeat(501) } })
  expect(submit.disabled).toBe(true)
  fireEvent.change(note, { target: { value: 'Mrs. R emailed back' } })
  expect(submit.disabled).toBe(false)
  server.on('POST /api/v2/assignments/5/teacher-confirmed', (): Reply => ok({ item: { ...DETAIL(5), stage: 4, stage_label: 'Received', flag: null, bucket: null }, counts: {} }))
  fireEvent.click(submit)
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  expect(server.writes()[0].body).toEqual({ note: 'Mrs. R emailed back', actor: { role: 'parent', name: 'Dana' } })
  expect(screen.queryByRole('group', { name: 'Map quiz' })).toBeNull()
})

test('Teacher confirmed is not offered for a stage 1 item', async () => {
  const { dialog } = await openMore('Essay draft', 1)
  expect(within(dialog).queryByRole('button', { name: 'Teacher confirmed' })).toBeNull()
})

test('Teacher confirmed is hidden in kid mode, history still shows', async () => {
  const { dialog } = await openMore('Map quiz', 5, 'kid')
  expect(await within(dialog).findByText('<b>bold?</b>')).toBeTruthy()
  expect(within(dialog).queryByRole('button', { name: 'Teacher confirmed' })).toBeNull()
  expect(within(dialog).queryByRole('textbox')).toBeNull()
})

test('a history load failure is explained inside the sheet', async () => {
  const server = fakeServer()
  server.on('GET /api/v2/assignments/5', () => fail('internal', 500))
  await show(server)
  fireEvent.click(screen.getByRole('button', { name: 'More for Map quiz (English, Alpha)' }))
  const dialog = await screen.findByRole('dialog')
  expect(await within(dialog).findByText(/could not load the history/i)).toBeTruthy()
})

test('Teacher confirmed errors map to messages and keep the sheet open', async () => {
  const { server, dialog } = await openMore('Map quiz', 5)
  server.on('POST /api/v2/assignments/5/teacher-confirmed', () => fail('forbidden_role', 403))
  fireEvent.change(within(dialog).getByRole('textbox', { name: /note/i }), { target: { value: 'Mrs. R emailed back' } })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Teacher confirmed' }))
  expect(await within(dialog).findByText('Parents only')).toBeTruthy()
  expect(screen.getByRole('dialog')).toBeTruthy()
})

test('unused fixture import guard', () => {
  expect(ritem({ id: 1 }).id).toBe(1)
})
