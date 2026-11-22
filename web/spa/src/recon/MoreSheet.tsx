import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { getDetail, postTeacherConfirmed } from './api'
import { messageFor } from './errors'
import { formatInstant } from './format'
import { DETAIL_KEY } from './queries'
import { Sheet } from './Sheet'
import type { Actor, ReconDetail, ReconEvent, ReconItem, Role } from './types'

const NOTE_MIN = 3
const NOTE_MAX = 500

interface Props {
  item: ReconItem
  role: Role
  opener: HTMLElement | null
  /** The actor when this device may write; otherwise null (and the setup sheet is already opening). */
  requireWrite: () => Actor | null
  onClose: () => void
  onConfirmed: (item: ReconItem) => void
}

function eventLine(e: ReconEvent): string {
  const change = e.from !== null && e.to !== null ? `: ${e.from} to ${e.to}` : ''
  return `${formatInstant(e.at)} - ${e.actor ?? e.setter} - ${e.kind}${change}`
}

function History({ detail }: { detail: ReturnType<typeof useDetail> }) {
  if (detail.isError) return <p className="recon-sheet__msg">Could not load the history.</p>
  if (!detail.data) return <p className="recon-sheet__hint">Loading history...</p>
  const d: ReconDetail = detail.data
  return (
    <>
      {d.events.length === 0 ? <p className="recon-sheet__hint">No history yet.</p> : (
        <ul className="recon-hist">
          {d.events.map((e, i) => (
            <li key={`${e.at}-${i}`}>
              <span>{eventLine(e)}</span>
              {e.detail && <span className="recon-hist__detail">{e.detail}</span>}
            </li>
          ))}
        </ul>
      )}
      {d.notes.length > 0 && (
        <>
          <h3 className="recon-sheet__sub">Note drafts</h3>
          <ul className="recon-hist">
            {d.notes.map(n => <li key={n.id}>{`Draft note (${n.flag}) - ${n.status} - ${formatInstant(n.created_at)}`}</li>)}
          </ul>
        </>
      )}
    </>
  )
}

const useDetail = (id: number) =>
  useQuery({ queryKey: DETAIL_KEY(id), queryFn: () => getDetail(id), retry: false, staleTime: 0 })

/** History, note drafts and (parents only) "Teacher confirmed" for one assignment. History is loaded when opened. */
export function MoreSheet({ item, role, opener, requireWrite, onClose, onConfirmed }: Props) {
  const detail = useDetail(item.id)
  const [note, setNote] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const trimmed = note.trim()
  const valid = trimmed.length >= NOTE_MIN && trimmed.length <= NOTE_MAX
  const canConfirm = role === 'parent' && item.kind === 'assignment' && !item.dismissed_at && (item.stage === 2 || item.stage === 3)

  const confirm = async () => {
    const actor = requireWrite()
    if (!actor) return
    setBusy(true)
    setMessage('')
    try {
      const res = await postTeacherConfirmed(item.id, trimmed, actor)
      if (res.item) onConfirmed(res.item)
      else { setMessage('Saved, but this row could not be refreshed - reload to see it'); setBusy(false) }
    } catch (e) {
      setMessage(messageFor(e))
      setBusy(false)
    }
  }

  return (
    <Sheet label={`More for ${item.title}`} opener={opener} onClose={onClose}>
      <h2 className="recon-sheet__title">{item.title}</h2>
      <h3 className="recon-sheet__sub">History</h3>
      <History detail={detail} />
      {canConfirm && (
        <form className="recon-sheet__form" onSubmit={e => { e.preventDefault(); if (valid) void confirm() }}>
          <h3 className="recon-sheet__sub">Teacher confirmed</h3>
          <label htmlFor="recon-confirm-note" className="recon-sheet__hint">
            Note for the teacher confirmation ({NOTE_MIN}-{NOTE_MAX} characters)
          </label>
          <textarea id="recon-confirm-note" rows={3} value={note} onChange={e => setNote(e.target.value)} />
          <p className="recon-sheet__msg" aria-live="polite">{message}</p>
          <div className="recon-sheet__actions">
            <button type="submit" className="recon-btn recon-btn--primary" disabled={!valid || busy}>Teacher confirmed</button>
          </div>
        </form>
      )}
      {!canConfirm && <p className="recon-sheet__msg" aria-live="polite">{message}</p>}
    </Sheet>
  )
}
