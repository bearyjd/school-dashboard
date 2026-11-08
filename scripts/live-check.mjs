#!/usr/bin/env node
// live-check.mjs — post-deploy browser check against a running dashboard.
//
// Usage: node scripts/live-check.mjs [BASE_URL]      (or env BASE_URL; default http://127.0.0.1:5000)
// Env:   LIVE_SHOTS   screenshot dir (default web/spa/test-results/live-shots)
//        SYNC_TOKEN   never read; this script sends no credentials
// Needs: `npm --prefix web/spa ci` and `npx --prefix web/spa playwright install chromium`.
// Checks 375 and 1440 px x both themes x the routes of web/spa/e2e/shared.ts: fails on console/page
// errors, failed requests, 4xx/5xx (favicon aside) and horizontal overflow. Saves screenshots.
// Output: COUNTS ONLY. Never page text, URLs or data values. Exit 1 on any problem, 2 on bad usage.
import { createRequire } from 'node:module'
import { chmodSync, mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

// Keep in step with ROUTES in web/spa/e2e/shared.ts.
const ROUTES = [
  { path: '', api: '/api/overview', ready: '.today__tiles' },
  { path: 'tonight', api: '/api/overview', ready: 'table' },
  { path: 'overdue', api: '/api/overview', ready: 'table' },
  { path: 'missing', api: '/api/overview', ready: 'table' },
  { path: 'events', api: '/api/overview', ready: 'table' },
  { path: 'calendar', api: '/api/overview', ready: '.cal-day__h' },
  { path: 'settings', api: null, ready: 'h1' },
  { path: 'chat', api: null, ready: '#main input' },
  { path: 'sync', api: '/api/sync/meta', ready: 'button:has-text("Pull")' },
  { path: 'classic/home', api: '/api/dashboard', ready: '#main h2' },
]
const THEMES = ['midnight', 'daylight']
const WIDTHS = [375, 1440]
const ALLOWED_4XX = [/\/favicon\.ico$/]

process.umask(0o077)
const base = (process.argv[2] ?? process.env.BASE_URL ?? 'http://127.0.0.1:5000').replace(/\/+$/, '')
if (!/^https?:\/\/[A-Za-z0-9.:_-]+$/.test(base)) {
  console.error('live-check: BASE_URL must look like http(s)://host[:port]')
  process.exit(2)
}
const shots = process.env.LIVE_SHOTS ?? fileURLToPath(new URL('../web/spa/test-results/live-shots', import.meta.url))

const spaRequire = createRequire(new URL('../web/spa/package.json', import.meta.url))
let chromium
try {
  ;({ chromium } = spaRequire('@playwright/test'))
} catch {
  console.error('live-check: Playwright missing; run `npm --prefix web/spa ci`')
  process.exit(2)
}

const totals = { screens: 0, failedScreens: 0, console: 0, page: 0, requestFailed: 0, http: 0, theme: 0, overflow: 0, timeout: 0 }

async function checkScreen(browser, theme, width, route) {
  const context = await browser.newContext({ viewport: { width, height: 900 }, serviceWorkers: 'block', baseURL: base })
  const page = await context.newPage()
  await page.addInitScript(t => localStorage.setItem('school-theme', t), theme)
  const c = { console: 0, page: 0, requestFailed: 0, http: 0, theme: 0, overflow: 0, timeout: 0 }
  page.on('console', m => { if (m.type() === 'error') c.console++ })
  page.on('pageerror', () => { c.page++ })
  page.on('requestfailed', () => { c.requestFailed++ })
  page.on('response', r => {
    const s = r.status()
    if (s >= 500 || (s >= 400 && !ALLOWED_4XX.some(re => re.test(r.url())))) c.http++
  })
  try {
    const response = route.api
      ? page.waitForResponse(r => new URL(r.url()).pathname === route.api && r.ok(), { timeout: 30_000 })
      : null
    response?.catch(() => {})
    await page.goto(`/app/${route.path}`, { waitUntil: 'load', timeout: 30_000 })
    await response
    await page.locator(route.ready).first().waitFor({ timeout: 30_000 })
    const applied = await page.evaluate(() => document.documentElement.dataset.theme)
    if (applied !== theme) c.theme++
    const over = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
    if (over > 0) c.overflow++
    mkdirSync(shots, { recursive: true, mode: 0o700 })
    chmodSync(shots, 0o700)
    await page.screenshot({ path: `${shots}/${route.path.replace('/', '-') || 'today'}-${theme}-${width}.png`, fullPage: true })
  } catch {
    c.timeout++
  }
  await context.close()
  return c
}

let browser
try {
  browser = await chromium.launch()
} catch {
  console.error('live-check: cannot start Chromium; run `npx --prefix web/spa playwright install chromium`')
  process.exit(2)
}
try {
  for (const theme of THEMES) {
    for (const width of WIDTHS) {
      for (const route of ROUTES) {
        const c = await checkScreen(browser, theme, width, route)
        totals.screens++
        for (const k of Object.keys(c)) totals[k] += c[k]
        if (Object.values(c).some(n => n > 0)) totals.failedScreens++
      }
    }
  }
} finally {
  await browser.close()
}

const problems = totals.console + totals.page + totals.requestFailed + totals.http + totals.theme + totals.overflow + totals.timeout
console.log(
  `live-check: screens=${totals.screens} failed_screens=${totals.failedScreens} console_errors=${totals.console} ` +
  `page_errors=${totals.page} request_failures=${totals.requestFailed} http_4xx_5xx=${totals.http} ` +
  `theme_mismatch=${totals.theme} overflow=${totals.overflow} load_timeouts=${totals.timeout}`,
)
console.log(problems === 0 ? 'LIVE-CHECK: PASS' : 'LIVE-CHECK: FAIL')
process.exit(problems === 0 ? 0 : 1)
