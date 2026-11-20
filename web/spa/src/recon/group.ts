import type { Counts, ReconBucket, ReconConfig, ReconFlag, ReconItem, ReconSummary } from './types'

export const BUCKET_ORDER: ReconBucket[] = ['needs_action', 'do_tonight', 'waiting_on_teacher']
export const BUCKET_LABEL: Record<ReconBucket, string> = {
  needs_action: 'Needs action', do_tonight: 'Do tonight', waiting_on_teacher: 'Waiting on teacher',
}
const NEEDS_ACTION_ORDER: [ReconFlag, string][] = [
  ['disputed', 'Disputed'], ['missing', 'Missing'], ['done_not_received', 'Done, not received'],
]

export interface ItemGroup { key: string; label: string | null; items: ReconItem[] }
export interface BucketView { id: ReconBucket; label: string; count: number; groups: ItemGroup[] }
export interface KidView {
  name: string
  color: string
  counts: Counts
  buckets: BucketView[]
  ixl: string | null
  /** items: something to do; clear: nothing to do and verified; unverified_clear: nothing to do but data may be stale. */
  state: 'items' | 'clear' | 'unverified_clear'
}

const REMAINING = /^\s*(\d+)/

function remaining(item: ReconItem): number | null {
  for (const ln of item.links) {
    const m = REMAINING.exec(ln.raw_status ?? '')
    if (m) return Number(m[1])
  }
  return null
}

/** "IXL: Math 4 remaining, ELA 2 remaining"; null when the kid has no IXL rows. */
export function ixlLine(practice: ReconItem[]): string | null {
  if (practice.length === 0) return null
  const todo = practice.flatMap(p => {
    const n = remaining(p)
    return n !== null && n > 0 ? [`${p.class ?? p.title} ${n} remaining`] : []
  })
  return todo.length > 0 ? `IXL: ${todo.join(', ')}` : 'IXL: nothing remaining'
}

const count = (rows: ReconItem[], flag: ReconFlag) => rows.filter(r => r.flag === flag).length

export function countRows(rows: ReconItem[]): Counts {
  const detail = { done_not_received: count(rows, 'done_not_received'), disputed: count(rows, 'disputed'), missing: count(rows, 'missing') }
  return {
    do_tonight: count(rows, 'do_tonight'), waiting_on_teacher: count(rows, 'waiting_on_teacher'),
    needs_action: detail.done_not_received + detail.disputed + detail.missing, needs_action_detail: detail,
  }
}

function groupsFor(id: ReconBucket, rows: ReconItem[]): ItemGroup[] {
  if (id !== 'needs_action') return [{ key: id, label: null, items: rows }]
  return NEEDS_ACTION_ORDER
    .map(([flag, label]) => ({ key: flag, label, items: rows.filter(r => r.flag === flag) }))
    .filter(g => g.items.length > 0)
}

function kidView(name: string, color: string, summary: ReconSummary, items: ReconItem[]): KidView {
  const mine = items.filter(i => i.kid === name)
  const rows = mine.filter(i => i.kind !== 'practice' && i.bucket !== null)
  const practice = summary.kids.find(k => k.name === name)?.practice ?? mine.filter(i => i.kind === 'practice')
  const ixl = ixlLine(practice)
  const buckets = BUCKET_ORDER.flatMap(id => {
    const inBucket = rows.filter(r => r.bucket === id)
    return inBucket.length === 0 ? [] : [{ id, label: BUCKET_LABEL[id], count: inBucket.length, groups: groupsFor(id, inBucket) }]
  })
  const counts = countRows(rows)
  // Like the server: rows that only wait on a teacher do not stop a kid from being "all clear".
  const work = counts.needs_action > 0 || counts.do_tonight > 0 || practice.some(p => (remaining(p) ?? 0) > 0)
  const unverified = !summary.ingest_ok || summary.unverified_sources.length > 0 || mine.some(i => i.unverified)
  return { name, color, counts, buckets, ixl, state: work ? 'items' : unverified ? 'unverified_clear' : 'clear' }
}

interface Args { config: ReconConfig | undefined; summary: ReconSummary; items: ReconItem[]; kid: string }

/** One view per kid: roster order first, then any other kid the summary knows. `kid` limits it to one (unknown = all). */
export function buildKidViews({ config, summary, items, kid }: Args): KidView[] {
  const roster = config?.kids ?? []
  const names = [...roster.map(k => k.name), ...summary.kids.map(k => k.name).filter(n => !roster.some(k => k.name === n))]
  const shown = kid && names.includes(kid) ? [kid] : names
  return shown.map(n => kidView(n, roster.find(k => k.name === n)?.color ?? '', summary, items))
}
