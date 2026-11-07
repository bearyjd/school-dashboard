const ROLLOVER_HOUR = 4
const DAY_MS = 86_400_000

interface Ymd { y: number; m: number; d: number }

function localParts(instant: Date, tz: string): Ymd & { hour: number } {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', hourCycle: 'h23',
  }).formatToParts(instant)
  const n = (t: string) => Number(parts.find(p => p.type === t)?.value)
  return { y: n('year'), m: n('month'), d: n('day'), hour: n('hour') }
}

function iso({ y, m, d }: Ymd): string {
  return `${String(y).padStart(4, '0')}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`
}

function addDays({ y, m, d }: Ymd, n: number): Ymd {
  const t = new Date(Date.UTC(y, m - 1, d) + n * DAY_MS)
  return { y: t.getUTCFullYear(), m: t.getUTCMonth() + 1, d: t.getUTCDate() }
}

/** Local calendar day (YYYY-MM-DD) where the day rolls over at 04:00, matching the server. */
export function localToday(now: Date, tz: string): string {
  const p = localParts(now, tz)
  return iso(p.hour < ROLLOVER_HOUR ? addDays(p, -1) : p)
}

/** Seven rolling days starting on the local day of `start`. `now` decides which one is today. */
export function weekDays(
  start: Date,
  tz: string,
  now: Date = new Date(),
): { date: string; label: string; isToday: boolean }[] {
  const first = localParts(start, tz)
  const today = localToday(now, tz)
  const fmt = new Intl.DateTimeFormat('en-US', { timeZone: 'UTC', weekday: 'short', month: 'short', day: 'numeric' })
  return Array.from({ length: 7 }, (_, i) => {
    const ymd = addDays(first, i)
    const date = iso(ymd)
    return { date, label: fmt.format(new Date(Date.UTC(ymd.y, ymd.m - 1, ymd.d, 12))), isToday: date === today }
  })
}

/** `start` moved by whole weeks in local calendar days (never by 168 hours, so DST cannot shift the date). */
export function shiftWeek(start: Date, weeks: number, tz = 'America/New_York'): Date {
  const p = addDays(localParts(start, tz), weeks * 7)
  return new Date(Date.UTC(p.y, p.m - 1, p.d, 12))
}
