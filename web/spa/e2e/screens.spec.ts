import { mkdirSync } from 'node:fs'
import { expect, test } from '@playwright/test'

import { ROUTES, THEMES } from './shared'

const WIDTHS = [320, 375, 768, 1024, 1440] as const
const SHOTS = process.env.E2E_SHOTS ?? 'test-results/shots'

for (const theme of THEMES) {
  for (const width of WIDTHS) {
    test.describe(`${theme} @ ${width}px`, () => {
      test.use({ viewport: { width, height: 900 } })

      test.beforeEach(async ({ page }) => {
        await page.addInitScript(t => localStorage.setItem('school-theme', t), theme)
      })

      for (const route of ROUTES) {
        test(`/${route || 'today'}`, async ({ page }) => {
          const problems: string[] = []
          page.on('console', m => { if (m.type() === 'error') problems.push(`console: ${m.text()}`) })
          page.on('pageerror', e => problems.push(`pageerror: ${e.message}`))
          page.on('requestfailed', r => problems.push(`failed: ${r.url()}`))
          page.on('response', r => { if (r.status() >= 400) problems.push(`${r.status()}: ${r.url()}`) })

          await page.goto(`/app/${route}`, { waitUntil: 'networkidle' })
          await expect(page.locator('h1')).toBeVisible()
          expect(await page.locator('html').getAttribute('data-theme')).toBe(theme)

          const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
          expect(overflow, 'horizontal overflow (px)').toBeLessThanOrEqual(0)
          expect(problems).toEqual([])

          mkdirSync(SHOTS, { recursive: true })
          await page.screenshot({ path: `${SHOTS}/${route || 'today'}-${theme}-${width}.png`, fullPage: true })
        })
      }
    })
  }
}
