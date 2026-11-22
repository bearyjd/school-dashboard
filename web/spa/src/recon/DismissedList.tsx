import { useState } from 'react'
import type { RowProps } from './KidSection'
import { useDismissedAssignments } from './queries'
import { ReconRow } from './ReconRow'

export function DismissedList({ kid, rows: { statusOf, ...controls } }: { kid: string; rows: RowProps }) {
  const [expanded, setExpanded] = useState(false)
  const dismissed = useDismissedAssignments(kid, expanded)
  const items = dismissed.data?.pages.flatMap(page => page.items) ?? []
  return (
    <details className="recon-dismissed" onToggle={e => setExpanded(e.currentTarget.open)}>
      <summary>Dismissed</summary>
      {expanded && dismissed.isError && !dismissed.data && (
        <>
          <p role="status">Could not load dismissed assignments.</p>
          <button type="button" className="recon-btn" onClick={() => void dismissed.refetch()}>Retry</button>
        </>
      )}
      {expanded && dismissed.isPending && <p role="status">Loading dismissed assignments…</p>}
      {expanded && dismissed.isSuccess && items.length === 0 && <p>No dismissed assignments.</p>}
      {expanded && items.length > 0 && (
        <ul className="recon-list">
          {items.map(item => <ReconRow key={item.id} {...controls} item={item} status={statusOf(item.id)} />)}
        </ul>
      )}
      {expanded && dismissed.hasNextPage && (
        <button type="button" className="recon-btn" disabled={dismissed.isFetching} onClick={() => void dismissed.fetchNextPage()}>
          {dismissed.isFetchingNextPage ? 'Loading…' : dismissed.isFetchNextPageError ? 'Retry loading more' : 'Load more'}
        </button>
      )}
    </details>
  )
}
