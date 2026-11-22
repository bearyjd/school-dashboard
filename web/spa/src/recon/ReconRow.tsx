import { portalLink, primaryAction, rowActions, type RowAction } from './actions'
import { formatDue } from './format'
import { SourceBadges, StatusChips } from './RowBadges'
import type { RowStatus } from './useRowStatus'
import type { ReconItem, Role } from './types'

const TEXT: Record<RowAction, string> = {
  done: 'Done', turned_in: 'Turned in', undo: 'Undo', dismiss: 'Dismiss', restore: 'Restore', draft_note: 'Draft note', more: 'More',
}
const NAME: Record<RowAction, (title: string) => string> = {
  done: t => `Mark ${t} done`,
  turned_in: t => `Mark ${t} turned in`,
  undo: t => `Undo ${t}`,
  dismiss: t => `Dismiss ${t}`,
  restore: t => `Restore ${t}`,
  draft_note: t => `Draft note for ${t}`,
  more: t => `More for ${t}`,
}

export interface ReconRowControls {
  role: Role
  /** Writes are switched off on the server. */
  disabled: boolean
  showKid: boolean
  onAction: (item: ReconItem, action: RowAction, opener: HTMLElement) => void
}

interface Props extends ReconRowControls {
  item: ReconItem
  status: RowStatus
}

/** One assignment: text first, actions as big buttons at the bottom right. All server text is rendered as plain text. */
/** "Reading log (English, Alex)": the title plus what tells same-titled rows apart. */
function label(item: ReconItem, showKid: boolean): string {
  const extra = [item.class, showKid ? item.kid : null].filter(Boolean).join(', ')
  return extra ? `${item.title} (${extra})` : item.title
}

export function ReconRow({ item, role, status, disabled, showKid, onAction }: Props) {
  const name = label(item, showKid)
  const actions = rowActions(item, role)
  const primary = primaryAction(actions)
  const portal = portalLink(item)
  const needs = item.bucket === 'needs_action'
  return (
    <li className="recon-row-item">
      <div role="group" aria-label={item.title} className="recon-row" aria-busy={status.pending || undefined}>
        <div className="recon-row__head">
          <p className="recon-row__title">{item.title}</p>
          <StatusChips item={item} />
        </div>
        <p className="recon-row__meta">
          {item.dismissed_at && showKid && <span>{item.kid}</span>}
          {item.class && <span>{item.class}</span>}
          <span>{formatDue(item.due)}</span>
        </p>
        <SourceBadges links={item.links} />
        <p className="recon-row__msg" aria-live="polite">{status.error}</p>
        <div className="recon-row__actions">
          {portal && (
            <a className="recon-btn recon-btn--quiet" href={portal} target="_blank" rel="noopener noreferrer" aria-label={`Open in portal: ${name}`}>
              Open in portal
            </a>
          )}
          {actions.map(a => (
            <button
              key={a}
              type="button"
              className={`recon-btn${a === primary ? (needs ? ' recon-btn--alert' : ' recon-btn--primary') : ''}`}
              aria-label={NAME[a](name)}
              disabled={disabled || status.pending}
              onClick={e => onAction(item, a, e.currentTarget)}
            >
              {TEXT[a]}
            </button>
          ))}
        </div>
      </div>
    </li>
  )
}
