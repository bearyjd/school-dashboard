import './design.css'

export interface TabItem {
  id: string
  label: string
}

interface TabsProps {
  items: TabItem[]
  active: string
  onChange: (id: string) => void
}

export function Tabs({ items, active, onChange }: TabsProps) {
  return (
    <div className="tabs" role="tablist">
      {items.map(t => (
        <button
          key={t.id}
          type="button"
          role="tab"
          aria-selected={t.id === active}
          className={`tabs__tab${t.id === active ? ' tabs__tab--active' : ''}`}
          onClick={() => onChange(t.id)}
        >
          {t.label}
        </button>
      ))}
    </div>
  )
}
