import { lazy, Suspense } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import { BottomNav } from './components/BottomNav'
import { TopBar } from './components/TopBar'
import { Today } from './pages/Today'
import { ListPage } from './pages/ListPage'
import { Calendar } from './pages/Calendar'
import { Chat } from './views/Chat'
import { Sync } from './views/Sync'
import { Settings } from './views/Settings'

const ClassicRoutes = lazy(() => import('./classic/ClassicRoutes'))

export default function App() {
  return (
    <>
      <TopBar />
      <Suspense fallback={<main className="screen"><p>Loading…</p></main>}>
        <Routes>
          <Route path="/" element={<Today />} />
          <Route path="/tonight" element={<ListPage kind="tonight" />} />
          <Route path="/overdue" element={<ListPage kind="overdue" />} />
          <Route path="/missing" element={<ListPage kind="missing" />} />
          <Route path="/events" element={<ListPage kind="events" />} />
          <Route path="/calendar" element={<Calendar />} />
          <Route path="/chat" element={<Chat />} />
          <Route path="/sync" element={<Sync />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/classic/*" element={<ClassicRoutes />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
      <BottomNav />
    </>
  )
}
