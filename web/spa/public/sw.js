const CACHE = 'school-v2';
const SHELL = ['/app/', '/app/index.html'];

function remember(key, response) {
  // Best effort: a full or unavailable cache must never break the response.
  return caches.open(CACHE).then(c => c.put(key, response)).catch(() => {});
}

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
  // Network only for API calls; never cached.
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
  // Network-first for page navigations so a new deploy is never hidden by a stale shell;
  // keep the latest shell for offline use.
  if (e.request.mode === 'navigate') {
    e.respondWith(
      fetch(e.request).then(res => {
        if (res.ok) e.waitUntil(remember('/app/index.html', res.clone()));
        return res;
      }).catch(() =>
        caches.match('/app/index.html').then(cached => cached || new Response('Offline', { status: 503 }))
      )
    );
    return;
  }
  // Cache-first for assets; only successful same-origin GETs under /app/assets/ are stored.
  const cacheable = e.request.method === 'GET' && url.origin === self.location.origin && url.pathname.startsWith('/app/assets/');
  e.respondWith(
    caches.match(e.request).then(cached =>
      cached || fetch(e.request).then(res => {
        if (cacheable && res.ok && res.type !== 'opaque') e.waitUntil(remember(e.request, res.clone()));
        return res;
      }).catch(() =>
        new Response('Offline', { status: 503 })
      )
    )
  );
});
