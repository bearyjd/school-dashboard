import { useLocation } from 'react-router-dom'

/** Test helper: shows the router location so tests can assert on navigation and ?kid=. */
export function Where() {
  const loc = useLocation()
  return <div data-testid="where">{loc.pathname + loc.search}</div>
}
