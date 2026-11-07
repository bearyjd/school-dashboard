import { NavLink, useLocation } from 'react-router-dom'
import './BottomNav.css'

const TABS = [
  { to: '/', label: 'Today', icon: '🏠', end: true },
  { to: '/tonight', label: 'Lists', icon: '📋' },
  { to: '/calendar', label: 'Calendar', icon: '📅' },
  { to: '/chat', label: 'Chat', icon: '💬' },
  { to: '/sync', label: 'Sync', icon: '🔄' },
  { to: '/settings', label: 'Settings', icon: '⚙️' },
]

const LIST_PATHS = ['/tonight', '/overdue', '/missing', '/events']

/** Phone navigation; hidden from 768px up, where TopBar is used instead. */
export function BottomNav() {
  const { pathname } = useLocation()
  return (
    <nav className="bottom-nav" aria-label="Main (phone)">
      {TABS.map(t => (
        <NavLink
          key={t.to}
          to={t.to}
          end={t.end}
          className={({ isActive }) => {
            const active = isActive || (t.to === '/tonight' && LIST_PATHS.includes(pathname))
            return `bottom-nav__tab${active ? ' bottom-nav__tab--active' : ''}`
          }}
        >
          <span className="bottom-nav__icon" aria-hidden="true">{t.icon}</span>
          <span className="bottom-nav__label">{t.label}</span>
        </NavLink>
      ))}
    </nav>
  )
}
