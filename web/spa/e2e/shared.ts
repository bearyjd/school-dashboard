import type { Page } from '@playwright/test'

export interface RouteSpec {
  /** Path under /app/ ('' is Today). */
  path: string
  /** API path whose successful response must arrive before the screen counts as loaded. */
  api: string | null
  /** Selector that only exists once the data has rendered. */
  ready: string
}

export const ROUTES: readonly RouteSpec[] = [
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

export const THEMES = ['midnight', 'daylight'] as const

export const label = (r: RouteSpec) => `/${r.path || 'today'}`

/** Navigates and resolves once the route's data response arrived and its data-dependent marker rendered. */
export async function openRoute(page: Page, route: RouteSpec): Promise<void> {
  const response = route.api
    ? page.waitForResponse(r => new URL(r.url()).pathname === route.api && r.ok())
    : null
  await page.goto(`/app/${route.path}`, { waitUntil: 'load' })
  await response
  await page.locator(route.ready).first().waitFor()
}
