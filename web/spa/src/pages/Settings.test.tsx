import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { watchSystemTheme } from '../design/theme'
import { FIXTURE } from '../test-utils'
import { Settings } from './Settings'

function fakeMedia(initial: boolean) {
  let listener: (() => void) | null = null
  const mql = {
    matches: initial,
    addEventListener: (_: string, l: () => void) => { listener = l },
    removeEventListener: () => { listener = null },
  }
  window.matchMedia = (() => mql) as unknown as typeof window.matchMedia
  return { mql, fire: () => listener?.() }
}

function renderSettings() {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, json: async () => FIXTURE })))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/settings']}>
        <Routes>
          <Route path="/settings" element={<Settings />} />
          <Route path="/classic/home" element={<h1>Classic home</h1>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

beforeEach(() => {
  localStorage.clear()
  delete document.documentElement.dataset.theme
})

test('theme toggle applies and persists', () => {
  fakeMedia(true)
  renderSettings()
  expect((screen.getByRole('radio', { name: /midnight/i }) as HTMLInputElement).checked).toBe(true)
  fireEvent.click(screen.getByRole('radio', { name: /daylight/i }))
  expect(document.documentElement.dataset.theme).toBe('daylight')
  expect(localStorage.getItem('school-theme')).toBe('daylight')
  expect((screen.getByRole('radio', { name: /daylight/i }) as HTMLInputElement).checked).toBe(true)
})

test('system follows matchMedia', () => {
  const m = fakeMedia(false)
  const stop = watchSystemTheme() // main.tsx does this once for the whole app
  renderSettings()
  fireEvent.click(screen.getByRole('radio', { name: /system/i }))
  expect(localStorage.getItem('school-theme')).toBe('system')
  expect(document.documentElement.dataset.theme).toBe('daylight')
  m.mql.matches = true
  m.fire()
  expect(document.documentElement.dataset.theme).toBe('midnight')
  stop()
})

test('classic link navigates to /classic/home', () => {
  fakeMedia(true)
  renderSettings()
  fireEvent.click(screen.getByRole('link', { name: /classic view/i }))
  expect(screen.getByRole('heading', { name: 'Classic home' })).toBeTruthy()
})

test('kid colour legend lists the roster in colour order, with names as text', async () => {
  fakeMedia(true)
  renderSettings()
  const legend = await screen.findByRole('list', { name: /kid colours/i })
  const rows = within(legend).getAllByRole('listitem')
  expect(rows.map(r => r.textContent)).toEqual(['AAlpha (colour 1)', 'BBeta (colour 2)'])
})

test('sync token control: labelled password input, save and clear', () => {
  fakeMedia(true)
  renderSettings()
  const input = screen.getByLabelText('Sync token') as HTMLInputElement
  expect(input.type).toBe('password')
  const save = screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement
  const clear = screen.getByRole('button', { name: 'Clear' }) as HTMLButtonElement
  expect(save.disabled).toBe(true)
  expect(clear.disabled).toBe(true)

  fireEvent.change(input, { target: { value: ' TESTTOKEN123 ' } })
  fireEvent.click(save)
  expect(localStorage.getItem('school-sync-token')).toBe('TESTTOKEN123')
  expect(input.value).toBe('')
  expect(screen.getByRole('status').textContent).toBe('Sync token saved on this device')
  expect(document.body.textContent).not.toContain('TESTTOKEN123')
  expect(clear.disabled).toBe(false)

  fireEvent.click(clear)
  expect(localStorage.getItem('school-sync-token')).toBeNull()
  expect(screen.getByRole('status').textContent).toBe('Sync token cleared')
})

test('sync token control: save failure is announced as an alert', () => {
  fakeMedia(true)
  renderSettings()
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('denied') })
  fireEvent.change(screen.getByLabelText('Sync token'), { target: { value: 'abc' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save' }))
  expect(screen.getByRole('alert').textContent).toMatch(/could not save/i)
  vi.restoreAllMocks()
})
