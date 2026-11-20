import { SourceBanners } from '../components/OverviewFrame'
import { Banner } from '../design/Banner'
import { useOverview } from '../lib/overview'
import { sourceLabel } from '../lib/view'
import type { ReconSummary } from './types'

const INGEST = 'recon_ingest'

/** "Not verified" notices from the v2 summary, plus the existing per-source banners from /api/overview (quiet on failure). */
export function ReconBanners({ summary }: { summary: ReconSummary }) {
  const overview = useOverview().data
  const stale = summary.unverified_sources.filter(s => s !== INGEST).map(sourceLabel)
  const mergeFailed = !summary.ingest_ok || summary.unverified_sources.includes(INGEST)
  return (
    <div className="frame__banners">
      {stale.length > 0 && <Banner tone="warn">{`Not verified: ${stale.join(', ')} data is out of date`}</Banner>}
      {mergeFailed && <Banner tone="warn">Not verified: the last assignment merge did not succeed, so this may be out of date</Banner>}
      {overview && <SourceBanners freshness={overview.freshness} />}
    </div>
  )
}
