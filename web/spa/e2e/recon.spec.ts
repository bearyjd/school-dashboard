import { mkdirSync } from 'node:fs'
import { expect, test, type Page } from '@playwright/test'
import { PARENT, RECON_READY } from './shared'

// Reconcile against scripts/spa-qa-server.py (synthetic data, v2 on, writes open). Specs that change data restore it.
const SHOTS = process.env.E2E_SHOTS ?? 'test-results/shots'
const BUCKET = (name: string) => `[data-bucket=${name}]`

test.beforeEach(async ({ page }) => {
  await page.addInitScript(actor => localStorage.setItem('school-actor', actor), PARENT)
})

const openHome = async (page: Page) => {
  await page.goto('/app/')
  await page.locator(RECON_READY).waitFor()
}
/** Action buttons are named "Mark <title> (<class>, <kid>) done"; match any class and kid. */
const action = (page: Page, verb: string, title: string, tail = '') =>
  page.getByRole('button', { name: new RegExp(`^${verb} ${title.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')} \\([^)]*\\)${tail}$`) })
const row = (page: Page, title: string) => page.getByRole('group', { name: title, exact: true })
const bucketOf = (page: Page, title: string) => row(page, title).locator('xpath=ancestor::section[@data-bucket]')
const count = async (page: Page, bucket: string, label: string) => {
  const text = await page.locator(BUCKET(bucket)).first().getByRole('heading', { level: 3 }).innerText()
  const m = new RegExp(`${label} \\((\\d+)\\)`).exec(text)
  expect(m, `heading "${text}"`).not.toBeNull()
  return Number(m?.[1])
}

test('tapping Done moves the row to Waiting on teacher, survives a reload, and Undo restores it', async ({ page }) => {
  const title = 'Spelling list 12'
  await openHome(page)
  await expect(bucketOf(page, title)).toHaveAttribute('data-bucket', 'do_tonight')
  const [todo, wait] = [await count(page, 'do_tonight', 'Do tonight'), await count(page, 'waiting_on_teacher', 'Waiting on teacher')]

  await action(page, 'Mark', title, ' done').click()
  await expect(bucketOf(page, title)).toHaveAttribute('data-bucket', 'waiting_on_teacher')
  expect(await count(page, 'do_tonight', 'Do tonight')).toBe(todo - 1)
  expect(await count(page, 'waiting_on_teacher', 'Waiting on teacher')).toBe(wait + 1)

  await openHome(page)
  await expect(bucketOf(page, title)).toHaveAttribute('data-bucket', 'waiting_on_teacher')

  await action(page, 'Undo', title).click()
  await expect(bucketOf(page, title)).toHaveAttribute('data-bucket', 'do_tonight')
  await openHome(page)
  await expect(bucketOf(page, title)).toHaveAttribute('data-bucket', 'do_tonight')
  expect(await count(page, 'do_tonight', 'Do tonight')).toBe(todo)
  expect(await count(page, 'waiting_on_teacher', 'Waiting on teacher')).toBe(wait)
})

test('an undated row stays dismissed after reload and stays visible after restore', async ({ page }) => {
  const title = 'Sketchbook prompts'
  const assignment = row(page, title)
  const dismissed = page.getByText('Dismissed', { exact: true })
  const stageWrite = () => page.waitForResponse(response =>
    response.request().method() === 'POST' && new URL(response.url()).pathname.endsWith('/stage'))

  await openHome(page)
  await expect(bucketOf(page, title)).toHaveAttribute('data-bucket', 'no_due_date')
  await expect(assignment.getByText('No due date', { exact: true })).toBeVisible()

  const dismissWrite = stageWrite()
  await action(page, 'Dismiss', title).click()
  expect((await dismissWrite).ok()).toBe(true)
  await expect(assignment).toBeHidden()

  await page.reload()
  await expect(dismissed).toBeVisible()
  await expect(assignment).toBeHidden()
  await dismissed.click()
  await expect(assignment).toBeVisible()

  const restoreWrite = stageWrite()
  await action(page, 'Restore', title).click()
  expect((await restoreWrite).ok()).toBe(true)
  await expect(bucketOf(page, title)).toHaveAttribute('data-bucket', 'no_due_date')

  await page.reload()
  await expect(bucketOf(page, title)).toHaveAttribute('data-bucket', 'no_due_date')
  await expect(assignment).toBeVisible()
})

test('when /api/v2 answers 404 the app shows the old Today with no error UI and no console errors', async ({ page }) => {
  const problems: string[] = []
  page.on('console', m => { if (m.type() === 'error' && !m.text().startsWith('Failed to load resource')) problems.push(m.text()) })
  page.on('pageerror', e => problems.push(e.message))
  await page.route('**/api/v2/**', r => r.fulfill({ status: 404, contentType: 'application/json', body: '{"ok":false,"data":null,"error":"recon_disabled","meta":{}}' }))
  await page.goto('/app/')
  await page.locator('.today__tiles').first().waitFor()
  await expect(page.getByRole('heading', { name: 'Reconcile' })).toHaveCount(0)
  await expect(page.locator('.frame__error, [role=alert]')).toHaveCount(0)
  await expect(page.getByText(/Could not load/i)).toHaveCount(0)
  expect(problems).toEqual([])
})

test('write_auth "token" with no stored token: an action opens the setup sheet and sends no write', async ({ page }) => {
  const writes: string[] = []
  page.on('request', r => { if (r.url().includes('/api/v2/') && r.method() !== 'GET') writes.push(`${r.method()} ${r.url()}`) })
  await page.route('**/api/v2/config', async r => {
    const resp = await r.fetch()
    const body = await resp.json()
    body.data.write_auth = 'token'
    await r.fulfill({ response: resp, json: body })
  })
  await openHome(page)
  await action(page, 'Mark', 'Spelling list 12', ' done').click()
  await expect(page.getByRole('dialog', { name: 'Set up this device' })).toBeVisible()
  expect(writes).toEqual([])
})

test('the draft note for the Disputed row is a draft only: copy, mail app and portal link, nothing sent', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 800 })
  await openHome(page)
  await action(page, 'Draft note for', 'Poster board').click()
  const dialog = page.getByRole('dialog', { name: 'Draft note: Poster board' })
  await expect(dialog).toBeVisible()
  await expect(dialog.getByText('Nothing is sent automatically.')).toBeVisible()
  await expect(dialog.getByRole('button', { name: 'Copy' })).toBeVisible()
  await expect(dialog.getByRole('link', { name: 'Open in mail app' })).toHaveAttribute('href', /^mailto:teacher@school\.example\.test/)
  await expect(dialog.getByRole('link', { name: 'Open in portal' })).toHaveAttribute('href', /^https:\/\/portal\.example\.test\//)

  const names = await dialog.locator('button, a[href]').evaluateAll(els =>
    els.map(el => el.getAttribute('aria-label') ?? (el.textContent ?? '').trim()))
  expect(names.filter(n => /send/i.test(n) && n !== 'I sent it')).toEqual([])

  mkdirSync(SHOTS, { recursive: true })
  await page.screenshot({ path: `${SHOTS}/recon-note-sheet-375.png` })
  await dialog.getByRole('button', { name: 'Discard' }).click()
  await expect(dialog).toHaveCount(0)
})

test('first view at 375 px: the first bucket heading is visible within 2 s', async ({ page }, info) => {
  await page.setViewportSize({ width: 375, height: 800 })
  const started = Date.now()
  await page.goto('/app/', { waitUntil: 'commit' })
  await page.locator('h3').first().waitFor({ state: 'visible' })
  const ms = Date.now() - started
  console.log(`first-view-ms=${ms}`)
  info.annotations.push({ type: 'first-view-ms', description: String(ms) })
  expect(ms).toBeLessThan(2000)
})

test('keyboard: Tab reaches a row action and Enter activates it; a sheet traps focus and Escape restores it', async ({ page }) => {
  await openHome(page)
  const focusedAction = () => page.evaluate(() => {
    const el = document.activeElement
    return el?.closest('.recon-row') ? el.getAttribute('aria-label') ?? '' : ''
  })
  const tabTo = async (re: RegExp) => {
    for (let i = 0; i < 80; i++) {
      await page.keyboard.press('Tab')
      if (re.test(await focusedAction())) return
    }
    throw new Error(`Tab never reached ${re}`)
  }

  await tabTo(/./)  // the first row action on the page is reachable by keyboard alone
  console.log(`first-row-action=${await focusedAction()}`)
  await tabTo(/^(Draft note for|More for) /)
  const opener = await focusedAction()
  await page.keyboard.press('Enter')
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  expect(await dialog.getAttribute('aria-label')).toBe(opener.replace(/ \([^)]*\)$/, '').replace(/^(Draft note for|More for) /, m => (m.startsWith('Draft') ? 'Draft note: ' : 'More for ')))

  for (let i = 0; i < 12; i++) {
    await page.keyboard.press('Tab')
    expect(await dialog.evaluate(d => d.contains(document.activeElement))).toBe(true)
  }
  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)
  expect(await focusedAction()).toBe(opener)

  if (opener.startsWith('Draft note for')) {  // a draft was stored when it opened; leave none behind
    await page.keyboard.press('Enter')  // the server hands back the same open draft
    const again = page.getByRole('dialog')
    await expect(again).toBeVisible()
    await again.getByRole('button', { name: 'Discard' }).click()
    await expect(again).toHaveCount(0)
  }
})

test('a title with markup renders as text: no element, no dialog, no alert', async ({ page }) => {
  let fired = false
  page.on('dialog', d => { fired = true; void d.dismiss() })
  await openHome(page)
  const title = '<img src=x onerror=alert(1)> poster'
  const group = row(page, title)
  await expect(group).toBeVisible()
  await expect(group.locator('img')).toHaveCount(0)
  await expect(group.getByText(title, { exact: true })).toBeVisible()
  await expect(page.locator('main img[src="x"]')).toHaveCount(0)
  await page.waitForTimeout(300)
  expect(fired).toBe(false)
})
