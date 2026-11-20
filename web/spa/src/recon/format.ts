import { APP_TZ } from '../lib/view'

const DUE = new Intl.DateTimeFormat('en-US', { timeZone: 'UTC', weekday: 'short', month: 'short', day: 'numeric' })
const AT = new Intl.DateTimeFormat('en-US', { timeZone: APP_TZ, month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })

/** "2026-11-20" -> "Due Fri Nov 20"; no or unparseable date -> "No due date". */
export function formatDue(iso: string | null): string {
  const m = iso ? /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso) : null
  if (!m) return 'No due date'
  return `Due ${DUE.format(new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], 12))).replace(',', '')}`
}

/** An ISO instant as "Nov 18, 9:30 AM" in the family's zone; unparseable -> "unknown". */
export function formatInstant(iso: string | null): string {
  const t = iso ? Date.parse(iso) : NaN
  return Number.isFinite(t) ? AT.format(new Date(t)) : 'unknown'
}
