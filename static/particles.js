/* ==========================================================================
   全站动态背景：粒子星链
   --------------------------------------------------------------------------
   一个铺满视口的 canvas，画在页面内容背后（z-index:-1），所以在任何一个
   工具页都能看到，切主题、切页面都不用管。效果由四层叠出来：

     1) 星云光斑   缓慢游走的大色块，负责氛围
     2) 星链网络   粒子之间按距离连线，鼠标靠近会被推开并点亮连线
     3) 微粒        带光晕的小点，越靠近鼠标越亮（只提亮，不额外画光圈）
     4) 流星        每隔十几秒划过一颗

   颜色跟着主题走（浅色用深一点的颜色，深色用亮色 + lighter 叠加）。
   尊重 prefers-reduced-motion：这种情况只静态画一帧，不跑动画。
   标签页切到后台会自动暂停，不白白耗电。
   ========================================================================== */
(function () {
  'use strict';
  if (window.TBFx) { return; }

  var reduced = false;
  try { reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) {}

  // 每个主题一套配色：[r,g,b]
  var PAL = {
    light: {
      tints: [[64, 78, 160], [10, 132, 110], [124, 80, 235]],
      orbs: [[109, 124, 255, .20], [45, 212, 191, .15], [167, 139, 250, .16]],
      dot: .74, line: .52, comp: 'source-over', starAlpha: .55
    },
    dark: {
      tints: [[156, 176, 255], [60, 224, 204], [176, 150, 252]],
      orbs: [[109, 124, 255, .34], [45, 212, 191, .23], [167, 139, 250, .26]],
      dot: .92, line: .56, comp: 'lighter', starAlpha: .95
    }
  };

  var LINK_DIST = 144;      // 连线距离上限
  var PUSH_DIST = 118;      // 鼠标推开粒子的半径
  var MOUSE_GLOW = 190;     // 鼠标影响半径：范围内的连线和粒子会稍微变亮

  var canvas = null, ctx = null;
  var W = 0, H = 0, dpr = 1;
  var parts = [], orbs = [], stars = [];
  var mouse = { x: -9999, y: -9999, on: false };
  var rafId = 0, prev = 0, running = false, t0 = 0;
  var nextStar = 6000;

  function pal() {
    return document.documentElement.getAttribute('data-theme') === 'dark' ? PAL.dark : PAL.light;
  }
  function rgba(c, a) {
    return 'rgba(' + c[0] + ',' + c[1] + ',' + c[2] + ',' + a + ')';
  }
  function rand(a, b) { return a + Math.random() * (b - a); }

  function makeCanvas() {
    if (canvas || !document.body) { return; }
    canvas = document.createElement('canvas');
    canvas.id = 'tb-fx';
    canvas.setAttribute('aria-hidden', 'true');
    document.body.insertBefore(canvas, document.body.firstChild);
    ctx = canvas.getContext('2d', { alpha: true });
    resize();
    seed();
    if (reduced) { render(performance.now()); return; }
    t0 = performance.now();
    prev = t0;
    start();
  }

  function resize() {
    if (!canvas) { return; }
    dpr = Math.min(2, window.devicePixelRatio || 1);
    W = Math.max(1, window.innerWidth);
    H = Math.max(1, window.innerHeight);
    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(H * dpr);
    canvas.style.width = W + 'px';
    canvas.style.height = H + 'px';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    buildOrbs();
    fitCount();
  }

  function buildOrbs() {
    var base = Math.max(W, H);
    orbs = [
      { r: base * .42, x: .16, y: .10, ax: .16, ay: .10, sp: .000060, ph: rand(0, 6.28), c: 0 },
      { r: base * .38, x: .90, y: .06, ax: .12, ay: .12, sp: .000072, ph: rand(0, 6.28), c: 1 },
      { r: base * .34, x: .55, y: .96, ax: .20, ay: .08, sp: .000055, ph: rand(0, 6.28), c: 2 },
      { r: base * .26, x: .82, y: .72, ax: .14, ay: .14, sp: .000085, ph: rand(0, 6.28), c: 2 }
    ];
  }

  function targetCount() {
    var n = Math.round((W * H) / 12500);
    if (W < 520) { n = Math.round(n * .72); }
    return Math.max(32, Math.min(140, n));
  }

  function spawn() {
    var p = pal();
    return {
      x: rand(0, W), y: rand(0, H),
      ang: rand(0, 6.2832),          // 当前漂移方向
      speed: rand(.004, .011),       // px/ms -> 每秒 4~11 像素，很慢
      turn: rand(.000035, .00009),   // 方向缓慢转弯，转一圈约 70~180 秒
      vx: 0, vy: 0,
      r: rand(.7, 1.9),
      tint: (Math.random() * p.tints.length) | 0,
      ph: rand(0, 6.28),
      sp: rand(.00022, .0006)        // 闪动频率
    };
  }

  function fitCount() {
    var n = targetCount();
    while (parts.length < n) { parts.push(spawn()); }
    if (parts.length > n) { parts.length = n; }
  }

  function seed() {
    parts.length = 0;
    fitCount();
    stars.length = 0;
  }

  function update(dt, now) {
    var i, p, mdx, mdy, d2, d;
    for (i = 0; i < parts.length; i++) {
      p = parts[i];
      // 速度朝「当前方向 * 固定速度」缓动，方向再慢慢转：
      // 这样每个粒子的速度上限就是自己的 speed（px/ms），不会越漂越快
      p.ang += p.turn * dt;
      var tvx = Math.cos(p.ang) * p.speed;
      var tvy = Math.sin(p.ang) * p.speed;
      var ease = Math.min(1, 0.03 * dt / 16.7);
      p.vx += (tvx - p.vx) * ease;
      p.vy += (tvy - p.vy) * ease;
      p.x += p.vx * dt; p.y += p.vy * dt;

      if (mouse.on) {
        mdx = p.x - mouse.x; mdy = p.y - mouse.y;
        d2 = mdx * mdx + mdy * mdy;
        if (d2 < PUSH_DIST * PUSH_DIST && d2 > 1) {
          d = Math.sqrt(d2);
          p.x += (mdx / d) * (1 - d / PUSH_DIST) * 1.6 * dt * .06;
          p.y += (mdy / d) * (1 - d / PUSH_DIST) * 1.6 * dt * .06;
        }
      }

      if (p.x < -20) { p.x = W + 20; }
      if (p.x > W + 20) { p.x = -20; }
      if (p.y < -20) { p.y = H + 20; }
      if (p.y > H + 20) { p.y = -20; }
    }

    // 流星
    for (i = stars.length - 1; i >= 0; i--) {
      var s = stars[i];
      s.x += s.vx * dt; s.y += s.vy * dt; s.life -= dt;
      if (s.life <= 0 || s.x < -300 || s.y > H + 300) { stars.splice(i, 1); }
    }
    nextStar -= dt;
    if (nextStar <= 0) {
      nextStar = rand(9000, 17000);
      var ang = rand(Math.PI * .12, Math.PI * .34);
      var sp = rand(.28, .46);
      stars.push({
        x: rand(-W * .2, W * .7), y: rand(-H * .1, H * .35),
        vx: Math.cos(ang) * sp * 1.6, vy: Math.sin(ang) * sp * 1.6,
        life: rand(900, 1500), max: 1500
      });
    }
  }

  function render(now) {
    if (!ctx) { return; }
    var p = pal();
    var dark = (document.documentElement.getAttribute('data-theme') === 'dark');
    var i, j, a, b, dx, dy, d, alpha, o, g;

    ctx.clearRect(0, 0, W, H);
    ctx.globalCompositeOperation = p.comp;

    // 1) 星云光斑
    for (i = 0; i < orbs.length; i++) {
      o = orbs[i];
      var oc = p.orbs[o.c] || p.orbs[0];
      var cx = W * o.x + Math.sin(now * o.sp + o.ph) * W * o.ax;
      var cy = H * o.y + Math.cos(now * o.sp * 1.3 + o.ph) * H * o.ay;
      var grd = ctx.createRadialGradient(cx, cy, 0, cx, cy, o.r);
      grd.addColorStop(0, rgba(oc, oc[3]));
      grd.addColorStop(.55, rgba(oc, oc[3] * .38));
      grd.addColorStop(1, rgba(oc, 0));
      ctx.fillStyle = grd;
      ctx.beginPath();
      ctx.arc(cx, cy, o.r, 0, 6.2832);
      ctx.fill();
    }

    // 2) 星链连线
    ctx.lineWidth = 1;
    for (i = 0; i < parts.length; i++) {
      a = parts[i];
      for (j = i + 1; j < parts.length; j++) {
        b = parts[j];
        dx = a.x - b.x; dy = a.y - b.y;
        if (dx > LINK_DIST || dx < -LINK_DIST || dy > LINK_DIST || dy < -LINK_DIST) { continue; }
        d = Math.sqrt(dx * dx + dy * dy);
        if (d >= LINK_DIST) { continue; }
        alpha = (1 - d / LINK_DIST) * p.line * .30;
        if (mouse.on) {
          var mx = (a.x + b.x) * .5 - mouse.x;
          var my = (a.y + b.y) * .5 - mouse.y;
          var md = Math.sqrt(mx * mx + my * my);
          if (md < MOUSE_GLOW) { alpha += (1 - md / MOUSE_GLOW) * 0.55; }
        }
        ctx.strokeStyle = rgba(p.tints[a.tint], Math.min(.85, alpha));
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
      }
    }

    // 3) 微粒（带光晕）
    for (i = 0; i < parts.length; i++) {
      a = parts[i];
      var tint = p.tints[a.tint];
      var tw = .72 + Math.sin(now * a.sp * 7 + a.ph) * .28;
      var near = 1;
      if (mouse.on) {
        dx = a.x - mouse.x; dy = a.y - mouse.y;
        d = Math.sqrt(dx * dx + dy * dy);
        if (d < MOUSE_GLOW) { near = 1 + (1 - d / MOUSE_GLOW) * 0.8; }
      }
      g = ctx.createRadialGradient(a.x, a.y, 0, a.x, a.y, a.r * 5.5 * near);
      g.addColorStop(0, rgba(tint, p.dot * tw * Math.min(1, near)));
      g.addColorStop(.35, rgba(tint, Math.min(.85, p.dot * .30 * tw * near)));
      g.addColorStop(1, rgba(tint, 0));
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(a.x, a.y, a.r * 5.5 * near, 0, 6.2832);
      ctx.fill();

      if (dark) {
        ctx.fillStyle = rgba([255, 255, 255], .55 * tw);
        ctx.beginPath();
        ctx.arc(a.x, a.y, a.r * .78, 0, 6.2832);
        ctx.fill();
      }
    }

    // 4) 流星
    ctx.lineWidth = 1.6;
    ctx.lineCap = 'round';
    for (i = 0; i < stars.length; i++) {
      var st = stars[i];
      var k = Math.max(0, st.life / st.max);
      var tail = 120 * k + 40;
      var len = Math.sqrt(st.vx * st.vx + st.vy * st.vy) || 1;
      var sg = ctx.createLinearGradient(st.x, st.y, st.x - st.vx / len * tail, st.y - st.vy / len * tail);
      sg.addColorStop(0, rgba(p.tints[0], p.starAlpha * k));
      sg.addColorStop(.35, rgba(p.tints[2], p.starAlpha * .45 * k));
      sg.addColorStop(1, rgba(p.tints[0], 0));
      ctx.strokeStyle = sg;
      ctx.beginPath();
      ctx.moveTo(st.x, st.y);
      ctx.lineTo(st.x - st.vx / len * tail, st.y - st.vy / len * tail);
      ctx.stroke();
    }

    ctx.globalCompositeOperation = 'source-over';
  }

  function loop(now) {
    if (!running) { return; }
    var dt = Math.min(48, now - prev);
    prev = now;
    update(dt, now);
    render(now);
    rafId = requestAnimationFrame(loop);
  }

  function start() {
    if (running || reduced) { return; }
    running = true;
    prev = performance.now();
    rafId = requestAnimationFrame(loop);
  }
  function stop() {
    running = false;
    if (rafId) { cancelAnimationFrame(rafId); rafId = 0; }
  }

  var rzTimer = 0;
  function onResize() {
    if (rzTimer) { clearTimeout(rzTimer); }
    rzTimer = setTimeout(function () {
      resize();
      if (reduced) { render(performance.now()); }
    }, 180);
  }

  window.addEventListener('resize', onResize);
  window.addEventListener('orientationchange', onResize);

  window.addEventListener('mousemove', function (e) {
    mouse.x = e.clientX; mouse.y = e.clientY; mouse.on = true;
  }, { passive: true });
  window.addEventListener('mouseleave', function () { mouse.on = false; });
  window.addEventListener('touchstart', function (e) {
    if (e.touches && e.touches[0]) {
      mouse.x = e.touches[0].clientX; mouse.y = e.touches[0].clientY; mouse.on = true;
    }
  }, { passive: true });
  window.addEventListener('touchmove', function (e) {
    if (e.touches && e.touches[0]) {
      mouse.x = e.touches[0].clientX; mouse.y = e.touches[0].clientY; mouse.on = true;
    }
  }, { passive: true });

  document.addEventListener('visibilitychange', function () {
    if (document.hidden) { stop(); } else { start(); }
  });
  window.addEventListener('tbthemechange', function () {
    if (reduced) { render(performance.now()); }
  });

  function boot() { makeCanvas(); }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

  window.TBFx = {
    repaint: function () { render(performance.now()); },
    canvas: function () { return canvas; },
    particles: function () { return parts; }
  };
})();
