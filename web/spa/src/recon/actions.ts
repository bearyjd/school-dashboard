import { safeHttpUrl } from './safeUrl'
import type { ReconItem, Role } from './types'

export type RowAction = 'draft_note' | 'done' | 'turned_in' | 'undo' | 'more'

const MAIN: Record<number, RowAction[]> = { 1: ['done', 'turned_in'], 2: ['turned_in', 'undo'], 3: ['undo'] }

/** The buttons a row offers, in display order. Practice (IXL) rows are read-only; tasks only toggle done. */
export function rowActions(item: ReconItem, role: Role): RowAction[] {
  if (item.kind === 'practice') return []
  if (item.kind === 'task') return item.stage >= 2 ? ['undo'] : ['done']
  const note: RowAction[] = role === 'parent' && item.bucket === 'needs_action' ? ['draft_note'] : []
  return [...note, ...(MAIN[item.stage] ?? []), 'more']
}

/** The one action drawn as the primary button: the first that is not Undo or More. */
export const primaryAction = (actions: RowAction[]): RowAction | undefined => actions.find(a => a !== 'undo' && a !== 'more')

/** First live link with a plain http(s) url. */
export function portalLink(item: ReconItem): string | null {
  for (const ln of item.links) {
    const url = ln.live ? safeHttpUrl(ln.url) : null
    if (url) return url
  }
  return null
}
