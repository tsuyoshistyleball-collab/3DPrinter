// PWA インストール用の最小サービスワーカー。
// キャッシュ戦略は持たず、常にネットワークへ素通しする
// （モデルやチャットは常に最新を取得したいため）。
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', () => {});
