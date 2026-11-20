import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { Settings } from '../pages/Settings'
import { FIXTURE } from '../test-utils'
import { ACTOR_KEY, fakeServer, TOKEN, TOKEN_KEY } from './fixtures'
import { clearReconToken, getActor, getReconToken, maskedToken } from './identity'

beforeEach(() => { localStorage.clear(); sessionStorage.clear(); clearReconToken() })
afterEach(() => vi.unstubAllGlobals())

function renderSettings() {
  const server = fakeServer()
  server.on('GET /api/overview', () => ({ status: 200, body: FIXTURE }))
  vi.stubGlobal('fetch', server.fetchMock)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><MemoryRouter><Settings /></MemoryRouter></QueryClientProvider>)
}

const section = () => screen.getByRole('region', { name: 'Assignments' })

test('has an Assignments section with the name, role and token fields', () => {
  renderSettings()
  expect(section()).toBeTruthy()
  expect(screen.getByLabelText(/who is using this device/i)).toBeTruthy()
  expect(screen.getByRole('radio', { name: /parent/i })).toBeTruthy()
  expect(screen.getByRole('radio', { name: /kid/i })).toBeTruthy()
  expect((screen.getByLabelText(/write token/i) as HTMLInputElement).type).toBe('password')
})

test('saving the name and role stores {name, role} under school-actor', () => {
  renderSettings()
  fireEvent.change(screen.getByLabelText(/who is using this device/i), { target: { value: '  Dana  ' } })
  fireEvent.click(screen.getByRole('radio', { name: /parent/i }))
  fireEvent.click(screen.getByRole('button', { name: /save name/i }))
  expect(JSON.parse(localStorage.getItem(ACTOR_KEY) ?? 'null')).toEqual({ name: 'Dana', role: 'parent' })
  expect(getActor()).toEqual({ name: 'Dana', role: 'parent' })
})

test.each([['', /1-40/], ['x'.repeat(41), /1-40/]])('name %j is rejected', (name, msg) => {
  renderSettings()
  fireEvent.change(screen.getByLabelText(/who is using this device/i), { target: { value: name } })
  fireEvent.click(screen.getByRole('radio', { name: /kid/i }))
  fireEvent.click(screen.getByRole('button', { name: /save name/i }))
  expect(screen.getByText(msg)).toBeTruthy()
  expect(localStorage.getItem(ACTOR_KEY)).toBeNull()
})

test('a name of exactly 40 characters is accepted', () => {
  renderSettings()
  fireEvent.change(screen.getByLabelText(/who is using this device/i), { target: { value: 'n'.repeat(40) } })
  fireEvent.click(screen.getByRole('radio', { name: /kid/i }))
  fireEvent.click(screen.getByRole('button', { name: /save name/i }))
  expect(getActor()?.role).toBe('kid')
})

test('the role must be chosen', () => {
  renderSettings()
  fireEvent.change(screen.getByLabelText(/who is using this device/i), { target: { value: 'Dana' } })
  fireEvent.click(screen.getByRole('button', { name: /save name/i }))
  expect(screen.getByText(/choose parent or kid/i)).toBeTruthy()
  expect(localStorage.getItem(ACTOR_KEY)).toBeNull()
})

test('the write token is stored under school-recon-token and never shown back', () => {
  renderSettings()
  const input = screen.getByLabelText(/write token/i) as HTMLInputElement
  fireEvent.change(input, { target: { value: `  ${TOKEN}  ` } })
  fireEvent.click(screen.getByRole('button', { name: /save token/i }))
  expect(localStorage.getItem(TOKEN_KEY)).toBe(TOKEN)
  expect(getReconToken()).toBe(TOKEN)
  expect(input.value).toBe('')
  expect(document.body.textContent).not.toContain(TOKEN)
  expect(screen.queryByDisplayValue(TOKEN)).toBeNull()
  expect(section().textContent).toMatch(/saved on this device/i)
  expect(maskedToken()).not.toContain('T')
})

test('a short token is rejected with the minimum length', () => {
  renderSettings()
  fireEvent.change(screen.getByLabelText(/write token/i), { target: { value: 'short' } })
  fireEvent.click(screen.getByRole('button', { name: /save token/i }))
  expect(screen.getByText(/at least 32/i)).toBeTruthy()
  expect(localStorage.getItem(TOKEN_KEY)).toBeNull()
})

test('Clear removes the token', () => {
  localStorage.setItem(TOKEN_KEY, TOKEN)
  renderSettings()
  fireEvent.click(screen.getByRole('button', { name: /clear token/i }))
  expect(localStorage.getItem(TOKEN_KEY)).toBeNull()
  expect(getReconToken()).toBe('')
})

test('storage that throws falls back to memory for this tab without crashing', () => {
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('denied') })
  renderSettings()
  fireEvent.change(screen.getByLabelText(/write token/i), { target: { value: TOKEN } })
  fireEvent.click(screen.getByRole('button', { name: /save token/i }))
  expect(getReconToken()).toBe(TOKEN)
  expect(section().textContent).toMatch(/until you close this tab/i)
  vi.restoreAllMocks()
})

test('the existing sync token section and theme are untouched', () => {
  renderSettings()
  expect(screen.getByLabelText('Sync token')).toBeTruthy()
  expect(screen.getByRole('radio', { name: /midnight/i })).toBeTruthy()
})
