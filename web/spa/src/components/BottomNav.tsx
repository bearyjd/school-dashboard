import { NavLink } from 'react-router-dom'
import './BottomNav.css'

const tabs = [
  { to: '/home', label: 'Home', icon: '🏠' },
  { to: '/chat', label: 'Chat', icon: '💬' },
  { to: '/sync', label: 'Sync', icon: '🔄' },
  { to: '/settings', label: 'Settings', icon: '⚙️' },
]

export function BottomNav() {
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
