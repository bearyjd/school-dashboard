import type { ReconBucket, ReconFlag, ReconItem, StageAction } from './types'

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
