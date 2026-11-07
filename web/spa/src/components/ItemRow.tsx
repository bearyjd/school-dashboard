import type { Freshness, Overview, OverviewItem } from '../api/types'
import { KidChip } from '../design/KidChip'
import { Row } from '../design/Row'
import { StatusBadge } from '../design/StatusBadge'
import { formatDay, itemFreshness, kidIndex, sourceLabel } from '../lib/view'
import { AsOfTag } from './OverviewFrame'

interface ItemRowProps {
  item: OverviewItem
  data: Overview
}

function metaFor(item: OverviewItem): string {
  const due = formatDay(item.due)
  return [item.course, due && `due ${due}`, sourceLabel(item.source)].filter(Boolean).join(' · ')
}

/** One homework row: kid chip, title (text only), course/due/source, status badge, and an as-of tag for stale sources. */
export function ItemRow({ item, data }: ItemRowProps) {
  const stale: Freshness | null = itemFreshness(item, data.freshness)
  return (
    <Row
      title={item.title}
      meta={metaFor(item)}
      href={item.url}
      lead={<KidChip name={item.kid} index={kidIndex(data, item.kid)} />}
      trail={
        <span className="item-row__trail">
          <StatusBadge status={item.status === 'done' ? 'upcoming' : item.status} />
          {stale && <AsOfTag freshness={stale} />}
        </span>
      }
    />
  )
}
