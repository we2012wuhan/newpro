/* ==========================================================================
   全局主题脚本：浅色 / 深色，所有页面共用
   --------------------------------------------------------------------------
   状态存在 localStorage['tb_theme']（按域名共享，所以首页切一次，
   所有工具页自动跟着变，不用每个工具再切一遍）。

   用法：
     <script src="/static/theme.js"></script>     放在 <head> 里、页面脚本之前
     TBTheme.mount(document.getElementById('x'))  把切换按钮挂到某个容器里
     TBTheme.toggle() / TBTheme.set('dark') / TBTheme.get()
     TBc('--tb-muted')                            取当前主题下某个颜色值（给图表用）

   防闪烁：页面 <head> 里先跑一小段内联脚本把 data-theme 写上，
   CSS 与页面内容就不会先亮一下再变暗。
   ========================================================================== */
(function () {
  'use strict';
  if (window.TBTheme) { return; }

  var KEY = 'tb_theme';
  var root = document.documentElement;

  function read() {
    try { return localStorage.getItem(KEY) === 'dark' ? 'dark' : 'light'; }
    catch (e) { return 'light'; }
  }
  function write(mode) {
    try { localStorage.setItem(KEY, mode); } catch (e) { /* 隐私模式：静默降级 */ }
  }
  function get() {
    return root.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
  }
  function token(name, fallback) {
    var v = '';
    try { v = getComputedStyle(root).getPropertyValue(name) || ''; } catch (e) { v = ''; }
    v = v.replace(/^\s+|\s+$/g, '');
    return v || (fallback || '');
  }

  function paint() {
    var dark = get() === 'dark';
    var list = document.querySelectorAll('.tb-tbtn');
    for (var i = 0; i < list.length; i++) {
      var b = list[i];
      var ico = b.querySelector('.tb-ico');
      var txt = b.querySelector('.tb-txt');
      if (ico) { ico.textContent = dark ? '\ud83c\udf19' : '\u2600\ufe0f'; }
      if (txt) { txt.textContent = dark ? '\u6df1\u8272' : '\u6d45\u8272'; }
      var label = dark ? '\u5207\u6362\u5230\u6d45\u8272' : '\u5207\u6362\u5230\u6df1\u8272';
      b.setAttribute('aria-label', label);
      b.setAttribute('title', label);
      b.setAttribute('aria-pressed', dark ? 'true' : 'false');
    }
    var m = document.querySelector('meta[name="theme-color"]');
    if (m) { m.setAttribute('content', dark ? '#0a0f1e' : '#f4f6fb'); }
  }

  function apply(mode, persist) {
    mode = (mode === 'dark') ? 'dark' : 'light';
    if (mode === 'dark') { root.setAttribute('data-theme', 'dark'); }
    else { root.removeAttribute('data-theme'); }
    if (persist !== false) { write(mode); }
    paint();
    try {
      window.dispatchEvent(new CustomEvent('tbthemechange', { detail: { theme: mode } }));
    } catch (e) { /* 老浏览器没有 CustomEvent：忽略 */ }
  }

  function toggle() { apply(get() === 'dark' ? 'light' : 'dark'); }

  function mount(el) {
    if (!el) { return null; }
    el.innerHTML = '<button class="tb-tbtn" type="button"><span class="tb-ico">\u2600\ufe0f</span>' +
      '<span class="tb-txt">\u6d45\u8272</span></button>';
    var btn = el.firstChild;
    btn.onclick = toggle;
    paint();
    return btn;
  }

  // 别的标签页切换了主题，这边跟着变
  window.addEventListener('storage', function (e) {
    if (e && e.key === KEY) { apply(read(), false); }
  });

  window.TBTheme = {
    KEY: KEY,
    get: get,
    set: function (mode) { apply(mode); },
    toggle: toggle,
    mount: mount,
    paint: paint,
    token: token
  };
  // 给图表等 JS 用的简写：取当前主题下的颜色值
  window.TBc = token;

  // 页面里没有手动 mount 时，自动认领 <span class="tb-mount"></span>
  function autoMount() {
    var list = document.querySelectorAll('.tb-mount');
    for (var i = 0; i < list.length; i++) {
      if (!list[i].querySelector('.tb-tbtn')) { mount(list[i]); }
    }
    paint();
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', autoMount);
  } else {
    autoMount();
  }
})();
