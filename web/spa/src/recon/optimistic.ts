import type { Counts, ReconBucket, ReconFlag, ReconItem, ReconSummary, StageAction } from './types'

const BUCKET_OF: Partial<Record<ReconFlag, ReconBucket>> = {
  disputed: 'needs_action', missing: 'needs_action', done_not_received: 'needs_action',
  do_tonight: 'do_tonight', waiting_on_teacher: 'waiting_on_teacher',
}
const STAGE_LABEL: Record<number, string> = { 1: 'Assigned', 2: 'Done at home', 3: 'Turned in' }
const TARGET: Record<StageAction, number> = { done: 2, turned_in: 3, undo: 1 }
const DAY_MS = 86_400_000

const nextDay = (iso: string): string => new Date(Date.parse(`${iso}T12:00:00Z`) + DAY_MS).toISOString().slice(0, 10)

/** Stage 1 again: the flag the server would most likely compute (school-day detail is left to the server answer). */
function openFlag(item: ReconItem, today: string): ReconFlag {
  if (item.portal_status === 'missing' || (item.due !== null && item.due < today)) return 'missing'
  return item.due !== null && item.due <= nextDay(today) ? 'do_tonight' : 'upcoming'
}

function flagAt(item: ReconItem, stage: number, today: string): ReconFlag {
  if (stage === 1) return openFlag(item, today)
  if (item.portal_status === 'missing') return stage === 3 ? 'disputed' : 'missing'
  return 'waiting_on_teacher'
}

/**
 * The row as it will most likely look once the server accepts the tap, so it can move at once. The server's answer
 * replaces it either way. A task that is marked done leaves the buckets (flag and bucket become null).
 */
export function optimisticItem(item: ReconItem, action: StageAction, today: string): ReconItem {
  const stage = TARGET[action]
  if (item.kind === 'task') {
    return stage === 1
      ? { ...item, stage, stage_label: STAGE_LABEL[1], flag: openFlag(item, today), bucket: BUCKET_OF[openFlag(item, today)] ?? null }
      : { ...item, stage, stage_label: STAGE_LABEL[2], flag: null, bucket: null }
  }
  const flag = flagAt(item, stage, today)
  return { ...item, stage, stage_label: STAGE_LABEL[stage], flag, bucket: BUCKET_OF[flag] ?? null }
}

const NEEDS: Partial<Record<ReconFlag, keyof Counts['needs_action_detail']>> = {
  disputed: 'disputed', missing: 'missing', done_not_received: 'done_not_received',
}

function bump(counts: Counts, flag: ReconFlag | null, by: 1 | -1): Counts {
  if (flag === null) return counts
  const detailKey = NEEDS[flag]
  if (detailKey) {
    const detail = { ...counts.needs_action_detail, [detailKey]: counts.needs_action_detail[detailKey] + by }
    return { ...counts, needs_action: counts.needs_action + by, needs_action_detail: detail }
  }
  if (flag === 'do_tonight' || flag === 'waiting_on_teacher') return { ...counts, [flag]: counts[flag] + by }
  return counts
}

/** The summary with `from` replaced by `to` in its kid's counts, so the tiles move with the row (the refetch confirms). */
export function shiftKidCounts(summary: ReconSummary, from: ReconItem, to: ReconItem): ReconSummary {
  if (from.kind === 'practice' || from.flag === to.flag) return summary
  const kids = summary.kids.map(k => (k.name === from.kid ? { ...k, counts: bump(bump(k.counts, from.flag, -1), to.flag, 1) } : k))
  return { ...summary, kids }
}
