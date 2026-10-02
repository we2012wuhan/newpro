/* ==========================================================================
首页「今天要处理的事」
--------------------------------------------------------------------------
把各工具里「已经到期、但还没处理」的记录回收上来，拼成一份待办。
只在有内容的时候出现；没内容时整块隐藏，不占任何空间。

数据来自各工具自己的 localStorage（字段改名时要同步这里）：
  判断力教练  jt_cases_v2   due      到期日      outcome 非空 = 已回看
  贝叶斯日记  pd_bets_v1    by       到期日      settled 非空 = 已结算
  读书落地    走 /reading-practice/api/plans（数据在服务器 SQLite 里，不读 localStorage）
========================================================================== */
(function () {
  'use strict';

  var MAX_ROWS = 3;    // 顶部最多列几条，多的收成一行提示
  var MAX_SCAN = 100;  // 每个工具最多扫多少条，记录很多时不至于卡

  function pad(n) { return (n < 10 ? '0' : '') + n; }

  function todayStr() {
    var d = new Date();
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
  }

  // b - a，单位天。两个参数都是 YYYY-MM-DD
  function daysBetween(a, b) {
    var pa = String(a).split('-'), pb = String(b).split('-');
    if (pa.length !== 3 || pb.length !== 3) { return 0; }
    var da = new Date(+pa[0], +pa[1] - 1, +pa[2]);
    var db = new Date(+pb[0], +pb[1] - 1, +pb[2]);
    return Math.round((db - da) / 86400000);
  }

  function load(key) {
    try {
      var raw = localStorage.getItem(key);
      var arr = raw ? JSON.parse(raw) : [];
      return Object.prototype.toString.call(arr) === '[object Array]' ? arr : [];
    } catch (e) { return []; }
  }

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  // 标题压成一行，太长就截断——顶部这块是提醒，不是内容本身
  function oneLine(s, n) {
    var t = String(s == null ? '' : s).replace(/\s+/g, ' ').replace(/^\s+|\s+$/g, '');
    if (!t) { return ''; }
    return t.length > n ? t.slice(0, n) + '…' : t;
  }

  function pct(r) { return r && r.conf ? '（当时说 ' + r.conf + '%）' : ''; }

  var SOURCES = [
    {
      key: 'jt_cases_v2', icon: '🧠', name: '判断力教练', href: '/judgement-trainer',
      due:  function (r) { return r.due; },
      done: function (r) { return !!r.outcome; },
      text: function (r) { return r.text; },
      note: function (r) { return '该看结果了' + pct(r); }
    },
    {
      key: 'pd_bets_v1', icon: '🎯', name: '贝叶斯日记', href: '/predict-diary',
      due:  function (r) { return r.by; },
      done: function (r) { return !!r.settled; },
      text: function (r) { return r.bet; },
      note: function (r) { return '该结算了' + pct(r); }
    },
    {
      // 读书落地已改成 SQLite 存数据，下面 loadRemote() 单独拉
      key: '__remote_reading__', icon: '📚', name: '读书落地', href: '/reading-practice',
      due:  function () { return ''; },
      done: function () { return true; },
      text: function () { return ''; },
      note: function () { return ''; }
    }
  ];

  // 从服务端拉回来的待办（读书落地）
  var remote = [];

  function loadRemote() {
    return fetch('/reading-practice/api/plans', { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d || !d.ok || !d.items) { return []; }
        var today = todayStr(), out = [];
        d.items.forEach(function (p) {
          if (!p || p.reviewed) { return; }
          var due = p.reviewAt;
          if (!due || String(due) > today) { return; }
          out.push({
            icon: '📚', name: '读书落地', href: '/reading-practice',
            text: oneLine(p.point, 60) || '（没写标题）',
            note: (p.book ? '《' + p.book + '》' : '') + '7 天了，用上了吗',
            late: daysBetween(due, today)
          });
        });
        return out;
      })
      .catch(function () { return []; });
  }

  function collect() {
    var today = todayStr();
    var out = [];
    SOURCES.forEach(function (src) {
      var rows = load(src.key).slice(0, MAX_SCAN);
      rows.forEach(function (r) {
        if (!r || src.done(r)) { return; }
        var due = src.due(r);
        // 只收「已经到期」的；没填日期或还没到的，不打扰
        if (!due || String(due) > today) { return; }
        out.push({
          icon: src.icon, name: src.name, href: src.href,
          text: oneLine(src.text(r), 60) || '（没写标题）',
          note: src.note(r),
          late: daysBetween(due, today)
        });
      });
    });
    // 逾期越久越靠前
    out.sort(function (a, b) { return b.late - a.late; });
    return out;
  }

  function rowHTML(it) {
    var late = it.late <= 0 ? '今天到期' : '逾期 ' + it.late + ' 天';
    return '<a class="td-row" href="' + esc(it.href) + '">' +
      '<span class="td-ico" aria-hidden="true">' + it.icon + '</span>' +
      '<span class="td-body">' +
        '<span class="td-txt">' + esc(it.text) + '</span>' +
        '<span class="td-sub">' + esc(it.name) + ' · ' + esc(it.note) + '</span>' +
      '</span>' +
      '<span class="td-late">' + esc(late) + '</span>' +
    '</a>';
  }

  function render() {
    var host = document.getElementById('today');
    if (!host) { return; }

    var items = collect().concat(remote);
    items.sort(function (a, b) { return b.late - a.late; });
    if (!items.length) {
      // 没事就不出现——不显示"今天没有待办"这种客套话，免得天天占一块地方
      host.hidden = true;
      host.innerHTML = '';
      return;
    }

    var head = '<div class="td-head">' +
      '<span class="td-ttl">今天要处理的事</span>' +
      '<span class="td-n">' + items.length + ' 件</span>' +
    '</div>';

    var more = items.length > MAX_ROWS
      ? '<div class="td-more">还有 ' + (items.length - MAX_ROWS) + ' 件，进对应的工具里看</div>'
      : '';

    host.innerHTML = head +
      items.slice(0, MAX_ROWS).map(rowHTML).join('') + more;
    host.hidden = false;
  }

  function boot() {
    render();
    loadRemote().then(function (rows) {
      if (!rows.length) { return; }
      remote = rows;
      render();
    });
    // 去工具里处理完、按浏览器返回时，重新算一遍
    window.addEventListener('pageshow', function (e) { if (e.persisted) { render(); } });
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden) { render(); }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();