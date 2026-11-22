import { useQueryClient, type InfiniteData } from '@tanstack/react-query'
import { useCallback, useState } from 'react'
import { APP_TZ } from '../lib/view'
import { localToday } from '../lib/week'
import { checkAccess, type Need } from './access'
import type { RowAction } from './actions'
import { LIST_LIMIT, postNote, postStage } from './api'
import { messageFor } from './errors'
import { optimisticItem, shiftKidCounts } from './optimistic'
import { DETAIL_KEY, DISMISSED_KEY, LIST_KEY, SUMMARY_KEY } from './queries'
import type { Actor, ListData, NoteDraft, ReconConfig, ReconItem, ReconSummary, StageAction, WriteResult } from './types'
import { IDLE, useRowStatus } from './useRowStatus'

export type SheetState =
  | { type: 'note'; item: ReconItem; draft: NoteDraft; actor: Actor; opener: HTMLElement }
  | { type: 'more'; item: ReconItem; opener: HTMLElement }
  | { type: 'setup'; needs: Need[]; opener: HTMLElement }

export interface ToastState {
  id: number
  text: string
  undo?: () => void
}

const DONE_TEXT: Record<StageAction, (title: string) => string> = {
  done: t => `Marked ${t} done`,
  turned_in: t => `Marked ${t} turned in`,
  dismiss: t => `Dismissed ${t}`,
  undo: t => `Undid ${t}`,
}

/**
 * Everything a tap can do: gate on name/token, optimistic stage changes with rollback, note drafts, and the
 * sheet and toast state the screen renders. The server's answer always replaces the optimistic row.
 */
export function useReconActions(config: ReconConfig | undefined) {
  const qc = useQueryClient()
  const rows = useRowStatus()
  const [sheet, setSheet] = useState<SheetState | null>(null)
  const [toast, setToast] = useState<ToastState | null>(null)

  const replace = useCallback((item: ReconItem, restoredFrom?: ReconItem) => {
    const cached = qc.getQueryData<ListData>(LIST_KEY)?.items.find(i => i.id === item.id)
    const before = cached ?? restoredFrom
    qc.setQueryData<ListData>(LIST_KEY, d => {
      if (!d) return d
      if (cached) return { ...d, items: d.items.map(i => (i.id === item.id ? item : i)) }
      if (!restoredFrom || item.dismissed_at || item.stage === 4) return d
      const items = [...d.items, item].sort((a, b) =>
        Number(a.due === null) - Number(b.due === null) || (a.due ?? '').localeCompare(b.due ?? '') || a.id - b.id)
      return { total: d.total + 1, items: items.slice(0, LIST_LIMIT) }
    })
    if (before) qc.setQueryData<ReconSummary>(SUMMARY_KEY, s => (s ? shiftKidCounts(s, before, item) : s))
  }, [qc])

  const showToast = useCallback((text: string, undo?: () => void) => {
    setToast(prev => ({ id: (prev?.id ?? 0) + 1, text, undo }))
  }, [])

  const requireWrite = useCallback((opener: HTMLElement): Actor | null => {
    const access = checkAccess(config)
    if (access.kind === 'ready') return access.actor
    if (access.kind === 'setup') setSheet({ type: 'setup', needs: access.needs, opener })
    return null
  }, [config])

  /** Apply one change and return the server's reply, or null after a failed write. */
  const tap = useCallback(async (item: ReconItem, action: StageAction, actor: Actor): Promise<WriteResult | null> => {
    const expectedDismissedAt = action === 'undo' ? item.dismissed_at ?? undefined : undefined
    rows.set(item.id, { pending: true, error: null })
    // A refetch that started before this tap must not land on top of the optimistic row.
    await qc.cancelQueries({ queryKey: LIST_KEY })
    if (expectedDismissedAt) await qc.cancelQueries({ queryKey: DISMISSED_KEY })
    if (!expectedDismissedAt) replace(optimisticItem(item, action, localToday(new Date(), APP_TZ)))
    try {
      const res = await postStage(item.id, action, actor, expectedDismissedAt)
      if (expectedDismissedAt) {
        const wasRestored = (row: ReconItem) => row.id === item.id && row.dismissed_at === expectedDismissedAt
        qc.setQueriesData<InfiniteData<ListData>>({ queryKey: DISMISSED_KEY }, data => {
          if (!data?.pages.some(page => page.items.some(wasRestored))) return data
          const pages = data.pages.map(page => ({ total: page.total - 1, items: page.items.filter(i => !wasRestored(i)) }))
          return { ...data, pages }
        })
      }
      // Saved but not rebuilt: the invalidation below refetches the row.
      if (res.item) replace(res.item, expectedDismissedAt ? item : undefined)
      rows.set(item.id, IDLE)
      return res
    } catch (e) {
      if (expectedDismissedAt) {
        rows.set(item.id, IDLE)
        showToast(messageFor(e))
      } else {
        replace(item)
        rows.set(item.id, { pending: false, error: messageFor(e) })
      }
      return null
    } finally {
      void qc.invalidateQueries({ queryKey: LIST_KEY })
      void qc.invalidateQueries({ queryKey: SUMMARY_KEY })
      if (action === 'dismiss' || expectedDismissedAt) {
        void qc.invalidateQueries({ queryKey: DISMISSED_KEY })
        void qc.invalidateQueries({ queryKey: DETAIL_KEY(item.id) })
      }
    }
  }, [qc, rows, replace, showToast])

  /** A tap from a row: the toast offers one Undo (which has no Undo of its own). */
  const runStage = useCallback(async (item: ReconItem, action: StageAction, actor: Actor) => {
    const result = await tap(item, action, actor)
    const done = result?.item
    if (!done) return
    if (action === 'dismiss' && (!result.dismissed_at || done.dismissed_at !== result.dismissed_at)) return
    if (action === 'undo' && item.dismissed_at && done.dismissed_at) return
    const undo = async () => {
      const restored = (await tap(done, 'undo', actor))?.item
      if (restored && !restored.dismissed_at) showToast(done.dismissed_at ? `Restored ${item.title}` : DONE_TEXT.undo(item.title))
    }
    const text = action === 'undo' && item.dismissed_at ? `Restored ${item.title}` : DONE_TEXT[action](item.title)
    showToast(text, action === 'undo' ? undefined : () => { void undo() })
  }, [tap, showToast])

  const draftNote = useCallback(async (item: ReconItem, actor: Actor, opener: HTMLElement) => {
    rows.set(item.id, { pending: true, error: null })
    try {
      const draft = await postNote(item.id, actor)
      rows.set(item.id, IDLE)
      setSheet({ type: 'note', item, draft, actor, opener })
    } catch (e) {
      rows.set(item.id, { pending: false, error: messageFor(e) })
    }
  }, [rows])

  const onAction = useCallback((item: ReconItem, action: RowAction, opener: HTMLElement) => {
    if (action === 'more') {
      setSheet({ type: 'more', item, opener })
      return
    }
    const actor = requireWrite(opener)
    if (!actor) return
    if (action === 'draft_note') void draftNote(item, actor, opener)
    else void runStage(item, action === 'restore' ? 'undo' : action, actor)
  }, [requireWrite, draftNote, runStage])

  return {
    statusOf: rows.statusOf, sheet, toast, onAction, requireWrite, replace, showToast,
    closeSheet: () => setSheet(null), dismissToast: () => setToast(null),
  }
}
