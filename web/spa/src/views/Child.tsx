import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { fetchItems, updateItem, deleteItem } from '../api'
import { ItemSheet } from '../components/ItemSheet'
import type { Item } from '../api/types'

const FILTER_TYPES = ['All', 'IXL', 'SGY', 'Manual']
const DONE_STATUSES = ['All', 'Open', 'Done']

export function Child() {
  const { name } = useParams<{ name: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [typeFilter, setTypeFilter] = useState('All')
  const [doneFilter, setDoneFilter] = useState('Open')
  const [editing, setEditing] = useState<Item | null>(null)

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
        <button className="btn btn--ghost" onClick={() => navigate('/home')}>‹</button>
        <h2 style={{ fontSize: '1.4rem', fontWeight: 800 }}>{name}</h2>
      </div>

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
          <div key={item.id} className="card" style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-md)', opacity: item.completed ? 0.5 : 1 }}>
            <button
              onClick={() => patchMutation.mutate({ id: item.id, updates: { completed: !item.completed } })}
              style={{ width: 24, height: 24, borderRadius: '50%', border: `2px solid ${item.completed ? 'var(--color-success)' : 'var(--color-border)'}`, background: item.completed ? 'var(--color-success)' : 'transparent', color: '#fff', flexShrink: 0 }}
            >{item.completed ? '✓' : ''}</button>
            <div style={{ flex: 1, minWidth: 0 }} onClick={() => setEditing(item)}>
              <div style={{ fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{item.title}</div>
              <div style={{ fontSize: '12px', color: 'var(--color-text-muted)' }}>
                {item.type} · {item.due_date ?? 'no due date'}
              </div>
            </div>
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
