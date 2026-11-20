import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { CONFIG, fail, fakeServer, link, ok, renderRecon, ritem, sampleItems } from './fixtures'
import { Reconcile } from './Reconcile'

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'], now: new Date('2026-11-18T15:00:00Z') })
  localStorage.clear()
  sessionStorage.clear()
})
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

async function show(route = '/', server = fakeServer()) {
  const utils = renderRecon(<Reconcile />, route, server)
  await screen.findByRole('heading', { level: 2, name: 'Alpha' })
  return utils
}
const row = (title: string) => screen.getByRole('group', { name: title })
const tile = (name: RegExp) => within(screen.getByRole('region', { name: 'Summary' })).getByRole('button', { name })

test('loads config, summary and the open list in parallel and marks itself ready', async () => {
  const { server } = await show()
  const paths = server.requests.map(r => r.url)
  expect(paths).toContain('/api/v2/summary')
  expect(paths).toContain('/api/v2/assignments?limit=500')
  expect(document.querySelector('main[data-recon-ready="true"]')).not.toBeNull()
  expect(screen.getByRole('heading', { level: 1, name: 'Reconcile' })).toBeTruthy()
})

test('shows a loading skeleton while the data is on its way', async () => {
  const server = fakeServer()
  server.on('GET /api/v2/summary', () => new Promise(() => undefined))
  renderRecon(<Reconcile />, '/', server)
  expect(await screen.findByRole('status', { name: /loading/i })).toBeTruthy()
})

test('kids appear in roster order with a heading each; Gamma is all clear', async () => {
  await show()
  expect(screen.getAllByRole('heading', { level: 2 }).map(h => h.textContent)).toEqual(['Alpha', 'Beta', 'Gamma'])
  const gamma = screen.getByRole('heading', { level: 2, name: 'Gamma' }).closest('section') as HTMLElement
  expect(within(gamma).getByText(/all clear/i)).toBeTruthy()
  expect(within(gamma).queryAllByRole('heading', { level: 3 })).toHaveLength(0)
})

test('buckets are ordered and empty buckets are omitted', async () => {
  await show()
  const alpha = screen.getByRole('heading', { level: 2, name: 'Alpha' }).closest('section') as HTMLElement
  expect(within(alpha).getAllByRole('heading', { level: 3 }).map(h => h.textContent))
    .toEqual(['Needs action (3)', 'Do tonight (1)', 'Waiting on teacher (1)'])
  const beta = screen.getByRole('heading', { level: 2, name: 'Beta' }).closest('section') as HTMLElement
  expect(within(beta).getAllByRole('heading', { level: 3 }).map(h => h.textContent)).toEqual(['Do tonight (2)'])
})

test('Needs action is sub-labelled Disputed, Missing, Done, not received (in that order)', async () => {
  await show()
  const alpha = screen.getByRole('heading', { level: 2, name: 'Alpha' }).closest('section') as HTMLElement
  expect(within(alpha).getAllByRole('heading', { level: 4 }).map(h => h.textContent))
    .toEqual(['Disputed', 'Missing', 'Done, not received'])
})

test('count tiles sum the shown kids; Needs action is the first tile', async () => {
  await show()
  const tiles = within(screen.getByRole('region', { name: 'Summary' })).getAllByRole('button')
  expect(tiles.map(t => t.textContent)).toEqual(['3Needs action', '3Do tonight', '1Waiting on teacher'])
})

test('tapping a tile scrolls to the first bucket of that kind', async () => {
  const spy = vi.fn()
  Element.prototype.scrollIntoView = spy
  await show()
  fireEvent.click(tile(/waiting on teacher/i))
  expect(spy).toHaveBeenCalledTimes(1)
  expect((spy.mock.contexts[0] as HTMLElement).getAttribute('data-bucket')).toBe('waiting_on_teacher')
})

test('kid switcher: All plus one chip per kid, selection lives in ?kid=', async () => {
  await show()
  const group = screen.getByRole('group', { name: /filter by child/i })
  expect(within(group).getAllByRole('button').map(b => b.getAttribute('aria-pressed'))).toEqual(['true', 'false', 'false', 'false'])
  fireEvent.click(within(group).getByRole('button', { name: /Beta/ }))
  expect(screen.getByTestId('where').textContent).toBe('/?kid=Beta')
  expect(screen.queryByRole('heading', { level: 2, name: 'Alpha' })).toBeNull()
  expect(screen.getByRole('heading', { level: 2, name: 'Beta' })).toBeTruthy()
  expect(within(tile(/do tonight/i)).getByText('2')).toBeTruthy()
  fireEvent.click(within(group).getByRole('button', { name: /all/i }))
  expect(screen.getByTestId('where').textContent).toBe('/')
  expect(screen.getByRole('heading', { level: 2, name: 'Alpha' })).toBeTruthy()
})

test('?kid= in the URL selects a kid on load; junk means everyone', async () => {
  renderRecon(<Reconcile />, '/?kid=Beta')
  await screen.findByRole('heading', { level: 2, name: 'Beta' })
  expect(screen.queryByRole('heading', { level: 2, name: 'Alpha' })).toBeNull()
})

test('junk ?kid= shows everyone', async () => {
  await show('/?kid=Nobody')
  expect(screen.getAllByRole('heading', { level: 2 })).toHaveLength(3)
})

test('IXL line from the practice rows; no line for a kid without practice', async () => {
  await show()
  expect(screen.getByText('IXL: Math 4 remaining, ELA 2 remaining')).toBeTruthy()
  expect(screen.getAllByText(/^IXL:/)).toHaveLength(1)
  expect(screen.queryByRole('group', { name: 'Math' })).toBeNull()
})

test('a row shows title, class, due date, source badge, and stage chip', async () => {
  await show()
  const r = within(row('Essay draft'))
  expect(r.getByText('Essay draft')).toBeTruthy()
  expect(r.getByText('English')).toBeTruthy()
  expect(r.getByText('Due Fri Nov 20')).toBeTruthy()
  expect(r.getByText('Assigned')).toBeTruthy()
  expect(r.getByRole('link', { name: /schoology.*assigned/i })).toBeTruthy()
  expect(within(row('Fractions')).getByText('No due date')).toBeTruthy()
  expect(within(row('Lab sheet')).getByText('Done at home')).toBeTruthy()
})

test('info chips are text', async () => {
  const items = [ritem({ id: 1, info: ['late', 'cleared_unconfirmed', 'conflict'], possible_duplicate: true, unverified: true }),
    ritem({ id: 2, title: 'Paper', stage: 3, stage_label: 'Turned in (unconfirmed)', info: ['unconfirmed_paper'], links: [], flag: 'waiting_on_teacher', bucket: 'waiting_on_teacher' })]
  await show('/', fakeServer({ items }))
  const r = within(row('Essay draft'))
  for (const t of ['Late in portal', 'Cleared, unconfirmed', 'Portals disagree', 'Possible duplicate', 'Not verified']) expect(r.getByText(t)).toBeTruthy()
  expect(within(row('Paper')).getByText('Turned in (unconfirmed)')).toBeTruthy()
})

test('hostile titles render as text, never as markup', async () => {
  const evil = '<script>alert(1)</script><img src=x onerror=alert(2)>'
  const { container } = await show('/', fakeServer({ items: [ritem({ id: 1, title: evil }), ...sampleItems().slice(1)] }))
  expect(screen.getByText(evil)).toBeTruthy()
  expect(container.querySelector('script')).toBeNull()
  expect(container.querySelector('img')).toBeNull()
})

test('source badges link only for http(s) urls', async () => {
  const items = [ritem({ id: 1, title: 'Linked', links: [
    link({ id: 1, source: 'schoology', url: 'https://portal.example.org/x' }),
    link({ id: 2, source: 'powerschool', url: 'javascript:alert(1)' }),
    link({ id: 3, source: 'ixl', url: 'data:text/html,hi' }),
    link({ id: 4, source: 'gc', url: null }),
  ] })]
  const { container } = await show('/', fakeServer({ items }))
  const r = within(row('Linked'))
  const anchors = Array.from(container.querySelectorAll('a')).map(a => a.getAttribute('href'))
  expect(anchors.filter(h => h?.startsWith('http'))).toContain('https://portal.example.org/x')
  expect(anchors.some(h => /^(javascript|data):/i.test(h ?? ''))).toBe(false)
  expect(r.getByText(/PowerSchool/)).toBeTruthy() // badge still shown, just not a link
  expect(r.getByText(/IXL/)).toBeTruthy()
  const safe = r.getByRole('link', { name: /schoology/i })
  expect(safe.getAttribute('target')).toBe('_blank')
  expect(safe.getAttribute('rel')).toBe('noopener noreferrer')
  expect(r.getAllByRole('link').every(a => a.getAttribute('href')?.startsWith('https://portal.example.org'))).toBe(true)
})

test('no link and no Open in portal when the row has no safe url', async () => {
  await show('/', fakeServer({ items: [ritem({ id: 1, links: [link({ url: null })] })] }))
  expect(within(row('Essay draft')).queryByRole('link')).toBeNull()
  expect(screen.queryByRole('link', { name: /open in portal: essay draft/i })).toBeNull()
})

test('"Data as of" is always shown', async () => {
  await show()
  expect(screen.getByText(/Data as of Nov 18, 9:30\sAM/)).toBeTruthy()
})

test('no banner when everything is verified', async () => {
  await show()
  expect(screen.queryByText(/not verified/i)).toBeNull()
})

test('a failed merge run and stale sources raise a "Not verified" banner', async () => {
  await show('/', fakeServer({ summary: { ingest_ok: false, unverified_sources: ['schoology', 'recon_ingest'] } }))
  expect(screen.getByText(/Not verified: Schoology data is out of date/)).toBeTruthy()
  expect(screen.getByText(/last assignment merge did not succeed/i)).toBeTruthy()
})

test('unverified sources alone raise the banner', async () => {
  await show('/', fakeServer({ summary: { unverified_sources: ['powerschool'] } }))
  expect(screen.getByText(/Not verified: PowerSchool data is out of date/)).toBeTruthy()
})

test('a clear kid under unverified data says "nothing open (not verified)", not "all clear"', async () => {
  await show('/', fakeServer({ summary: { unverified_sources: ['schoology'] } }))
  const gamma = screen.getByRole('heading', { level: 2, name: 'Gamma' }).closest('section') as HTMLElement
  expect(within(gamma).getByText(/nothing open \(not verified\)/i)).toBeTruthy()
  expect(within(gamma).queryByText(/^all clear/i)).toBeNull()
})

test('existing source banners (from /api/overview freshness) still show', async () => {
  const { FIXTURE, fresh } = await import('../test-utils')
  const server = fakeServer()
  server.on('GET /api/overview', () => ({ status: 200, body: { ...FIXTURE, freshness: { ...FIXTURE.freshness, sgy: fresh({ stale: true, age_hours: 30 }) } } }))
  await show('/', server)
  expect(await screen.findByText(/Schoology/, { selector: 'strong' })).toBeTruthy()
})

test('an overview failure shows no error', async () => {
  await show()
  expect(screen.queryByRole('alert')).toBeNull()
})

test('a failed list or summary load shows a retryable error', async () => {
  const server = fakeServer()
  server.on('GET /api/v2/summary', () => fail('internal', 500))
  renderRecon(<Reconcile />, '/', server)
  expect(await screen.findByText(/could not load/i)).toBeTruthy()
  expect(screen.getByRole('button', { name: /retry/i })).toBeTruthy()
})

test('layout: touch targets and no hover-only affordances are classes, not inline hacks', async () => {
  await show()
  const buttons = within(row('Essay draft')).getAllByRole('button')
  for (const b of buttons) expect(b.className).toContain('recon-btn')
})

test('Older view link goes to /today', async () => {
  await show()
  expect(screen.getByRole('link', { name: /older view/i }).getAttribute('href')).toBe('/today')
})

test('waiting for config alone does not block: cached config renders the page', async () => {
  sessionStorage.setItem('school-recon-config', JSON.stringify(CONFIG))
  const server = fakeServer({ config: ok(CONFIG) })
  await show('/', server)
  await waitFor(() => expect(screen.getAllByRole('heading', { level: 2 })).toHaveLength(3))
})
