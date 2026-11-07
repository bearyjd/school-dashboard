import { NavLink } from 'react-router-dom'
import './TopBar.css'

const LINKS = [
  { to: '/', label: 'Today', end: true },
  { to: '/tonight', label: 'Tonight' },
  { to: '/overdue', label: 'Overdue' },
  { to: '/missing', label: 'Missing' },
  { to: '/events', label: 'Events' },
  { to: '/calendar', label: 'Calendar' },
  { to: '/chat', label: 'Chat' },
  { to: '/sync', label: 'Sync' },
  { to: '/settings', label: 'Settings' },
]

/** Desktop navigation; hidden on phones, where BottomNav is used instead. */
export function TopBar() {
  return (
    <nav className="top-bar" aria-label="Main">
      {LINKS.map(l => (
        <NavLink
          key={l.to}
          to={l.to}
          end={l.end}
          className={({ isActive }) => `top-bar__link${isActive ? ' top-bar__link--active' : ''}`}
        >
          {l.label}
        </NavLink>
      ))}
    </nav>
  )
}
