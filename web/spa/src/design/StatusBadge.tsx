import './design.css'

export type Status = 'overdue' | 'missing' | 'late' | 'tonight' | 'upcoming'

const ICONS: Record<Status, string> = {
  overdue: '!',
  missing: '?',
  late: '⏱',
  tonight: '☾',
  upcoming: '→',
}

export function StatusBadge({ status }: { status: Status }) {
  return (
    <span className={`status-badge status-badge--${status}`}>
      <span aria-hidden="true">{ICONS[status]}</span>
      {status}
    </span>
  )
}
