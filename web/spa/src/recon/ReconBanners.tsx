import { SourceBanners } from '../components/OverviewFrame'
import { Banner } from '../design/Banner'
import { formatAge } from '../lib/age'
import { useOverview } from '../lib/overview'
import { problemSources, sourceLabel } from '../lib/view'
import type { ReconSummary } from './types'

const INGEST = 'recon_ingest'

/** The overview names Schoology "sgy"; the v2 summary names it "schoology". */
const freshnessKey = (source: string): string => (source === 'schoology' ? 'sgy' : source)

/**
 * "Not verified" notices from the v2 summary plus the per-source banners from /api/overview (quiet on failure).
 * A source the overview already flags gets ONE banner that says both; the rest keep the plain notice.
 */
export function ReconBanners({ summary }: { summary: ReconSummary }) {
  const overview = useOverview().data
  const problems = new Map(overview ? problemSources(overview.freshness) : [])
  const stale = summary.unverified_sources.filter(s => s !== INGEST)
  const merged = stale.filter(s => problems.has(freshnessKey(s)))
  const plain = stale.filter(s => !problems.has(freshnessKey(s))).map(sourceLabel)
  const mergedKeys = new Set(merged.map(freshnessKey))
  const mergeFailed = !summary.ingest_ok || summary.unverified_sources.includes(INGEST)
  const rest = overview ? Object.fromEntries(Object.entries(overview.freshness).filter(([k]) => !mergedKeys.has(k))) : {}
  return (
    <div className="frame__banners">
      {plain.length > 0 && <Banner tone="warn">{`Not verified: ${plain.join(', ')} data is out of date`}</Banner>}
      {merged.map(s => {
        const f = problems.get(freshnessKey(s))
        const detail = f?.issue === 'never_ok' ? 'no successful sync yet' : `last good ${formatAge(f?.age_hours ?? null)}`
        return <Banner key={s} tone={f?.failing ? 'error' : 'warn'}>{`Not verified: ${sourceLabel(s)} data is out of date (${detail})`}</Banner>
      })}
      {mergeFailed && <Banner tone="warn">Not verified: the last assignment merge did not succeed, so this may be out of date</Banner>}
      {overview && <SourceBanners freshness={rest} />}
    </div>
  )
}
