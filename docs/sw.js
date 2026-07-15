const CACHE = 'market-v2';

// Take over immediately — don't wait for old tabs to close
self.addEventListener('install', e => {
  self.skipWaiting();
  e.waitUntil(caches.open(CACHE));
});

self.addEventListener('activate', e =>
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  )
);

// Network-first: always try fresh content, fall back to cache only when offline
self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  e.respondWith(
    fetch(e.request)
      .then(resp => {
        if (resp && resp.ok) {
          const copy = resp.clone();
          caches.open(CACHE).then(c => c.put(e.request, copy)).catch(() => {});
        }
        return resp;
      })
      .catch(() =>
        caches.match(e.request).then(r => r || caches.match('./index.html'))
      )
  );
});
