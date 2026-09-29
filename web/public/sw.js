// Sports Follow's service worker: the app opens instantly, and offline it shows the last update of
// your players. Live data stays live: scores and streams always go to the network.
//
// Pages: network first, the cached app shell when offline (the app is one page, so "/" serves every
// route). Built assets: cache first (their names change with their content). Fonts and other files:
// served from cache while refreshed. /api/me/following and player cards: network first, the last
// answer offline. Every other /api call and every live stream passes straight through.

const VERSION = 'v1'
const SHELL = `shell-${VERSION}`
const FILES = `files-${VERSION}`
const DATA = `data-${VERSION}`
const OFFLINE_DATA = /^\/api\/(me\/following|players\/\d+\/card)$/

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(SHELL).then((cache) => cache.addAll(['/', '/manifest.webmanifest', '/icons/icon-192.png'])))
  // No skipWaiting here: an open tab keeps its version until the fan taps "Reload" (see pwa.tsx).
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => ![SHELL, FILES, DATA].includes(k)).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  )
})

self.addEventListener('message', (event) => {
  if (event.data === 'skip-waiting') self.skipWaiting()
})

self.addEventListener('fetch', (event) => {
  const req = event.request
  if (req.method !== 'GET') return
  const url = new URL(req.url)
  if (url.pathname.endsWith('/stream') || (req.headers.get('accept') || '').includes('text/event-stream')) return

  if (url.origin === self.location.origin) {
    if (req.mode === 'navigate') return event.respondWith(networkFirst(req, SHELL, '/'))
    if (url.pathname.startsWith('/assets/')) return event.respondWith(cacheFirst(req, FILES))
    if (url.pathname.startsWith('/api/')) {
      if (OFFLINE_DATA.test(url.pathname)) event.respondWith(networkFirst(req, DATA))
      return
    }
    if (url.pathname === '/sw.js') return
    return event.respondWith(staleWhileRevalidate(req, FILES))
  }
  if (url.hostname === 'fonts.googleapis.com' || url.hostname === 'fonts.gstatic.com') {
    event.respondWith(staleWhileRevalidate(req, FILES))
  }
})

async function networkFirst(req, cacheName, key) {
  const cache = await caches.open(cacheName)
  try {
    const res = await fetch(req)
    if (res.ok) cache.put(key || req, res.clone())
    return res
  } catch (err) {
    const hit = await cache.match(key || req)
    if (hit) return hit
    throw err
  }
}

async function cacheFirst(req, cacheName) {
  const cache = await caches.open(cacheName)
  const hit = await cache.match(req)
  if (hit) return hit
  const res = await fetch(req)
  if (res.ok) cache.put(req, res.clone())
  return res
}

async function staleWhileRevalidate(req, cacheName) {
  const cache = await caches.open(cacheName)
  const hit = await cache.match(req)
  const fresh = fetch(req).then((res) => {
    if (res.ok || res.type === 'opaque') cache.put(req, res.clone())
    return res
  }).catch(() => hit)
  return hit || fresh
}
