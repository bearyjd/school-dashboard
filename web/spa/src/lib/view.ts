import type { Freshness, Overview, OverviewEvent, OverviewItem, OverviewSource } from '../api/types'

// Fixed to the server's schedule zone (TZ=America/New_York in Docker). If the server TZ is ever
// changed this must follow; a per-family zone is out of scope for v1.
export const APP_TZ = 'America/New_York'

const SOURCE_LABELS: Record<string, string> = {
  ixl: 'IXL',
  sgy: 'Schoology',
  schoology: 'Schoology',
  powerschool: 'PowerSchool',
  gc: 'GameChanger',
  school: 'School calendar',
  manual: 'Manual',
}

export function sourceLabel(source: string): string {
  return SOURCE_LABELS[source] ?? source
}

/** Item source name -> key in `Overview.freshness` (manual items have no scrape). */
export function freshnessKey(source: OverviewSource): string | null {
  if (source === 'schoology') return 'sgy'
  if (source === 'manual') return null
  return source
}

export function itemFreshness(item: OverviewItem, freshness: Record<string, Freshness>): Freshness | null {
  const key = freshnessKey(item.source)
  const f = key ? freshness[key] : undefined
  return f && (f.stale || f.failing) ? f : null
}

export function problemSources(freshness: Record<string, Freshness>): [string, Freshness][] {
  return Object.entries(freshness).filter(([, f]) => f.stale || f.failing)
}

/** Any string is accepted; a name that is not on the roster means "everyone". */
export function kidParser(data: Overview | undefined): (raw: string | null) => string {
  return raw => (raw && data?.kids.some(k => k.name === raw) ? raw : '')
}

export function kidIndex(data: Overview, name: string): number {
  return data.kids.find(k => k.name === name)?.colorIndex ?? 0
}

export function itemMatchesKid(item: OverviewItem, kid: string): boolean {
  return !kid || item.kid === kid
}

/** School-wide events (no kid) always show, even under a kid filter. */
export function eventMatchesKid(event: OverviewEvent, kid: string): boolean {
  return !kid || event.kids.length === 0 || event.kids.includes(kid)
}

const DUE_FMT = new Intl.DateTimeFormat('en-US', { timeZone: 'UTC', month: 'short', day: 'numeric' })

/** "2026-11-05" -> "Nov 5"; anything unparseable -> "" . */
export function formatDay(iso: string | null): string {
  if (!iso) return ''
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso)
  if (!m) return ''
  return DUE_FMT.format(new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], 12)))
}

export function formatTime(hhmm: string): string {
  const m = /^(\d{2}):(\d{2})$/.exec(hhmm)
  if (!m) return ''
  const h = +m[1]
  return `${h % 12 || 12}:${m[2]} ${h < 12 ? 'AM' : 'PM'}`
}

export function ageHoursSince(iso: string, now: number = Date.now()): number | null {
  const t = Date.parse(iso)
  return Number.isFinite(t) ? Math.max(0, (now - t) / 3_600_000) : null
}
