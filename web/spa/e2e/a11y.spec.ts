import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'
import { ROUTES, THEMES, label, openRoute } from './shared'

const BLOCKING = ['serious', 'critical']

for (const theme of THEMES) {
  test.describe(`axe, ${theme}`, () => {
    test.beforeEach(async ({ page }) => {
      await page.addInitScript(t => localStorage.setItem('school-theme', t), theme)
    })

    for (const route of ROUTES) {
      for (const width of [375, 1280]) {
        test(`${label(route)} @ ${width}px has no serious or critical violations`, async ({ page }) => {
          await page.setViewportSize({ width, height: 900 })
          await openRoute(page, route)
          const { violations } = await new AxeBuilder({ page })
            .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
            .analyze()
          const blocking = violations.filter(v => BLOCKING.includes(v.impact ?? ''))
          expect(blocking.map(v => `${v.id} (${v.impact}): ${v.nodes.map(n => n.target).join(' | ')}`)).toEqual([])
        })
      }
    }
  })
}

test('keyboard focus order: skip link first, then the main navigation in order', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await openRoute(page, ROUTES[0])
  const labels = ['Skip to main content', 'Today', 'Tonight', 'Overdue', 'Missing', 'Events', 'Calendar', 'Chat', 'Sync', 'Settings', 'Classic view']
  for (const label of labels) {
    await page.keyboard.press('Tab')
    const focused = await page.evaluate(() => {
      const el = document.activeElement
      return { text: (el?.textContent ?? '').trim(), visible: !!el && el !== document.body && el.getClientRects().length > 0 }
    })
    expect(focused, `focus on ${label}`).toEqual({ text: label, visible: true })
  }
  // Focus keeps moving through the page content, never lost to <body>.
  for (let i = 0; i < 6; i++) {
    await page.keyboard.press('Tab')
    expect(await page.evaluate(() => document.activeElement === document.body)).toBe(false)
  }
})
