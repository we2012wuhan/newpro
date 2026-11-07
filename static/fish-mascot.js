/* 全站大肥鱼精灵
 * ------------------------------------------------------------
 * 右下角常驻的一只胖鱼：呼吸、摆尾、眨眼、吐泡泡，眼神会跟着鼠标走。
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

  var SVG = [
    '<svg viewBox="0 0 220 170" aria-hidden="true" draggable="false">',
    '<defs>',
    '<linearGradient id="fmBody" x1="0" y1="0" x2="1" y2="1">',
    '<stop offset="0" stop-color="#a8f3ff"/><stop offset=".28" stop-color="#62c4ff"/>',
    '<stop offset=".62" stop-color="#4b83ff"/><stop offset="1" stop-color="#7458ff"/>',
    '</linearGradient>',
    '<radialGradient id="fmBelly" cx=".42" cy=".35" r=".8">',
    '<stop offset="0" stop-color="#ffffff"/><stop offset=".58" stop-color="#eafaff"/>',
    '<stop offset="1" stop-color="#bde8ff"/></radialGradient>',
    '<linearGradient id="fmTail" x1="0" y1="0" x2="1" y2="1">',
    '<stop offset="0" stop-color="#7de8ff"/><stop offset=".45" stop-color="#4d8dff"/>',
    '<stop offset="1" stop-color="#684dff"/></linearGradient>',
    '<linearGradient id="fmFin" x1="0" y1="0" x2="1" y2="1">',
    '<stop offset="0" stop-color="#a8eeff"/><stop offset="1" stop-color="#5a8dff"/></linearGradient>',
    '<radialGradient id="fmGlow">',
    '<stop offset="0" stop-color="#69d8ff" stop-opacity=".34"/>',
    '<stop offset=".55" stop-color="#6f7dff" stop-opacity=".15"/>',
    '<stop offset="1" stop-color="#6f7dff" stop-opacity="0"/></radialGradient>',
    '</defs>',
    '<g class="glow"><ellipse cx="104" cy="98" rx="98" ry="66" fill="url(#fmGlow)"/></g>',
    '<g class="bubbles">',
    '<circle class="bub b1" cx="35" cy="47" r="3.3"/>',
    '<circle class="bub b2" cx="18" cy="66" r="2.3"/>',
    '<circle class="bub b3" cx="45" cy="28" r="1.9"/>',
    '</g>',
    '<g class="float"><g class="tilt">',
    '<g class="tail">',
    '<path d="M148 80 C164 76 174 60 178 40 C198 46 212 66 208 88 C205 105 191 116 176 118 C187 125 192 137 188 150 C179 157 165 154 157 145 C148 134 145 121 146 109 C139 100 141 87 148 80 Z" fill="url(#fmTail)" stroke="#2f58b5" stroke-opacity=".3" stroke-width="2"/>',
    '<path d="M162 92 C174 91 185 84 195 72" fill="none" stroke="#e8fbff" stroke-opacity=".58" stroke-width="3" stroke-linecap="round"/>',
    '<path d="M160 111 C170 116 178 126 181 138" fill="none" stroke="#bfeaff" stroke-opacity=".34" stroke-width="2.4" stroke-linecap="round"/>',
    '</g>',
    '<path class="dorsal" d="M72 40 C79 21 98 13 114 21 C101 24 89 31 82 45 Z" fill="url(#fmFin)" stroke="#3566c6" stroke-opacity=".3" stroke-width="1.8"/>',
    '<path d="M22 90 C22 58 48 33 90 33 C133 33 161 52 164 81 C167 109 143 137 99 137 C56 137 24 121 22 90 Z" fill="url(#fmBody)" stroke="#2c57b0" stroke-opacity=".28" stroke-width="2"/>',
    '<path d="M42 99 C56 125 90 141 125 132 C143 127 155 115 160 102 C157 126 132 142 99 142 C65 141 47 124 42 99 Z" fill="url(#fmBelly)" opacity=".93"/>',
    '<path d="M30 76 C36 49 62 34 93 33" fill="none" stroke="#ddFAFF" stroke-opacity=".45" stroke-width="3" stroke-linecap="round"/>',
    '<path d="M42 60 C55 43 80 34 105 35 C82 42 62 52 51 70 Z" fill="#ffffff" opacity=".25"/>',
    '<g class="scales" fill="none" stroke="#e9fbff" stroke-opacity=".22" stroke-width="2" stroke-linecap="round">',
    '<path d="M109 58 q8 8 0 16"/><path d="M121 57 q8 8 0 16"/>',
    '<path d="M133 59 q7 7 0 14"/><path d="M115 75 q8 8 0 16"/>',
    '<path d="M127 74 q7 7 0 14"/>',
    '</g>',
    '<path class="pectoral" d="M75 102 C64 106 57 117 59 128 C68 127 82 117 90 106 Z" fill="url(#fmFin)" stroke="#3566c6" stroke-opacity=".3" stroke-width="1.8"/>',
    '<path class="ventral" d="M107 133 C111 143 118 150 127 152 C122 143 120 136 118 130 Z" fill="url(#fmFin)" stroke="#3566c6" stroke-opacity=".26" stroke-width="1.6"/>',
    '<g class="face">',
    '<ellipse cx="54" cy="98" rx="8" ry="4.2" fill="#ff9fc8" opacity=".34"/>',
    '<ellipse cx="108" cy="93" rx="7" ry="3.6" fill="#ff9fc8" opacity=".28"/>',
    '<g class="eyes">',
    '<g class="eye eye-l"><ellipse cx="68" cy="79" rx="8.6" ry="10.4" fill="#12254a"/><ellipse cx="65.4" cy="75.6" rx="2.9" ry="3.6" fill="#ffffff"/><circle cx="71" cy="83" r="1.5" fill="#7ce8ff"/></g>',
    '<g class="eye eye-r"><ellipse cx="98" cy="76" rx="8.1" ry="9.8" fill="#12254a"/><ellipse cx="95.6" cy="72.8" rx="2.7" ry="3.4" fill="#ffffff"/><circle cx="100.6" cy="80" r="1.4" fill="#7ce8ff"/></g>',
    '</g>',
    '<path d="M59 65 Q67 61 76 64" fill="none" stroke="#214a91" stroke-opacity=".5" stroke-width="2" stroke-linecap="round"/>',
    '<path d="M91 62 Q98 59 106 62" fill="none" stroke="#214a91" stroke-opacity=".42" stroke-width="2" stroke-linecap="round"/>',
    '<path d="M64 96 Q75 104 86 96" fill="none" stroke="#1c3c78" stroke-width="2.6" stroke-linecap="round"/>',
    '<circle cx="52" cy="88" r="1" fill="#29549c" opacity=".45"/><circle cx="58" cy="86" r=".8" fill="#29549c" opacity=".35"/>',
    '</g>',
    '</g>',
    '<g class="sparkles">',
    '<path class="sp sp1" d="M183 27 l2.2 6 6 2.2 -6 2.2 -2.2 6 -2.2 -6 -6 -2.2 6 -2.2Z" fill="#c9f7ff"/>',
    '<path class="sp sp2" d="M204 123 l1.7 4.6 4.6 1.7 -4.6 1.7 -1.7 4.6 -1.7 -4.6 -4.6 -1.7 4.6 -1.7Z" fill="#bde9ff"/>',
    '</g>',
    '</g></svg>'
  ].join('');

  var CSS = [
    ':host{all:initial;position:fixed;right:24px;bottom:20px;z-index:2147482000;',
    'width:112px;height:86px;display:block;pointer-events:none;opacity:0;',
    'transition:opacity .45s ease,right .18s ease,bottom .18s ease,left .18s ease,top .18s ease;',
    'font-family:-apple-system,"PingFang SC","Microsoft YaHei",system-ui,sans-serif}',
    ':host([data-ready]){opacity:1}',
    ':host(.dragging){transition:none}',
    ':host(.is-covered){opacity:0;pointer-events:none}',
    ':host(.is-covered) .fish{pointer-events:none}',
    '.fish{display:block;width:100%;height:100%;padding:0;border:0;background:none;',
    'pointer-events:auto;cursor:grab;position:relative;border-radius:22px;touch-action:none;',
    'user-select:none;-webkit-user-select:none;-webkit-user-drag:none;',
    'filter:drop-shadow(0 12px 18px rgba(54,92,210,.32));-webkit-tap-highlight-color:transparent}',
    '.dragging .fish{cursor:grabbing;filter:drop-shadow(0 18px 26px rgba(54,92,210,.46))}',
    '.dragging .float{animation-play-state:paused}',
    '.dragging .bubble{opacity:0!important}',
    '.fish:focus-visible{outline:2px solid #6d7cff;outline-offset:5px}',
    '.fish svg{display:block;width:100%;height:100%;overflow:visible}',
    '.glow{animation:fmGlow 5.2s ease-in-out infinite}',
    '.float{animation:fmBob 5.6s ease-in-out infinite;transform-origin:108px 108px}',
    '.tilt{transform:rotate(var(--tilt,0deg));transform-origin:106px 104px;transition:transform .35s ease}',
    '.tail{transform-origin:154px 94px;animation:fmWag 1.9s ease-in-out infinite}',
    '.dorsal{transform-origin:92px 43px;animation:fmDorsal 4.8s ease-in-out infinite}',
    '.pectoral{transform-origin:81px 107px;animation:fmFin 3.7s ease-in-out infinite}',
    '.ventral{transform-origin:115px 138px;animation:fmFin 4.4s ease-in-out infinite reverse}',
    '.eyes{transform:translate(var(--look-x,0px),var(--look-y,0px));transition:transform .28s ease}',
    '.eye{transform-box:fill-box;transform-origin:center;animation:fmBlink 7.4s infinite}',
    '.eye-r{animation-delay:.08s}',
    '.bub{fill:#8fe8ff;opacity:0;transform-box:fill-box;transform-origin:center;animation:fmBubble 5.8s ease-in-out infinite}',
    '.b2{animation-delay:1.7s}.b3{animation-delay:3.1s}',
    '.sp{transform-box:fill-box;transform-origin:center;animation:fmTwinkle 4.2s ease-in-out infinite}',
    '.sp2{animation-delay:1.9s}',
    '.poke .float{animation:fmPoke .9s cubic-bezier(.2,.8,.3,1)}',
    '.poke .tail{animation:fmWagFast .22s ease-in-out 4 alternate}',
    '.bubble{position:absolute;right:6px;bottom:calc(100% + 2px);max-width:190px;width:max-content;',
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
    '@keyframes fmBob{0%,100%{transform:translateY(1px) rotate(-.8deg)}50%{transform:translateY(-7px) rotate(.8deg)}}',
    '@keyframes fmWag{0%,100%{transform:rotate(-7deg)}50%{transform:rotate(8deg)}}',
    '@keyframes fmWagFast{0%{transform:rotate(-9deg)}100%{transform:rotate(11deg)}}',
    '@keyframes fmDorsal{0%,100%{transform:rotate(-2deg)}50%{transform:rotate(3deg)}}',
    '@keyframes fmFin{0%,100%{transform:rotate(-4deg)}50%{transform:rotate(6deg)}}',
    '@keyframes fmBlink{0%,90%,96%,100%{transform:scaleY(1)}93%{transform:scaleY(.08)}}',
    '@keyframes fmBubble{0%{opacity:0;transform:translate(0,5px) scale(.65)}22%{opacity:.75}',
    '75%{opacity:.4}100%{opacity:0;transform:translate(-7px,-13px) scale(1.15)}}',
    '@keyframes fmTwinkle{0%,100%{opacity:.25;transform:scale(.75)}50%{opacity:.95;transform:scale(1.12)}}',
    '@keyframes fmGlow{0%,100%{opacity:.55;transform:scale(.97)}50%{opacity:1;transform:scale(1.03)}}',
    '@keyframes fmPoke{0%{transform:translateY(0) rotate(0)}25%{transform:translateY(-13px) rotate(-7deg) scale(1.06)}',
    '55%{transform:translateY(2px) rotate(4deg) scale(.99)}78%{transform:translateY(-4px) rotate(-2deg)}100%{transform:translateY(1px) rotate(0)}}',
    '@media(max-width:700px){:host{width:82px;height:63px}.bubble{max-width:158px;font-size:11px;padding:7px 9px}}',
    '@media(prefers-reduced-motion:reduce){',
    '.glow,.float,.tail,.dorsal,.pectoral,.ventral,.eye,.bub,.sp{animation:none!important}',
    '.bub{opacity:.4}.sp{opacity:.6}}',
    '@media print{:host{display:none!important}}'
  ].join('');

  var POS_KEY = 'tb_fish_pos_v1';
  var host = null, root = null, fish = null, bubble = null, noteTimer = 0, pokeTimer = 0;
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
      '<span class="bubble"></span>' + SVG + '</button>';
    fish = root.querySelector('.fish');
    bubble = root.querySelector('.bubble');
    return true;
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
    var h = host.offsetHeight || 86;
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
    }
    if (e.cancelable) { e.preventDefault(); }
    var w = host.offsetWidth || 112;
    var h = host.offsetHeight || 86;
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
      clearTimeout(clickTimer);
      var wasCustom = !!customPos;
      resetPos(true);
      bounce();
      say(wasCustom ? '回到老位置啦。' : '我一直住在这里呀。');
    });

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
      if (!open) { layout(); }
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
