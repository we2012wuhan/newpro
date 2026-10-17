/* 使用足迹：打开任意工具页时记一笔。
 * 服务端按「用户 + 路径」5 分钟内去重，所以刷新/来回切不会灌水。
 * 页面想暂停上报：window.TB_NO_USAGE = true 或 <body data-usage="off">
 */
(function () {
  'use strict';
  if (window.TBUsage) { return; }
  function hit(tool) {
    var path = String(tool || location.pathname || '/').split('?')[0];
    if (!path || path === '/login') { return; }
    try {
      return fetch('/api/usage/hit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tool: path }),
        keepalive: true
      }).then(function (r) { return r.json(); }).catch(function () { return null; });
    } catch (e) { return Promise.resolve(null); }
  }
  window.TBUsage = { hit: hit, ready: Promise.resolve(false) };
  if (window.TB_NO_USAGE) { return; }
  if (document.body && document.body.getAttribute('data-usage') === 'off') { return; }
  window.TBUsage.ready = hit(location.pathname);
})();
