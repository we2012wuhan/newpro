/* /trip 的四个标签页
   ------------------------------------------------------------
   只负责显示哪个面板、同步 #hash 和通知其他模块。
   具体功能各自监听 tbtrip:tab 事件，没打开过的标签不提前请求数据。 */
(function () {
  'use strict';

  var VALID = { plan: 1, places: 1, routes: 1, footprints: 1 };
  var current = 'plan';

  function tabs() {
    return Array.prototype.slice.call(document.querySelectorAll('#tripTabs .tab'));
  }

  function panels() {
    return {
      plan: document.getElementById('panel-plan'),
      places: document.getElementById('panel-places'),
      routes: document.getElementById('panel-routes'),
      footprints: document.getElementById('panel-footprints')
    };
  }

  function urlFor(name) {
    var base = window.location.pathname + window.location.search;
    return name === 'plan' ? base : base + '#' + name;
  }

  function show(name, opts) {
    opts = opts || {};
    if (!VALID[name]) { name = 'plan'; }
    var box = panels();
    var list = tabs();
    if (!box.plan) { return; }

    current = name;
    list.forEach(function (tab) {
      var on = tab.getAttribute('data-tab') === name;
      tab.classList.toggle('on', on);
      tab.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    Object.keys(box).forEach(function (key) {
      if (box[key]) { box[key].hidden = key !== name; }
    });

    if (opts.hash !== false) {
      try {
        window.history.replaceState(null, '', urlFor(name));
      } catch (err) { /* file:// 之类环境忽略 */ }
    }
    document.dispatchEvent(new CustomEvent('tbtrip:tab', { detail: { name: name } }));
    return name;
  }

  function bind() {
    var nav = document.getElementById('tripTabs');
    if (nav) {
      nav.addEventListener('click', function (event) {
        var tab = event.target.closest('.tab');
        if (tab && nav.contains(tab)) {
          show(tab.getAttribute('data-tab'));
        }
      });
    }

    document.addEventListener('click', function (event) {
      var goto = event.target.closest('[data-goto]');
      if (goto) { show(goto.getAttribute('data-goto')); }
    });

    window.addEventListener('hashchange', function () {
      var name = (window.location.hash || '').replace('#', '');
      show(VALID[name] ? name : 'plan', { hash: false });
    });

    document.addEventListener('tbtrip:show', function (event) {
      if (event.detail && event.detail.name) { show(event.detail.name); }
    });
  }

  function init() {
    bind();
    var rid = new URLSearchParams(window.location.search).get('r');
    var hash = (window.location.hash || '').replace('#', '');
    // 从历史行程深链进来时固定停在行程规划，不被 #places 之类覆盖。
    show(rid ? 'plan' : (VALID[hash] ? hash : 'plan'), { hash: false });
  }

  window.TBTripShell = {
    show: function (name) { return show(name); },
    notify: function (name) {
      document.dispatchEvent(new CustomEvent('tbtrip:tab', { detail: { name: name } }));
    },
    get current() { return current; }
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
