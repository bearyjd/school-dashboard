import { mkdirSync } from 'node:fs'
import { expect, test } from '@playwright/test'
import { ROUTES, THEMES, label, openRoute } from './shared'

// Captures screenshots for human review (there is no pixel diff) and checks, per screen: no console
// errors, no failed requests, no unexpected 4xx/5xx, no horizontal overflow.
const WIDTHS = [320, 375, 768, 1024, 1440] as const
const SHOTS = process.env.E2E_SHOTS ?? 'test-results/shots'
/** Known-benign responses. Anything else with status >= 400 fails the screen. */
const ALLOWED_4XX: readonly RegExp[] = [/\/favicon\.ico$/]

for (const theme of THEMES) {
  for (const width of WIDTHS) {
    test.describe(`${theme} @ ${width}px`, () => {
      test.use({ viewport: { width, height: 900 } })

      test.beforeEach(async ({ page }) => {
        await page.addInitScript(t => localStorage.setItem('school-theme', t), theme)
      })

      for (const route of ROUTES) {
        test(label(route), async ({ page }) => {
          const problems: string[] = []
          page.on('console', m => { if (m.type() === 'error') problems.push(`console: ${m.text()}`) })
          page.on('pageerror', e => problems.push(`pageerror: ${e.message}`))
          page.on('requestfailed', r => problems.push(`failed: ${r.url()}`))
          page.on('response', r => {
            const unexpected = r.status() >= 500 || (r.status() >= 400 && !ALLOWED_4XX.some(re => re.test(r.url())))
            if (unexpected) problems.push(`${r.status()}: ${r.url()}`)
          })

          await openRoute(page, route)
          expect(await page.locator('html').getAttribute('data-theme')).toBe(theme)

          const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
          expect(overflow, 'horizontal overflow (px)').toBeLessThanOrEqual(0)
          expect(problems).toEqual([])

          mkdirSync(SHOTS, { recursive: true })
          await page.screenshot({ path: `${SHOTS}/${route.path.replace('/', '-') || 'today'}-${theme}-${width}.png`, fullPage: true })
        })
      }
    })
  }
}
