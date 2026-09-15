/* 会话过期兜底：任何一个请求拿到 401，就回登录页，并记住当前地址 */
(function () {
  'use strict';
  if (window.__authGuard) { return; }
  window.__authGuard = true;

  function toLogin() {
    if (location.pathname.indexOf('/login') === 0) { return; }
    var here = location.pathname + location.search;
    var next = (here && here !== '/') ? ('?next=' + encodeURIComponent(here)) : '';
    location.replace('/login' + next);
  }

  var rawFetch = window.fetch;
  if (rawFetch) {
    window.fetch = function () {
      return rawFetch.apply(this, arguments).then(function (res) {
        if (res && res.status === 401) { toLogin(); }
        return res;
      });
    };
  }

  var rawOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function () {
    this.addEventListener('load', function () {
      if (this.status === 401) { toLogin(); }
    });
    return rawOpen.apply(this, arguments);
  };
})();