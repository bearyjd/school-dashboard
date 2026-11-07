import { Navigate, Route, Routes } from 'react-router-dom'
import { Home } from './Home'
import { Child } from './Child'

/** The pre-redesign screens, lazy-loaded under /classic/* until they are removed. */
export default function ClassicRoutes() {
  return (
    <Routes>
      <Route path="home" element={<Home />} />
      <Route path="child/:name" element={<Child />} />
      <Route path="*" element={<Navigate to="/classic/home" replace />} />
    </Routes>
  )
}
