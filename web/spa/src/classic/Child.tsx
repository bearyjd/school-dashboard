import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { fetchItems, fetchDashboard, updateItem, deleteItem } from '../api'
import { ItemSheet } from '../components/ItemSheet'
import { InlineAgent } from '../components/InlineAgent'
import type { Item, PsAssignment } from '../api/types'

const FILTER_TYPES = ['All', 'IXL', 'SGY', 'Manual']
const DONE_STATUSES = ['All', 'Open', 'Done']

const PS_SECTIONS = [
  { key: 'missing', label: 'Missing', color: 'var(--color-error)' },
  { key: 'late', label: 'Late', color: 'var(--color-warn)' },
  { key: 'upcoming', label: 'Due soon', color: 'var(--color-text-muted)' },
] as const

function psLine(a: PsAssignment): string {
  const head = [a.course, a.title].filter(Boolean).join(' - ') || 'Untitled'
  return a.due ? `${head} (due ${a.due})` : head
}

export function Child() {
  const { name } = useParams<{ name: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [typeFilter, setTypeFilter] = useState('All')
  const [doneFilter, setDoneFilter] = useState('Open')
  const [editing, setEditing] = useState<Item | null>(null)

  const { data: dashboard } = useQuery({ queryKey: ['dashboard'], queryFn: fetchDashboard })
  const ps = name ? dashboard?.powerschool?.[name] : undefined

  const { data, isLoading } = useQuery({
    queryKey: ['items', name, doneFilter === 'All'],
    queryFn: () => fetchItems(name, doneFilter === 'All'),
  })

  const patchMutation = useMutation({
    mutationFn: ({ id, updates }: { id: number; updates: Partial<Item> }) => updateItem(id, updates),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['items'] }); qc.invalidateQueries({ queryKey: ['dashboard'] }); setEditing(null) },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteItem(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['items'] }); setEditing(null) },
  })

  const items = (data?.items ?? []).filter(item => {
    if (typeFilter !== 'All' && item.type.toLowerCase() !== typeFilter.toLowerCase() && item.source.toLowerCase() !== typeFilter.toLowerCase()) return false
    if (doneFilter === 'Open' && item.completed) return false
    if (doneFilter === 'Done' && !item.completed) return false
    return true
  })

  return (
    <div className="screen">
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-md)', marginBottom: 'var(--space-md)' }}>
        <button className="btn btn--ghost" onClick={() => navigate('/classic/home')}>‹</button>
        <h2 style={{ fontSize: '1.4rem', fontWeight: 800 }}>{name}</h2>
      </div>

      {ps && (
        <div className="card" style={{ marginBottom: 'var(--space-md)', display: 'flex', flexDirection: 'column', gap: 'var(--space-sm)' }}>
          <div style={{ fontWeight: 700 }}>PowerSchool{ps.updated ? ` (as of ${ps.updated.slice(0, 10)})` : ''}</div>
          {PS_SECTIONS.map(sec => (
            <div key={sec.key}>
              <div style={{ fontSize: '12px', fontWeight: 700, color: sec.color }}>
                {sec.label} ({ps[sec.key].length})
              </div>
              {ps[sec.key].length === 0
                ? <div style={{ fontSize: '13px', color: 'var(--color-text-muted)' }}>None</div>
                : ps[sec.key].map((a, i) => <div key={i} style={{ fontSize: '13px' }}>{psLine(a)}</div>)}
            </div>
          ))}
        </div>
      )}

      {/* Filter chips */}
      <div style={{ display: 'flex', gap: 'var(--space-sm)', marginBottom: 'var(--space-md)', flexWrap: 'wrap' }}>
        {FILTER_TYPES.map(f => (
          <button key={f} className={`btn ${typeFilter === f ? 'btn--primary' : 'btn--secondary'}`}
            style={{ padding: '6px 14px', fontSize: '13px' }}
            onClick={() => setTypeFilter(f)}>{f}</button>
        ))}
        <div style={{ width: 1, background: 'var(--color-border)', margin: '0 4px' }} />
        {DONE_STATUSES.map(f => (
          <button key={f} className={`btn ${doneFilter === f ? 'btn--primary' : 'btn--secondary'}`}
            style={{ padding: '6px 14px', fontSize: '13px' }}
            onClick={() => setDoneFilter(f)}>{f}</button>
        ))}
      </div>

      {isLoading && <p style={{ color: 'var(--color-text-muted)' }}>Loading…</p>}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-sm)' }}>
        {items.map(item => (
          <div key={item.id} className="card" style={{ opacity: item.completed ? 0.5 : 1 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-md)' }}>
              <button
                onClick={() => patchMutation.mutate({ id: item.id, updates: { completed: !item.completed } })}
                style={{
                  width: 24, height: 24, borderRadius: '50%',
                  border: `2px solid ${item.completed ? 'var(--color-success)' : 'var(--color-border)'}`,
                  background: item.completed ? 'var(--color-success)' : 'transparent',
                  color: '#fff', flexShrink: 0,
                }}
              >{item.completed ? '✓' : ''}</button>
              <div style={{ flex: 1, minWidth: 0 }} onClick={() => setEditing(item)}>
                <div style={{ fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {item.title}
                </div>
                <div style={{ fontSize: '12px', color: 'var(--color-text-muted)' }}>
                  {item.type} · {item.due_date ?? 'no due date'}
                </div>
              </div>
            </div>
            <InlineAgent
              contextType="item"
              contextId={String(item.id)}
              suggestions={['Explain this', 'Mark done', 'Reschedule']}
            />
          </div>
        ))}
        {!isLoading && items.length === 0 && (
          <div className="card" style={{ textAlign: 'center', color: 'var(--color-text-muted)', padding: 'var(--space-xl)' }}>
            No items
          </div>
        )}
      </div>

      <ItemSheet
        item={editing}
        onClose={() => setEditing(null)}
        onSave={updates => editing && patchMutation.mutate({ id: editing.id, updates })}
        onDelete={() => editing && deleteMutation.mutate(editing.id)}
      />
    </div>
  )
}
