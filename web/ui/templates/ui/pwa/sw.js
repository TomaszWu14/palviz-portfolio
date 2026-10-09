// PalViz service worker — minimal offline shell so the app is installable and
// launches full-screen. Network-first for navigations (always fresh control data),
// cache fallback when offline.
const CACHE = 'palviz-v3';   // bump → activate() purges the old cache (incl. stale HTML)

self.addEventListener('install', (e) => { self.skipWaiting(); });
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then(keys =>
    Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))));
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  event.respondWith(
    fetch(req)
      .then((res) => {
        // Cache only same-origin static assets (WhiteNoise fingerprints them, so they
        // self-invalidate by URL). Never cache HTML navigations — a cached shell would
        // be served stale after a deploy the next time the network blips.
        if (res && res.status === 200 && req.url.startsWith(self.location.origin)
            && req.destination !== 'document' && req.mode !== 'navigate') {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy));
        }
        return res;
      })
      .catch(() => caches.match(req))
  );
});
