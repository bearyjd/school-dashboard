import { NavLink } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { fetchDashboard } from '../api'
import './BottomNav.css'

const buildTabs = (childTo: string | null) => [
  { to: '/home', label: 'Home', icon: '🏠' },
  ...(childTo ? [{ to: childTo, label: 'Child', icon: '👤' }] : []),
  { to: '/chat', label: 'Chat', icon: '💬' },
  { to: '/sync', label: 'Sync', icon: '🔄' },
  { to: '/settings', label: 'Settings', icon: '⚙️' },
]

export function BottomNav() {
  const { data: dashboard } = useQuery({ queryKey: ['dashboard'], queryFn: fetchDashboard })
  const first = dashboard?.children?.[0]
  const tabs = buildTabs(first ? `/child/${encodeURIComponent(first.name)}` : null)
  return (
    <nav className="bottom-nav">
      {tabs.map(t => (
        <NavLink
          key={t.to}
          to={t.to}
          className={({ isActive }) => `bottom-nav__tab${isActive ? ' bottom-nav__tab--active' : ''}`}
        >
          <span className="bottom-nav__icon">{t.icon}</span>
          <span className="bottom-nav__label">{t.label}</span>
        </NavLink>
      ))}
    </nav>
  )
}
