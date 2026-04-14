import { useQuery } from '@tanstack/react-query'
import { fetchSyncMeta } from '../api'
import { useSync } from '../hooks/useSync'

const SOURCES = [
  { key: 'ixl', label: 'IXL', icon: '📚', description: 'Skills + diagnostic scores' },
  { key: 'sgy', label: 'Schoology', icon: '🏫', description: 'Assignments + grades' },
  { key: 'gc', label: 'GameChanger', icon: '⚾', description: 'Team schedule + events' },
]

function formatAge(ts: string | undefined): string {
  if (!ts) return 'never'
  const ago = (Date.now() - new Date(ts + 'Z').getTime()) / 1000
  const days = Math.floor(ago / 86400)
  const hours = Math.floor(ago / 3600)
  if (days > 1) return `${days}d ago`
  if (hours > 0) return `${hours}h ago`
  return 'just now'
}

export function Sync() {
  const { data: meta = {}, refetch: refetchMeta } = useQuery({ queryKey: ['syncMeta'], queryFn: fetchSyncMeta, refetchInterval: 5000 })
  const { status, triggering, error, trigger } = useSync()

  const isRunning = status?.running || triggering

  const handleSync = async (sources: string, digest = 'none') => {
    await trigger(sources, digest)
    setTimeout(() => refetchMeta(), 5000)
  }

  return (
    <div className="screen">
      <h2 style={{ fontSize: '1.5rem', fontWeight: 800, marginBottom: 'var(--space-lg)' }}>Sync</h2>

      {error && (
        <div className="card" style={{ background: 'rgba(255,107,107,0.1)', border: '1px solid var(--color-error)', marginBottom: 'var(--space-md)', color: 'var(--color-error)' }}>
          {error}
        </div>
      )}

      {/* Per-source rows */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-sm)', marginBottom: 'var(--space-lg)' }}>
        {SOURCES.map(src => {
          const entry = meta[src.key as keyof typeof meta]
          const age = formatAge(entry?.last_run)
          const result = entry?.last_result
          const badgeCls = !entry ? 'badge--error' : result === 'ok' ? 'badge--ok' : 'badge--error'

          return (
            <div key={src.key} className="card" style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-md)' }}>
              <span style={{ fontSize: '24px' }}>{src.icon}</span>
              <div style={{ flex: 1 }}>
                <div style={{ fontWeight: 700 }}>{src.label}</div>
                <div style={{ fontSize: '12px', color: 'var(--color-text-muted)' }}>{src.description}</div>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4 }}>
                <span className={`badge ${badgeCls}`}>{age}</span>
                <button
                  className="btn btn--secondary"
                  style={{ padding: '6px 12px', fontSize: '12px' }}
                  disabled={isRunning}
                  onClick={() => handleSync(src.key)}
                >
                  Pull
                </button>
              </div>
            </div>
          )
        })}
      </div>

      {/* Batch actions */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-sm)' }}>
        <button className="btn btn--primary" disabled={isRunning} onClick={() => handleSync('ixl,sgy', 'none')}>
          {isRunning ? '⏳ Running…' : '📋 Check Homework'}
        </button>
        <button className="btn btn--secondary" disabled={isRunning} onClick={() => handleSync('ixl,sgy', 'quick')}>
          Quick digest after sync
        </button>
        <button className="btn btn--secondary" disabled={isRunning} onClick={() => handleSync('ixl,sgy,gc', 'full')}>
          Full sync + full digest
        </button>
      </div>

      {/* Status */}
      {status && (
        <div className="card" style={{ marginTop: 'var(--space-lg)', fontSize: '13px', color: 'var(--color-text-muted)' }}>
          Last run: {status.last_run ?? 'never'} ·
          Result: <span style={{ color: status.last_result === 'ok' ? 'var(--color-success)' : 'var(--color-error)' }}>{status.last_result ?? '—'}</span>
          {status.last_error && <div style={{ color: 'var(--color-error)', marginTop: 4 }}>{status.last_error}</div>}
        </div>
      )}
    </div>
  )
}
