import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, expect, test } from 'vitest'
import { watchSystemTheme } from '../design/theme'
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
  return render(
    <MemoryRouter initialEntries={['/settings']}>
      <Routes>
        <Route path="/settings" element={<Settings />} />
        <Route path="/classic/home" element={<h1>Classic home</h1>} />
      </Routes>
    </MemoryRouter>,
  )
}

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
