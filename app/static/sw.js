const APP_CACHE_VERSION = 't-lab-v1.3.1';

self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.map((key) => caches.delete(key)));
    await self.clients.claim();
  })());
});

// 画面を構成するファイルは常にネットワークから取得する。
// APIやSTLなどのデータ取得は従来どおりブラウザへ任せる。
self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return;
  const freshAsset =
    event.request.mode === 'navigate'
    || ['script', 'style', 'manifest', 'serviceworker'].includes(event.request.destination)
    || /\.(?:js|css|html|webmanifest)$/.test(url.pathname);
  if (!freshAsset) return;
  event.respondWith(fetch(new Request(event.request, { cache: 'no-store' })));
});
