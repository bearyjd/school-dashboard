import type { ReactNode } from 'react'
import type { UseQueryResult } from '@tanstack/react-query'
import type { Freshness, Overview } from '../api/types'
import { Banner } from '../design/Banner'
import { FreshnessTag } from '../design/FreshnessTag'
import { formatAge } from '../lib/age'
import { KidChip } from '../design/KidChip'
import { ageHoursSince, kidIndex, problemSources, sourceLabel } from '../lib/view'
import './OverviewFrame.css'

/** Banner per stale or failing source, with its age. */
export function SourceBanners({ freshness }: { freshness: Record<string, Freshness> }) {
  const bad = problemSources(freshness)
  if (bad.length === 0) return null
  return (
    <div className="frame__banners">
      {bad.map(([key, f]) => (
        <Banner key={key} tone={f.failing ? 'error' : 'warn'}>
          <strong>{sourceLabel(key)}</strong>{' '}
          {f.issue === 'never_ok'
            ? <span className="freshness freshness--stale">No successful sync yet</span>
            : <FreshnessTag ageHours={f.age_hours} stale={f.stale && !f.failing} failing={f.failing} bare />}
        </Banner>
      ))}
    </div>
  )
}

/** Muted "as of" tag for rows whose source is stale or failing (failing gets its own icon). */
export function AsOfTag({ freshness }: { freshness: Freshness }) {
  if (freshness.paused) {
    return (
      <span className="as-of as-of--paused">
        <span aria-hidden="true">{'⏸'}</span> paused, last data as of {formatAge(freshness.age_hours)}
      </span>
    )
  }
  const icon = freshness.failing ? '✖' : '⚠'
  return <span className="as-of"><span aria-hidden="true">{icon}</span> as of {formatAge(freshness.age_hours)}</span>
}

interface KidFilterProps {
  data: Overview
  value: string
  onChange: (kid: string) => void
}

/** "Everyone" plus one pill per roster kid; selection lives in the URL via the caller. */
export function KidFilter({ data, value, onChange }: KidFilterProps) {
  return (
    <div className="kid-filter" role="group" aria-label="Filter by child">
      <button type="button" className="pill" aria-pressed={value === ''} onClick={() => onChange('')}>Everyone</button>
      {data.kids.map(k => (
        <button key={k.name} type="button" className="pill" aria-pressed={value === k.name} onClick={() => onChange(k.name)}>
          <KidChip name={k.name} index={kidIndex(data, k.name)} wide />
        </button>
      ))}
    </div>
  )
}

interface PanelProps {
  id: string
  labelledBy: string
}

interface FrameProps {
  title: string
  query: UseQueryResult<Overview>
  children: (data: Overview) => ReactNode
  /** Rendered under the title in every state (loading, error, data), e.g. tabs. */
  header?: ReactNode
  /** Wraps everything below `header` in a tabpanel; `aria-busy` while the query shows placeholder data. */
  panel?: PanelProps
}

/** Page chrome shared by Today, the tabs and the calendar: heading, loading, error + cached age, freshness banners. */
export function OverviewFrame({ title, query, children, header, panel }: FrameProps) {
  const { data, isError, isPending, isPlaceholderData, refetch } = query
  const body = (
    <>
      {isPending && <p role="status">Loading…</p>}
      {!data && isError && (
        <div className="frame__error">
          <Banner tone="error">Could not load the latest data.</Banner>
          <button type="button" className="btn" onClick={() => void refetch()}>Retry</button>
        </div>
      )}
      {data && isError && (
        <div className="frame__error">
          <Banner tone="warn">
            Could not refresh. Showing saved data. <FreshnessTag ageHours={ageHoursSince(data.generated_at)} />
          </Banner>
          <button type="button" className="btn" onClick={() => void refetch()}>Retry</button>
        </div>
      )}
      {data && (
        <>
          <SourceBanners freshness={data.freshness} />
          {data.warnings.length > 0 && <Banner tone="info">{data.warnings.join(' ')}</Banner>}
          {children(data)}
        </>
      )}
    </>
  )
  return (
    <main className="screen frame">
      <h1 className="page-title">{title}</h1>
      {header}
      {panel ? (
        <div
          role="tabpanel"
          id={panel.id}
          aria-labelledby={panel.labelledBy}
          aria-busy={isPlaceholderData || undefined}
          className={isPlaceholderData ? 'frame__panel frame__panel--busy' : 'frame__panel'}
        >
          {body}
        </div>
      ) : body}
    </main>
  )
}
