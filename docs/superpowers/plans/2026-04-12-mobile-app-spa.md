# Mobile App — Plan 2: React SPA

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a mobile-first React + Vite PWA in `web/spa/` served by Flask at `/app`. Includes five screens (Home, Child, Chat, Sync, Settings stub), an agentic tool layer for the chat, and Fold-aware two-pane layout.

**Architecture:** Vite project in `web/spa/` with TypeScript. React Router v6 for navigation. TanStack Query for server state. Typed fetch wrappers in `src/api/` consuming existing Flask endpoints. Service worker caches app shell. Flask serves built bundle at `/app` catch-all.

**Tech Stack:** React 18, TypeScript, Vite, React Router v6, @tanstack/react-query, vite-plugin-pwa (or manual manifest/SW), CSS Modules

**Prerequisite:** Plan 1 (backend changes) must be complete and deployed before this plan is executed.

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `web/spa/` | Create (directory) | Entire Vite project |
| `web/spa/src/api/index.ts` | Create | Typed fetch wrappers for all Flask endpoints |
| `web/spa/src/api/types.ts` | Create | Shared TypeScript types |
| `web/spa/src/App.tsx` | Create | Router + layout shell |
| `web/spa/src/components/BottomNav.tsx` | Create | Bottom tab bar |
| `web/spa/src/components/ActionCard.tsx` | Create | Agentic confirmation card |
| `web/spa/src/components/ItemSheet.tsx` | Create | Bottom sheet for item edit |
| `web/spa/src/components/SourceRow.tsx` | Create | Per-source sync status row |
| `web/spa/src/views/Home.tsx` | Create | Per-child summary cards |
| `web/spa/src/views/Child.tsx` | Create | Item list + inline edit |
| `web/spa/src/views/Chat.tsx` | Create | Agentic chat interface |
| `web/spa/src/views/Sync.tsx` | Create | Manual sync triggers |
| `web/spa/src/views/Settings.tsx` | Create | Placeholder settings |
| `web/spa/src/agent/tools.ts` | Create | Tool definitions + parser |
| `web/spa/src/hooks/useSync.ts` | Create | Sync trigger + polling hook |
| `web/spa/src/styles/global.css` | Create | Design tokens + reset |
| `web/spa/src/styles/fold.css` | Create | Two-pane media query |
| `web/spa/public/manifest.json` | Create | PWA manifest |
| `web/spa/public/sw.js` | Create | Service worker (shell cache) |
| `web/app.py` | Modify | Add SPA catch-all routes |
| `Dockerfile` | Modify | Add npm build step |
| `CLAUDE.md` | Modify | Document SPA quick-start |

---

### Task 1: Vite project scaffold

**Files:**
- Create: `web/spa/` (entire project)

- [ ] **Step 1: Scaffold Vite + React + TypeScript**

```bash
cd /var/home/user/Documents/vibe-code/openclaw-programs/school/school-dashboard
npm create vite@latest web/spa -- --template react-ts
cd web/spa
npm install
npm install react-router-dom @tanstack/react-query
```

- [ ] **Step 2: Configure `web/spa/vite.config.ts`**

Replace generated content:

```typescript
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  base: '/app/',
  server: {
    proxy: {
      '/api': 'http://localhost:5000',
      '/.well-known': 'http://localhost:5000',
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
```

- [ ] **Step 3: Update `web/spa/index.html`**

Replace generated `index.html`:

```html
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover" />
    <meta name="theme-color" content="#1a1a2e" />
    <meta name="mobile-web-app-capable" content="yes" />
    <meta name="apple-mobile-web-app-capable" content="yes" />
    <link rel="manifest" href="/app/manifest.json" />
    <title>School Dashboard</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 4: Verify dev server starts**

```bash
cd web/spa && npm run dev
```

Expected: Vite server starts on port 5173. Visit `http://localhost:5173/app/` — React boilerplate renders.

- [ ] **Step 5: Add `web/spa/` to `.gitignore` exclusions**

In the root `.gitignore`, ensure `web/spa/node_modules` is excluded (add if missing):

```
web/spa/node_modules/
web/spa/dist/
```

- [ ] **Step 6: Commit**

```bash
cd /var/home/user/Documents/vibe-code/openclaw-programs/school/school-dashboard
git add web/spa/.gitignore web/spa/package.json web/spa/package-lock.json web/spa/vite.config.ts web/spa/index.html web/spa/tsconfig*.json web/spa/src/ web/spa/public/
git commit -m "feat: scaffold React + Vite SPA in web/spa/"
```

---

### Task 2: TypeScript types and API layer

**Files:**
- Create: `web/spa/src/api/types.ts`
- Create: `web/spa/src/api/index.ts`

- [ ] **Step 1: Create `web/spa/src/api/types.ts`**

```typescript
export interface Item {
  id: number;
  child: string;
  title: string;
  type: string;
  source: string;
  due_date: string | null;
  notes: string | null;
  completed: boolean;
  created_at: string;
}

export interface IxlSubject {
  subject: string;
  remaining: number;
  assigned: number;
  done: number;
}

export interface Dashboard {
  schoology: Record<string, Array<{ title: string; course: string; due_date: string; status: string; url: string }>>;
  ixl: Record<string, IxlSubject[]>;
  email_items: Array<{ id: string; child: string; summary: string; due_iso: string | null; due_raw: string }>;
  last_updated: string;
}

export interface SyncStatus {
  running: boolean;
  last_run: string | null;
  last_result: string | null;
  last_sources: string[];
  last_error: string | null;
}

export interface SyncMeta {
  ixl?: { last_run: string; last_result: string };
  sgy?: { last_run: string; last_result: string };
  gc?: { last_run: string; last_result: string };
}

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}
```

- [ ] **Step 2: Create `web/spa/src/api/index.ts`**

```typescript
import type { Item, Dashboard, SyncStatus, SyncMeta, ChatMessage } from './types';

const BASE = '';  // same origin — Flask serves both SPA and API

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, init);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || res.statusText);
  }
  return res.json() as Promise<T>;
}

// Items
export const getItems = (child?: string, includeCompleted = false) =>
  apiFetch<{ items: Item[] }>(
    `/api/items?${child ? `child=${encodeURIComponent(child)}&` : ''}include_completed=${includeCompleted ? 1 : 0}`
  );

export const createItem = (data: Partial<Item>) =>
  apiFetch<Item>('/api/items', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });

export const updateItem = (id: number, data: Partial<Item>) =>
  apiFetch<{ ok: boolean }>(`/api/items/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });

export const deleteItem = (id: number) =>
  apiFetch<{ ok: boolean }>(`/api/items/${id}`, { method: 'DELETE' });

// Dashboard
export const getDashboard = () => apiFetch<Dashboard>('/api/dashboard');

// Sync
export const getSyncStatus = () => apiFetch<SyncStatus>('/api/sync/status');

export const getSyncMeta = () => apiFetch<SyncMeta>('/api/sync/meta');

export const triggerSync = (sources: string, digest: string, token: string) =>
  apiFetch<{ started: boolean; sources: string; digest: string }>('/api/sync', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Sync-Token': token },
    body: JSON.stringify({ sources, digest }),
  });

// Chat
export const sendChat = (message: string, history: ChatMessage[]) =>
  apiFetch<{ reply: string }>('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, history }),
  });
```

- [ ] **Step 3: Commit**

```bash
git add web/spa/src/api/
git commit -m "feat: typed API layer for Flask endpoints"
```

---

### Task 3: Design tokens, global styles, and PWA manifest

**Files:**
- Create: `web/spa/src/styles/global.css`
- Create: `web/spa/src/styles/fold.css`
- Create: `web/spa/public/manifest.json`
- Create: `web/spa/public/sw.js`

- [ ] **Step 1: Create `web/spa/src/styles/global.css`**

```css
:root {
  --color-bg: #0f0f1a;
  --color-surface: #1a1a2e;
  --color-surface2: #252540;
  --color-accent: #6c63ff;
  --color-accent2: #ff6b6b;
  --color-text: #e8e8f0;
  --color-text-muted: #8888a8;
  --color-success: #4caf7d;
  --color-warn: #f5a623;
  --color-error: #ff6b6b;
  --color-border: rgba(255,255,255,0.08);

  --radius-sm: 8px;
  --radius-md: 14px;
  --radius-lg: 20px;

  --space-xs: 4px;
  --space-sm: 8px;
  --space-md: 16px;
  --space-lg: 24px;
  --space-xl: 32px;

  --nav-height: 64px;
  --safe-bottom: env(safe-area-inset-bottom, 0px);

  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  font-size: 16px;
  line-height: 1.5;
  color: var(--color-text);
  background: var(--color-bg);
}

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

body {
  min-height: 100dvh;
  overflow-x: hidden;
  -webkit-tap-highlight-color: transparent;
}

#root {
  display: flex;
  flex-direction: column;
  min-height: 100dvh;
}

.screen {
  flex: 1;
  overflow-y: auto;
  padding: var(--space-md);
  padding-bottom: calc(var(--nav-height) + var(--safe-bottom) + var(--space-md));
}

.card {
  background: var(--color-surface);
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border);
  padding: var(--space-md);
}

.badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 100px;
}

.badge--ok { background: rgba(76,175,125,0.15); color: var(--color-success); }
.badge--warn { background: rgba(245,166,35,0.15); color: var(--color-warn); }
.badge--error { background: rgba(255,107,107,0.15); color: var(--color-error); }

button {
  cursor: pointer;
  border: none;
  font-family: inherit;
  font-size: inherit;
}

.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-sm);
  padding: 10px 20px;
  border-radius: var(--radius-sm);
  font-weight: 600;
  transition: opacity 0.15s;
}
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn--primary { background: var(--color-accent); color: #fff; }
.btn--secondary { background: var(--color-surface2); color: var(--color-text); }
.btn--ghost { background: transparent; color: var(--color-text-muted); }

input, textarea {
  background: var(--color-surface2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  color: var(--color-text);
  font-family: inherit;
  font-size: inherit;
  padding: 10px 12px;
  width: 100%;
  outline: none;
}
input:focus, textarea:focus { border-color: var(--color-accent); }
```

- [ ] **Step 2: Create `web/spa/src/styles/fold.css`**

```css
/* Samsung Fold inner screen: two-pane layout at 600px+ */
@media (min-width: 600px) {
  .fold-container {
    display: grid;
    grid-template-columns: 280px 1fr;
    height: 100dvh;
    overflow: hidden;
  }

  .fold-pane-left {
    border-right: 1px solid var(--color-border);
    overflow-y: auto;
  }

  .fold-pane-right {
    overflow-y: auto;
  }

  .fold-hide-desktop {
    display: none;
  }

  .bottom-nav {
    position: relative;
    border-top: 1px solid var(--color-border);
    border-right: none;
    flex-direction: column;
    width: 80px;
    height: 100dvh;
    border-right: 1px solid var(--color-border);
    justify-content: flex-start;
    padding-top: var(--space-lg);
    gap: var(--space-md);
  }

  .bottom-nav__label {
    font-size: 10px;
  }
}
```

- [ ] **Step 3: Create `web/spa/public/manifest.json`**

```json
{
  "name": "School Dashboard",
  "short_name": "School",
  "description": "Family school dashboard — homework, grades, events",
  "start_url": "/app/",
  "scope": "/app/",
  "display": "standalone",
  "orientation": "any",
  "background_color": "#0f0f1a",
  "theme_color": "#1a1a2e",
  "icons": [
    {
      "src": "/app/icons/icon-192.png",
      "sizes": "192x192",
      "type": "image/png",
      "purpose": "any maskable"
    },
    {
      "src": "/app/icons/icon-512.png",
      "sizes": "512x512",
      "type": "image/png",
      "purpose": "any maskable"
    }
  ]
}
```

- [ ] **Step 4: Create placeholder icons**

```bash
mkdir -p web/spa/public/icons
# Generate simple colored squares as placeholder icons
python3 -c "
import struct, zlib

def make_png(size, color):
    def chunk(tag, data):
        c = zlib.crc32(tag + data) & 0xffffffff
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', c)
    r,g,b = color
    ihdr = chunk(b'IHDR', struct.pack('>IIBBBBB', size, size, 8, 2, 0, 0, 0))
    rows = b''.join(b'\x00' + bytes([r,g,b]*size) for _ in range(size))
    idat = chunk(b'IDAT', zlib.compress(rows))
    iend = chunk(b'IEND', b'')
    return b'\x89PNG\r\n\x1a\n' + ihdr + idat + iend

open('web/spa/public/icons/icon-192.png','wb').write(make_png(192, (108,99,255)))
open('web/spa/public/icons/icon-512.png','wb').write(make_png(512, (108,99,255)))
print('Icons created')
"
```

- [ ] **Step 5: Create `web/spa/public/sw.js`** (service worker)

```javascript
const CACHE = 'school-v1';
const SHELL = ['/app/', '/app/index.html'];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  // Network-first for API calls
  if (url.pathname.startsWith('/api/')) {
    e.respondWith(
      fetch(e.request).catch(() =>
        new Response(JSON.stringify({ error: 'offline' }), {
          headers: { 'Content-Type': 'application/json' },
          status: 503,
        })
      )
    );
    return;
  }
  // Cache-first for assets
  e.respondWith(
    caches.match(e.request).then(cached => cached || fetch(e.request))
  );
});
```

- [ ] **Step 6: Register service worker in `web/spa/src/main.tsx`**

Replace generated `main.tsx`:

```tsx
import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import App from './App'
import './styles/global.css'
import './styles/fold.css'

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: 1 } },
})

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/app/sw.js').catch(() => {})
  })
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter basename="/app">
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>
)
```

- [ ] **Step 7: Commit**

```bash
git add web/spa/src/styles/ web/spa/public/ web/spa/src/main.tsx
git commit -m "feat: PWA manifest, service worker, design tokens"
```

---

### Task 4: App shell, routing, and bottom nav

**Files:**
- Create: `web/spa/src/App.tsx`
- Create: `web/spa/src/components/BottomNav.tsx`
- Create: `web/spa/src/views/Settings.tsx` (stub)

- [ ] **Step 1: Create `web/spa/src/components/BottomNav.tsx`**

```tsx
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
        <NavLink key={t.to} to={t.to} className={({ isActive }) => `bottom-nav__tab${isActive ? ' bottom-nav__tab--active' : ''}`}>
          <span className="bottom-nav__icon">{t.icon}</span>
          <span className="bottom-nav__label">{t.label}</span>
        </NavLink>
      ))}
    </nav>
  )
}
```

- [ ] **Step 2: Create `web/spa/src/components/BottomNav.css`**

```css
.bottom-nav {
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  height: var(--nav-height);
  padding-bottom: var(--safe-bottom);
  background: var(--color-surface);
  border-top: 1px solid var(--color-border);
  display: flex;
  align-items: center;
  z-index: 100;
}

.bottom-nav__tab {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  text-decoration: none;
  color: var(--color-text-muted);
  padding: 8px 0;
  transition: color 0.15s;
}

.bottom-nav__tab--active { color: var(--color-accent); }

.bottom-nav__icon { font-size: 20px; line-height: 1; }
.bottom-nav__label { font-size: 10px; font-weight: 600; }
```

- [ ] **Step 3: Create `web/spa/src/views/Settings.tsx`**

```tsx
export function Settings() {
  return (
    <div className="screen">
      <h2 style={{ marginBottom: 'var(--space-md)', fontSize: '1.5rem', fontWeight: 700 }}>Settings</h2>
      <div className="card" style={{ color: 'var(--color-text-muted)', textAlign: 'center', padding: 'var(--space-xl)' }}>
        Coming soon
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Create `web/spa/src/App.tsx`**

```tsx
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
```

- [ ] **Step 5: Create placeholder views so TypeScript compiles**

Create these minimal stubs — they'll be replaced in later tasks:

`web/spa/src/views/Home.tsx`:
```tsx
export function Home() { return <div className="screen"><h2>Home</h2></div> }
```

`web/spa/src/views/Child.tsx`:
```tsx
export function Child() { return <div className="screen"><h2>Child</h2></div> }
```

`web/spa/src/views/Chat.tsx`:
```tsx
export function Chat() { return <div className="screen"><h2>Chat</h2></div> }
```

`web/spa/src/views/Sync.tsx`:
```tsx
export function Sync() { return <div className="screen"><h2>Sync</h2></div> }
```

- [ ] **Step 6: Verify TypeScript compiles**

```bash
cd web/spa && npm run build
```

Expected: build succeeds, no type errors.

- [ ] **Step 7: Commit**

```bash
git add web/spa/src/
git commit -m "feat: app shell, routing, bottom nav, view stubs"
```

---

### Task 5: Home screen

**Files:**
- Modify: `web/spa/src/views/Home.tsx`

- [ ] **Step 1: Create full `web/spa/src/views/Home.tsx`**

```tsx
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { getDashboard, getSyncMeta } from '../api'
import type { SyncMeta } from '../api/types'

function freshnessLabel(meta: SyncMeta, source: keyof SyncMeta): { label: string; cls: string } {
  const entry = meta[source]
  if (!entry) return { label: 'never', cls: 'badge--error' }
  const ago = (Date.now() - new Date(entry.last_run + 'Z').getTime()) / 1000
  const hours = Math.floor(ago / 3600)
  const days = Math.floor(ago / 86400)
  const label = days > 1 ? `${days}d ago` : hours > 0 ? `${hours}h ago` : 'just now'
  const cls = days > 1 ? 'badge--warn' : 'badge--ok'
  return { label, cls }
}

export function Home() {
  const navigate = useNavigate()
  const { data: dashboard, isLoading } = useQuery({ queryKey: ['dashboard'], queryFn: getDashboard })
  const { data: meta = {} } = useQuery({ queryKey: ['syncMeta'], queryFn: getSyncMeta })

  if (isLoading) return <div className="screen"><p style={{ color: 'var(--color-text-muted)' }}>Loading…</p></div>

  const children = Array.from(new Set([
    ...Object.keys(dashboard?.ixl ?? {}),
    ...Object.keys(dashboard?.schoology ?? {}),
  ])).sort()

  return (
    <div className="screen">
      <h1 style={{ fontSize: '1.75rem', fontWeight: 800, marginBottom: 'var(--space-lg)' }}>
        School Dashboard
      </h1>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-md)' }}>
        {children.map(child => {
          const ixlSubjects = dashboard?.ixl[child] ?? []
          const ixlRemaining = ixlSubjects.reduce((s, x) => s + x.remaining, 0)
          const sgyAssignments = dashboard?.schoology[child] ?? []
          const openSgy = sgyAssignments.filter(a => !['submitted','graded','complete','completed','turned in'].includes((a.status ?? '').toLowerCase()))
          const ixlBadge = freshnessLabel(meta, 'ixl')
          const sgyBadge = freshnessLabel(meta, 'sgy')

          return (
            <div key={child} className="card" onClick={() => navigate(`/child/${child}`)}
              style={{ cursor: 'pointer', display: 'flex', flexDirection: 'column', gap: 'var(--space-sm)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <h2 style={{ fontSize: '1.2rem', fontWeight: 700 }}>{child}</h2>
                <span style={{ color: 'var(--color-text-muted)', fontSize: '18px' }}>›</span>
              </div>
              <div style={{ display: 'flex', gap: 'var(--space-sm)', flexWrap: 'wrap' }}>
                <div className="card" style={{ flex: 1, minWidth: 120, background: 'var(--color-surface2)', padding: '10px 12px' }}>
                  <div style={{ fontSize: '11px', color: 'var(--color-text-muted)', marginBottom: 4, display: 'flex', justifyContent: 'space-between' }}>
                    <span>IXL</span>
                    <span className={`badge ${ixlBadge.cls}`}>{ixlBadge.label}</span>
                  </div>
                  <div style={{ fontSize: '1.4rem', fontWeight: 700, color: ixlRemaining > 0 ? 'var(--color-warn)' : 'var(--color-success)' }}>
                    {ixlRemaining > 0 ? `${ixlRemaining} left` : '✓ Done'}
                  </div>
                </div>
                <div className="card" style={{ flex: 1, minWidth: 120, background: 'var(--color-surface2)', padding: '10px 12px' }}>
                  <div style={{ fontSize: '11px', color: 'var(--color-text-muted)', marginBottom: 4, display: 'flex', justifyContent: 'space-between' }}>
                    <span>SGY</span>
                    <span className={`badge ${sgyBadge.cls}`}>{sgyBadge.label}</span>
                  </div>
                  <div style={{ fontSize: '1.4rem', fontWeight: 700, color: openSgy.length > 0 ? 'var(--color-warn)' : 'var(--color-success)' }}>
                    {openSgy.length > 0 ? `${openSgy.length} open` : '✓ Done'}
                  </div>
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Build to verify no type errors**

```bash
cd web/spa && npm run build
```

Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
git add web/spa/src/views/Home.tsx
git commit -m "feat: Home screen with per-child IXL/SGY summary cards and freshness badges"
```

---

### Task 6: Child screen (item list + inline edit)

**Files:**
- Modify: `web/spa/src/views/Child.tsx`
- Create: `web/spa/src/components/ItemSheet.tsx`
- Create: `web/spa/src/components/ItemSheet.css`

- [ ] **Step 1: Create `web/spa/src/components/ItemSheet.tsx`**

```tsx
import { useState } from 'react'
import type { Item } from '../api/types'
import './ItemSheet.css'

interface Props {
  item: Item | null
  onClose: () => void
  onSave: (updates: Partial<Item>) => void
  onDelete?: () => void
}

export function ItemSheet({ item, onClose, onSave, onDelete }: Props) {
  const [title, setTitle] = useState(item?.title ?? '')
  const [dueDate, setDueDate] = useState(item?.due_date ?? '')
  const [notes, setNotes] = useState(item?.notes ?? '')

  if (!item) return null

  return (
    <div className="sheet-overlay" onClick={onClose}>
      <div className="sheet" onClick={e => e.stopPropagation()}>
        <div className="sheet__handle" />
        <h3 className="sheet__title">Edit Item</h3>
        <label className="sheet__label">Title</label>
        <input value={title} onChange={e => setTitle(e.target.value)} />
        <label className="sheet__label">Due Date</label>
        <input type="date" value={dueDate} onChange={e => setDueDate(e.target.value)} />
        <label className="sheet__label">Notes</label>
        <textarea rows={3} value={notes} onChange={e => setNotes(e.target.value)} />
        <div className="sheet__actions">
          {onDelete && <button className="btn btn--ghost" onClick={onDelete}>Delete</button>}
          <button className="btn btn--secondary" onClick={onClose}>Cancel</button>
          <button className="btn btn--primary" onClick={() => onSave({ title, due_date: dueDate || null, notes: notes || null })}>
            Save
          </button>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Create `web/spa/src/components/ItemSheet.css`**

```css
.sheet-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.6);
  z-index: 200;
  display: flex;
  align-items: flex-end;
}

.sheet {
  background: var(--color-surface);
  border-radius: var(--radius-lg) var(--radius-lg) 0 0;
  padding: var(--space-md);
  padding-bottom: calc(var(--space-lg) + var(--safe-bottom));
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: var(--space-md);
}

.sheet__handle {
  width: 40px;
  height: 4px;
  background: var(--color-border);
  border-radius: 2px;
  align-self: center;
  margin-bottom: var(--space-sm);
}

.sheet__title { font-size: 1.1rem; font-weight: 700; }
.sheet__label { font-size: 12px; font-weight: 600; color: var(--color-text-muted); margin-bottom: -8px; }

.sheet__actions {
  display: flex;
  gap: var(--space-sm);
  justify-content: flex-end;
  margin-top: var(--space-sm);
}
```

- [ ] **Step 3: Implement full `web/spa/src/views/Child.tsx`**

```tsx
import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getItems, updateItem, deleteItem } from '../api'
import { ItemSheet } from '../components/ItemSheet'
import type { Item } from '../api/types'

const FILTER_TYPES = ['All', 'IXL', 'SGY', 'Manual']
const DONE_STATUSES = ['All', 'Open', 'Done']

export function Child() {
  const { name } = useParams<{ name: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [typeFilter, setTypeFilter] = useState('All')
  const [doneFilter, setDoneFilter] = useState('Open')
  const [editing, setEditing] = useState<Item | null>(null)

  const { data, isLoading } = useQuery({
    queryKey: ['items', name, doneFilter === 'All'],
    queryFn: () => getItems(name, doneFilter === 'All'),
  })

  const patchMutation = useMutation({
    mutationFn: ({ id, updates }: { id: number; updates: Partial<Item> }) => updateItem(id, updates),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['items'] }); qc.invalidateQueries({ queryKey: ['dashboard'] }); setEditing(null) },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteItem(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['items'] }); setEditing(null) },
  })

  const items = (data?.items ?? []).filter(item => {
    if (typeFilter !== 'All' && item.type.toLowerCase() !== typeFilter.toLowerCase() && item.source.toLowerCase() !== typeFilter.toLowerCase()) return false
    if (doneFilter === 'Open' && item.completed) return false
    if (doneFilter === 'Done' && !item.completed) return false
    return true
  })

  return (
    <div className="screen">
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-md)', marginBottom: 'var(--space-md)' }}>
        <button className="btn btn--ghost" onClick={() => navigate('/home')}>‹</button>
        <h2 style={{ fontSize: '1.4rem', fontWeight: 800 }}>{name}</h2>
      </div>

      {/* Filter chips */}
      <div style={{ display: 'flex', gap: 'var(--space-sm)', marginBottom: 'var(--space-md)', flexWrap: 'wrap' }}>
        {FILTER_TYPES.map(f => (
          <button key={f} className={`btn ${typeFilter === f ? 'btn--primary' : 'btn--secondary'}`}
            style={{ padding: '6px 14px', fontSize: '13px' }}
            onClick={() => setTypeFilter(f)}>{f}</button>
        ))}
        <div style={{ width: 1, background: 'var(--color-border)', margin: '0 4px' }} />
        {DONE_STATUSES.map(f => (
          <button key={f} className={`btn ${doneFilter === f ? 'btn--primary' : 'btn--secondary'}`}
            style={{ padding: '6px 14px', fontSize: '13px' }}
            onClick={() => setDoneFilter(f)}>{f}</button>
        ))}
      </div>

      {isLoading && <p style={{ color: 'var(--color-text-muted)' }}>Loading…</p>}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-sm)' }}>
        {items.map(item => (
          <div key={item.id} className="card" style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-md)', opacity: item.completed ? 0.5 : 1 }}>
            <button
              onClick={() => patchMutation.mutate({ id: item.id, updates: { completed: !item.completed } })}
              style={{ width: 24, height: 24, borderRadius: '50%', border: `2px solid ${item.completed ? 'var(--color-success)' : 'var(--color-border)'}`, background: item.completed ? 'var(--color-success)' : 'transparent', color: '#fff', flexShrink: 0 }}
            >{item.completed ? '✓' : ''}</button>
            <div style={{ flex: 1, minWidth: 0 }} onClick={() => setEditing(item)}>
              <div style={{ fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{item.title}</div>
              <div style={{ fontSize: '12px', color: 'var(--color-text-muted)' }}>
                {item.type} · {item.due_date ?? 'no due date'}
              </div>
            </div>
          </div>
        ))}
        {!isLoading && items.length === 0 && (
          <div className="card" style={{ textAlign: 'center', color: 'var(--color-text-muted)', padding: 'var(--space-xl)' }}>
            No items
          </div>
        )}
      </div>

      <ItemSheet
        item={editing}
        onClose={() => setEditing(null)}
        onSave={updates => editing && patchMutation.mutate({ id: editing.id, updates })}
        onDelete={() => editing && deleteMutation.mutate(editing.id)}
      />
    </div>
  )
}
```

- [ ] **Step 4: Build to verify**

```bash
cd web/spa && npm run build
```

- [ ] **Step 5: Commit**

```bash
git add web/spa/src/views/Child.tsx web/spa/src/components/ItemSheet.tsx web/spa/src/components/ItemSheet.css
git commit -m "feat: Child screen with item list, filter chips, and inline edit sheet"
```

---

### Task 7: Sync screen

**Files:**
- Modify: `web/spa/src/views/Sync.tsx`
- Create: `web/spa/src/hooks/useSync.ts`

- [ ] **Step 1: Create `web/spa/src/hooks/useSync.ts`**

```typescript
import { useState, useCallback, useRef, useEffect } from 'react'
import { triggerSync, getSyncStatus } from '../api'
import type { SyncStatus } from '../api/types'

const SYNC_TOKEN = (document.querySelector('meta[name="sync-token"]') as HTMLMetaElement | null)?.content ?? ''

export function useSync() {
  const [status, setStatus] = useState<SyncStatus | null>(null)
  const [triggering, setTriggering] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const pollRef = useRef<number | null>(null)

  const stopPolling = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
  }, [])

  const startPolling = useCallback(() => {
    stopPolling()
    pollRef.current = window.setInterval(async () => {
      try {
        const s = await getSyncStatus()
        setStatus(s)
        if (!s.running) stopPolling()
      } catch {}
    }, 3000)
  }, [stopPolling])

  useEffect(() => () => stopPolling(), [stopPolling])

  const trigger = useCallback(async (sources: string, digest = 'none') => {
    if (!SYNC_TOKEN) { setError('SYNC_TOKEN not configured'); return }
    setTriggering(true)
    setError(null)
    try {
      await triggerSync(sources, digest, SYNC_TOKEN)
      startPolling()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'sync failed')
    } finally {
      setTriggering(false)
    }
  }, [startPolling])

  return { status, triggering, error, trigger }
}
```

Note: the `sync-token` meta tag is already injected by Flask's `index()` route at `{{ sync_token }}` in `web/templates/index.html`. The SPA's `index.html` does NOT have access to it. Instead, we read it from a global constant. For the SPA, store the token in a `window.__SYNC_TOKEN__` injected by the Flask SPA route (added in Task 10).

Update `web/spa/src/hooks/useSync.ts` — change the token line to:

```typescript
const SYNC_TOKEN = (window as unknown as { __SYNC_TOKEN__?: string }).__SYNC_TOKEN__ ?? ''
```

- [ ] **Step 2: Implement full `web/spa/src/views/Sync.tsx`**

```tsx
import { useQuery } from '@tanstack/react-query'
import { getSyncMeta } from '../api'
import { useSync } from '../hooks/useSync'

const SOURCES = [
  { key: 'ixl', label: 'IXL', icon: '📚', description: 'Skills + diagnostic scores' },
  { key: 'sgy', label: 'Schoology', icon: '🏫', description: 'Assignments + grades' },
  { key: 'gc', label: 'GameChanger', icon: '⚾', description: 'Team schedule + events' },
]

function formatAge(ts: string | undefined): string {
  if (!ts) return 'never'
  const ago = (Date.now() - new Date(ts + 'Z').getTime()) / 1000
  const days = Math.floor(ago / 86400)
  const hours = Math.floor(ago / 3600)
  if (days > 1) return `${days}d ago`
  if (hours > 0) return `${hours}h ago`
  return 'just now'
}

export function Sync() {
  const { data: meta = {}, refetch: refetchMeta } = useQuery({ queryKey: ['syncMeta'], queryFn: getSyncMeta, refetchInterval: 5000 })
  const { status, triggering, error, trigger } = useSync()

  const isRunning = status?.running || triggering

  const handleSync = async (sources: string, digest = 'none') => {
    await trigger(sources, digest)
    setTimeout(() => refetchMeta(), 5000)
  }

  return (
    <div className="screen">
      <h2 style={{ fontSize: '1.5rem', fontWeight: 800, marginBottom: 'var(--space-lg)' }}>Sync</h2>

      {error && (
        <div className="card" style={{ background: 'rgba(255,107,107,0.1)', border: '1px solid var(--color-error)', marginBottom: 'var(--space-md)', color: 'var(--color-error)' }}>
          {error}
        </div>
      )}

      {/* Per-source rows */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-sm)', marginBottom: 'var(--space-lg)' }}>
        {SOURCES.map(src => {
          const entry = meta[src.key as keyof typeof meta]
          const age = formatAge(entry?.last_run)
          const result = entry?.last_result
          const badgeCls = !entry ? 'badge--error' : result === 'ok' ? 'badge--ok' : 'badge--error'

          return (
            <div key={src.key} className="card" style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-md)' }}>
              <span style={{ fontSize: '24px' }}>{src.icon}</span>
              <div style={{ flex: 1 }}>
                <div style={{ fontWeight: 700 }}>{src.label}</div>
                <div style={{ fontSize: '12px', color: 'var(--color-text-muted)' }}>{src.description}</div>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4 }}>
                <span className={`badge ${badgeCls}`}>{age}</span>
                <button
                  className="btn btn--secondary"
                  style={{ padding: '6px 12px', fontSize: '12px' }}
                  disabled={isRunning}
                  onClick={() => handleSync(src.key)}
                >
                  Pull
                </button>
              </div>
            </div>
          )
        })}
      </div>

      {/* Batch actions */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-sm)' }}>
        <button className="btn btn--primary" disabled={isRunning} onClick={() => handleSync('ixl,sgy', 'none')}>
          {isRunning ? '⏳ Running…' : '📋 Check Homework'}
        </button>
        <button className="btn btn--secondary" disabled={isRunning} onClick={() => handleSync('ixl,sgy', 'quick')}>
          Quick digest after sync
        </button>
        <button className="btn btn--secondary" disabled={isRunning} onClick={() => handleSync('ixl,sgy,gc', 'full')}>
          Full sync + full digest
        </button>
      </div>

      {/* Status */}
      {status && (
        <div className="card" style={{ marginTop: 'var(--space-lg)', fontSize: '13px', color: 'var(--color-text-muted)' }}>
          Last run: {status.last_run ?? 'never'} ·
          Result: <span style={{ color: status.last_result === 'ok' ? 'var(--color-success)' : 'var(--color-error)' }}>{status.last_result ?? '—'}</span>
          {status.last_error && <div style={{ color: 'var(--color-error)', marginTop: 4 }}>{status.last_error}</div>}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 3: Build to verify**

```bash
cd web/spa && npm run build
```

- [ ] **Step 4: Commit**

```bash
git add web/spa/src/views/Sync.tsx web/spa/src/hooks/useSync.ts
git commit -m "feat: Sync screen with per-source pull buttons and status"
```

---

### Task 8: Agentic tool layer

**Files:**
- Create: `web/spa/src/agent/tools.ts`

- [ ] **Step 1: Create `web/spa/src/agent/tools.ts`**

```typescript
import { updateItem, createItem, triggerSync } from '../api'
import type { Item } from '../api/types'

export interface ToolCall {
  name: string
  args: Record<string, unknown>
}

export interface ToolResult {
  toolCall: ToolCall
  result?: unknown
  error?: string
  requiresConfirmation: boolean
  confirmLabel?: string
  confirmed?: boolean
}

/**
 * Parse tool calls from LLM response text.
 * Looks for JSON blocks like: [tool_call: name, {...}]
 */
export function parseToolCalls(text: string): { clean: string; calls: ToolCall[] } {
  const calls: ToolCall[] = []
  const clean = text.replace(/\[tool_call:\s*(\w+),\s*(\{[^}]*\})\]/g, (_, name, argsStr) => {
    try {
      calls.push({ name, args: JSON.parse(argsStr) })
    } catch {}
    return ''
  }).trim()
  return { clean, calls }
}

/** Returns true if the tool needs a confirmation card before executing. */
export function needsConfirmation(name: string): boolean {
  return ['mark_item_done', 'create_item'].includes(name)
}

/** Human-readable description of what a tool call will do. */
export function describeToolCall(tc: ToolCall): string {
  switch (tc.name) {
    case 'mark_item_done':
      return `Mark "${tc.args.title ?? tc.args.id}" as done`
    case 'create_item':
      return `Create item: "${tc.args.title}" for ${tc.args.child}`
    case 'trigger_sync':
    case 'sync_source':
      return `Sync ${tc.args.sources ?? tc.args.source}`
    case 'query_items':
      return `Look up items for ${tc.args.child ?? 'all children'}`
    default:
      return tc.name
  }
}

const SYNC_TOKEN = (window as unknown as { __SYNC_TOKEN__?: string }).__SYNC_TOKEN__ ?? ''

/** Execute a tool call and return the result string for injecting back into LLM. */
export async function executeTool(tc: ToolCall): Promise<string> {
  switch (tc.name) {
    case 'mark_item_done': {
      const id = tc.args.id as number
      await updateItem(id, { completed: true })
      return `Item ${id} marked as done.`
    }
    case 'create_item': {
      const item = await createItem(tc.args as Partial<Item>)
      return `Created item id=${item.id}: "${item.title}"`
    }
    case 'trigger_sync':
    case 'sync_source': {
      const sources = (tc.args.sources ?? tc.args.source ?? 'ixl,sgy') as string
      const digest = (tc.args.digest ?? 'none') as string
      await triggerSync(sources, digest, SYNC_TOKEN)
      return `Sync started for: ${sources}. Check /sync for status.`
    }
    case 'query_items': {
      const { getItems } = await import('../api')
      const data = await getItems(tc.args.child as string | undefined)
      return `Items: ${JSON.stringify(data.items.slice(0, 10))}`
    }
    default:
      return `Unknown tool: ${tc.name}`
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add web/spa/src/agent/
git commit -m "feat: agentic tool layer — parse, describe, and execute LLM tool calls"
```

---

### Task 9: Chat screen with agentic layer

**Files:**
- Modify: `web/spa/src/views/Chat.tsx`
- Create: `web/spa/src/components/ActionCard.tsx`
- Create: `web/spa/src/components/ActionCard.css`

- [ ] **Step 1: Create `web/spa/src/components/ActionCard.tsx`**

```tsx
import type { ToolCall } from '../agent/tools'
import { describeToolCall } from '../agent/tools'
import './ActionCard.css'

interface Props {
  toolCall: ToolCall
  onConfirm: () => void
  onCancel: () => void
}

export function ActionCard({ toolCall, onConfirm, onCancel }: Props) {
  return (
    <div className="action-card">
      <div className="action-card__label">Action required</div>
      <div className="action-card__desc">{describeToolCall(toolCall)}</div>
      <div className="action-card__btns">
        <button className="btn btn--ghost" onClick={onCancel}>Cancel</button>
        <button className="btn btn--primary" onClick={onConfirm}>Confirm</button>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Create `web/spa/src/components/ActionCard.css`**

```css
.action-card {
  background: var(--color-surface2);
  border: 1px solid var(--color-accent);
  border-radius: var(--radius-md);
  padding: var(--space-md);
  display: flex;
  flex-direction: column;
  gap: var(--space-sm);
}

.action-card__label {
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--color-accent);
}

.action-card__desc {
  font-weight: 600;
  font-size: 1rem;
}

.action-card__btns {
  display: flex;
  gap: var(--space-sm);
  justify-content: flex-end;
}
```

- [ ] **Step 3: Implement full `web/spa/src/views/Chat.tsx`**

```tsx
import { useState, useRef, useEffect } from 'react'
import { sendChat } from '../api'
import { parseToolCalls, needsConfirmation, executeTool } from '../agent/tools'
import { ActionCard } from '../components/ActionCard'
import type { ChatMessage } from '../api/types'
import type { ToolCall } from '../agent/tools'

const QUICK_ACTIONS = [
  "What's due today?",
  "Check homework",
  "Sync IXL",
  "Any GC games this week?",
]

interface Message {
  role: 'user' | 'assistant' | 'system'
  content: string
  toolCall?: ToolCall
  pending?: boolean
}

export function Chat() {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [pendingTool, setPendingTool] = useState<{ toolCall: ToolCall; resolve: (confirmed: boolean) => void } | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const confirmTool = (toolCall: ToolCall): Promise<boolean> =>
    new Promise(resolve => setPendingTool({ toolCall, resolve }))

  const send = async (text: string) => {
    if (!text.trim() || loading) return
    const userMsg: Message = { role: 'user', content: text }
    setMessages(prev => [...prev, userMsg])
    setInput('')
    setLoading(true)

    try {
      const history: ChatMessage[] = messages
        .filter(m => m.role === 'user' || m.role === 'assistant')
        .map(m => ({ role: m.role as 'user' | 'assistant', content: m.content }))

      const { reply } = await sendChat(text, history)
      const { clean, calls } = parseToolCalls(reply)

      // Add assistant message (cleaned text)
      if (clean) {
        setMessages(prev => [...prev, { role: 'assistant', content: clean }])
      }

      // Handle tool calls sequentially
      for (const toolCall of calls) {
        let confirmed = true
        if (needsConfirmation(toolCall.name)) {
          setMessages(prev => [...prev, { role: 'assistant', content: '', toolCall, pending: true }])
          confirmed = await confirmTool(toolCall)
          setPendingTool(null)
          // Remove the pending card
          setMessages(prev => prev.filter(m => m !== prev[prev.length - 1]))
        }

        if (confirmed) {
          const result = await executeTool(toolCall)
          setMessages(prev => [...prev, { role: 'system', content: `✓ ${result}` }])
          // Inject result back into conversation
          const followup = await sendChat(`Tool result: ${result}`, [...history, { role: 'assistant', content: clean }])
          setMessages(prev => [...prev, { role: 'assistant', content: followup.reply }])
        } else {
          setMessages(prev => [...prev, { role: 'system', content: '✗ Cancelled' }])
        }
      }
    } catch (e: unknown) {
      setMessages(prev => [...prev, { role: 'system', content: `Error: ${e instanceof Error ? e.message : 'failed'}` }])
    } finally {
      setLoading(false)
    }
  }

  const handleVoice = () => {
    const SR = (window as unknown as { webkitSpeechRecognition?: new() => SpeechRecognition }).webkitSpeechRecognition
      || (window as unknown as { SpeechRecognition?: new() => SpeechRecognition }).SpeechRecognition
    if (!SR) return
    const rec = new SR()
    rec.onresult = (e: SpeechRecognitionEvent) => { setInput(e.results[0][0].transcript) }
    rec.start()
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100dvh', paddingBottom: 'var(--nav-height)' }}>
      {/* Quick actions */}
      <div style={{ display: 'flex', gap: 'var(--space-sm)', padding: 'var(--space-sm) var(--space-md)', overflowX: 'auto', flexShrink: 0, borderBottom: '1px solid var(--color-border)' }}>
        {QUICK_ACTIONS.map(q => (
          <button key={q} className="btn btn--secondary" style={{ whiteSpace: 'nowrap', padding: '6px 14px', fontSize: '13px' }}
            onClick={() => send(q)}>{q}</button>
        ))}
      </div>

      {/* Messages */}
      <div style={{ flex: 1, overflowY: 'auto', padding: 'var(--space-md)', display: 'flex', flexDirection: 'column', gap: 'var(--space-md)' }}>
        {messages.length === 0 && (
          <div style={{ textAlign: 'center', color: 'var(--color-text-muted)', marginTop: 'var(--space-xl)' }}>
            Ask about homework, grades, or upcoming events.<br />I can also take actions — try "Mark Ford's math done."
          </div>
        )}
        {messages.map((msg, i) => (
          <div key={i}>
            {msg.toolCall ? (
              pendingTool?.toolCall === msg.toolCall ? (
                <ActionCard
                  toolCall={msg.toolCall}
                  onConfirm={() => pendingTool.resolve(true)}
                  onCancel={() => pendingTool.resolve(false)}
                />
              ) : null
            ) : (
              <div style={{
                alignSelf: msg.role === 'user' ? 'flex-end' : 'flex-start',
                maxWidth: '85%',
                background: msg.role === 'user' ? 'var(--color-accent)' : msg.role === 'system' ? 'var(--color-surface2)' : 'var(--color-surface)',
                borderRadius: msg.role === 'user' ? 'var(--radius-md) var(--radius-md) 4px var(--radius-md)' : 'var(--radius-md) var(--radius-md) var(--radius-md) 4px',
                padding: '10px 14px',
                fontSize: '14px',
                color: msg.role === 'system' ? 'var(--color-text-muted)' : 'var(--color-text)',
                whiteSpace: 'pre-wrap',
              }}>{msg.content}</div>
            )}
          </div>
        ))}
        {loading && <div style={{ color: 'var(--color-text-muted)', fontSize: '13px' }}>Thinking…</div>}
        <div ref={bottomRef} />
      </div>

      {/* Input bar */}
      <div style={{ display: 'flex', gap: 'var(--space-sm)', padding: 'var(--space-sm) var(--space-md)', borderTop: '1px solid var(--color-border)', flexShrink: 0 }}>
        <button className="btn btn--ghost" style={{ fontSize: '20px', padding: '0 8px' }} onClick={handleVoice} title="Voice input">🎤</button>
        <input
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && !e.shiftKey && send(input)}
          placeholder="Ask anything…"
          style={{ flex: 1 }}
        />
        <button className="btn btn--primary" disabled={loading || !input.trim()} onClick={() => send(input)}>
          Send
        </button>
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Build to verify**

```bash
cd web/spa && npm run build
```

Expected: build succeeds. If TypeScript complains about `SpeechRecognition`, add to `vite-env.d.ts`:
```typescript
interface SpeechRecognitionEvent extends Event { results: SpeechRecognitionResultList }
interface SpeechRecognition extends EventTarget { onresult: ((e: SpeechRecognitionEvent) => void) | null; start(): void }
```

- [ ] **Step 5: Commit**

```bash
git add web/spa/src/views/Chat.tsx web/spa/src/components/ActionCard.tsx web/spa/src/components/ActionCard.css
git commit -m "feat: Chat screen with agentic tool call parsing and confirmation cards"
```

---

### Task 10: Flask SPA routes and sync token injection

**Files:**
- Modify: `web/app.py`

- [ ] **Step 1: Add SPA routes to `web/app.py`**

Add these routes after the existing routes (before `if __name__ == "__main__"`):

```python
# ── SPA (React build served at /app) ─────────────────────────────────────────

_SPA_DIST = Path(__file__).parent / "spa" / "dist"


@app.route("/app/", defaults={"path": ""})
@app.route("/app/<path:path>")
def spa(path: str):
    """Serve the React SPA — inject sync token and serve index.html for all routes."""
    sync_token = os.environ.get("SYNC_TOKEN", "")
    index = _SPA_DIST / "index.html"
    if not index.exists():
        return "SPA not built. Run: npm --prefix web/spa run build", 503
    html = index.read_text()
    # Inject SYNC_TOKEN as a global so the SPA can read it without a separate API call
    injection = f'<script>window.__SYNC_TOKEN__="{sync_token}";</script>'
    html = html.replace("</head>", f"{injection}</head>", 1)
    return Response(html, mimetype="text/html")
```

Also add a static asset route. Vite outputs assets to `dist/assets/`:

```python
@app.route("/app/assets/<path:filename>")
def spa_assets(filename: str):
    from flask import send_from_directory
    return send_from_directory(_SPA_DIST / "assets", filename)


@app.route("/app/icons/<path:filename>")
def spa_icons(filename: str):
    from flask import send_from_directory
    return send_from_directory(_SPA_DIST / "icons", filename)


@app.route("/app/manifest.json")
def spa_manifest():
    from flask import send_from_directory
    return send_from_directory(_SPA_DIST, "manifest.json", mimetype="application/manifest+json")


@app.route("/app/sw.js")
def spa_sw():
    from flask import send_from_directory
    return send_from_directory(_SPA_DIST, "sw.js", mimetype="application/javascript")
```

- [ ] **Step 2: Run tests to make sure nothing broke**

```bash
python -m pytest tests/ -q
```

Expected: all tests pass

- [ ] **Step 3: Commit**

```bash
git add web/app.py
git commit -m "feat: Flask routes to serve React SPA at /app with __SYNC_TOKEN__ injection"
```

---

### Task 11: Dockerfile build step

**Files:**
- Modify: `Dockerfile`

- [ ] **Step 1: Read current Dockerfile**

```bash
cat Dockerfile
```

- [ ] **Step 2: Add Node.js and npm build step**

In the Dockerfile, before the Python pip install steps, add:

```dockerfile
# Install Node.js for SPA build
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm && rm -rf /var/lib/apt/lists/*

# Build React SPA
COPY web/spa/package*.json /app/web/spa/
RUN npm --prefix /app/web/spa ci
COPY web/spa/ /app/web/spa/
RUN npm --prefix /app/web/spa run build
```

Place this section BEFORE the `COPY . .` or existing app copy, to maximize Docker layer cache reuse. The node_modules are only rebuilt when package-lock.json changes.

- [ ] **Step 3: Verify build locally (optional — CI will catch issues)**

```bash
docker build -t school-dashboard-test . --progress=plain 2>&1 | tail -30
```

Expected: build succeeds. If Node.js version is too old in the base image (`python:3.12-slim`), pin a newer version:
```dockerfile
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && apt-get install -y nodejs && rm -rf /var/lib/apt/lists/*
```

- [ ] **Step 4: Commit**

```bash
git add Dockerfile
git commit -m "feat: add Node.js + npm SPA build step to Dockerfile"
```

---

### Task 12: CLAUDE.md update and push

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update CLAUDE.md**

In the **Quick Start** section, add after `docker compose up -d`:
```
# Access SPA at https://school.grepon.cc/app (install to homescreen for PWA)
```

In the **Development** section, add:
```bash
# Run SPA dev server (proxies API to Flask on :5000)
npm --prefix web/spa run dev   # → http://localhost:5173/app/
```

In the **Architecture** section, add under `web/`:
```
  spa/                  React + Vite SPA (source). Built to spa/dist/ by Dockerfile.
```

- [ ] **Step 2: Final test run**

```bash
python -m pytest tests/ -q
```

Expected: all tests pass.

- [ ] **Step 3: Push**

```bash
git add CLAUDE.md
git commit -m "docs: SPA dev workflow and architecture in CLAUDE.md"
git push
```
