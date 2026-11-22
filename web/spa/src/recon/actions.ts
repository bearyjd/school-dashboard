import { safeHttpUrl } from './safeUrl'
import type { ReconItem, Role } from './types'

export type RowAction = 'draft_note' | 'done' | 'turned_in' | 'undo' | 'dismiss' | 'restore' | 'more'

const MAIN: Record<number, RowAction[]> = { 1: ['done', 'turned_in'], 2: ['turned_in', 'undo'], 3: ['undo'] }

/** The buttons a row offers, in display order. Practice (IXL) rows are read-only; tasks only toggle done. */
export function rowActions(item: ReconItem, role: Role): RowAction[] {
  if (item.kind === 'practice') return []
  if (item.kind === 'task') return item.stage >= 2 ? ['undo'] : ['done']
  if (item.dismissed_at) return ['restore', 'more']
  const note: RowAction[] = role === 'parent' && item.bucket === 'needs_action' ? ['draft_note'] : []
  const dismiss: RowAction[] = item.due === null && item.stage >= 1 && item.stage <= 3 ? ['dismiss'] : []
  return [...note, ...(MAIN[item.stage] ?? []), ...dismiss, 'more']
}

/** The first progress or note action is drawn as the primary button. */
export const primaryAction = (actions: RowAction[]): RowAction | undefined =>
  actions.find(a => a === 'done' || a === 'turned_in' || a === 'draft_note')

/** First live link with a plain http(s) url. */
export function portalLink(item: ReconItem): string | null {
  for (const ln of item.links) {
    const url = ln.live ? safeHttpUrl(ln.url) : null
    if (url) return url
  }
  return null
}
