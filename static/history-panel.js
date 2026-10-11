/* ==========================================================================
   右侧「历史记录」面板：共用组件（列表 + 搜索 + 页码翻页 + 收起/展开）
   --------------------------------------------------------------------------
   5Why 分析法 / 苏格拉底提问 / 第一性原理 三个工具右侧那一栏是同一套东西。
   以前每个页面各抄一份，改一次要改三处 —— 现在统一收到这里。

   页面怎么用：
     1) <head> 里引一行（放在 theme.css / theme.js 之后就行）：
            <script src="/static/history-panel.js?v=1"></script>
     2) HTML 骨架照旧（组件自己认这些 id）：
            <aside class="col-side"><div class="card">
              <div class="hist-head"><div class="card-h">历史记录</div>
                <div class="vctl" id="vctl" hidden>…朗读按钮…</div></div>
              <div class="hsearch"><input id="hq" …><button id="hqClear" …></div>
              <div class="hq-info mini" id="hqInfo"></div>
              <div id="history"></div><div class="hist-pager" id="histPager"></div>
            </div></aside>
     3) 页面脚本里调一次 TBHistory.init({...})，只传本工具特有的东西（见 init 注释）。
     4) 需要刷新列表时：TBHistory.reload()（回第 1 页：TBHistory.reload({reset:true})）。

   页面不用再自己写：分页、搜索防抖、命中高亮、空态/错误态、收起到右边缘再展开。
   ========================================================================== */
(function () {
  'use strict';
  if (window.TBHistory) { return; }

  var KEY = 'tb_hist_fold';          /* 收起状态（纯外观偏好，跟 tb_theme 一样） */
  var WIDE = '(min-width: 1100px)';
  var DUR = 300;
  var root = document.documentElement;
  var head = document.head || document.getElementsByTagName('head')[0];

  /* ------------------------------------------------------------------ 样式 */
  var CSS = [
    /* 标题行：标题靠左，朗读开关和收起按钮都靠右 */
    '.hist-head{display:flex;align-items:center;gap:10px;margin-bottom:6px}',
    '.hist-head>.card-h{margin-bottom:0;margin-right:auto}',
    '.vctl{display:flex;gap:8px;align-items:center;flex:0 0 auto}',
    '.vctl[hidden]{display:none}',
    '.vbtn{min-height:36px;padding:0 12px;border-radius:999px;border:1px solid var(--line);',
    'background:var(--field);color:var(--muted);font-family:inherit;font-size:12.5px;cursor:pointer;white-space:nowrap}',
    '.vbtn:hover{border-color:var(--acc-line);color:var(--ink)}',
    '.vbtn.on{border-color:var(--acc);background:var(--acc-l);color:var(--acc);font-weight:600}',
    '.vbtn[hidden]{display:none}',
    '@media (max-width:560px){.vbtn{padding:0 9px;font-size:12px}}',
    /* 列表条目 */
    '.hist-i{display:flex;align-items:stretch;gap:4px;margin-top:8px}',
    '.hist-open{flex:1 1 auto;min-width:0;text-align:left;padding:10px 12px;border:1px solid var(--line);',
    'border-radius:12px;background:var(--field);cursor:pointer;transition:border-color .15s,background .15s}',
    '.hist-open:hover{border-color:var(--acc-line);background:var(--acc-l)}',
    '.hist-i.on .hist-open{border-color:var(--acc);background:var(--acc-l);box-shadow:inset 3px 0 0 var(--acc)}',
    '.hist-t{display:block;font-size:14px;font-weight:600;color:var(--ink);line-height:1.45;',
    'overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
    '.hist-m{display:block;font-size:11px;color:var(--muted);margin-top:3px;overflow:hidden;',
    'text-overflow:ellipsis;white-space:nowrap}',
    '.hist-s{display:block;font-size:11.5px;color:var(--muted);margin-top:4px;line-height:1.55;',
    'overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
    '.hist-del{flex:0 0 34px;border:1px solid var(--line);border-radius:10px;background:var(--field);',
    'color:var(--muted);cursor:pointer;font-size:15px;line-height:1}',
    '.hist-del:hover{color:var(--err);border-color:var(--err-line);background:var(--err-l)}',
    '.hist-exp{flex:0 0 34px;border:1px solid var(--line);border-radius:10px;background:var(--field);',
    'color:var(--muted);font-family:inherit;font-size:14px;cursor:pointer}',
    '.hist-exp:hover{color:var(--acc);border-color:var(--acc-line);background:var(--acc-l)}',
    '.hist-exp:disabled{opacity:.55;cursor:default}',
    /* 搜索框 */
    '.hsearch{position:relative;margin-top:6px}',
    '.hsearch input{width:100%;border:1px solid var(--line);border-radius:12px;padding:10px 34px 10px 12px;',
    'background:var(--field);color:var(--ink);font-family:inherit;font-size:14px;outline:none;min-height:42px}',
    '.hsearch input:focus{border-color:var(--acc-line);box-shadow:var(--focus-ring)}',
    '.hsearch input::placeholder{color:var(--muted);opacity:.72}',
    '.hq-clear{position:absolute;right:6px;top:50%;transform:translateY(-50%);width:26px;height:26px;',
    'border:0;border-radius:50%;background:var(--soft);color:var(--muted);cursor:pointer;',
    'font-size:15px;line-height:1;padding:0}',
    '.hq-clear:hover{color:var(--err)}',
    '.hq-info{margin-top:6px}',
    '.hq-info:empty{display:none}',
    '#history mark{background:var(--acc-l);color:var(--acc);border-radius:3px;padding:0 1px}',
    /* 页码 */
    '.hist-pager{display:flex;flex-wrap:wrap;justify-content:center;align-items:center;gap:6px;margin-top:10px}',
    '.hist-pager:empty{display:none}',
    '.hist-pager .pg{flex:0 0 auto;min-width:30px;height:30px;padding:0 7px;border:1px solid var(--line);',
    'border-radius:9px;background:var(--field);color:var(--text);font-family:inherit;',
    'font-size:12.5px;line-height:1;cursor:pointer}',
    '.hist-pager .pg:hover:enabled{border-color:var(--acc-line);background:var(--acc-l);color:var(--ink)}',
    '.hist-pager .pg.on{background:var(--acc);border-color:var(--acc);color:var(--card);font-weight:600;cursor:default}',
    '.hist-pager .pg-gap{color:var(--muted);font-size:12.5px}',
    '.hist-pager .pg-n{flex:1 1 100%;text-align:center;font-size:11.5px;color:var(--muted)}',
    '.hist-fold{flex:0 0 34px;width:34px;height:34px;padding:0;display:inline-flex;',
    'align-items:center;justify-content:center;border:1px solid var(--line);',
    'border-radius:10px;background:var(--field);color:var(--muted);font-family:inherit;cursor:pointer}',
    '.hist-fold:hover{color:var(--acc);border-color:var(--acc-line);background:var(--acc-l)}',
    '.hist-fold svg{display:block;width:16px;height:16px}',
    /* 宽屏：右栏不再无限长，最多和左栏一样高，只有列表自己滚；页面底部不留多余空隙 */
    '@media (min-width:1100px){',
    '.col-main{min-width:0;min-height:calc(100vh - var(--stick,78px) - 28px)}',
    '.wrap{padding-bottom:env(safe-area-inset-bottom)}',
    '.col-side>.card{display:flex;flex-direction:column;max-height:calc(100vh - var(--stick,78px) - 28px)}',
    '.col-side #history{min-height:0;overflow:auto;overscroll-behavior:contain}}',
    /* 收起 / 展开 */
    '.col-main{min-width:0}',
    '.col-side>.card{width:var(--tbSideW,100%)}',
    '.wrap{transition:grid-template-columns .3s cubic-bezier(.4,0,.2,1),gap .3s cubic-bezier(.4,0,.2,1)}',
    '.col-side{transition:opacity .26s ease,transform .3s cubic-bezier(.4,0,.2,1)}',
    '.hist-reopen{position:fixed;right:0;top:50%;z-index:40;display:inline-flex;',
    'align-items:center;justify-content:center;writing-mode:vertical-rl;',
    'padding:14px 7px;min-height:96px;border:1px solid var(--line);border-right:0;',
    'border-radius:12px 0 0 12px;background:var(--card);color:var(--ink);',
    'font-family:inherit;font-size:12.5px;font-weight:600;letter-spacing:.08em;',
    'cursor:pointer;box-shadow:-8px 0 20px -14px rgba(16,24,40,.55);',
    'opacity:0;pointer-events:none;transform:translateY(-50%) translateX(120%);',
    'transition:opacity .22s ease,transform .26s cubic-bezier(.4,0,.2,1)}',
    'html[data-hist-fold="on"] .hist-reopen{opacity:1;pointer-events:auto;',
    'transform:translateY(-50%) translateX(0)}',
    '.hist-reopen:hover{color:var(--acc);border-color:var(--acc-line)}',
    '@media (min-width:1100px){',
    'html[data-hist-fold="on"] .wrap{grid-template-columns:minmax(0,1fr) 0px;gap:0}',
    'html[data-hist-fold="on"] .col-side{min-width:0;overflow:hidden;opacity:0;',
    'transform:translateX(28px);pointer-events:none}}',
    '@media (max-width:1099.98px){',
    'html[data-hist-fold="on"] .col-side{display:none}}',
    '@media print{.hist-fold,.hist-reopen{display:none!important}}'
  ].join('');

  function injectCss() {
    if (!head || head.querySelector('#tbHistoryCss')) { return; }
    var el = document.createElement('style');
    el.id = 'tbHistoryCss';
    el.appendChild(document.createTextNode(CSS));
    head.appendChild(el);
  }

  /* ------------------------------------------------------------------ 小工具 */
  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  /* 把命中的关键词标出来。先 esc 再高亮，顺序不能反。 */
  function hl(text, q) {
    var out = esc(text);
    var terms = String(q || '').trim().split(/\s+/).filter(Boolean).slice(0, 6)
      .map(function (t) { return esc(t); }).filter(Boolean);
    if (!terms.length) { return out; }
    var re = terms.map(function (t) { return t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }).join('|');
    if (!re) { return out; }
    try { return out.replace(new RegExp('(' + re + ')', 'ig'), '<mark>$1</mark>'); }
    catch (e) { return out; }
  }

  function hasTerm(text, q) {
    var terms = String(q || '').trim().toLowerCase().split(/\s+/).filter(Boolean);
    var low = String(text || '').toLowerCase();
    for (var i = 0; i < terms.length; i++) { if (low.indexOf(terms[i]) >= 0) { return true; } }
    return false;
  }

  /* 命中的词要是只出现在正文里，光看标题不知道为什么命中 —— 从正文抠一句出来。
     列表接口不带整段对话（太重），所以页面只把「能搜的那些字段」传进来。 */
  function snippetFrom(fields, q) {
    var terms = String(q || '').trim().toLowerCase().split(/\s+/).filter(Boolean);
    if (!terms.length) { return ''; }
    fields = (fields || []).map(function (f) { return String(f == null ? '' : f); });
    for (var i = 0; i < fields.length; i++) {
      var f = fields[i];
      if (!f) { continue; }
      var low = f.toLowerCase(), at = -1;
      for (var k = 0; k < terms.length; k++) {
        var pos = low.indexOf(terms[k]);
        if (pos >= 0 && (at < 0 || pos < at)) { at = pos; }
      }
      if (at < 0) { continue; }
      var start = Math.max(0, at - 10);
      var end = Math.min(f.length, at + 46);
      return (start > 0 ? '…' : '') + f.slice(start, end) + (end < f.length ? '…' : '');
    }
    return '';
  }

  function httpGet(url) {
    return fetch(url, { headers: { 'Accept': 'application/json' } })
      .then(function (r) { return r.json(); })
      .catch(function () { return { ok: false, message: '网络不通，稍后再试' }; });
  }

  /* ------------------------------------------------------------------ 状态 */
  var O = {};                     /* 页面传进来的配置 */
  var S = {                       /* 面板自己的状态，页面不用管 */
    items: [], err: '', q: '', busy: false,
    page: 1, pages: 1, total: 0, size: 6, hit: null
  };
  var qTimer = null;
  var fitTimer = null;

  /* 页码：页少就全列出，页多就 1 … 当前附近 … 末页（0 = 省略号） */
  function pageList(cur, pages) {
    var out = [], i;
    if (pages <= 7) {
      for (i = 1; i <= pages; i++) { out.push(i); }
      return out;
    }
    var from = Math.max(2, Math.min(cur - 1, pages - 3));
    var to = from + 2;
    out.push(1);
    if (from > 2) { out.push(0); }
    for (i = from; i <= to; i++) { out.push(i); }
    if (to < pages - 1) { out.push(0); }
    out.push(pages);
    return out;
  }

  /* 一页几条：按当前窗口高度估一个，让一页正好把卡片填满 */
  function fitSize() {
    try {
      var stick = parseFloat(getComputedStyle(root).getPropertyValue('--stick')) || 66;
      var room = window.innerHeight - stick - 28 - 210;   /* 210 ≈ 标题 + 搜索框 + 翻页条 */
      return Math.max(4, Math.min(20, Math.floor(room / 68)));
    } catch (e) { return 6; }
  }

  /* 估多了就往下降：宁可这一页少放一条，也不要列表自己带滚动条 */
  function fitList() {
    var el = $('history');
    if (!el || S.busy || S.err || S.size <= 4) { return; }
    var over = el.scrollHeight - el.clientHeight;
    if (over <= 1) { return; }
    S.size = Math.max(4, S.size - Math.max(1, Math.ceil(over / 68)));
    load();
  }

  /* ------------------------------------------------------------------ 渲染 */
  /* 搜索框下面那行：命中几条 / 没搜到 / 搜着… */
  function renderInfo() {
    var info = $('hqInfo'), clr = $('hqClear');
    var q = (S.q || '').trim();
    if (clr) { clr.hidden = !q; }
    if (!info) { return; }
    if (!q || S.err) { info.textContent = ''; return; }
    if (S.busy) { info.textContent = '搜着…'; return; }
    if (!S.hit) { info.textContent = ''; return; }
    info.textContent = S.hit.total ? ('命中 ' + S.hit.total + ' 条') : '没搜到';
  }

  /* 一页装不下就少放一条，别让列表内部出现滚动条 */
  function renderPager() {
    var el = $('histPager');
    if (!el) { return; }
    var pages = S.pages || 1;
    if (S.err || pages <= 1) { el.innerHTML = ''; return; }
    var h = pageList(S.page || 1, pages).map(function (n) {
      if (!n) { return '<span class="pg-gap">…</span>'; }
      var on = (n === S.page);
      return '<button class="pg' + (on ? ' on' : '') + '" type="button" data-pg="' + n + '"' +
        (on ? ' aria-current="page"' : '') + '>' + n + '</button>';
    }).join('');
    h += '<span class="pg-n">共 ' + (S.total || 0) + ' 条</span>';
    el.innerHTML = h;
    Array.prototype.forEach.call(el.querySelectorAll('[data-pg]'), function (b) {
      b.onclick = function () { goPage(Number(b.getAttribute('data-pg'))); };
    });
  }

  function bindRows() {
    var el = $('history');
    if (!el) { return; }
    Array.prototype.forEach.call(el.querySelectorAll('[data-open]'), function (b) {
      b.onclick = function () { O.open(Number(b.getAttribute('data-open'))); };
    });
    Array.prototype.forEach.call(el.querySelectorAll('[data-del]'), function (b) {
      b.onclick = function () { delRow(Number(b.getAttribute('data-del'))); };
    });
    Array.prototype.forEach.call(el.querySelectorAll('[data-exp]'), function (b) {
      b.onclick = function () { if (O.exp) { O.exp(b.getAttribute('data-exp'), b); } };
    });
  }

  function renderList() {
    var el = $('history');
    if (!el) { return; }
    renderInfo();
    renderPager();
    var q = (S.q || '').trim();
    if (S.err) {
      el.innerHTML = '<div class="mini" style="margin-top:6px">' + esc(S.err) + '</div>';
      return;
    }
    if (!S.items.length) {
      var txt = q ? ((O.empty && O.empty.noHit) || '没有含「{q}」的记录。换个词试试。')
        : ((O.empty && O.empty.none) || '还没有记录。');
      el.innerHTML = '<div class="mini" style="margin-top:6px">' +
        txt.replace('{q}', '<b>' + esc(q) + '</b>') + '</div>';
      return;
    }
    var h = S.items.map(function (it) {
      var cur = O.isActive ? O.isActive(it.id) : false;
      var title = O.title(it);
      var meta = O.meta ? O.meta(it) : '';
      var snip = (q && O.snippet) ? O.snippet(it, q) : '';
      var extra = (snip && !hasTerm(title, q)) ? ('<span class="hist-s">' + hl(snip, q) + '</span>') : '';
      return '<div class="hist-i' + (cur ? ' on' : '') + '">' +
        '<button class="hist-open" type="button" data-open="' + it.id + '">' +
          '<span class="hist-t">' + hl(title, q) + '</span>' +
          '<span class="hist-m">' + esc(meta) + '</span>' + extra +
        '</button>' +
        (O.exp ? '<button class="hist-exp" type="button" data-exp="' + it.id +
          '" title="导出这条 Markdown">⤓</button>' : '') +
        '<button class="hist-del" type="button" data-del="' + it.id + '" title="删掉这条">×</button>' +
        '</div>';
    }).join('');
    el.innerHTML = h;
    bindRows();
  }

  /* ------------------------------------------------------------------ 取数 */
  function load() {
    var q = (S.q || '').trim();
    var url = O.url + '?page=' + (S.page || 1) + '&size=' + (S.size || 6) +
      (q ? ('&q=' + encodeURIComponent(q)) : '');
    S.busy = true;
    renderInfo();
    return httpGet(url).then(function (d) {
      S.busy = false;
      d = d || {};
      S.items = (d.ok && d.items) || [];
      S.err = d.ok ? '' : (d.message || '读不到历史记录');
      S.total = (d.ok && typeof d.total === 'number') ? d.total : S.items.length;
      S.pages = (d.ok && typeof d.pages === 'number') ? d.pages : 1;
      S.page = (d.ok && typeof d.page === 'number') ? d.page : 1;  /* 服务端会把越界页码夹回来 */
      S.hit = d.ok ? { total: S.total, q: d.q || '' } : null;
      renderList();
      fitList();
    });
  }

  function goPage(n) {
    if (S.busy) { return; }
    n = Number(n) || 1;
    if (n < 1 || n > (S.pages || 1) || n === S.page) { return; }
    S.page = n;
    load();
  }

  function delRow(id) {
    if (!window.confirm(O.confirmDel || '删掉这条记录？删了就找不回来了。')) { return; }
    Promise.resolve(O.del(id)).then(function () { load(); });
  }

  /* 搜索框只在启动时绑一次：它不在 #history 里面，重画列表不会把它冲掉，
     所以打字打一半不会被清空、也不会丢焦点。 */
  function bindSearch() {
    var el = $('hq');
    if (!el) { return; }
    el.value = S.q || '';
    el.oninput = function () {
      S.q = el.value;
      S.page = 1;                     /* 换了搜索词就回到第一页 */
      renderInfo();
      clearTimeout(qTimer);
      qTimer = setTimeout(load, 260);
    };
    el.onkeydown = function (e) {
      if (e.key === 'Enter') { e.preventDefault(); clearTimeout(qTimer); load(); }
      else if (e.key === 'Escape') { el.value = ''; S.q = ''; S.page = 1; clearTimeout(qTimer); load(); }
    };
    var clr = $('hqClear');
    if (clr) {
      clr.onclick = function () {
        el.value = ''; S.q = ''; S.page = 1; clearTimeout(qTimer); load(); el.focus();
      };
    }
  }

  /* --------------------------------------------------------- 收起 / 展开 */
  function pref() {
    try { return localStorage.getItem(KEY) === '1'; } catch (e) { return false; }
  }
  function save(on) {
    try { localStorage.setItem(KEY, on ? '1' : '0'); } catch (e) { /* 隐私模式：静默降级 */ }
  }
  function isOn() { return root.getAttribute('data-hist-fold') === 'on'; }

  function applyFold(on) {
    if (on) { root.setAttribute('data-hist-fold', 'on'); }
    else { root.removeAttribute('data-hist-fold'); }
    var b = $('histFold'), t = $('histReopen');
    if (b) {
      b.setAttribute('aria-expanded', on ? 'false' : 'true');
      b.title = on ? '展开历史记录' : '收起历史记录';
    }
    if (t) { t.setAttribute('aria-expanded', on ? 'true' : 'false'); }
  }

  /* 按新宽度重新适配一遍：页面里靠窗口尺寸算的东西（吸顶偏移、图表）都听 resize 的 */
  function relayout() {
    try { window.dispatchEvent(new Event('resize')); } catch (e) {}
  }
  function setFold(on) {
    applyFold(on);
    relayout();
    setTimeout(relayout, DUR + 60);
  }
  function wide() {
    try { return window.matchMedia(WIDE).matches; } catch (e) { return true; }
  }
  /* 收起前先记住这一栏多宽（过渡时卡片保持原宽度，不跟着列一起重排） */
  function pin(side) {
    if (!side) { return; }
    try {
      var w = side.offsetWidth;
      if (w > 0) { side.style.setProperty('--tbSideW', w + 'px'); }
    } catch (e) {}
  }
  function unpin(side) {
    if (!side) { return; }
    setTimeout(function () {
      try { side.style.removeProperty('--tbSideW'); } catch (e) {}
    }, DUR + 80);
  }
  /* 窄屏的滑出 / 滑入（宽屏交给 CSS 过渡） */
  function anim(entering, done) {
    var side = $('tbHistSide');
    if (!side || typeof side.animate !== 'function') { done(); return; }
    if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      done(); return;
    }
    var frames = entering
      ? [{ transform: 'translateX(36px)', opacity: 0 }, { transform: 'translateX(0)', opacity: 1 }]
      : [{ transform: 'translateX(0)', opacity: 1 }, { transform: 'translateX(36px)', opacity: 0 }];
    try {
      var a = side.animate(frames, { duration: 200, easing: 'ease' });
      a.onfinish = function () { done(); };
      a.oncancel = function () { done(); };
    } catch (e) { done(); }
  }

  var busy = false;
  function toggleFold() {
    if (busy) { return; }               /* 过渡期间不接第二次点击 */
    busy = true;
    setTimeout(function () { busy = false; }, DUR + 80);
    var on = !isOn();
    save(on);
    if (wide()) {
      var side = $('tbHistSide');
      if (on) { pin(side); }
      setFold(on);
      if (!on) { unpin(side); }
      return;
    }
    if (on) { anim(false, function () { setFold(true); }); }        /* 收起：先滑出去，再隐掉 */
    else { applyFold(false); anim(true, function () { setFold(false); }); }
  }

  var SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"'
    + ' stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    + '<polyline points="6.5 5 13 12 6.5 19"></polyline>'
    + '<polyline points="12.5 5 19 12 12.5 19"></polyline></svg>';

  function mountFold() {
    var side = document.querySelector('.col-side');
    var headEl = side && side.querySelector('.hist-head');
    if (!side || !headEl) { return; }
    if (!side.id) { side.id = 'tbHistSide'; }

    var btn = document.createElement('button');
    btn.type = 'button';
    btn.id = 'histFold';
    btn.className = 'hist-fold';
    btn.title = '收起历史记录';
    btn.setAttribute('aria-label', '收起历史记录');
    btn.setAttribute('aria-controls', side.id);
    btn.innerHTML = SVG;
    btn.onclick = toggleFold;
    headEl.appendChild(btn);

    var tab = document.createElement('button');
    tab.type = 'button';
    tab.id = 'histReopen';
    tab.className = 'hist-reopen';
    tab.title = '展开历史记录';
    tab.setAttribute('aria-label', '展开历史记录');
    tab.setAttribute('aria-controls', side.id);
    tab.textContent = '‹ 历史记录';
    tab.onclick = toggleFold;
    document.body.appendChild(tab);

    applyFold(isOn());
  }

  /* ------------------------------------------------------------------ 对外 */
  function init(opts) {
    O = opts || {};
    if (!O.url || !O.title || !O.open || !O.del) {
      throw new Error('TBHistory.init 缺少必填项：url / title / open / del');
    }
    S.size = fitSize();               /* 一页几条按窗口高度定 */
    mountFold();
    bindSearch();
    clearTimeout(fitTimer);
    window.addEventListener('resize', function () {
      clearTimeout(fitTimer);
      fitTimer = setTimeout(fitList, 320);
    });
    return load();
  }

  function reload(o) {
    if (o && o.reset) { S.page = 1; }
    return load();
  }

  /* 别的标签页切了收起状态，这边跟着变 */
  window.addEventListener('storage', function (e) {
    if (e && e.key === KEY) { applyFold(pref()); }
  });

  /* <head> 阶段先把样式注进去、把上次的收起状态挂上（刷新时不会先亮一下再收起） */
  injectCss();
  applyFold(pref());

  window.TBHistory = {
    KEY: KEY,
    init: init,
    reload: reload,
    goPage: goPage,
    snippetFrom: snippetFrom,
    state: function () {
      return { page: S.page, pages: S.pages, total: S.total, size: S.size, items: S.items };
    },
    fold: { get: isOn, set: function (on) { save(!!on); setFold(!!on); }, toggle: toggleFold }
  };
})();
