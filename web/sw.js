// Service Worker: App-Shell offline halten, Daten netzwerkzuerst mit Cache-Rückfall.
// Unterwegs mit schlechtem Empfang sind die letzten bekannten Preise mehr wert
// als eine Fehlermeldung — deshalb wird jede API-Antwort mitgeschrieben.
const VERSION = 'tankradar-v3';
const SHELL = [
  '/', '/index.html', '/dashboard', '/app.css', '/app.js', '/sprite.svg',
  '/discounts.json', '/manifest.webmanifest', '/icon-192.png', '/icon-512.png', '/icon.svg',
  '/vendor/leaflet.js', '/vendor/leaflet.css'
];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(VERSION).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(ks => Promise.all(ks.filter(k => k !== VERSION).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const { request } = e;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (url.pathname.startsWith('/api/')) {
    e.respondWith(
      fetch(request)
        .then(r => {
          if (r.ok) { const cp = r.clone(); caches.open(VERSION).then(c => c.put(request, cp)); }
          return r;
        })
        .catch(() => caches.match(request).then(r => r || new Response(
          JSON.stringify({ offline: true }), { headers: { 'Content-Type': 'application/json' } })))
    );
    return;
  }

  e.respondWith(
    caches.match(request).then(hit => hit || fetch(request).then(r => {
      if (r.ok) { const cp = r.clone(); caches.open(VERSION).then(c => c.put(request, cp)); }
      return r;
    }).catch(() => caches.match('/index.html')))
  );
});
