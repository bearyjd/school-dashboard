import { Link, useLocation } from 'react-router-dom'
import './BottomNav.css'

interface Tab {
  to: string
  label: string
  icon: string
  /** Paths (and their sub-paths) that mark this tab as the current page. */
  match: string[]
}

const TABS: Tab[] = [
  { to: '/', label: 'Today', icon: '🏠', match: [] },
  { to: '/tonight', label: 'Lists', icon: '📋', match: ['/tonight', '/overdue', '/missing', '/events'] },
  { to: '/calendar', label: 'Calendar', icon: '📅', match: ['/calendar'] },
  { to: '/chat', label: 'Chat', icon: '💬', match: ['/chat'] },
  { to: '/sync', label: 'Sync', icon: '🔄', match: ['/sync'] },
  { to: '/settings', label: 'Settings', icon: '⚙️', match: ['/settings'] },
]

function isCurrent(tab: Tab, pathname: string): boolean {
  if (tab.to === '/') return pathname === '/'
  return tab.match.some(p => pathname === p || pathname.startsWith(`${p}/`))
}

/** Phone navigation; hidden from 768px up, where TopBar is used instead. */
export function BottomNav() {
  const { pathname } = useLocation()
  return (
    <nav className="bottom-nav" aria-label="Main (phone)">
      {TABS.map(t => {
        const current = isCurrent(t, pathname)
        return (
          <Link
            key={t.to}
            to={t.to}
            aria-current={current ? 'page' : undefined}
            className={`bottom-nav__tab${current ? ' bottom-nav__tab--active' : ''}`}
          >
            <span className="bottom-nav__icon" aria-hidden="true">{t.icon}</span>
            <span className="bottom-nav__label">{t.label}</span>
          </Link>
        )
      })}
    </nav>
  )
}
