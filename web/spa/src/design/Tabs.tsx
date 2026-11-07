import type { KeyboardEvent } from 'react'
import './design.css'

export interface TabItem {
  id: string
  label: string
}

interface TabsProps {
  items: TabItem[]
  active: string
  onChange: (id: string) => void
  /**
   * Prefix for element ids. The panel the caller renders must use
   * `role="tabpanel"`, `id={`${idPrefix}-panel-${active}`}` and `aria-labelledby={`${idPrefix}-tab-${active}`}`.
   */
  idPrefix?: string
}

export function Tabs({ items, active, onChange, idPrefix = 'tabs' }: TabsProps) {
  const select = (id: string) => {
    onChange(id)
    document.getElementById(`${idPrefix}-tab-${id}`)?.focus()
  }

  const onKeyDown = (e: KeyboardEvent, index: number) => {
    const last = items.length - 1
    const target =
      e.key === 'ArrowRight' ? (index === last ? 0 : index + 1)
      : e.key === 'ArrowLeft' ? (index === 0 ? last : index - 1)
      : e.key === 'Home' ? 0
      : e.key === 'End' ? last
      : null
    if (target === null) return
    e.preventDefault()
    select(items[target].id)
  }

  return (
    <div className="tabs" role="tablist">
      {items.map((t, i) => (
        <button
          key={t.id}
          id={`${idPrefix}-tab-${t.id}`}
          type="button"
          role="tab"
          aria-selected={t.id === active}
          aria-controls={`${idPrefix}-panel-${t.id}`}
          tabIndex={t.id === active ? 0 : -1}
          className={`tabs__tab${t.id === active ? ' tabs__tab--active' : ''}`}
          onClick={() => onChange(t.id)}
          onKeyDown={e => onKeyDown(e, i)}
        >
          {t.label}
        </button>
      ))}
    </div>
  )
}
