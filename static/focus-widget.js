/* ==========================================================================
   专注台 · 全局悬浮旋钮
   --------------------------------------------------------------------------
   放在工具箱的每个页面右下角。旋钮本身就是入口：
     · 空闲 —— 🍅，点开可以挑目标动作、选时长、开始；
     · 计时 —— 圆环收窄 + 剩余时间，切到别的页面也接着走；
     · 结束 —— ✓，提示保存，保存时把实际分钟写回目标拆解器；
     · 休息 —— ☕ + 倒计时；
     · 浮窗 —— Chrome / Edge 桌面版可把倒计时放进置顶小窗，浏览器最小化也可见。
   进行中的那段沿用 focus.js 的 localStorage 键（tb_focus_active_v1），
   所以在这边开始、去 /focus 页保存，或者反过来，都是同一段。
   ========================================================================== */
(function () {
  'use strict';

  var W = window;
  if (W.TBFocusWidget) { return; }

  // 暂时隐藏右下角番茄钟；改回 false 就恢复。
  // 专注台 /focus、目标拆解的回写和本地计时都不受影响。
  var HIDDEN = true;
  if (HIDDEN) { return; }

  // 登录页没有意义；专注台自己就是完整页面，不再叠一个；iframe 里的工具页也跳过
  var path = location.pathname;
  if (path === '/login' || path === '/focus') { return; }
  if (W.self !== W.top) { return; }
  try {
    if (new URLSearchParams(location.search).get('embed') === '1') { return; }
  } catch (e) { /* 老浏览器：继续 */ }

  var ACTIVE_KEY = 'tb_focus_active_v1';
  var BREAK_KEY = 'tb_focus_break_v1';
  var PREFS_KEY = 'tb_focus_prefs_v1';
  var RING_LEN = 182.21;
  var MIN_PRESETS = [15, 25, 45, 60];
  var BREAK_PRESETS = [0, 3, 5, 10];

  var S = {
    run: null,
    break: null,
    breakOver: false,
    after: null,        // 刚保存完、还没开始休息时的信息
    sources: [],
    sourcesAt: 0,
    sourcesLoaded: false,
    source: null,       // 准备阶段关联的目标动作
    pickedMin: 25,
    breakMin: 5,
    notify: false,
    view: 'setup',
    open: false,
    busy: false
  };

  var host = null;
  var shadow = null;
  var el = {};          // 常用节点缓存

  /* ---------------- 小工具 ---------------- */
  function $(id) { return shadow ? shadow.getElementById(id) : null; }
  function clean(s, n) {
    var t = String(s == null ? '' : s).replace(/\s+/g, ' ').replace(/^\s+|\s+$/g, '');
    return n && t.length > n ? t.slice(0, n) : t;
  }
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function clamp(n, lo, hi) { return Math.max(lo, Math.min(hi, n)); }
  function uid() { return Date.now().toString(36) + Math.random().toString(36).slice(2, 10); }
  function readJSON(key) {
    try { return JSON.parse(localStorage.getItem(key) || 'null'); }
    catch (e) { return null; }
  }
  function writeJSON(key, val) {
    try {
      if (val == null) { localStorage.removeItem(key); }
      else { localStorage.setItem(key, JSON.stringify(val)); }
    } catch (e) { /* 隐私模式下写不进去，页面还能正常计时 */ }
  }
  function toSql(ms) {
    var d = new Date(ms || Date.now());
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) + ' ' +
      pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
  }
  function hhmm(ms) {
    var d = new Date(ms || Date.now());
    return pad(d.getHours()) + ':' + pad(d.getMinutes());
  }
  function fmt(ms) {
    var sec = Math.max(0, Math.ceil(ms / 1000));
    return pad(Math.floor(sec / 60)) + ':' + pad(sec % 60);
  }
  function finePointer() {
    try { return W.matchMedia('(pointer:fine)').matches; }
    catch (e) { return true; }
  }

  /* ---------------- 本地状态 ---------------- */
  function saveActive() { writeJSON(ACTIVE_KEY, S.run); }
  function clearActive() { writeJSON(ACTIVE_KEY, null); S.run = null; }
  function clearBreak() {
    writeJSON(BREAK_KEY, null);
    S.break = null;
  }

  function loadPrefs() {
    var p = readJSON(PREFS_KEY) || {};
    var min = parseInt(p.min, 10);
    var brk = parseInt(p.break, 10);
    S.pickedMin = clamp(min || 25, 5, 180);
    S.breakMin = BREAK_PRESETS.indexOf(brk) >= 0 ? brk : 5;
    S.notify = !!p.notify;
  }
  function savePrefs() {
    writeJSON(PREFS_KEY, { min: S.pickedMin, break: S.breakMin, notify: S.notify });
  }

  /* ---------------- 计时 ---------------- */
  function elapsedMs(run) {
    run = run || S.run;
    if (!run) { return 0; }
    var end = run.pausedAt || Date.now();
    return Math.max(0, end - run.startedAt - (run.pausedTotal || 0));
  }
  function remainingMs(run) {
    run = run || S.run;
    if (!run) { return 0; }
    return Math.max(0, run.plannedMs - elapsedMs(run));
  }
  function breakLeft(brk) {
    if (!brk) { return 0; }
    return Math.max(0, brk.ms - (Date.now() - brk.startedAt));
  }
  function actualOf(run) {
    if (!run) { return 0; }
    if (run.actualMin != null) { return run.actualMin; }
    return Math.max(0, Math.round((run.actualMs || 0) / 60000));
  }

  /* ---------------- 声音 / 通知 ---------------- */
  var actx = null;
  function unlockAudio() {
    try {
      var Ctx = W.AudioContext || W.webkitAudioContext;
      if (!Ctx) { return; }
      if (!actx) { actx = new Ctx(); }
      if (actx.state === 'suspended') { actx.resume().catch(function () {}); }
    } catch (e) { /* 没有 Web Audio 就安静计时 */ }
  }
  function chime(kind) {
    if (!actx) { return; }
    try {
      if (actx.state === 'suspended') { actx.resume().catch(function () {}); }
      var t0 = actx.currentTime + 0.02;
      var notes = kind === 'break' ? [659.25, 523.25] : [783.99, 987.77, 1318.51];
      notes.forEach(function (freq, i) {
        var osc = actx.createOscillator();
        var gain = actx.createGain();
        osc.type = 'sine';
        osc.frequency.value = freq;
        var start = t0 + i * 0.18;
        gain.gain.setValueAtTime(0.0001, start);
        gain.gain.exponentialRampToValueAtTime(0.18, start + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.55);
        osc.connect(gain);
        gain.connect(actx.destination);
        osc.start(start);
        osc.stop(start + 0.6);
      });
    } catch (e) { /* 音频失败不影响计时 */ }
  }
  function maybeNotify(title, body) {
    if (!S.notify || !('Notification' in W) || W.Notification.permission !== 'granted') { return; }
    if (!document.hidden) { return; }
    try { new W.Notification(title, { body: body || '', tag: 'tb-focus' }); }
    catch (e) { /* 某些浏览器要求 Service Worker，不弹就算了 */ }
  }

  /* ---------------- 状态迁移 ---------------- */
  function validRun(o) {
    return !!(o && o.startedAt && o.plannedMs && o.task);
  }

  function syncFromStorage() {
    var a = readJSON(ACTIVE_KEY);
    if (validRun(a)) {
      S.run = a;
      S.break = null;
      S.breakOver = false;
      try { localStorage.removeItem(BREAK_KEY); } catch (e) {}
      if (a.status === 'done' || a.status === 'abandoned') {
        if (a.actualMin == null) {
          a.actualMin = a.status === 'abandoned'
            ? Math.max(0, Math.round((a.actualMs || 0) / 60000))
            : a.plannedMin;
        }
        S.view = 'done';
      } else if (a.status === 'running' && remainingMs(a) <= 0) {
        naturalComplete();
      } else {
        S.view = 'run';
      }
      return;
    }

    S.run = null;
    var b = readJSON(BREAK_KEY);
    if (b && b.startedAt && b.ms) {
      var left = breakLeft(b);
      if (left <= 0) {
        try { localStorage.removeItem(BREAK_KEY); } catch (e) {}
        S.break = null;
        S.breakOver = false;
        if (S.view === 'break') { S.view = 'setup'; }
      } else {
        S.break = b;
        S.breakOver = false;
        S.view = 'break';
      }
    } else {
      S.break = null;
      if (S.view === 'run' || S.view === 'done' || S.view === 'break') { S.view = 'setup'; }
    }
  }

  function naturalComplete() {
    var run = S.run;
    if (!run || run.status !== 'running') { return; }
    run.status = 'done';
    run.actualMs = run.plannedMs;
    run.actualMin = run.plannedMin;
    run.completedAt = Date.now();
    saveActive();
    chime('done');
    maybeNotify('专注结束', run.task);
    S.view = 'done';
    show('done');
    say('专注结束，记得保存这一段。');
  }

  function enterDone(run, abandoned) {
    run.status = abandoned ? 'abandoned' : 'done';
    run.actualMs = abandoned ? elapsedMs(run) : Math.min(elapsedMs(run), run.plannedMs);
    run.actualMin = abandoned
      ? Math.max(0, Math.round(run.actualMs / 60000))
      : Math.max(1, Math.round(run.actualMs / 60000));
    run.completedAt = Date.now();
    saveActive();
    if (!abandoned) { chime('done'); }
    S.view = 'done';
    show('done');
  }

  function startRun() {
    if (S.run || S.break) { return; }
    var task = clean(el.taskIn.value, 120);
    if (!task) {
      el.setupMsg.textContent = '先写这段时间要做什么。';
      el.setupMsg.className = 'msg err';
      el.taskIn.focus();
      return;
    }
    var planned = clamp(parseInt(el.customMin.value, 10) || S.pickedMin, 5, 180);
    S.pickedMin = planned;
    el.customMin.value = planned;
    renderChips();
    savePrefs();

    if (S.source && task !== S.source.task) {
      S.source = null;
      el.setupMsg.textContent = '任务改过了，已取消和目标动作的关联。';
      el.setupMsg.className = 'msg';
    }
    var source = S.source ? {
      rid: S.source.rid,
      aid: S.source.aid,
      task: S.source.task,
      goal_title: S.source.goal_title || ''
    } : null;

    S.run = {
      v: 1,
      id: uid(),
      task: task,
      intent: clean(el.intentIn.value, 200),
      plannedMin: planned,
      plannedMs: planned * 60000,
      breakMin: S.breakMin,
      source: source,
      startedAt: Date.now(),
      pausedAt: null,
      pausedTotal: 0,
      status: 'running',
      interruptions: [],
      actualMs: null,
      actualMin: null,
      completedAt: null,
      note: '',
      nextStep: ''
    };
    saveActive();
    S.source = null;
    el.intrForm.hidden = true;
    el.intrIn.value = '';
    unlockAudio();
    S.view = 'run';
    show('run');
    say('专注开始，' + planned + ' 分钟。');
  }

  function togglePause() {
    var run = S.run;
    if (!run || (run.status !== 'running' && run.status !== 'paused')) { return; }
    if (run.status === 'running') {
      run.pausedAt = Date.now();
      run.status = 'paused';
    } else {
      run.pausedTotal += Date.now() - (run.pausedAt || Date.now());
      run.pausedAt = null;
      run.status = 'running';
    }
    saveActive();
    show('run');
  }

  function finishEarly() {
    var run = S.run;
    if (!run || (run.status !== 'running' && run.status !== 'paused')) { return; }
    var ms = Math.min(elapsedMs(run), run.plannedMs);
    if (ms < 10000 && !W.confirm('还不到 10 秒，确定要记成完成吗？')) { return; }
    enterDone(run, false);
  }

  function abandonRun() {
    var run = S.run;
    if (!run || (run.status !== 'running' && run.status !== 'paused')) { return; }
    if (!W.confirm('放弃这一段？已经坐过的时间会保留，但不会把动作标成完成。')) { return; }
    enterDone(run, true);
  }

  function addInterruption() {
    var run = S.run;
    if (!run) { return; }
    var text = clean(el.intrIn.value, 80);
    if (!text) { el.intrIn.focus(); return; }
    run.interruptions = run.interruptions || [];
    run.interruptions.push({ text: text, at: toSql(Date.now()) });
    el.intrIn.value = '';
    el.intrForm.hidden = true;
    saveActive();
    renderInterruptions();
    say('打断已记下，计时没有停。');
  }

  /* ---------------- 保存 / 休息 ---------------- */
  function saveSession() {
    var run = S.run;
    if (!run || S.busy) { return; }
    S.busy = true;
    el.saveBtn.disabled = true;
    el.saveMsg.textContent = '保存中…';
    el.saveMsg.className = 'msg';

    var actual = actualOf(run);
    var status = run.status === 'abandoned' ? 'abandoned' : 'completed';
    var payload = {
      task: run.task,
      intent: run.intent || '',
      status: status,
      planned_min: run.plannedMin,
      actual_min: actual,
      break_min: run.breakMin || 0,
      source_tool: (run.source && run.source.rid) ? 'goal-split' : '',
      source_id: (run.source && run.source.rid) || 0,
      source_action_id: (run.source && run.source.aid) || '',
      source_title: (run.source && (run.source.goal_title || run.source.task)) || '',
      interruptions: run.interruptions || [],
      note: el.noteIn.value || '',
      next_step: el.nextIn.value || '',
      started_at: toSql(run.startedAt),
      ended_at: toSql(run.completedAt || Date.now()),
      client_id: run.id,
      mark_done: !!(run.source && run.source.rid && el.markDone.checked && status === 'completed')
    };

    fetch('/focus/api/sessions', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    }).then(function (r) {
      return r.json().catch(function () {
        return { ok: false, message: '服务返回看不懂的内容（HTTP ' + r.status + '）' };
      });
    }).then(function (d) {
      S.busy = false;
      el.saveBtn.disabled = false;
      if (!d || !d.ok) {
        var m = (d && d.message) || '没存上，再试一次。';
        el.saveMsg.textContent = m;
        el.saveMsg.className = 'msg err';
        return;
      }
      S.after = {
        actual: actual,
        planned: run.plannedMin,
        breakMin: run.breakMin || 0,
        source: run.source || null,
        nextStep: payload.next_step || '',
        wb: d.writeback || null,
        duplicate: !!d.duplicate
      };
      clearActive();
      S.view = 'saved';
      show('saved');
      loadSources(true);
      say('这一段已保存，' + actual + ' 分钟。');
    }).catch(function () {
      S.busy = false;
      el.saveBtn.disabled = false;
      el.saveMsg.textContent = '网络断了，再点一次保存。';
      el.saveMsg.className = 'msg err';
    });
  }

  function startBreak() {
    if (S.break || !S.after || !S.after.breakMin) { goNext(false); return; }
    S.break = { startedAt: Date.now(), ms: S.after.breakMin * 60000 };
    writeJSON(BREAK_KEY, S.break);
    S.breakOver = false;
    unlockAudio();
    S.view = 'break';
    show('break');
  }

  function breakDone() {
    if (!S.break) { return; }
    clearBreak();
    chime('break');
    maybeNotify('休息结束', '回来吧，下一段从一个更小的地方开始。');
    S.breakOver = true;
    S.view = 'break';
    show('break');
    say('休息结束。');
  }

  function skipBreak() {
    clearBreak();
    S.breakOver = false;
    goNext(true);
  }

  function goNext(prefill) {
    clearBreak();
    S.breakOver = false;
    resetSetup(prefill);
  }

  function resetSetup(prefill) {
    var next = prefill && S.after && S.after.nextStep ? S.after.nextStep : '';
    S.source = null;
    S.after = null;
    el.taskIn.value = next;
    el.intentIn.value = '';
    el.setupMsg.textContent = next ? '下一步已经填好，直接开始就行。' : '';
    el.setupMsg.className = 'msg';
    el.noteIn.value = '';
    el.nextIn.value = '';
    el.intrList.innerHTML = '';
    S.view = 'setup';
    show('setup');
    if (next && finePointer()) { try { el.taskIn.focus(); } catch (e) {} }
  }

  function discardDone() {
    if (!W.confirm('丢掉这一段的记录？还没保存，丢掉就没了。')) { return; }
    clearActive();
    S.source = null;
    S.after = null;
    el.noteIn.value = '';
    el.nextIn.value = '';
    S.view = 'setup';
    show('setup');
    say('丢掉了，这一段没有进记录。');
  }

  /* ---------------- 目标来源 ---------------- */
  function sourceKey(it) { return String(it.rid) + ':' + String(it.aid); }

  function findSource(key) {
    if (S.source && sourceKey(S.source) === key) { return S.source; }
    for (var i = 0; i < S.sources.length; i++) {
      if (sourceKey(S.sources[i]) === key) { return S.sources[i]; }
    }
    return null;
  }

  function loadSources(force) {
    if (!force && S.sourcesLoaded && Date.now() - S.sourcesAt < 120000) {
      renderSources();
      return;
    }
    fetch('/focus/api/sources', { credentials: 'same-origin' })
      .then(function (r) { return r.json().catch(function () { return null; }); })
      .then(function (d) {
        S.sourcesLoaded = true;
        S.sourcesAt = Date.now();
        S.sources = (d && d.ok && Array.isArray(d.items)) ? d.items : [];
        renderSources();
      })
      .catch(function () {
        S.sourcesLoaded = true;
        S.sourcesAt = Date.now();
        S.sources = [];
        renderSources();
      });
  }

  function addOption(sel, value, label, disabled, selected) {
    var o = document.createElement('option');
    o.value = value;
    o.textContent = label;
    if (disabled) { o.disabled = true; }
    if (selected) { o.selected = true; }
    sel.appendChild(o);
  }

  function renderSources() {
    if (!el.srcSel) { return; }
    var cur = S.source ? sourceKey(S.source) : '';
    el.srcSel.innerHTML = '';
    addOption(el.srcSel, '', '不关联，直接写一件要做的事', false, !cur);
    if (!S.sourcesLoaded) {
      addOption(el.srcSel, '', '正在读取今天的目标动作…', true);
    } else if (!S.sources.length && !S.source) {
      addOption(el.srcSel, '', '今天没有到期的目标动作', true);
    }
    var seen = {};
    S.sources.forEach(function (it) {
      var k = sourceKey(it);
      if (seen[k]) { return; }
      seen[k] = 1;
      var label = (it.task || '目标动作') + ' · ' + (it.min || 25) + ' 分钟';
      if (it.overdue > 0) { label += ' · 逾期 ' + it.overdue + ' 天'; }
      addOption(el.srcSel, k, label, false, k === cur);
    });
    if (S.source && !seen[cur]) {
      addOption(el.srcSel, cur,
        (S.source.task || '目标动作') + ' · ' + (S.source.min || 25) + ' 分钟', false, true);
    }
    el.srcSel.value = cur;

    if (S.sourcesLoaded && !S.sources.length && !S.source) {
      el.srcHint.hidden = false;
      el.srcHint.innerHTML = '今天没有到期动作，可以 <a href="/goal-split">去目标拆解器</a> 拆一条，或直接写任务。';
    } else {
      el.srcHint.hidden = true;
    }
  }

  function chooseSource(key) {
    if (!key) {
      S.source = null;
      return;
    }
    var it = findSource(key);
    if (!it) { S.source = null; return; }
    S.source = {
      rid: it.rid,
      aid: it.aid,
      task: clean(it.task, 120),
      out: clean(it.out, 200),
      min: clamp(parseInt(it.min, 10) || 25, 5, 180),
      goal_title: clean(it.goal_title, 120)
    };
    el.taskIn.value = S.source.task;
    el.intentIn.value = S.source.out || '';
    S.pickedMin = S.source.min;
    el.customMin.value = S.pickedMin;
    renderChips();
    el.setupMsg.textContent = '已关联「' + (S.source.goal_title || '目标动作') + '」，保存后会写回。';
    el.setupMsg.className = 'msg';
  }

  function applyAction(a) {
    if (S.run || S.break) {
      setOpen(true);
      if (S.run) {
        show(S.run.status === 'done' || S.run.status === 'abandoned' ? 'done' : 'run');
      } else {
        show('break');
      }
      notifyPanel('先结束现在这一段，再换任务。', true);
      return;
    }
    var min = clamp(parseInt(a.min, 10) || 25, 5, 180);
    var src = {
      rid: parseInt(a.rid, 10) || 0,
      aid: clean(a.aid, 24),
      task: clean(a.task, 120),
      out: clean(a.out, 200),
      min: min,
      goal_title: clean(a.goal, 120)
    };
    el.taskIn.value = src.task;
    el.intentIn.value = src.out || '';
    S.pickedMin = min;
    el.customMin.value = min;
    // 动作还没保存进目标（没有 aid）时，任务照带，只是没法写回
    if (!src.rid || !src.aid) {
      S.source = null;
      el.setupMsg.textContent = '这条动作还没保存进目标，专注分钟暂时不会写回。';
      el.setupMsg.className = 'msg';
    } else {
      S.source = src;
      el.setupMsg.textContent = '已从目标拆解器带入，保存后会把分钟写回这条动作。';
      el.setupMsg.className = 'msg';
    }
    show('setup');
    renderChips();
    renderSources();
    setOpen(true);
    if (finePointer()) { try { el.taskIn.focus(); } catch (e) {} }
  }

  /* ---------------- 渲染 ---------------- */
  function say(text) {
    if (el.sr) { el.sr.textContent = String(text || ''); }
  }

  function notifyPanel(text, err) {
    el.flashBar.textContent = text || '';
    el.flashBar.className = 'flash' + (err ? ' err' : '');
    el.flashBar.hidden = false;
    clearTimeout(notifyPanel._t);
    notifyPanel._t = setTimeout(function () { el.flashBar.hidden = true; }, 3200);
  }

  function renderChips() {
    if (!el.minChips) { return; }
    el.minChips.innerHTML = '';
    MIN_PRESETS.forEach(function (m) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'chip' + (m === S.pickedMin ? ' on' : '');
      b.textContent = m + ' 分钟';
      b.onclick = function () {
        if (S.run || S.break) { return; }
        S.pickedMin = m;
        el.customMin.value = m;
        renderChips();
        savePrefs();
      };
      el.minChips.appendChild(b);
    });
    if (el.customMin && !el.customMin.matches(':focus')) {
      el.customMin.value = S.pickedMin;
    }
  }

  function renderBreakSel() {
    if (!el.breakSel) { return; }
    el.breakSel.innerHTML = '';
    BREAK_PRESETS.forEach(function (m) {
      addOption(el.breakSel, String(m), m ? m + ' 分钟' : '不休息', false, m === S.breakMin);
    });
    el.breakSel.value = String(S.breakMin);
  }

  function renderInterruptions() {
    if (!S.run) { return; }
    var list = S.run.interruptions || [];
    el.intrList.innerHTML = '';
    list.forEach(function (it) {
      var d = document.createElement('div');
      d.className = 'intr-item';
      var time = String(it.at || '').slice(11, 16);
      d.textContent = (time ? time + ' · ' : '') + (it.text || '');
      el.intrList.appendChild(d);
    });
  }

  function renderRun() {
    var run = S.run;
    if (!run) { return; }
    var paused = run.status === 'paused';
    el.runTimer.textContent = fmt(remainingMs(run));
    el.runState.textContent = paused ? '已暂停' : '专注中';
    el.runState.className = 'run-state' + (paused ? ' paused' : '');
    el.runTask.textContent = run.task;
    el.runIntent.textContent = run.intent || '';
    el.runIntent.hidden = !run.intent;
    var meta = run.plannedMin + ' 分钟 · 开始于 ' + hhmm(run.startedAt);
    meta += ' · 打断 ' + ((run.interruptions || []).length) + ' 次';
    el.runMeta.textContent = meta;
    el.pauseBtn.textContent = paused ? '继续' : '暂停';
    renderInterruptions();
  }

  function renderDone() {
    var run = S.run;
    if (!run) { return; }
    var bad = run.status === 'abandoned';
    var actual = actualOf(run);
    el.doneIco.textContent = bad ? '!' : '✓';
    el.doneIco.className = 'done-ico' + (bad ? ' bad' : '');
    el.doneTitle.textContent = bad ? '先停在这里' : '这一段结束了';
    el.doneSub.textContent = bad
      ? '已经坐过 ' + actual + ' 分钟，保存后按中断记。'
      : (actual >= run.plannedMin ? '完整坐满 ' + run.plannedMin + ' 分钟。' : '提前完成，实际 ' + actual + ' 分钟。');
    el.doneActual.textContent = actual + ' 分钟';
    el.donePlanned.textContent = run.plannedMin + ' 分钟';
    el.doneIntr.textContent = ((run.interruptions || []).length) + ' 次';
    el.noteIn.value = run.note || '';
    el.nextIn.value = run.nextStep || '';
    el.saveMsg.textContent = '';
    el.saveMsg.className = 'msg';
    el.saveBtn.disabled = false;

    if (run.source && run.source.rid && run.source.aid) {
      el.wbBox.hidden = false;
      el.markDone.disabled = bad;
      el.markDone.checked = !bad && actual >= run.plannedMin * 0.8;
      var text;
      if (bad) {
        text = '这段按中断记录。' + (actual > 0
          ? '已经坐过的 ' + actual + ' 分钟仍会写回目标，但不会把动作标成完成。'
          : '不到 1 分钟，不会写回目标。');
      } else {
        text = '会写回「' + (run.source.goal_title || '目标') + '」里的这条动作：实际投入 +' + actual + ' 分钟。';
      }
      el.wbText.textContent = text;
    } else {
      el.wbBox.hidden = true;
    }
  }

  function renderSaved() {
    var a = S.after;
    if (!a) { S.view = 'setup'; show('setup'); return; }
    el.savedIco.textContent = '✓';
    el.savedTitle.textContent = '这一段已经保存';
    el.savedSub.textContent = a.actual + ' 分钟已经记下。' +
      (a.breakMin > 0 ? '休息完再回来。' : '可以再来一段，也可以收工。');

    var hint = a.duplicate ? '这段之前已经保存过，没有重复记账。' : '已记 ' + a.actual + ' 分钟。';
    if (a.wb && a.wb.ok) {
      hint += ' 已写回目标：实际投入 ' + (a.wb.actual || 0) + ' 分钟' +
        (a.wb.done ? '，动作已标完成。' : '。');
    } else if (a.wb && a.wb.ok === false) {
      hint += ' 目标没写成：' + (a.wb.message || '目标可能已经被删了') + '。';
    }
    if (a.nextStep) { hint += ' 下一段：' + a.nextStep; }
    el.savedHint.textContent = hint;

    el.breakBtn.hidden = !(a.breakMin > 0);
    el.skipBreakBtn.hidden = !(a.breakMin > 0);
    el.breakBtn.textContent = '开始休息 ' + (a.breakMin || 5) + ' 分钟';
    el.nextBtn.textContent = a.nextStep ? '按下一步再来一段' : '再来一段';
  }

  function renderBreakView() {
    if (S.break) {
      el.breakIco.textContent = '☕';
      el.breakTimer.hidden = false;
      el.breakTimer.textContent = fmt(breakLeft(S.break));
      el.breakTitle.textContent = '休息中';
      el.breakSub.textContent = '离开屏幕，站起来走两步。休息不算浪费，是下一段的本钱。';
      el.breakSkipBtn.hidden = false;
      el.breakNextBtn.hidden = true;
    } else {
      el.breakIco.textContent = '☀';
      el.breakTimer.hidden = false;
      el.breakTimer.textContent = '00:00';
      el.breakTitle.textContent = '休息结束';
      el.breakSub.textContent = '回来吧，下一段从一个更小的地方开始。';
      el.breakSkipBtn.hidden = true;
      el.breakNextBtn.hidden = false;
    }
  }

  function updateHeader() {
    var title = '专注台';
    var sub = '选一段时间，坐下就是赢一半。';
    if (S.view === 'run') {
      var paused = S.run && S.run.status === 'paused';
      title = paused ? '已暂停' : '专注中';
      sub = paused ? '计时停住了，点「继续」接着走。' : '这段时间只做这一件事。';
    } else if (S.view === 'done') {
      title = '这一段结束了';
      sub = '记一句，下一段会更好开始。';
    } else if (S.view === 'saved') {
      title = '已保存';
      sub = '这段已经进记录。';
    } else if (S.view === 'break') {
      if (S.break) {
        title = '休息中';
        sub = '计时在走，离开屏幕也行。';
      } else {
        title = '休息结束';
        sub = '回来吧，下一段从一个更小的地方开始。';
      }
    }
    el.pTitle.textContent = title;
    el.pSub.textContent = sub;
  }

  var VIEW_IDS = {
    setup: 'setupView', run: 'runView', done: 'doneView',
    saved: 'savedView', break: 'breakView'
  };

  function show(view) {
    S.view = view;
    Object.keys(VIEW_IDS).forEach(function (k) {
      el[VIEW_IDS[k]].hidden = k !== view;
    });
    if (view === 'setup') {
      renderChips();
      renderBreakSel();
      renderSources();
    } else if (view === 'run') {
      renderRun();
    } else if (view === 'done') {
      renderDone();
    } else if (view === 'saved') {
      renderSaved();
    } else if (view === 'break') {
      renderBreakView();
    }
    updateHeader();
    renderKnob();
  }

  function setOpen(open) {
    open = !!open;
    S.open = open;
    el.panel.hidden = !open;
    el.knob.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open) {
      if (S.run) {
        show(S.run.status === 'done' || S.run.status === 'abandoned' ? 'done' : 'run');
      } else if (S.break) {
        show('break');
      } else if (S.breakOver) {
        show('break');
      } else if (S.view === 'saved' && S.after) {
        show('saved');
      } else {
        show('setup');
      }
      if (!S.sourcesLoaded) { loadSources(); }
      unlockAudio();
      if (finePointer()) {
        setTimeout(function () {
          try {
            if (S.view === 'setup') { el.taskIn.focus(); }
          } catch (e) { /* 忽略 */ }
        }, 60);
      }
    } else {
      // 已保存但还没开始休息：关掉面板就回到准备状态，避免下次打开还停在旧结果
      if (S.view === 'saved') {
        S.after = null;
        S.source = null;
        el.taskIn.value = '';
        el.intentIn.value = '';
        el.setupMsg.textContent = '';
        el.setupMsg.className = 'msg';
        S.view = 'setup';
        show('setup');
      } else if (S.view === 'break' && !S.break) {
        S.breakOver = false;
        S.view = 'setup';
        show('setup');
      }
    }
    renderKnob();
    try {
      document.dispatchEvent(new CustomEvent('tb-focus-panel', { detail: { open: open } }));
    } catch (e) { /* 老浏览器 */ }
  }

  function renderBubble() {
    var text = '';
    if (!S.open) {
      if (S.run && S.run.status === 'done') { text = '时间到，点这里保存'; }
      else if (S.run && S.run.status === 'abandoned') { text = '这段中断了，记得保存'; }
      else if (S.breakOver) { text = '休息结束'; }
    }
    el.bubble.hidden = !text;
    if (text) { el.bubble.textContent = text; el.bubble.className = 'bubble'; }
  }

  function renderKnob() {
    if (!el.knob) { return; }
    var run = S.run;
    var running = run && (run.status === 'running' || run.status === 'paused');
    el.knob.classList.toggle('idle', !run && !S.break && !S.breakOver);
    el.knob.classList.toggle('paused', !!(run && run.status === 'paused'));
    el.knob.classList.toggle('done', !!(run && (run.status === 'done' || run.status === 'abandoned')));

    el.knobIco.hidden = false;
    el.knobIco.className = 'knob-ico';
    el.knobTime.hidden = true;
    el.knobTime.className = 'knob-time';
    el.knobBadge.hidden = true;

    var label = '打开专注台';
    if (running) {
      var left = remainingMs(run);
      el.knobIco.hidden = true;
      el.knobTime.hidden = false;
      el.knobTime.textContent = fmt(left);
      el.knobRing.style.strokeDashoffset =
        (RING_LEN * (1 - left / (run.plannedMs || 1))).toFixed(2);
      label = (run.status === 'paused' ? '专注已暂停' : '专注中') +
        '，剩余 ' + Math.ceil(left / 60000) + ' 分钟，打开专注台';
    } else if (run && (run.status === 'done' || run.status === 'abandoned')) {
      el.knobIco.textContent = run.status === 'abandoned' ? '!' : '✓';
      el.knobBadge.hidden = false;
      el.knobRing.style.strokeDashoffset = '0';
      label = run.status === 'abandoned'
        ? '这一段中断了，待保存，打开专注台'
        : '专注结束，待保存，打开专注台';
    } else if (S.break) {
      var bleft = breakLeft(S.break);
      el.knobIco.textContent = '☕';
      el.knobIco.className = 'knob-ico tiny';
      el.knobTime.hidden = false;
      el.knobTime.className = 'knob-time small';
      el.knobTime.textContent = fmt(bleft);
      el.knobRing.style.strokeDashoffset =
        (RING_LEN * (1 - bleft / (S.break.ms || 1))).toFixed(2);
      label = '休息中，剩余 ' + Math.ceil(bleft / 60000) + ' 分钟，打开专注台';
    } else if (S.breakOver) {
      el.knobIco.textContent = '☀';
      el.knobRing.style.strokeDashoffset = '0';
      label = '休息结束，打开专注台';
    } else {
      el.knobIco.textContent = '🍅';
      el.knobRing.style.strokeDashoffset = String(RING_LEN);
      label = '打开专注台';
    }

    el.knob.setAttribute('aria-label', label);
    el.knob.title = label;
    renderBubble();
    updatePiP();
  }

  function tick() {
    if (S.run && S.run.status === 'running' && remainingMs(S.run) <= 0) {
      naturalComplete();
      return;
    }
    if (S.break && breakLeft(S.break) <= 0) {
      breakDone();
      return;
    }
    renderKnob();
    if (S.open) {
      if (S.view === 'run' && S.run) {
        el.runTimer.textContent = fmt(remainingMs(S.run));
      } else if (S.view === 'break' && S.break) {
        el.breakTimer.textContent = fmt(breakLeft(S.break));
      }
    }
  }

  /* ---------------- 主题 ---------------- */
  function applyAccent() {
    if (!host) { return; }
    var dark = false;
    if (W.TBTheme && typeof W.TBTheme.get === 'function') {
      dark = W.TBTheme.get() === 'dark';
    } else {
      dark = document.documentElement.getAttribute('data-theme') === 'dark';
    }
    host.style.setProperty('--fw-acc', dark ? '#ff9b8d' : '#e2564a');
    host.style.setProperty('--fw-acc-on', dark ? '#231313' : '#ffffff');
    host.style.setProperty('--fw-acc-soft', dark ? 'rgba(255,155,141,.16)' : 'rgba(226,86,74,.12)');
    host.style.setProperty('--fw-acc-line', dark ? 'rgba(255,155,141,.4)' : '#f3c8bf');
    host.style.setProperty('--fw-ok', dark ? '#7ed7aa' : '#2f7d5b');
    host.style.setProperty('--fw-ok-soft', dark ? 'rgba(126,215,170,.14)' : '#eaf6ef');
    host.style.setProperty('--fw-ok-line', dark ? 'rgba(126,215,170,.35)' : '#c8e6d6');
    host.style.setProperty('--fw-warn', dark ? '#f1c27d' : '#a86a1f');
    host.style.setProperty('--fw-err', dark ? '#ffa1a8' : '#b8404a');
    updatePiP();
  }

  /* ---------------- 置顶小窗（Document Picture-in-Picture） ---------------- */
  var pipWin = null;
  var pipPending = false;   // requestWindow 是异步的，防连点开两个窗

  function pipSupported() {
    return !!(W.documentPictureInPicture &&
      typeof W.documentPictureInPicture.requestWindow === 'function');
  }

  function pipOpen() {
    if (!pipWin) { return false; }
    try {
      if (pipWin.closed) { pipWin = null; return false; }
    } catch (e) {}
    return true;
  }

  function pipRelevant() {
    return !!(S.run || S.break || S.breakOver || (S.view === 'saved' && S.after));
  }

  function pipSnapshot() {
    var dark = false;
    try {
      dark = (W.TBTheme && typeof W.TBTheme.get === 'function')
        ? W.TBTheme.get() === 'dark'
        : document.documentElement.getAttribute('data-theme') === 'dark';
    } catch (e) {}
    var theme = dark ? 'dark' : 'light';

    var run = S.run;
    if (run) {
      var st = run.status || 'running';
      return {
        mode: st === 'paused' ? 'pause' : ((st === 'done' || st === 'abandoned') ? 'done' : 'run'),
        task: run.task || '',
        plannedMs: run.plannedMs || 0,
        startedAt: run.startedAt || 0,
        pausedTotal: run.pausedTotal || 0,
        pausedAt: run.pausedAt || 0,
        actualMin: actualOf(run),
        interrupts: (run.interruptions || []).length,
        abandoned: st === 'abandoned',
        theme: theme
      };
    }
    if (S.break) {
      return {
        mode: 'break',
        breakMs: S.break.ms || 0,
        breakStartedAt: S.break.startedAt || 0,
        theme: theme
      };
    }
    if (S.breakOver) { return { mode: 'breakOver', theme: theme }; }
    if (S.view === 'saved' && S.after) {
      return {
        mode: 'saved',
        actualMin: S.after.actual || 0,
        breakMin: S.after.breakMin || 0,
        theme: theme
      };
    }
    return { mode: 'idle', theme: theme };
  }

  function renderPipBtn() {
    if (!el.pipBtn) { return; }
    var visible = pipSupported() && pipRelevant();
    var open = pipOpen();
    el.pipBtn.hidden = !visible;
    el.pipBtn.textContent = open ? '浮窗中' : '浮窗';
    el.pipBtn.classList.toggle('on', open);
    el.pipBtn.setAttribute('aria-pressed', open ? 'true' : 'false');
  }

  function updatePiP() {
    if (!pipOpen()) {
      renderPipBtn();
      return;
    }
    var snap = pipSnapshot();
    if (snap.mode === 'idle') {
      closePiP();
      return;
    }
    try {
      pipWin.__tbSnap = snap;
      if (typeof pipWin.__tbPipRender === 'function') { pipWin.__tbPipRender(); }
    } catch (e) {
      closePiP();
      return;
    }
    renderPipBtn();
  }

  function closePiP() {
    var win = pipWin;
    pipWin = null;
    if (win) { try { win.close(); } catch (e) {} }
    renderPipBtn();
  }

  function focusOpener() {
    try { W.focus(); } catch (e) {}
    setOpen(true);
  }

  function pipHook(action) {
    action = String(action || 'back');
    if (action === 'toggle') {
      if (S.run && (S.run.status === 'running' || S.run.status === 'paused')) {
        togglePause();
      }
    } else if (action === 'startBreak') {
      startBreak();
    } else if (action === 'skipBreak') {
      skipBreak();
    } else {
      focusOpener();
    }
    updatePiP();
  }

  /* 这段函数会被 toString() 后放进置顶小窗自己的文档里执行。
     小窗是独立窗口，即使主页面被最小化、被节流，它的 500ms 刷新也不受影响。
     状态快照由主页面写入 __tbSnap，按时间戳自己算倒计时。 */
  function pipClientScript() {
    var mainAction = 'back';
    function pad(n) { return (n < 10 ? '0' : '') + n; }
    function fmt(ms) {
      var sec = Math.max(0, Math.ceil(ms / 1000));
      return pad(Math.floor(sec / 60)) + ':' + pad(sec % 60);
    }
    function $(id) { return document.getElementById(id); }
    function render() {
      var s = window.__tbSnap || {};
      var mode = s.mode || 'idle';
      var root = $('root');
      if (!root) { return; }
      var now = Date.now();
      var state = '专注台';
      var sub = '回到页面开始一段';
      var time = '--:--';
      var label = '回到页面';
      var task = '';
      var progress = -1;
      var alert = false;

      if (mode === 'run' || mode === 'pause') {
        var planned = Math.max(1, s.plannedMs || 1);
        var start = s.startedAt || now;
        var end = mode === 'pause' ? (s.pausedAt || now) : now;
        var elapsed = Math.max(0, end - start - (s.pausedTotal || 0));
        var left = Math.max(0, planned - elapsed);
        progress = elapsed / planned * 100;
        time = fmt(left);
        task = s.task || '';
        if (mode === 'pause') {
          state = '已暂停';
          sub = '点「继续」接着走';
          label = '继续';
          mainAction = 'toggle';
        } else if (left <= 0) {
          state = '时间到';
          sub = '回到页面保存这一段';
          label = '回来保存';
          mainAction = 'back';
          alert = true;
        } else {
          state = '专注中';
          sub = '计划 ' + Math.round(planned / 60000) + ' 分钟';
          if (s.interrupts) { sub += ' · 打断 ' + s.interrupts + ' 次'; }
          label = '暂停';
          mainAction = 'toggle';
        }
      } else if (mode === 'break') {
        var bms = Math.max(1, s.breakMs || 1);
        var bleft = Math.max(0, bms - (now - (s.breakStartedAt || now)));
        var bpct = (bms - bleft) / bms * 100;
        time = fmt(bleft);
        progress = bpct;
        if (bleft <= 0) {
          state = '休息结束';
          sub = '回到页面开始下一段';
          label = '回到页面';
          mainAction = 'back';
          alert = true;
        } else {
          state = '休息中';
          sub = '离开屏幕走两步';
          label = '跳过休息';
          mainAction = 'skipBreak';
        }
      } else if (mode === 'done') {
        time = (s.actualMin || 0) + ' 分';
        state = s.abandoned ? '先停在这里' : '这一段结束了';
        sub = '回到页面保存这段';
        label = '回来保存';
        mainAction = 'back';
        task = s.task || '';
      } else if (mode === 'saved') {
        time = (s.actualMin || 0) + ' 分';
        state = '已保存';
        sub = '已记 ' + (s.actualMin || 0) + ' 分钟';
        if (s.breakMin > 0) {
          label = '休息 ' + s.breakMin + ' 分钟';
          mainAction = 'startBreak';
        } else {
          label = '回到页面';
          mainAction = 'back';
        }
      } else if (mode === 'breakOver') {
        state = '休息结束';
        sub = '回来吧，下一段小一点';
        label = '回到页面';
        mainAction = 'back';
      }

      document.documentElement.setAttribute('data-theme', s.theme === 'dark' ? 'dark' : 'light');
      root.setAttribute('data-mode', mode);
      root.classList.toggle('alert', alert);
      $('state').textContent = state;
      $('task').textContent = task;
      $('task').title = task;
      $('time').textContent = time;
      $('sub').textContent = sub;
      $('main').textContent = label;
      $('main').setAttribute('data-action', mainAction);
      $('back').hidden = mainAction === 'back';
      if (progress >= 0) {
        $('barBox').style.visibility = 'visible';
        $('bar').style.width = Math.max(0, Math.min(100, progress)).toFixed(1) + '%';
      } else {
        $('barBox').style.visibility = 'hidden';
      }
      document.title = (mode === 'run' || mode === 'pause' || mode === 'break')
        ? time + ' · ' + state
        : state + ' · 专注';
    }

    window.__tbPipRender = render;
    document.getElementById('main').addEventListener('click', function () {
      if (typeof window.__tbHook === 'function') { window.__tbHook(mainAction || 'back'); }
    });
    document.getElementById('back').addEventListener('click', function () {
      if (typeof window.__tbHook === 'function') { window.__tbHook('back'); }
    });
    setInterval(render, 500);
    render();
  }

  var PIP_CSS = [
    ':root{color-scheme:light;--bg:#fff;--ink:#152033;--text:#42506a;--muted:#7e8ca3;--line:#e4e9f1;--acc:#e2564a;--acc-on:#fff;--soft:rgba(226,86,74,.12);--track:#eef1f6;--ok:#2f7d5b;--warn:#a86a1f;--blue:#4a6fd0}',
    ':root[data-theme=dark]{color-scheme:dark;--bg:#161d29;--ink:#e8edf7;--text:#c3cde3;--muted:#93a0ba;--line:rgba(255,255,255,.1);--acc:#ff9b8d;--acc-on:#231313;--soft:rgba(255,155,141,.16);--track:rgba(255,255,255,.08);--ok:#7ed7aa;--warn:#f1c27d;--blue:#8ab0ff}',
    '*{box-sizing:border-box}',
    'html,body{height:100%;margin:0;background:var(--bg)}',
    'body{overflow:hidden;font-family:-apple-system,"PingFang SC","Microsoft YaHei",system-ui,sans-serif;line-height:1.35;color:var(--text)}',
    '.pip{height:100%;padding:10px 12px;display:flex;flex-direction:column;gap:3px;background:var(--bg)}',
    '.top{display:flex;align-items:center;gap:6px;min-width:0;height:14px}',
    '.dot{flex:none;width:7px;height:7px;border-radius:50%;background:var(--acc);box-shadow:0 0 0 3px var(--soft)}',
    '[data-mode=pause] .dot{background:var(--warn);box-shadow:0 0 0 3px rgba(217,164,65,.16)}',
    '[data-mode=break] .dot{background:var(--blue);box-shadow:0 0 0 3px rgba(91,141,239,.16)}',
    '[data-mode=done] .dot,[data-mode=saved] .dot,[data-mode=breakOver] .dot{background:var(--ok);box-shadow:0 0 0 3px rgba(47,157,106,.16)}',
    '.state{flex:none;font-size:10.5px;font-weight:720;color:var(--ink);letter-spacing:.01em}',
    '.task{min-width:0;flex:1;font-size:10.5px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    '.task:empty{display:none}',
    '.time{font-size:34px;font-weight:780;line-height:1;letter-spacing:-.035em;font-variant-numeric:tabular-nums;color:var(--ink);margin-top:2px}',
    '.sub{font-size:10px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-height:14px}',
    '.bar{height:3px;border-radius:999px;background:var(--track);overflow:hidden;margin-top:2px}',
    '.bar i{display:block;height:100%;width:0;border-radius:inherit;background:var(--acc);transition:width .45s linear}',
    '[data-mode=pause] .bar i{background:var(--warn)}',
    '[data-mode=break] .bar i{background:var(--blue)}',
    '.actions{display:flex;gap:6px;margin-top:auto;padding-top:4px}',
    '.actions button{min-height:29px;padding:0 10px;border-radius:8px;border:1px solid var(--line);background:transparent;color:var(--muted);font-family:inherit;font-size:11px;font-weight:650;cursor:pointer;white-space:nowrap}',
    '.actions button:hover{color:var(--ink)}',
    '.actions button:first-child{flex:1;min-height:30px;background:var(--acc);border-color:var(--acc);color:var(--acc-on)}',
    '[data-mode=break] .actions button:first-child{background:var(--blue);border-color:var(--blue);color:#fff}',
    '[data-mode=done] .actions button:first-child,[data-mode=saved] .actions button:first-child{background:var(--ok);border-color:var(--ok);color:#fff}',
    '[data-theme=dark] [data-mode=done] .actions button:first-child,[data-theme=dark] [data-mode=saved] .actions button:first-child{color:#10231a}',
    '[data-theme=dark] [data-mode=break] .actions button:first-child{color:#101b2e}',
    '.alert .time{color:var(--acc);animation:pipPulse 1.3s ease-in-out infinite}',
    '@keyframes pipPulse{0%,100%{opacity:1}50%{opacity:.45}}',
    '@media (max-height:158px){.pip{padding:8px 10px;gap:2px}.time{font-size:28px}.sub{min-height:0}}',
    '@media (max-height:130px){.sub{display:none}.time{font-size:25px}.actions button{min-height:26px}}',
    '@media (prefers-reduced-motion:reduce){.bar i{transition:none}.alert .time{animation:none}}'
  ].join('');

  var PIP_HTML = [
    '<div class="pip" id="root" data-mode="idle">',
    '<div class="top"><span class="dot"></span><span class="state" id="state">专注台</span><span class="task" id="task"></span></div>',
    '<div class="time" id="time">--:--</div>',
    '<div class="sub" id="sub"></div>',
    '<div class="bar" id="barBox"><i id="bar"></i></div>',
    '<div class="actions"><button type="button" id="main">回到页面</button><button type="button" id="back" hidden>回到页面</button></div>',
    '</div>'
  ].join('');

  function writePiPDoc(win) {
    var doc = win.document;
    var html = '<!doctype html><html lang="zh-CN" data-theme="light"><head><meta charset="utf-8">' +
      '<meta name="color-scheme" content="light dark"><title>专注 · 置顶小窗</title>' +
      '<style>' + PIP_CSS + '</style></head><body>' + PIP_HTML +
      '<script>(' + pipClientScript.toString() + ')();<\/script></body></html>';
    doc.open();
    doc.write(html);
    doc.close();
  }

  function openPiP() {
    if (!pipSupported()) {
      notifyPanel('当前浏览器不支持置顶小窗，请用桌面版 Chrome 或 Edge。', true);
      return;
    }
    if (pipOpen()) {
      try { pipWin.focus(); } catch (e) {}
      return;
    }
    if (pipSnapshot().mode === 'idle') {
      notifyPanel('先开始一段专注，再把倒计时放到桌面上。', true);
      return;
    }
    if (pipPending) { return; }
    pipPending = true;
    try {
      W.documentPictureInPicture.requestWindow({
        width: 300,
        height: 176,
        disallowReturnToOpener: false
      }).then(function (win) {
        pipPending = false;
        // 窗口申请期间这段可能已经结束，别留一个空置顶窗
        if (pipSnapshot().mode === 'idle') {
          try { win.close(); } catch (e) {}
          pipWin = null;
          renderPipBtn();
          return;
        }
        pipWin = win;
        writePiPDoc(win);
        win.__tbHook = pipHook;
        win.__tbSnap = pipSnapshot();
        win.addEventListener('pagehide', function () {
          if (pipWin === win) { pipWin = null; }
          renderPipBtn();
        });
        try { win.__tbPipRender(); } catch (e) {}
        renderPipBtn();
        notifyPanel('浮窗已置顶：浏览器最小化后仍会留在桌面最上层。');
        say('置顶小窗已打开。');
      }).catch(function (err) {
        pipPending = false;
        pipWin = null;
        renderPipBtn();
        notifyPanel('浮窗没打开：' + ((err && err.message) || '浏览器拒绝了这次请求') + '。', true);
      });
    } catch (e) {
      pipPending = false;
      pipWin = null;
      renderPipBtn();
      notifyPanel('浮窗没打开：' + ((e && e.message) || '浏览器不支持置顶小窗') + '。', true);
    }
  }

  /* ---------------- 位置 ---------------- */
  function place() {
    if (!host) { return; }
    var narrow = false;
    try { narrow = W.matchMedia('(max-width:640px)').matches; } catch (e) {}
    var gap = narrow ? 12 : 18;
    host.style.right = gap + 'px';
    // sdd-decomposer 底部有一条固定 tabbar，别把旋钮压在上面
    var extra = 0;
    var bar = document.querySelector('.tabbar');
    if (bar) {
      var r = bar.getBoundingClientRect();
      if (r.height > 0 && r.top > W.innerHeight * 0.55) {
        extra = Math.max(0, Math.round(W.innerHeight - r.top));
      }
    }
    host.style.bottom = 'calc(' + (gap + extra) + 'px + env(safe-area-inset-bottom))';
    // 让全站 AI 入口跟着旋钮挪，sdd 的 tabbar 和手机安全区都一致
    try {
      document.dispatchEvent(new CustomEvent('tb-focus-dock', { detail: getAnchor() || {} }));
    } catch (e) { /* 老浏览器 */ }
  }

  function getAnchor() {
    if (!host) { return null; }
    var target = el && el.knob ? el.knob : host;
    var r = target.getBoundingClientRect();
    if (!r.width || !r.height) { return null; }
    return {
      rect: {
        left: r.left, top: r.top, right: r.right, bottom: r.bottom,
        width: r.width, height: r.height
      }
    };
  }

  /* ---------------- 构建 ---------------- */
  var CSS = [
    ':host{all:initial}',
    '*{box-sizing:border-box}',
    '[hidden]{display:none!important}',
    '.fw-wrap{display:flex;flex-direction:column;align-items:flex-end;gap:9px;font-family:-apple-system,"PingFang SC","Microsoft YaHei",system-ui,sans-serif;line-height:1.5;color:var(--tb-text,#42506a)}',
    '.sr{position:absolute;width:1px;height:1px;margin:-1px;padding:0;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap;border:0}',
    /* 旋钮 */
    '.knob{position:relative;width:60px;height:60px;padding:0;border:0;background:none;border-radius:50%;cursor:pointer;-webkit-tap-highlight-color:transparent}',
    '.knob-ring{position:absolute;inset:0;width:100%;height:100%;transform:rotate(-90deg)}',
    '.kr-bg{fill:none;stroke:var(--tb-line-2,#d3dae7);stroke-width:3;opacity:.55}',
    '.kr-fg{fill:none;stroke:var(--fw-acc,#e2564a);stroke-width:3.5;stroke-linecap:round;stroke-dasharray:182.21;stroke-dashoffset:0;transition:stroke-dashoffset .45s linear,stroke .2s ease}',
    '.knob.paused .kr-fg{stroke:var(--fw-warn,#a86a1f)}',
    '.knob.done .kr-fg{stroke:var(--fw-ok,#2f7d5b)}',
    '.knob.idle .kr-fg{opacity:0}',
    '.knob-face{position:absolute;inset:4px;border-radius:50%;background:var(--tb-menu-bg,#fff);border:1px solid var(--tb-line,#e4e9f1);box-shadow:0 10px 26px -14px rgba(15,23,42,.55);display:flex;flex-direction:column;align-items:center;justify-content:center;transition:transform .15s ease}',
    '.knob:hover .knob-face{transform:scale(1.04)}',
    '.knob:active .knob-face{transform:scale(.97)}',
    '.knob:focus-visible{outline:none}',
    '.knob:focus-visible .knob-face{box-shadow:0 0 0 3px var(--fw-acc-soft,rgba(226,86,74,.12)),0 10px 26px -14px rgba(15,23,42,.55)}',
    '.knob-ico{font-size:23px;line-height:1}',
    '.knob-ico.tiny{font-size:14px;margin-bottom:1px}',
    '.knob-time{font-size:11.5px;font-weight:720;font-variant-numeric:tabular-nums;letter-spacing:-.02em;color:var(--tb-ink,#152033)}',
    '.knob-time.small{font-size:10px}',
    '.knob-badge{position:absolute;top:7px;right:7px;width:7px;height:7px;border-radius:50%;background:var(--fw-ok,#2f7d5b);box-shadow:0 0 0 2px var(--tb-bg,#f4f6fb)}',
    '.bubble{max-width:190px;padding:6px 10px;border-radius:10px;border:0;background:#23262f;color:#fff;font-family:inherit;font-size:11px;line-height:1.45;box-shadow:0 12px 30px -14px rgba(0,0,0,.7);cursor:pointer;animation:fwIn .16s ease-out}',
    /* 面板 */
    '.panel{width:344px;max-height:min(74vh,660px);display:flex;flex-direction:column;background:var(--tb-menu-bg,#fff);border:1px solid var(--tb-line,#e4e9f1);border-radius:18px;box-shadow:0 26px 68px -30px rgba(15,23,42,.55);overflow:hidden;animation:fwIn .16s ease-out}',
    '.p-head{display:flex;align-items:flex-start;gap:8px;padding:13px 14px 10px;border-bottom:1px solid var(--tb-line,#e4e9f1)}',
    '.p-title{min-width:0;flex:1}',
    '.p-title b{display:block;font-size:14px;font-weight:720;color:var(--tb-ink,#152033);line-height:1.35}',
    '.p-title span{display:block;font-size:10.5px;color:var(--tb-muted,#7e8ca3);margin-top:2px;line-height:1.5}',
    '.icon-btn{flex:none;width:28px;height:28px;border:0;border-radius:9px;background:var(--tb-hover,rgba(21,32,51,.05));color:var(--tb-muted,#7e8ca3);font-size:17px;line-height:1;cursor:pointer}',
    '.icon-btn:hover{color:var(--tb-ink,#152033)}',
    '.pip-btn{flex:none;height:28px;padding:0 8px;border:1px solid var(--fw-acc-line,#f3c8bf);border-radius:9px;background:var(--fw-acc-soft,rgba(226,86,74,.12));color:var(--fw-acc,#e2564a);font-family:inherit;font-size:10.5px;font-weight:650;line-height:1;cursor:pointer;white-space:nowrap}',
    '.pip-btn:hover{filter:brightness(.97)}',
    '.pip-btn.on{background:var(--fw-acc,#e2564a);border-color:var(--fw-acc,#e2564a);color:var(--fw-acc-on,#fff)}',
    '.p-body{padding:11px 14px;overflow:auto;flex:1 1 auto}',
    '.p-foot{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:8px 14px;border-top:1px solid var(--tb-line,#e4e9f1);font-size:10px;color:var(--tb-faint,#a7b1c2)}',
    '.p-foot a{color:var(--fw-acc,#e2564a);text-decoration:none;font-weight:650;font-size:11px;white-space:nowrap}',
    '.p-foot a:hover{text-decoration:underline}',
    '.flash{margin:8px 14px 0;padding:6px 9px;border-radius:8px;font-size:11px;background:var(--fw-acc-soft,rgba(226,86,74,.12));color:var(--tb-text,#42506a);border:1px solid var(--fw-acc-line,#f3c8bf)}',
    '.flash.err{background:var(--fw-err,#b8404a);border-color:var(--fw-err,#b8404a);color:#fff}',
    /* 表单 */
    '.fld{display:block;margin-bottom:9px}',
    '.lb{display:block;font-size:10.5px;font-weight:650;color:var(--tb-muted,#7e8ca3);margin-bottom:4px}',
    '.lb em{font-style:normal;font-weight:400;opacity:.75}',
    'input[type=text],input[type=number],select{width:100%;min-height:36px;border:1px solid var(--tb-line-2,#d3dae7);border-radius:9px;background:var(--tb-field,#fff);color:var(--tb-ink,#152033);padding:7px 9px;outline:none;font-family:inherit;font-size:12px;line-height:1.5}',
    'input[type=text]:focus,input[type=number]:focus,select:focus{border-color:var(--fw-acc,#e2564a);box-shadow:0 0 0 3px var(--fw-acc-soft,rgba(226,86,74,.12))}',
    'input::placeholder{color:var(--tb-faint,#a7b1c2)}',
    '.src-hint{font-size:10.5px;color:var(--tb-muted,#7e8ca3);line-height:1.6;margin:-4px 0 9px}',
    '.src-hint a{color:var(--fw-acc,#e2564a)}',
    '.row{display:flex;align-items:flex-end;justify-content:space-between;gap:8px;flex-wrap:wrap;margin:1px 0 9px}',
    '.chips{display:flex;gap:5px;flex-wrap:wrap}',
    '.chip{min-height:30px;padding:0 10px;border-radius:999px;border:1px solid var(--tb-line,#e4e9f1);background:var(--tb-field,#fff);color:var(--tb-text,#42506a);font-family:inherit;font-size:11.5px;cursor:pointer;white-space:nowrap}',
    '.chip:hover{border-color:var(--fw-acc-line,#f3c8bf);color:var(--fw-acc,#e2564a)}',
    '.chip.on{background:var(--fw-acc,#e2564a);border-color:var(--fw-acc,#e2564a);color:var(--fw-acc-on,#fff);font-weight:650}',
    '.mini-num{display:flex;align-items:center;gap:4px;font-size:10px;color:var(--tb-muted,#7e8ca3);white-space:nowrap}',
    '.mini-num input{width:60px;min-height:30px;text-align:center;padding:4px 6px}',
    '.mini-sel{display:flex;align-items:center;gap:5px;font-size:10.5px;color:var(--tb-muted,#7e8ca3)}',
    '.mini-sel select{width:auto;min-height:30px;padding:3px 6px;font-size:11px}',
    '.btn{display:inline-flex;align-items:center;justify-content:center;min-height:34px;padding:0 12px;border-radius:9px;border:1px solid var(--tb-line-2,#d3dae7);background:var(--tb-card-2,#fafbfe);color:var(--tb-text,#42506a);font-family:inherit;font-size:12px;cursor:pointer;text-decoration:none;white-space:nowrap}',
    '.btn:hover{border-color:var(--fw-acc-line,#f3c8bf)}',
    '.btn.primary{background:var(--fw-acc,#e2564a);border-color:var(--fw-acc,#e2564a);color:var(--fw-acc-on,#fff);font-weight:650}',
    '.btn.primary:hover{filter:brightness(.96)}',
    '.btn.ghost{background:transparent}',
    '.btn.sm{min-height:30px;padding:0 9px;font-size:11px}',
    '.btn:disabled{opacity:.5;cursor:not-allowed}',
    '.msg{font-size:10.5px;color:var(--tb-muted,#7e8ca3);line-height:1.5;margin:6px 0 0}',
    '.msg.err{color:var(--fw-err,#b8404a)}',
    '.tip{font-size:10px;color:var(--tb-faint,#a7b1c2);line-height:1.6;margin:8px 0 0;padding-top:8px;border-top:1px dashed var(--tb-line,#e4e9f1)}',
    /* 计时 */
    '.run-top{text-align:center;padding:4px 0 2px}',
    '.run-time{font-size:38px;font-weight:760;color:var(--tb-ink,#152033);font-variant-numeric:tabular-nums;letter-spacing:-.02em;line-height:1.1}',
    '.run-state{font-size:10.5px;color:var(--tb-muted,#7e8ca3);margin-top:2px}',
    '.run-state.paused{color:var(--fw-warn,#a86a1f);font-weight:650}',
    '.run-task{text-align:center;font-size:14px;font-weight:700;color:var(--tb-ink,#152033);margin:8px 0 2px;overflow-wrap:anywhere}',
    '.run-intent{text-align:center;font-size:11px;color:var(--tb-muted,#7e8ca3);line-height:1.5;overflow-wrap:anywhere}',
    '.run-meta{text-align:center;font-size:10px;color:var(--tb-muted,#7e8ca3);margin:6px 0 8px}',
    '.run-actions{display:flex;gap:6px;flex-wrap:wrap;justify-content:center;margin:10px 0 2px}',
    '.intr{margin-top:7px;padding-top:7px;border-top:1px dashed var(--tb-line,#e4e9f1);text-align:center}',
    '.lnk{border:0;background:none;color:var(--fw-acc,#e2564a);font-family:inherit;font-size:11.5px;cursor:pointer;padding:2px}',
    '.lnk:hover{text-decoration:underline}',
    '.intr-form{display:flex;gap:5px;margin-top:6px}',
    '.intr-form input{flex:1;min-width:0}',
    '.intr-list{display:flex;flex-direction:column;gap:3px;margin-top:6px;text-align:left}',
    '.intr-item{font-size:10px;color:var(--tb-muted,#7e8ca3);background:var(--tb-card-2,#fafbfe);border:1px solid var(--tb-line,#e4e9f1);border-radius:6px;padding:4px 6px;line-height:1.5}',
    /* 完成 */
    '.done-head{display:flex;gap:10px;align-items:center;margin:2px 0 10px}',
    '.done-ico{flex:none;width:36px;height:36px;border-radius:11px;display:grid;place-items:center;background:var(--fw-ok-soft,#eaf6ef);color:var(--fw-ok,#2f7d5b);font-size:18px;border:1px solid var(--fw-ok-line,#c8e6d6)}',
    '.done-ico.bad{background:var(--fw-acc-soft,rgba(226,86,74,.12));color:var(--fw-warn,#a86a1f);border-color:var(--fw-acc-line,#f3c8bf)}',
    '.done-head b{display:block;font-size:13.5px;color:var(--tb-ink,#152033);line-height:1.4}',
    '.done-head span{display:block;font-size:10.5px;color:var(--tb-muted,#7e8ca3);line-height:1.5;margin-top:1px}',
    '.done-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-bottom:9px}',
    '.done-stats div{border:1px solid var(--tb-line,#e4e9f1);border-radius:9px;padding:6px 8px;background:var(--tb-card-2,#fafbfe)}',
    '.done-stats span{display:block;font-size:9.5px;color:var(--tb-muted,#7e8ca3)}',
    '.done-stats b{display:block;font-size:13px;color:var(--tb-ink,#152033);font-weight:720}',
    '.wb{display:flex;align-items:flex-start;gap:7px;border:1px solid var(--fw-acc-line,#f3c8bf);background:var(--fw-acc-soft,rgba(226,86,74,.12));border-radius:10px;padding:8px 9px;margin-bottom:9px;font-size:10.5px;color:var(--tb-text,#42506a);cursor:pointer;line-height:1.55}',
    '.wb input{margin:2px 0 0;accent-color:var(--fw-acc,#e2564a)}',
    /* 已保存 / 休息 */
    '.saved-box{text-align:center;padding:8px 0 4px}',
    '.saved-ico{display:inline-grid;place-items:center;width:40px;height:40px;border-radius:13px;background:var(--fw-ok-soft,#eaf6ef);color:var(--fw-ok,#2f7d5b);border:1px solid var(--fw-ok-line,#c8e6d6);font-size:20px}',
    '.saved-box b{display:block;font-size:14.5px;color:var(--tb-ink,#152033);margin-top:8px}',
    '.saved-box p{font-size:11px;color:var(--tb-muted,#7e8ca3);line-height:1.6;margin:4px auto 0;max-width:285px}',
    '.saved-box .run-time{font-size:30px;margin-top:2px}',
    '.saved-box .hintline{font-size:10px;color:var(--tb-muted,#7e8ca3);background:var(--tb-card-2,#fafbfe);border:1px solid var(--tb-line,#e4e9f1);border-radius:8px;padding:6px 8px;margin-top:10px;text-align:left;max-width:none}',
    '@keyframes fwIn{from{opacity:0;transform:translateY(6px) scale(.98)}to{opacity:1;transform:none}}',
    '@media (max-width:520px){.panel{width:min(352px,calc(100vw - 24px));max-height:72vh}.knob{width:56px;height:56px}}',
    '@media (prefers-reduced-motion:reduce){.panel,.bubble{animation:none}.kr-fg,.knob-face{transition:none}}',
    '@media print{.fw-wrap{display:none!important}}'
  ].join('');

  var HTML = [
    '<div class="fw-wrap">',
    '<div class="panel" id="panel" role="dialog" aria-modal="false" aria-labelledby="pTitle" hidden>',
    '<div class="p-head"><div class="p-title"><b id="pTitle">专注台</b><span id="pSub">选一段时间，坐下就是赢一半。</span></div>',
    '<button class="pip-btn" id="pipBtn" type="button" title="置顶小窗：浏览器最小化后仍可见" aria-pressed="false" hidden>浮窗</button>',
    '<button class="icon-btn" id="closeBtn" type="button" aria-label="收起专注台">×</button></div>',
    '<div class="flash" id="flashBar" hidden></div>',
    '<div class="p-body">',
    // 准备
    '<div class="view" id="setupView">',
    '<label class="fld"><span class="lb">关联今天的动作 <em>可选</em></span><select id="srcSel"></select></label>',
    '<div class="src-hint" id="srcHint" hidden></div>',
    '<label class="fld"><span class="lb">这段时间做什么 <em>必填</em></span><input id="taskIn" type="text" maxlength="120" placeholder="例：录一段 3 分钟自我介绍"></label>',
    '<label class="fld"><span class="lb">做完想拿到什么 <em>可选</em></span><input id="intentIn" type="text" maxlength="200" placeholder="例：一个能发出去的 mp3"></label>',
    '<div class="row"><div><span class="lb">专注多久</span><div class="chips" id="minChips"></div></div>',
    '<label class="mini-num"><span>自定义</span><input id="customMin" type="number" min="5" max="180" step="5" value="25"><span>分</span></label></div>',
    '<div class="row"><label class="mini-sel"><span>结束后休息</span><select id="breakSel"></select></label>',
    '<button class="btn primary" id="startBtn" type="button">开始专注</button></div>',
    '<p class="msg" id="setupMsg"></p>',
    '<p class="tip" id="setupTip">计时按时间戳算，切页、刷新都不会少走。保存后实际分钟会写回目标动作。</p>',
    '</div>',
    // 计时
    '<div class="view" id="runView" hidden>',
    '<div class="run-top"><div class="run-time" id="runTimer">25:00</div><div class="run-state" id="runState">专注中</div></div>',
    '<div class="run-task" id="runTask"></div>',
    '<div class="run-intent" id="runIntent"></div>',
    '<div class="run-meta" id="runMeta"></div>',
    '<div class="run-actions"><button class="btn primary" id="pauseBtn" type="button">暂停</button>',
    '<button class="btn" id="finishBtn" type="button">提前完成</button>',
    '<button class="btn ghost" id="abandonBtn" type="button">放弃</button></div>',
    '<div class="intr"><button class="lnk" id="intrBtn" type="button">＋ 记一个打断</button>',
    '<div class="intr-form" id="intrForm" hidden><input id="intrIn" type="text" maxlength="80" placeholder="刚刚是什么打断了你？">',
    '<button class="btn sm" id="intrSave" type="button">记下</button></div>',
    '<div class="intr-list" id="intrList"></div></div>',
    '</div>',
    // 完成
    '<div class="view" id="doneView" hidden>',
    '<div class="done-head"><span class="done-ico" id="doneIco">✓</span><div><b id="doneTitle">这一段结束了</b><span id="doneSub"></span></div></div>',
    '<div class="done-stats"><div><span>实际专注</span><b id="doneActual">25 分钟</b></div>',
    '<div><span>计划</span><b id="donePlanned">25 分钟</b></div>',
    '<div><span>打断</span><b id="doneIntr">0 次</b></div></div>',
    '<label class="wb" id="wbBox" hidden><input type="checkbox" id="markDone"><span id="wbText"></span></label>',
    '<label class="fld"><span class="lb">刚才实际发生了什么 <em>可选</em></span><input id="noteIn" type="text" maxlength="600" placeholder="例：前 10 分钟在找素材"></label>',
    '<label class="fld"><span class="lb">下一段第一件事 <em>可选</em></span><input id="nextIn" type="text" maxlength="200" placeholder="例：把录音剪到 3 分钟以内"></label>',
    '<div class="run-actions"><button class="btn primary" id="saveBtn" type="button">保存这段</button>',
    '<button class="btn ghost" id="discardBtn" type="button">丢掉</button></div>',
    '<p class="msg" id="saveMsg"></p>',
    '</div>',
    // 已保存
    '<div class="view" id="savedView" hidden>',
    '<div class="saved-box"><span class="saved-ico" id="savedIco">✓</span><b id="savedTitle">这一段已经保存</b>',
    '<p id="savedSub"></p><p class="hintline" id="savedHint"></p>',
    '<div class="run-actions"><button class="btn primary" id="breakBtn" type="button">开始休息</button>',
    '<button class="btn" id="nextBtn" type="button">再来一段</button>',
    '<button class="btn ghost" id="skipBreakBtn" type="button">跳过休息</button></div></div>',
    '</div>',
    // 休息
    '<div class="view" id="breakView" hidden>',
    '<div class="saved-box"><span class="saved-ico" id="breakIco">☕</span>',
    '<div class="run-time" id="breakTimer">05:00</div><b id="breakTitle">休息中</b><p id="breakSub"></p>',
    '<div class="run-actions"><button class="btn primary" id="breakNextBtn" type="button">再来一段</button>',
    '<button class="btn ghost" id="breakSkipBtn" type="button">跳过休息</button></div></div>',
    '</div>',
    '</div>',
    '<div class="p-foot"><a href="/focus" id="fullLink">打开完整专注台 →</a><span>统计和历史在完整页</span></div>',
    '</div>',
    '<button class="bubble" id="bubble" type="button" hidden>时间到，点这里保存</button>',
    '<button class="knob idle" id="knob" type="button" aria-haspopup="dialog" aria-expanded="false" aria-label="打开专注台">',
    '<svg class="knob-ring" viewBox="0 0 64 64" aria-hidden="true">',
    '<circle class="kr-bg" cx="32" cy="32" r="29"></circle>',
    '<circle class="kr-fg" id="knobRing" cx="32" cy="32" r="29"></circle>',
    '</svg>',
    '<span class="knob-face"><span class="knob-ico" id="knobIco">🍅</span>',
    '<span class="knob-time" id="knobTime" hidden>25:00</span>',
    '<span class="knob-badge" id="knobBadge" hidden></span></span>',
    '</button>',
    '<span class="sr" id="sr" aria-live="polite"></span>',
    '</div>'
  ].join('');

  function cache() {
    ['panel', 'pTitle', 'pSub', 'pipBtn', 'closeBtn', 'flashBar', 'setupView', 'srcSel', 'srcHint',
      'taskIn', 'intentIn', 'minChips', 'customMin', 'breakSel', 'startBtn', 'setupMsg', 'setupTip',
      'runView', 'runTimer', 'runState', 'runTask', 'runIntent', 'runMeta', 'pauseBtn',
      'finishBtn', 'abandonBtn', 'intrBtn', 'intrForm', 'intrIn', 'intrSave', 'intrList',
      'doneView', 'doneIco', 'doneTitle', 'doneSub', 'doneActual', 'donePlanned', 'doneIntr',
      'wbBox', 'markDone', 'wbText', 'noteIn', 'nextIn', 'saveBtn', 'discardBtn', 'saveMsg',
      'savedView', 'savedIco', 'savedTitle', 'savedSub', 'savedHint', 'breakBtn', 'nextBtn',
      'skipBreakBtn', 'breakView', 'breakIco', 'breakTimer', 'breakTitle', 'breakSub',
      'breakNextBtn', 'breakSkipBtn', 'knob', 'knobRing', 'knobIco', 'knobTime', 'knobBadge',
      'bubble', 'sr', 'fullLink'
    ].forEach(function (id) { el[id] = $(id); });
  }

  function bind() {
    el.knob.onclick = function () {
      unlockAudio();
      setOpen(!S.open);
    };
    el.bubble.onclick = function () { setOpen(true); };
    el.closeBtn.onclick = function () { setOpen(false); };
    el.pipBtn.onclick = function () { openPiP(); };

    el.startBtn.onclick = startRun;
    el.pauseBtn.onclick = togglePause;
    el.finishBtn.onclick = finishEarly;
    el.abandonBtn.onclick = abandonRun;
    el.intrBtn.onclick = function () {
      el.intrForm.hidden = !el.intrForm.hidden;
      if (!el.intrForm.hidden) { el.intrIn.focus(); }
    };
    el.intrSave.onclick = addInterruption;
    el.intrIn.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); addInterruption(); }
    });
    el.saveBtn.onclick = saveSession;
    el.discardBtn.onclick = discardDone;
    el.breakBtn.onclick = startBreak;
    el.nextBtn.onclick = function () { goNext(true); };
    el.skipBreakBtn.onclick = skipBreak;
    el.breakSkipBtn.onclick = skipBreak;
    el.breakNextBtn.onclick = function () { goNext(false); };

    el.srcSel.onchange = function () {
      if (S.run || S.break) {
        renderSources();
        return;
      }
      chooseSource(this.value);
    };

    el.taskIn.addEventListener('input', function () {
      if (!S.source) { return; }
      var value = clean(el.taskIn.value, 120);
      // 深链只带了目标、没带动作文字时，第一次输入就把动作文字补上
      if (!S.source.task) { S.source.task = value; return; }
      if (value !== S.source.task) {
        S.source = null;
        renderSources();
        el.setupMsg.textContent = '任务改过了，已取消和目标动作的关联。';
        el.setupMsg.className = 'msg';
      }
    });

    el.customMin.addEventListener('change', function () {
      var n = clamp(parseInt(this.value, 10) || 25, 5, 180);
      S.pickedMin = n;
      this.value = n;
      renderChips();
      savePrefs();
    });

    el.breakSel.addEventListener('change', function () {
      var n = parseInt(this.value, 10);
      S.breakMin = BREAK_PRESETS.indexOf(n) >= 0 ? n : 5;
      savePrefs();
    });

    el.noteIn.addEventListener('input', function () {
      if (S.run) { S.run.note = this.value; saveActive(); }
    });
    el.nextIn.addEventListener('input', function () {
      if (S.run) { S.run.nextStep = this.value; saveActive(); }
    });

    document.addEventListener('pointerdown', function (e) {
      if (!S.open) { return; }
      var pathArr = e.composedPath ? e.composedPath() : [];
      if (pathArr.indexOf(host) === -1) { setOpen(false); }
    }, true);

    document.addEventListener('keydown', function (e) {
      if (S.open && (e.key === 'Escape' || e.key === 'Esc')) {
        e.stopPropagation();
        setOpen(false);
        try { el.knob.focus(); } catch (err) {}
      }
    }, true);

    // AI 助手打开时收起专注面板，两个浮层不打架
    document.addEventListener('tb-assistant-panel', function (e) {
      if (e && e.detail && e.detail.open && S.open) { setOpen(false); }
    });

    W.addEventListener('storage', function (e) {
      if (e.key && [ACTIVE_KEY, BREAK_KEY, PREFS_KEY].indexOf(e.key) === -1) { return; }
      loadPrefs();
      syncFromStorage();
      if (S.run) {
        show(S.run.status === 'done' || S.run.status === 'abandoned' ? 'done' :
          (S.run.status === 'paused' ? 'run' : (remainingMs(S.run) <= 0 ? 'done' : 'run')));
      } else if (S.break) {
        show('break');
      } else if (S.view !== 'saved') {
        S.view = 'setup';
        show('setup');
      }
      tick();
    });

    window.addEventListener('pageshow', function (e) {
      if (e.persisted) {
        loadPrefs();
        syncFromStorage();
        show(S.view);
        tick();
      }
    });
    // 页面关闭 / 跳转时置顶小窗跟着走（Document PiP 本身也会随 opener 关闭）
    window.addEventListener('pagehide', function () {
      if (!pipWin) { return; }
      try { pipWin.close(); } catch (e) {}
      pipWin = null;
    });
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden) { tick(); }
    });
    window.addEventListener('resize', place);
    window.addEventListener('tbthemechange', applyAccent);
  }

  function boot() {
    if (document.getElementById('tb-focus-widget')) { return; }
    host = document.createElement('div');
    host.id = 'tb-focus-widget';
    host.style.cssText = [
      'all:initial',
      'position:fixed',
      'right:18px',
      'bottom:calc(18px + env(safe-area-inset-bottom))',
      'z-index:45',
      'display:block',
      'font-family:-apple-system,"PingFang SC","Microsoft YaHei",system-ui,sans-serif',
      'line-height:1.5',
      'color:var(--tb-text,#42506a)'
    ].join(';') + ';';
    (document.body || document.documentElement).appendChild(host);
    shadow = host.attachShadow({ mode: 'open' });
    shadow.innerHTML = '<style>' + CSS + '</style>' + HTML;

    cache();
    bind();
    if (pipSupported()) {
      el.setupTip.textContent = '计时按时间戳算，切页、刷新都不会少走。开始后点右上角「浮窗」，浏览器最小化也能看到。保存后实际分钟会写回目标动作。';
    }
    applyAccent();
    place();
    loadPrefs();
    syncFromStorage();
    show(S.view);
    renderKnob();

    setInterval(tick, 500);

    W.TBFocusWidget = {
      version: 2,
      open: function () { setOpen(true); },
      close: function () { setOpen(false); },
      toggle: function () { setOpen(!S.open); },
      openAction: function (a) {
        if (!a || typeof a !== 'object') { setOpen(true); return true; }
        applyAction(a);
        return true;
      },
      pip: function () { openPiP(); },
      closePip: function () { closePiP(); },
      getAnchor: getAnchor,
      state: function () {
        return {
          open: S.open,
          view: S.view,
          task: S.run ? S.run.task : '',
          status: S.run ? S.run.status : (S.break ? 'break' : 'idle'),
          pip: pipOpen()
        };
      }
    };
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
