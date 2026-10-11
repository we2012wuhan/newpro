/* ==========================================================================
   Service Worker：断网时别再白屏
   --------------------------------------------------------------------------
   这个站是「服务端渲染 + 登录」的，所以策略故意保守：

     · 页面（导航请求）：网络优先。连不上时给 /static/offline.html 那张
       「连不上服务器」的提示页，而不是浏览器的白屏/恐龙页。
       页面 HTML 本身**不进缓存** —— 免得发新版后有人还在看旧页面。
     · /static/ 下的静态资源（CSS / JS / 图标）：网络优先，顺手存一份；
       断网时用缓存顶上。发新版照样立刻生效，不会吃到旧文件。
     · /api/ 一律不碰，永远走网络（免得把接口结果缓存出花来）。
     · 只处理同源 GET，其它请求原样放过去。

   改了这个文件，浏览器会自动更新（SW 脚本每次都回源校验）。
   ========================================================================== */
var VERSION = 'v1';
var CACHE = 'toolbox-' + VERSION;
var OFFLINE = '/static/offline.html';

/* 装好就先囤一份，断网时至少有东西可显示 */
var PRECACHE = [
  OFFLINE,
  '/static/theme.css',
  '/static/theme.js',
  '/static/particles.js',
  '/static/auth-guard.js',
  '/static/favicon.svg',
  '/static/pwa/icon-192.png'
];

self.addEventListener('install', function (e) {
  e.waitUntil(
    caches.open(CACHE).then(function (c) {
      return Promise.all(PRECACHE.map(function (u) {
        /* cache:'reload' 绕过 HTTP 缓存，保证囤的是最新那份；个别文件拿不到不算失败 */
        return c.add(new Request(u, { cache: 'reload' })).catch(function () {});
      }));
    }).then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener('activate', function (e) {
  e.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.map(function (k) {
        return k === CACHE ? null : caches.delete(k);
      }));
    }).then(function () { return self.clients.claim(); })
  );
});

function offlinePage() {
  return caches.match(OFFLINE).then(function (hit) {
    return hit || new Response(
      '<!doctype html><meta charset="utf-8"><title>离线了</title>' +
      '<p style="font:16px/1.7 system-ui;padding:40px">现在连不上服务器，检查一下网络再刷新。</p>',
      { status: 200, headers: { 'Content-Type': 'text/html; charset=utf-8' } }
    );
  });
}

self.addEventListener('fetch', function (e) {
  var req = e.request;
  if (req.method !== 'GET') { return; }                    /* 表单/删除之类不碰 */
  var url;
  try { url = new URL(req.url); } catch (err) { return; }
  if (url.origin !== self.location.origin) { return; }     /* 外站的请求不碰 */
  if (url.pathname.indexOf('/api/') === 0) { return; }     /* 接口永远走网络 */

  /* 页面：网络优先，断了就给离线提示页 */
  if (req.mode === 'navigate') {
    e.respondWith(fetch(req).catch(offlinePage));
    return;
  }

  /* 静态资源：网络优先 + 存一份，断了用缓存 */
  if (url.pathname.indexOf('/static/') === 0) {
    e.respondWith(
      fetch(req).then(function (res) {
        if (res && res.ok && res.type === 'basic') {
          var copy = res.clone();
          caches.open(CACHE).then(function (c) { c.put(req, copy); }).catch(function () {});
        }
        return res;
      }).catch(function () {
        return caches.match(req).then(function (hit) {
          return hit || new Response('', { status: 504, statusText: 'offline' });
        });
      })
    );
  }
});
