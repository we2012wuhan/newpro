/* 全站大肥鱼精灵
 * ------------------------------------------------------------
 * 右下角常驻的一只胖鱼：呼吸、摆尾、眨眼、吐气泡，眼神会跟着鼠标走。
 * 点一下会弹一句小提示，再点一下还会蹦一下。
 * 它和 AI 助手入口并排停靠，AI 面板打开时自动让位。
 * 可以按住拖到任意位置，位置记在 localStorage；双击回到 AI 入口旁边。
 *
 * 隐藏：window.TB_NO_FISH = true 或 <body data-fish-mascot="off">
 * 手动喊话：window.TBFishMascot.say('今天也慢慢来～')
 * 复位位置：window.TBFishMascot.reset()
 */
(function () {
  'use strict';

  var W = window;
  if (W.TBFishMascot) { return; }
  if (location.pathname === '/login') { return; }
  try { if (W.self !== W.top) { return; } } catch (e) { return; }
  try {
    if (new URLSearchParams(location.search).get('embed') === '1') { return; }
  } catch (e) { /* 老浏览器：继续 */ }

  var NOTES = [
    '嗨，我是大肥鱼，今天也慢慢来～',
    '慢慢来，会比较快。',
    '记得喝口水，再继续。',
    '需要帮忙，就点右边的 ✦。',
    '摸到我了，那就再坚持一下。'
  ];

  // 视图状态用于拖动和休息动作；角色使用 PNG 三视图素材。
  var VIEWS = { front: '/static/fish-mascot/front.png', side: '/static/fish-mascot/side.png', back: '/static/fish-mascot/back.png' };
  var PET_HTML = '<span class="pet"><img class="pet-img" src="' + VIEWS.front + '" alt="大肥鱼" draggable="false"></span>';

  var CSS = [
    ':host{all:initial;position:fixed;right:24px;bottom:20px;z-index:2147482000;',
    'width:112px;height:150px;display:block;pointer-events:none;opacity:0;',
    'transition:opacity .45s ease,right .18s ease,bottom .18s ease,left .18s ease,top .18s ease;',
    'font-family:-apple-system,"PingFang SC","Microsoft YaHei",system-ui,sans-serif}',
    ':host([data-ready]){opacity:1}',
    ':host(.dragging){transition:none}',
    ':host(.is-covered){opacity:0;pointer-events:none}',
    ':host(.is-covered) .fish{pointer-events:none}',
    '.fish{display:block;width:100%;height:100%;padding:0;border:0;background:none;',
    'pointer-events:auto;cursor:grab;position:relative;border-radius:26px;touch-action:none;',
    'user-select:none;-webkit-user-select:none;-webkit-user-drag:none;',
    '-webkit-tap-highlight-color:transparent}',
    '.dragging .fish{cursor:grabbing}',
    '.pet{position:absolute;inset:0;z-index:1;display:flex;align-items:flex-end;justify-content:center;animation:fmFloat 5.6s ease-in-out infinite;',
    'transform:translate(var(--look-x,0px),var(--look-y,0px)) rotate(var(--tilt,0deg));',
    'transform-origin:50% 90%;transition:filter .2s ease}',
    '.pet-img{display:block;width:100%;height:100%;object-fit:contain;pointer-events:none;',
    'user-select:none;-webkit-user-drag:none;filter:drop-shadow(0 10px 16px rgba(38,54,120,.34))}',
    '.fish[data-view="side"] .pet{transform:translate(var(--look-x,0px),var(--look-y,0px)) rotate(var(--tilt,0deg))}',
    '.fish[data-view="back"] .pet{opacity:.86}',
    '.dragging .pet{filter:drop-shadow(0 18px 24px rgba(38,54,120,.46));animation-play-state:paused}',
    '.poke .pet{animation:fmPoke .9s cubic-bezier(.2,.8,.3,1)}',
    '.fish:focus-visible{outline:2px solid #6d7cff;outline-offset:5px}',
    '.bubble{position:absolute;z-index:2;right:6px;bottom:calc(100% + 2px);max-width:190px;width:max-content;',
    'padding:8px 11px;border-radius:13px;background:var(--tb-menu-bg,#ffffff);color:var(--tb-ink,#152033);',
    'border:1px solid var(--tb-line,#e4e9f1);box-shadow:0 12px 28px -14px rgba(16,24,40,.45);',
    'font-size:12px;line-height:1.55;letter-spacing:.2px;pointer-events:none;opacity:0;',
    'transform:translateY(6px) scale(.96);transform-origin:bottom right;transition:opacity .2s ease,transform .24s ease}',
    '.bubble::after{content:"";position:absolute;right:22px;bottom:-6px;width:11px;height:11px;',
    'background:inherit;border-right:1px solid var(--tb-line,#e4e9f1);border-bottom:1px solid var(--tb-line,#e4e9f1);',
    'transform:rotate(45deg);border-bottom-right-radius:2px}',
    '.bubble.left{left:6px;right:auto;transform-origin:bottom left}',
    '.bubble.left::after{right:auto;left:22px}',
    '.bubble.below{top:calc(100% + 4px);bottom:auto;transform-origin:top right}',
    '.bubble.below.left{transform-origin:top left}',
    '.bubble.below::after{top:-6px;bottom:auto;border-right:0;border-bottom:0;',
    'border-left:1px solid var(--tb-line,#e4e9f1);border-top:1px solid var(--tb-line,#e4e9f1);',
    'transform:rotate(45deg);border-bottom-right-radius:0;border-top-left-radius:2px}',
    '.bubble.show{opacity:1;transform:translateY(0) scale(1)}',
    '.sleeping *{animation-play-state:paused!important}',
    '@keyframes fmFloat{0%,100%{transform:translate(var(--look-x,0px),var(--look-y,0px)) rotate(var(--tilt,0deg))}50%{transform:translate(var(--look-x,0px),calc(var(--look-y,0px) - 5px)) rotate(calc(var(--tilt,0deg) + .7deg))}}',
    '@keyframes fmPoke{0%{transform:translate(var(--look-x,0px),var(--look-y,0px)) rotate(var(--tilt,0deg))}',
    '25%{transform:translate(var(--look-x,0px),calc(var(--look-y,0px) - 13px)) rotate(-6deg) scale(1.04)}',
    '55%{transform:translate(var(--look-x,0px),calc(var(--look-y,0px) + 2px)) rotate(3deg) scale(.99)}',
    '78%{transform:translate(var(--look-x,0px),calc(var(--look-y,0px) - 4px)) rotate(-2deg)}',
    '100%{transform:translate(var(--look-x,0px),var(--look-y,0px)) rotate(var(--tilt,0deg))}}',
    '@media(max-width:700px){:host{width:82px;height:110px}.bubble{max-width:158px;font-size:11px;padding:7px 9px}}',
    '@media(prefers-reduced-motion:reduce){.pet{animation:none!important}}',
    '@media print{:host{display:none!important}}'
  ].join('');

  var POS_KEY = 'tb_fish_pos_v1';
  var IDLE_TURN_MS = 90000;
  var host = null, root = null, fish = null, bubble = null;
  var noteTimer = 0, pokeTimer = 0, idleTimer = 0;
  var view = 'front';
  var petImage = null;
  var reduced = false;
  var lastPoint = 0;
  var customPos = null;
  var drag = null;
  var clickTimer = 0, suppressTimer = 0, suppressClick = false;

  function build() {
    if (document.getElementById('tb-fish-mascot')) { return false; }
    host = document.createElement('div');
    host.id = 'tb-fish-mascot';
    host.setAttribute('aria-live', 'polite');
    (document.body || document.documentElement).appendChild(host);
    root = host.attachShadow({ mode: 'open' });
    root.innerHTML = '<style>' + CSS + '</style>' +
      '<button class="fish" type="button" draggable="false" aria-label="摸摸大肥鱼；拖动可以换位置，双击回到 AI 助手旁边">' +
      '<span class="bubble"></span>' + PET_HTML + '</button>';
    fish = root.querySelector('.fish');
    bubble = root.querySelector('.bubble');
    petImage = root.querySelector('.pet-img');
    return true;
  }

  // 拖动时换侧面图，长时间无操作时换背面图。
  function setView(name) {
    if (!fish || !VIEWS[name] || view === name) { return; }
    view = name;
    fish.setAttribute('data-view', name);
    if (petImage) { petImage.src = VIEWS[name]; }
  }

  // 长时间没互动就转身；鼠标一动、点一下或开拖都会转回来并重新计时。
  function wake() {
    if (view === 'back') { setView('front'); }
    clearTimeout(idleTimer);
    idleTimer = setTimeout(function () {
      if (!drag) { setView('back'); }
    }, IDLE_TURN_MS);
  }

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  // 气泡靠近窗口顶部时翻到鱼下面，靠近左侧时改为左对齐，避免拖到边上后被裁掉。
  function placeBubble() {
    if (!host || !bubble) { return; }
    var r = host.getBoundingClientRect();
    var vw = window.innerWidth || document.documentElement.clientWidth;
    var bw = bubble.offsetWidth || (vw <= 700 ? 158 : 190);
    var bh = bubble.offsetHeight || 44;
    var rightFits = r.right - 6 - bw >= 6;
    if (rightFits) {
      bubble.classList.remove('left');
    } else {
      var leftFits = r.left + 6 + bw <= vw - 6;
      bubble.classList.toggle('left', leftFits || r.left + r.width / 2 < vw / 2);
    }
    bubble.classList.toggle('below', r.top - 6 - bh < 6);
  }

  function say(text) {
    if (!bubble) { return; }
    bubble.textContent = text || NOTES[Math.floor(Math.random() * NOTES.length)];
    placeBubble();
    bubble.classList.add('show');
    clearTimeout(noteTimer);
    noteTimer = setTimeout(function () { bubble.classList.remove('show'); }, 2800);
  }

  function bounce() {
    if (!fish) { return; }
    fish.classList.add('poke');
    clearTimeout(pokeTimer);
    pokeTimer = setTimeout(function () { fish.classList.remove('poke'); }, 920);
  }

  function poke() {
    bounce();
    say();
  }

  function readStoredPos() {
    try {
      var raw = W.localStorage.getItem(POS_KEY);
      if (!raw) { return null; }
      var p = JSON.parse(raw);
      var x = Number(p && p.x);
      var y = Number(p && p.y);
      if (!isFinite(x) || !isFinite(y)) { return null; }
      return { x: clamp(x, 0, 1), y: clamp(y, 0, 1) };
    } catch (e) { return null; }
  }

  function savePos(x, y) {
    customPos = { x: clamp(x, 0, 1), y: clamp(y, 0, 1) };
    try { W.localStorage.setItem(POS_KEY, JSON.stringify(customPos)); } catch (e) { /* 隐身模式 */ }
  }

  // 鱼在 AI 入口下面一层；贴上去会被挡住抓不回来。落点如果和入口重叠，往左挪开。
  function avoidOrb(left, top, w, h, vw, vh, margin) {
    var orb = assistantOrb();
    if (!orb) { return { left: left, top: top }; }
    var pad = 8;
    var hit = left < orb.right + pad && left + w > orb.left - pad &&
              top < orb.bottom + pad && top + h > orb.top - pad;
    if (!hit) { return { left: left, top: top }; }
    var beside = orb.left - pad - w;
    if (beside >= margin) { return { left: beside, top: top }; }
    var above = orb.top - pad - h;
    if (above >= margin) { return { left: left, top: above }; }
    var rightSide = orb.right + pad;
    if (rightSide + w <= vw - margin) { return { left: rightSide, top: top }; }
    return { left: left, top: top };
  }

  // 记住的是鱼中心在视口中的比例，换尺寸 / 转屏后仍落在相近位置。
  function applyPos() {
    if (!host || !customPos) { return false; }
    var w = host.offsetWidth || 112;
    var h = host.offsetHeight || 150;
    var vw = W.innerWidth || document.documentElement.clientWidth;
    var vh = W.innerHeight || document.documentElement.clientHeight;
    var margin = 2;
    var left = clamp(customPos.x * vw - w / 2, margin, Math.max(margin, vw - w - margin));
    var top = clamp(customPos.y * vh - h / 2, margin, Math.max(margin, vh - h - margin));
    var fixed = avoidOrb(left, top, w, h, vw, vh, margin);
    left = fixed.left;
    top = fixed.top;
    host.style.left = Math.round(left) + 'px';
    host.style.top = Math.round(top) + 'px';
    host.style.right = 'auto';
    host.style.bottom = 'auto';
    host.setAttribute('data-custom', '');
    host.setAttribute('data-ready', '');
    return true;
  }

  function resetPos(silent, text) {
    clearTimeout(clickTimer);
    customPos = null;
    try { W.localStorage.removeItem(POS_KEY); } catch (e) { /* 忽略 */ }
    if (host) {
      host.style.left = '';
      host.style.top = '';
      host.removeAttribute('data-custom');
    }
    layout();
    if (!silent) {
      bounce();
      say(text || '回到 AI 助手旁边啦。');
    }
  }

  function onDragStart(e) {
    if (!host || !fish || drag) { return; }
    if (e.pointerType === 'mouse' && e.button !== 0) { return; }
    if (host.classList.contains('is-covered')) { return; }
    var r = host.getBoundingClientRect();
    if (!r.width || !r.height) { return; }
    drag = {
      id: e.pointerId,
      sx: e.clientX,
      sy: e.clientY,
      left: r.left,
      top: r.top,
      moved: false
    };
    clearTimeout(clickTimer);
    clearTimeout(suppressTimer);
    suppressClick = false;
    if (bubble) { bubble.classList.remove('show'); }
    try { fish.setPointerCapture(e.pointerId); } catch (err) { /* 老浏览器 */ }
  }

  function onDragMove(e) {
    if (!drag || e.pointerId !== drag.id) { return; }
    var dx = e.clientX - drag.sx;
    var dy = e.clientY - drag.sy;
    if (!drag.moved) {
      if (dx * dx + dy * dy < 16) { return; }
      drag.moved = true;
      host.classList.add('dragging');
      setView('side');
    }
    if (e.cancelable) { e.preventDefault(); }
    var w = host.offsetWidth || 112;
    var h = host.offsetHeight || 150;
    var vw = W.innerWidth || document.documentElement.clientWidth;
    var vh = W.innerHeight || document.documentElement.clientHeight;
    var margin = 2;
    var left = clamp(drag.left + dx, margin, Math.max(margin, vw - w - margin));
    var top = clamp(drag.top + dy, margin, Math.max(margin, vh - h - margin));
    host.style.left = Math.round(left) + 'px';
    host.style.top = Math.round(top) + 'px';
    host.style.right = 'auto';
    host.style.bottom = 'auto';
    host.setAttribute('data-custom', '');
  }

  function onDragEnd(e) {
    if (!drag || (e && e.pointerId !== drag.id)) { return; }
    var moved = drag.moved;
    var id = drag.id;
    drag = null;
    try { fish.releasePointerCapture(id); } catch (err) { /* 已释放 */ }
    if (!host) { return; }
    if (!moved) {
      host.classList.remove('dragging');
      wake();
      return;
    }
    var vw = W.innerWidth || document.documentElement.clientWidth;
    var vh = W.innerHeight || document.documentElement.clientHeight;
    var r = host.getBoundingClientRect();
    var fixed = avoidOrb(r.left, r.top, r.width, r.height, vw, vh, 2);
    if (Math.abs(fixed.left - r.left) > 0.5 || Math.abs(fixed.top - r.top) > 0.5) {
      host.style.left = Math.round(fixed.left) + 'px';
      host.style.top = Math.round(fixed.top) + 'px';
      r = host.getBoundingClientRect();
    }
    host.classList.remove('dragging');
    setView('front');
    wake();
    savePos((r.left + r.width / 2) / Math.max(1, vw), (r.top + r.height / 2) / Math.max(1, vh));
    // 拖完松手时浏览器仍可能补一个 click，别让它误触“摸鱼”。
    suppressClick = true;
    clearTimeout(suppressTimer);
    // mouseup 后浏览器会立刻补一个 click；只挡这一下，别把用户紧接着的点击吃掉。
    suppressTimer = setTimeout(function () { suppressClick = false; }, 120);
  }

  function assistantOrb() {
    var h = document.getElementById('tbqa-host');
    if (!h || !h.shadowRoot) { return null; }
    var orb = h.shadowRoot.getElementById('orb');
    if (!orb) { return null; }
    var st = window.getComputedStyle(orb);
    if (st.display === 'none' || st.visibility === 'hidden') { return null; }
    var r = orb.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) { return null; }
    return r;
  }

  function layout() {
    if (!host) { return; }
    if (applyPos()) { return; }
    var narrow = window.innerWidth <= 700;
    var orb = assistantOrb();
    if (orb) {
      // 贴在 AI 入口左侧，底部对齐
      host.style.right = Math.max(6, Math.round(window.innerWidth - orb.left + (narrow ? 8 : 12))) + 'px';
      host.style.bottom = Math.max(6, Math.round(window.innerHeight - orb.bottom - (narrow ? 0 : 2))) + 'px';
    } else {
      host.style.right = (narrow ? 16 : 24) + 'px';
      host.style.bottom = (narrow ? 14 : 20) + 'px';
    }
    host.setAttribute('data-ready', '');
  }

  function cover(open) {
    if (host) { host.classList.toggle('is-covered', !!open); }
  }

  function bind() {
    fish.addEventListener('dragstart', function (e) { e.preventDefault(); });
    // 拖着鱼经过链接 / 图片时，浏览器仍可能起原生拖拽并打断 pointer capture。
    document.addEventListener('dragstart', function (e) {
      if (drag) { e.preventDefault(); }
    }, true);
    fish.addEventListener('pointerdown', onDragStart);
    document.addEventListener('pointermove', onDragMove, { passive: false });
    document.addEventListener('pointerup', onDragEnd);
    document.addEventListener('pointercancel', onDragEnd);

    fish.addEventListener('click', function (e) {
      wake();
      if (suppressClick) {
        suppressClick = false;
        clearTimeout(suppressTimer);
        return;
      }
      // 等一小会儿，确认不是双击；双击用来回到默认停靠位置。
      if (e.detail > 1) { return; }
      clearTimeout(clickTimer);
      clickTimer = setTimeout(poke, 220);
    });

    fish.addEventListener('dblclick', function (e) {
      e.preventDefault();
      wake();
      clearTimeout(clickTimer);
      var wasCustom = !!customPos;
      resetPos(true);
      bounce();
      say(wasCustom ? '回到老位置啦。' : '我一直住在这里呀。');
    });

    // 转身后，鼠标随便动一下就转回来；一秒最多判断一次，避免高频定时器。
    var lastWake = 0;
    document.addEventListener('pointermove', function (e) {
      if (e.pointerType === 'touch' || Date.now() - lastWake < 1000) { return; }
      lastWake = Date.now();
      wake();
    }, { passive: true });

    document.addEventListener('pointermove', function (e) {
      if (drag || reduced || e.pointerType === 'touch' || Date.now() - lastPoint < 55) { return; }
      lastPoint = Date.now();
      var r = fish.getBoundingClientRect();
      if (!r.width) { return; }
      var dx = e.clientX - (r.left + r.width * 0.46);
      var dy = e.clientY - (r.top + r.height * 0.5);
      var len = Math.sqrt(dx * dx + dy * dy) || 1;
      var reach = Math.min(1, len / 260);
      var lx = dx / len * 3.2 * reach;
      var ly = dy / len * 2.4 * reach;
      fish.style.setProperty('--look-x', lx.toFixed(2) + 'px');
      fish.style.setProperty('--look-y', ly.toFixed(2) + 'px');
      fish.style.setProperty('--tilt', (dx / 900).toFixed(2) + 'deg');
    }, { passive: true });

    document.addEventListener('mouseleave', function () {
      if (!fish) { return; }
      fish.style.setProperty('--look-x', '0px');
      fish.style.setProperty('--look-y', '0px');
      fish.style.setProperty('--tilt', '0deg');
    });

    document.addEventListener('tb-assistant-panel', function (e) {
      var open = !!(e && e.detail && e.detail.open);
      cover(open);
      if (bubble) { bubble.classList.remove('show'); }
      if (!open) { wake(); layout(); }
    });
    document.addEventListener('tb-focus-panel', function (e) {
      cover(e && e.detail && e.detail.open);
    });

    window.addEventListener('resize', layout);
    window.addEventListener('orientationchange', layout);
    document.addEventListener('visibilitychange', function () {
      if (host) { host.classList.toggle('sleeping', document.hidden); }
    });

    // AI 入口会延迟两次重新定位，鱼跟着对齐
    setTimeout(layout, 700);
    setTimeout(layout, 1800);
  }

  function init() {
    if (W.TB_NO_FISH) { return; }
    if (document.body && document.body.getAttribute('data-fish-mascot') === 'off') { return; }
    reduced = !!(W.matchMedia && W.matchMedia('(prefers-reduced-motion: reduce)').matches);
    if (!build()) { return; }
    customPos = readStoredPos();
    layout();
    bind();
    wake();

    W.TBFishMascot = {
      say: say,
      poke: poke,
      reset: function () { resetPos(false); },
      hide: function () { if (host) { host.style.display = 'none'; } },
      show: function () { if (host) { host.style.display = 'block'; layout(); } }
    };
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
