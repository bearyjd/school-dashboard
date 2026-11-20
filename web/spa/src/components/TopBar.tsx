import { Link, NavLink, useLocation } from 'react-router-dom'
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
  { to: '/classic/home', label: 'Classic view' },
]

const HOME_PATHS = ['/', '/today', '/recon']

/** Desktop navigation; hidden on phones, where BottomNav is used instead. */
export function TopBar() {
  const { pathname } = useLocation()
  return (
    <nav className="top-bar" aria-label="Main">
      {LINKS.map(l => (l.to === '/' ? (
        // Home covers the Reconcile screen (/, /recon) and the older Today view (/today), like BottomNav.
        <Link
          key={l.to}
          to={l.to}
          aria-current={HOME_PATHS.includes(pathname) ? 'page' : undefined}
          className={`top-bar__link${HOME_PATHS.includes(pathname) ? ' top-bar__link--active' : ''}`}
        >
          {l.label}
        </Link>
      ) : (
        <NavLink
          key={l.to}
          to={l.to}
          end={l.end}
          className={({ isActive }) => `top-bar__link${isActive ? ' top-bar__link--active' : ''}`}
        >
          {l.label}
        </NavLink>
      )))}
    </nav>
  )
}
