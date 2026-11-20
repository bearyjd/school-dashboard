import { useId } from 'react'
import { KidChip } from '../design/KidChip'
import { kidSlot } from '../lib/kidSlot'
import type { RowAction } from './actions'
import type { BucketView, KidView } from './group'
import { ReconRow } from './ReconRow'
import type { ReconItem, Role } from './types'
import type { RowStatus } from './useRowStatus'

interface RowProps {
  role: Role
  disabled: boolean
  /** The all-kids view: action names then say which kid the row is for. */
  showKid: boolean
  statusOf: (id: number) => RowStatus
  onAction: (item: ReconItem, action: RowAction, opener: HTMLElement) => void
}

function Bucket({ bucket, rows }: { bucket: BucketView; rows: RowProps }) {
  const id = useId()
  return (
    <section data-bucket={bucket.id} aria-labelledby={id} className={`recon-bucket recon-bucket--${bucket.id}`}>
      <h3 id={id} className="recon-bucket__h">{`${bucket.label} (${bucket.count})`}</h3>
      {bucket.groups.map(g => (
        <div key={g.key} className="recon-group">
          {g.label && <h4 className="recon-group__h">{g.label}</h4>}
          <ul className="recon-list">
            {g.items.map(i => (
              <ReconRow key={i.id} item={i} role={rows.role} status={rows.statusOf(i.id)} disabled={rows.disabled} showKid={rows.showKid} onAction={rows.onAction} />
            ))}
          </ul>
        </div>
      ))}
    </section>
  )
}

/** One kid: coloured rule and name, the IXL line, then the buckets (or an all-clear line). */
export function KidSection({ view, rows }: { view: KidView; rows: RowProps }) {
  const id = useId()
  return (
    <section aria-labelledby={id} className={`recon-kid kid-${kidSlot(view.color)}`}>
      <div className="recon-kid__head">
        <KidChip name={view.name} color={view.color} />
        <h2 id={id} className="recon-kid__name">{view.name}</h2>
      </div>
      {view.ixl && <p className="recon-kid__ixl">{view.ixl}</p>}
      {view.state === 'clear' && <p className="recon-kid__clear">All clear</p>}
      {view.state === 'unverified_clear' && <p className="recon-kid__clear recon-kid__clear--warn">Nothing open (not verified)</p>}
      <div className="recon-kid__buckets">
        {view.buckets.map(b => <Bucket key={b.id} bucket={b} rows={rows} />)}
      </div>
    </section>
  )
}
