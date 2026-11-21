import { useState } from 'react'
import { patchNote } from './api'
import { copyText } from './clipboard'
import { messageFor } from './errors'
import { safeHttpUrl, safeMailto } from './safeUrl'
import { Sheet } from './Sheet'
import type { Actor, NoteDraft, ReconItem } from './types'

interface Props {
  item: ReconItem
  draft: NoteDraft
  actor: Actor
  opener: HTMLElement | null
  onClose: () => void
  /** The parent said what happened; the sheet closes and the screen shows `message`. */
  onResolved: (message: string) => void
}

/** A draft for the parent to copy or open in their own mail app. Nothing here, or anywhere, sends anything. */
export function NoteSheet({ item, draft, actor, opener, onClose, onResolved }: Props) {
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const text = `${draft.subject}\n\n${draft.body}`
  const mailto = safeMailto(draft.mailto)
  const portal = safeHttpUrl(draft.portal_url)

  const copy = async () => setMessage((await copyText(text)) ? 'Copied' : 'Could not copy - select the text and copy it yourself')

  const resolve = async (status: 'sent_marked' | 'discarded') => {
    setBusy(true)
    setMessage('')
    try {
      await patchNote(item.id, draft.id, status, actor)
      onResolved(status === 'sent_marked' ? 'Marked as sent' : 'Draft discarded')
    } catch (e) {
      setMessage(messageFor(e))
      setBusy(false)
    }
  }

  return (
    <Sheet label={`Draft note: ${item.title}`} opener={opener} onClose={onClose}>
      <h2 className="recon-sheet__title">Draft note</h2>
      <p className="recon-sheet__hint">For {item.kid}: {item.title}</p>
      <textarea className="recon-sheet__text" readOnly rows={10} aria-label="Draft note text" value={text} />
      <p className="recon-sheet__hint">Nothing is sent automatically.</p>
      <div className="recon-sheet__actions recon-sheet__actions--wrap">
        <button type="button" className="recon-btn" onClick={() => void copy()}>Copy</button>
        {mailto && <a className="recon-btn" href={mailto}>Open in mail app</a>}
        {portal && <a className="recon-btn" href={portal} target="_blank" rel="noopener noreferrer">Open in portal</a>}
      </div>
      <p className="recon-sheet__msg" aria-live="polite">{message}</p>
      <div className="recon-sheet__actions">
        <button type="button" className="recon-btn" disabled={busy} onClick={() => void resolve('discarded')}>Discard</button>
        <button type="button" className="recon-btn recon-btn--primary" disabled={busy} onClick={() => void resolve('sent_marked')}>I sent it</button>
      </div>
    </Sheet>
  )
}
