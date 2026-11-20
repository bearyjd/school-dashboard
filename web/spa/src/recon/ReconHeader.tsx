import { KidChip } from '../design/KidChip'
import { Tile } from '../design/Tile'
import type { KidView } from './group'
import type { ReconBucket } from './types'

interface Props {
  views: KidView[]
  roster: { name: string; color: string }[]
  kid: string
  onKid: (name: string) => void
}

const TILES: { id: ReconBucket; label: string }[] = [
  { id: 'needs_action', label: 'Needs action' },
  { id: 'do_tonight', label: 'Do tonight' },
  { id: 'waiting_on_teacher', label: 'Waiting on teacher' },
]

const total = (views: KidView[], id: ReconBucket): number => views.reduce((sum, v) => sum + v.counts[id], 0)

function scrollTo(id: ReconBucket) {
  const target = document.querySelector<HTMLElement>(`[data-bucket="${id}"]`)
  const calm = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
  target?.scrollIntoView?.({ behavior: calm ? 'auto' : 'smooth', block: 'start' })
}

/** Sticky: the three counts (tap = jump to that bucket) and the kid switcher. */
export function ReconHeader({ views, roster, kid, onKid }: Props) {
  return (
    <div className="recon__sticky">
      <section aria-label="Summary" className="recon__tiles">
        {TILES.map(t => {
          const n = total(views, t.id)
          const tone = t.id === 'needs_action' ? (n > 0 ? 'overdue' : 'ok') : t.id === 'do_tonight' ? 'tonight' : 'neutral'
          return (
            <div key={t.id} className={`recon__tile recon__tile--${t.id}${n > 0 ? ' recon__tile--hot' : ''}`}>
              <Tile label={t.label} value={n} tone={tone} onClick={() => scrollTo(t.id)} />
            </div>
          )
        })}
      </section>
      <div className="recon__kids" role="group" aria-label="Filter by child">
        <button type="button" className="pill" aria-pressed={kid === ''} onClick={() => onKid('')}>All</button>
        {roster.map(k => (
          <button key={k.name} type="button" className="pill" aria-pressed={kid === k.name} onClick={() => onKid(k.name)}>
            <KidChip name={k.name} color={k.color} wide />
          </button>
        ))}
      </div>
    </div>
  )
}
