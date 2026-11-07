import { beforeEach, describe, expect, test } from 'vitest'
import { getStoredTheme, resolveTheme, setTheme } from './theme'

describe('resolveTheme', () => {
  test('system follows the device', () => {
    expect(resolveTheme('system', true)).toBe('midnight')
    expect(resolveTheme('system', false)).toBe('daylight')
  })
  test('explicit choices win', () => {
    expect(resolveTheme('midnight', false)).toBe('midnight')
    expect(resolveTheme('daylight', true)).toBe('daylight')
  })
})

describe('stored theme', () => {
  beforeEach(() => {
    localStorage.clear()
    delete document.documentElement.dataset.theme
  })
  test('defaults to midnight when empty', () => {
    expect(getStoredTheme()).toBe('midnight')
  })
  test('defaults to midnight on junk', () => {
    localStorage.setItem('school-theme', '<script>')
    expect(getStoredTheme()).toBe('midnight')
  })
  test('setTheme persists and applies', () => {
    setTheme('daylight')
    expect(localStorage.getItem('school-theme')).toBe('daylight')
    expect(document.documentElement.dataset.theme).toBe('daylight')
    expect(getStoredTheme()).toBe('daylight')
  })
  test('system resolves via matchMedia', () => {
    window.matchMedia = ((q: string) => ({ matches: q.includes('dark') })) as typeof window.matchMedia
    setTheme('system')
    expect(document.documentElement.dataset.theme).toBe('midnight')
    expect(getStoredTheme()).toBe('system')
  })
})
