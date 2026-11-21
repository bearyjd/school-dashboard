import { sourceLabel } from '../lib/view'
import { safeHttpUrl } from './safeUrl'
import type { ReconItem, ReconLink } from './types'

const INFO_LABELS: Record<string, string> = {
  late: 'Late in portal',
  cleared_unconfirmed: 'Cleared, unconfirmed',
  conflict: 'Portals disagree',
}

function badgeText(ln: ReconLink): string {
  return `${sourceLabel(ln.source)}: ${ln.status}${ln.live ? '' : ' (not seen lately)'}`
}

/** One badge per portal link: source and portal status. It is a link only for a plain http(s) url. */
export function SourceBadges({ links }: { links: ReconLink[] }) {
  // "none" is a placeholder (legacy or manual record): shown only when it is all there is.
  const shown = links.length === 1 ? links : links.filter(ln => ln.status !== 'none')
  if (shown.length === 0) return null
  return (
    <div className="recon-badges">
      {shown.map(ln => {
        const url = safeHttpUrl(ln.url)
        return url ? (
          <a key={ln.id} className="recon-badge recon-badge--link" href={url} target="_blank" rel="noopener noreferrer">{badgeText(ln)}</a>
        ) : (
          <span key={ln.id} className="recon-badge">{badgeText(ln)}</span>
        )
      })}
    </div>
  )
}

/** Stage label plus the info flags, always as words (never colour alone). */
export function StatusChips({ item }: { item: ReconItem }) {
  const info = item.info.flatMap(i => (INFO_LABELS[i] ? [INFO_LABELS[i]] : []))
  if (item.possible_duplicate) info.push('Possible duplicate')
  if (item.unverified) info.push('Not verified')
  return (
    <div className="recon-chips">
      <span className="recon-chip recon-chip--stage">{item.stage_label}</span>
      {info.map(t => <span key={t} className="recon-chip recon-chip--warn">{t}</span>)}
    </div>
  )
}
