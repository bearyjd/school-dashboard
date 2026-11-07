// @vitest-environment node
import { readFileSync } from 'node:fs'
import { describe, expect, test } from 'vitest'

const css = readFileSync(new URL('./tokens.css', import.meta.url), 'utf8')

function block(theme: string): Record<string, string> {
  const m = css.match(new RegExp(`\\[data-theme=${theme}\\]\\s*\\{([^}]*)\\}`))
  if (!m) throw new Error(`no block for ${theme}`)
  const out: Record<string, string> = {}
  for (const d of m[1].matchAll(/--([\w-]+)\s*:\s*([^;]+);/g)) out[d[1]] = d[2].trim().toLowerCase()
  return out
}

function lum(hex: string): number {
  const c = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map(x => (x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4))
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
}
function contrast(a: string, b: string): number {
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

const EXACT = {
  midnight: {
    bg: '#0d1220', surface: '#151c31', border: '#232c4a', text: '#e8ecf7', muted: '#8c97b5', selected: '#2b3a73',
    overdue: '#ff6b6b', missing: '#ffb454', tonight: '#7aa2ff', ok: '#27c28a',
    'kid-0': '#4d7cff', 'kid-1': '#27c28a', 'kid-2': '#b07cff',
  },
  daylight: {
    bg: '#faf7f2', surface: '#ffffff', border: '#e9e3d8', text: '#1f2430', muted: '#5b6070',
    overdue: '#c8402f', missing: '#b9770e', tonight: '#2a55c8',
    'kid-0': '#2a55c8', 'kid-1': '#1c8a63', 'kid-2': '#8f4fc9', hero: '#1f2430',
  },
} as const

describe.each(Object.entries(EXACT))('%s tokens', (theme, expected) => {
  const t = block(theme)
  test('exact spec hex values', () => {
    for (const [k, v] of Object.entries(expected)) expect(t[k], k).toBe(v)
  })
  test('text and semantic text colours reach 4.5:1 on surface and bg', () => {
    for (const bg of [t.surface, t.bg]) {
      for (const k of ['text', 'muted', 'overdue-text', 'missing-text', 'tonight-text', 'ok-text']) {
        expect(t[k], `${k} defined`).toMatch(/^#[0-9a-f]{6}$/)
        expect(contrast(t[k], bg), `${theme} ${k} on ${bg}`).toBeGreaterThanOrEqual(4.5)
      }
    }
  })
  test('semantic and kid fills reach 3:1 on surface (non-text)', () => {
    for (const k of ['overdue', 'missing', 'tonight', 'ok', 'kid-0', 'kid-1', 'kid-2']) {
      expect(contrast(t[k], t.surface), `${theme} ${k}`).toBeGreaterThanOrEqual(3)
    }
  })
})

test('manifest and theme-color use the Midnight background', () => {
  const manifest = JSON.parse(readFileSync(new URL('../../public/manifest.json', import.meta.url), 'utf8'))
  const html = readFileSync(new URL('../../index.html', import.meta.url), 'utf8')
  const bg = block('midnight').bg
  expect(manifest.background_color.toLowerCase()).toBe(bg)
  expect(manifest.theme_color.toLowerCase()).toBe(bg)
  expect(html).toContain(`name="theme-color" content="${bg}"`)
})
