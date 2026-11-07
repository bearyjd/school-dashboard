import './design.css'

interface FreshnessTagProps {
  ageHours: number | null
  stale?: boolean
  failing?: boolean
}

function formatAge(hours: number | null): string {
  if (hours === null || !Number.isFinite(hours)) return 'unknown age'
  if (hours < 1) return 'just now'
  const whole = Math.floor(hours)
  if (whole < 24) return `${whole}h ago`
  return `${Math.floor(whole / 24)}d ${whole % 24}h ago`
}

export function FreshnessTag({ ageHours, stale = false, failing = false }: FreshnessTagProps) {
  const age = formatAge(ageHours)
  if (failing) {
    return <span className="freshness freshness--failing"><span aria-hidden="true">{'✖'}</span> Failing, last good {age}</span>
  }
  if (stale) {
    return <span className="freshness freshness--stale"><span aria-hidden="true">{'⚠'}</span> Stale, as of {age}</span>
  }
  return <span className="freshness">Updated {age}</span>
}
