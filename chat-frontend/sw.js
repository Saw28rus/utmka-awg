/* Service worker чата поддержки: офлайн-оболочка + push-уведомления.
   ВАЖНО: при изменении статики поднимайте CACHE — иначе клиенты получат старое. */

var CACHE = 'utmka-chat-v13';
var SHELL = [
  '/',
  '/index.html',
  '/style.css?v=13',
  '/app.js?v=13',
  '/manifest.webmanifest?v=13',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
  '/icons/apple-touch-icon.png',
  '/icons/badge-96.png',
  '/icons/favicon-64.png'
];

self.addEventListener('install', function (e) {
  e.waitUntil(
    caches.open(CACHE).then(function (c) {
      return c.addAll(SHELL).catch(function () { /* частичный кэш не фатален */ });
    }).then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener('activate', function (e) {
  e.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.map(function (k) {
        if (k !== CACHE) return caches.delete(k);
      }));
    }).then(function () { return self.clients.claim(); })
  );
});

self.addEventListener('message', function (e) {
  if (e.data === 'skip-waiting') self.skipWaiting();
});

self.addEventListener('fetch', function (e) {
  var req = e.request;
  if (req.method !== 'GET') return;
  var url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  // API никогда не кэшируем (авторизация, свежие сообщения)
  if (url.pathname.indexOf('/api/') === 0) return;

  // Навигация: сеть -> при офлайне отдаём кэш оболочки
  if (req.mode === 'navigate') {
    e.respondWith(
      fetch(req).catch(function () {
        return caches.match('/index.html').then(function (r) {
          return r || caches.match('/');
        });
      })
    );
    return;
  }

  // Статика: stale-while-revalidate
  e.respondWith(
    caches.match(req).then(function (cached) {
      var net = fetch(req).then(function (res) {
        if (res && res.status === 200 && res.type === 'basic') {
          var copy = res.clone();
          caches.open(CACHE).then(function (c) { c.put(req, copy); });
        }
        return res;
      }).catch(function () { return cached; });
      return cached || net;
    })
  );
});

// --- push -------------------------------------------------------------------

self.addEventListener('push', function (e) {
  var data = {};
  try { data = e.data ? e.data.json() : {}; } catch (err) { data = {}; }
  var title = data.title || 'Чат поддержки';
  var body = data.body || 'Новое сообщение';
  var url = data.url || '/';
  e.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (list) {
      var focused = list.some(function (c) { return c.focused || c.visibilityState === 'visible'; });
      // Сообщаем открытому приложению, что есть новое — оно подтянет сообщение.
      list.forEach(function (c) { c.postMessage({ type: 'push-msg' }); });
      // Если чат открыт и активен — не показываем системное уведомление и не
      // вешаем значок на иконку (пользователь и так всё видит).
      if (focused) return;
      return self.registration.showNotification(title, {
        body: body,
        icon: '/icons/icon-192.png',
        badge: '/icons/badge-96.png',
        tag: 'chat-support',
        renotify: true,
        data: { url: url },
        vibrate: [80, 40, 80]
      });
    })
  );
});

self.addEventListener('notificationclick', function (e) {
  e.notification.close();
  var target = (e.notification.data && e.notification.data.url) || '/';
  e.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (list) {
      for (var i = 0; i < list.length; i++) {
        var c = list[i];
        if ('focus' in c) {
          c.postMessage({ type: 'open-chat' });
          return c.focus();
        }
      }
      if (self.clients.openWindow) return self.clients.openWindow(target);
    })
  );
});
