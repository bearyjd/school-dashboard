import { formatAge } from '../lib/age'
import './design.css'

interface FreshnessTagProps {
  ageHours: number | null
  stale?: boolean
  failing?: boolean
  /** No leading icon; use inside a Banner, which already draws one. */
  bare?: boolean
}

export function FreshnessTag({ ageHours, stale = false, failing = false, bare = false }: FreshnessTagProps) {
  const age = formatAge(ageHours)
  if (failing) {
    return <span className="freshness freshness--failing">{!bare && <span aria-hidden="true">{'✖'}</span>} Failing, last good {age}</span>
  }
  if (stale) {
    return <span className="freshness freshness--stale">{!bare && <span aria-hidden="true">{'⚠'}</span>} Stale, as of {age}</span>
  }
  return <span className="freshness">Updated {age}</span>
}
