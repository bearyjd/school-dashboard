import { Routes, Route, Navigate } from 'react-router-dom'
import { BottomNav } from './components/BottomNav'
import { Home } from './views/Home'
import { Child } from './views/Child'
import { Chat } from './views/Chat'
import { Sync } from './views/Sync'
import { Settings } from './views/Settings'

export default function App() {
  return (
    <>
      <Routes>
        <Route path="/" element={<Navigate to="/home" replace />} />
        <Route path="/home" element={<Home />} />
        <Route path="/child/:name" element={<Child />} />
        <Route path="/chat" element={<Chat />} />
        <Route path="/sync" element={<Sync />} />
        <Route path="/settings" element={<Settings />} />
      </Routes>
      <BottomNav />
    </>
  )
}
