import { portalLink, primaryAction, rowActions, type RowAction } from './actions'
import { formatDue } from './format'
import { SourceBadges, StatusChips } from './RowBadges'
import type { RowStatus } from './useRowStatus'
import type { ReconItem, Role } from './types'

const TEXT: Record<RowAction, string> = { done: 'Done', turned_in: 'Turned in', undo: 'Undo', draft_note: 'Draft note', more: 'More' }
const NAME: Record<RowAction, (title: string) => string> = {
  done: t => `Mark ${t} done`,
  turned_in: t => `Mark ${t} turned in`,
  undo: t => `Undo ${t}`,
  draft_note: t => `Draft note for ${t}`,
  more: t => `More for ${t}`,
}

interface Props {
  item: ReconItem
  role: Role
  status: RowStatus
  /** Writes are switched off on the server. */
  disabled: boolean
  onAction: (item: ReconItem, action: RowAction, opener: HTMLElement) => void
}

/** One assignment: text first, actions as big buttons at the bottom right. All server text is rendered as plain text. */
export function ReconRow({ item, role, status, disabled, onAction }: Props) {
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
          {item.class && <span>{item.class}</span>}
          <span>{formatDue(item.due)}</span>
        </p>
        <SourceBadges links={item.links} />
        <p className="recon-row__msg" aria-live="polite">{status.error}</p>
        <div className="recon-row__actions">
          {portal && (
            <a className="recon-btn recon-btn--quiet" href={portal} target="_blank" rel="noopener noreferrer" aria-label={`Open in portal: ${item.title}`}>
              Open in portal
            </a>
          )}
          {actions.map(a => (
            <button
              key={a}
              type="button"
              className={`recon-btn${a === primary ? (needs ? ' recon-btn--alert' : ' recon-btn--primary') : ''}`}
              aria-label={NAME[a](item.title)}
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
