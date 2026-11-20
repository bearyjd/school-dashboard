import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useState } from 'react'
import { APP_TZ } from '../lib/view'
import { localToday } from '../lib/week'
import { checkAccess, type Need } from './access'
import type { RowAction } from './actions'
import { postNote, postStage } from './api'
import { messageFor } from './errors'
import { optimisticItem } from './optimistic'
import { LIST_KEY, SUMMARY_KEY } from './queries'
import type { Actor, ListData, NoteDraft, ReconConfig, ReconItem, StageAction } from './types'
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

  const replace = useCallback((item: ReconItem) => {
    qc.setQueryData<ListData>(LIST_KEY, d => (d ? { ...d, items: d.items.map(i => (i.id === item.id ? item : i)) } : d))
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

  /** One optimistic stage change; resolves to the server's row, or null after rolling back. */
  const tap = useCallback(async (item: ReconItem, action: StageAction, actor: Actor): Promise<ReconItem | null> => {
    rows.set(item.id, { pending: true, error: null })
    // A refetch that started before this tap must not land on top of the optimistic row.
    await qc.cancelQueries({ queryKey: LIST_KEY })
    replace(optimisticItem(item, action, localToday(new Date(), APP_TZ)))
    try {
      const res = await postStage(item.id, action, actor)
      replace(res.item)
      rows.set(item.id, IDLE)
      return res.item
    } catch (e) {
      replace(item)
      rows.set(item.id, { pending: false, error: messageFor(e) })
      return null
    } finally {
      void qc.invalidateQueries({ queryKey: LIST_KEY })
      void qc.invalidateQueries({ queryKey: SUMMARY_KEY })
    }
  }, [qc, rows, replace])

  /** A tap from a row: the toast offers one Undo (which has no Undo of its own). */
  const runStage = useCallback(async (item: ReconItem, action: StageAction, actor: Actor) => {
    const done = await tap(item, action, actor)
    if (!done) return
    const undo = async () => {
      if (await tap(done, 'undo', actor)) showToast(DONE_TEXT.undo(item.title))
    }
    showToast(DONE_TEXT[action](item.title), action === 'undo' ? undefined : () => { void undo() })
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
    else void runStage(item, action, actor)
  }, [requireWrite, draftNote, runStage])

  return {
    statusOf: rows.statusOf, sheet, toast, onAction, requireWrite, replace, showToast,
    closeSheet: () => setSheet(null), dismissToast: () => setToast(null),
  }
}
