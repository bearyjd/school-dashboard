import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { fetchDashboard, fetchSyncMeta } from '../api'
import type { SyncMeta } from '../api/types'

function freshnessLabel(meta: SyncMeta, source: keyof SyncMeta): { label: string; cls: string } {
  const entry = meta[source]
  if (!entry) return { label: 'never', cls: 'badge--error' }
  const ago = (Date.now() - new Date(entry.last_run + 'Z').getTime()) / 1000
  const hours = Math.floor(ago / 3600)
  const days = Math.floor(ago / 86400)
  const label = days > 1 ? `${days}d ago` : hours > 0 ? `${hours}h ago` : 'just now'
  const cls = days > 1 ? 'badge--warn' : 'badge--ok'
  return { label, cls }
}

export function Home() {
  const navigate = useNavigate()
  const { data: dashboard, isLoading } = useQuery({ queryKey: ['dashboard'], queryFn: fetchDashboard })
  const { data: meta = {} } = useQuery({ queryKey: ['syncMeta'], queryFn: fetchSyncMeta })

  if (isLoading) return <div className="screen"><p style={{ color: 'var(--color-text-muted)' }}>Loading…</p></div>

  const children = Array.from(new Set([
    ...Object.keys(dashboard?.ixl ?? {}),
    ...Object.keys(dashboard?.schoology ?? {}),
  ])).sort()

  return (
    <div className="screen">
      <h1 style={{ fontSize: '1.75rem', fontWeight: 800, marginBottom: 'var(--space-lg)' }}>
        School Dashboard
      </h1>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-md)' }}>
        {children.map(child => {
          const ixlSubjects = dashboard?.ixl[child] ?? []
          const ixlRemaining = ixlSubjects.reduce((s, x) => s + x.remaining, 0)
          const sgyAssignments = dashboard?.schoology[child] ?? []
          const openSgy = sgyAssignments.filter(a => !['submitted','graded','complete','completed','turned in'].includes((a.status ?? '').toLowerCase()))
          const ixlBadge = freshnessLabel(meta, 'ixl')
          const sgyBadge = freshnessLabel(meta, 'sgy')

          return (
            <div key={child} className="card" onClick={() => navigate(`/child/${child}`)}
              style={{ cursor: 'pointer', display: 'flex', flexDirection: 'column', gap: 'var(--space-sm)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <h2 style={{ fontSize: '1.2rem', fontWeight: 700 }}>{child}</h2>
                <span style={{ color: 'var(--color-text-muted)', fontSize: '18px' }}>›</span>
              </div>
              <div style={{ display: 'flex', gap: 'var(--space-sm)', flexWrap: 'wrap' }}>
                <div className="card" style={{ flex: 1, minWidth: 120, background: 'var(--color-surface2)', padding: '10px 12px' }}>
                  <div style={{ fontSize: '11px', color: 'var(--color-text-muted)', marginBottom: 4, display: 'flex', justifyContent: 'space-between' }}>
                    <span>IXL</span>
                    <span className={`badge ${ixlBadge.cls}`}>{ixlBadge.label}</span>
                  </div>
                  <div style={{ fontSize: '1.4rem', fontWeight: 700, color: ixlRemaining > 0 ? 'var(--color-warn)' : 'var(--color-success)' }}>
                    {ixlRemaining > 0 ? `${ixlRemaining} left` : '✓ Done'}
                  </div>
                </div>
                <div className="card" style={{ flex: 1, minWidth: 120, background: 'var(--color-surface2)', padding: '10px 12px' }}>
                  <div style={{ fontSize: '11px', color: 'var(--color-text-muted)', marginBottom: 4, display: 'flex', justifyContent: 'space-between' }}>
                    <span>SGY</span>
                    <span className={`badge ${sgyBadge.cls}`}>{sgyBadge.label}</span>
                  </div>
                  <div style={{ fontSize: '1.4rem', fontWeight: 700, color: openSgy.length > 0 ? 'var(--color-warn)' : 'var(--color-success)' }}>
                    {openSgy.length > 0 ? `${openSgy.length} open` : '✓ Done'}
                  </div>
                </div>
              </div>
            </div>
          )
        })}
        {children.length === 0 && !isLoading && (
          <div className="card" style={{ textAlign: 'center', color: 'var(--color-text-muted)', padding: 'var(--space-xl)' }}>
            No data — run a sync first
          </div>
        )}
      </div>
    </div>
  )
}
