/* ==========================================================================
   专注台 / 番茄钟
   --------------------------------------------------------------------------
   计时不靠 setInterval 递减计数，而是每次都用 startedAt / pausedTotal 算出
   「已经过去多少」。切标签页、锁屏、刷新之后回来，数字仍然是对的。

   进行中的那一段只存在 localStorage（tb_focus_active_v1），不往服务器写半成品；
   点「保存这段」才 POST /focus/api/sessions，服务器落 history(tool='focus')，
   如果这段来自目标拆解器，还会把实际分钟写回那条动作。
   ========================================================================== */
(function () {
  'use strict';

  var ACTIVE_KEY = 'tb_focus_active_v1';
  var BREAK_KEY = 'tb_focus_break_v1';
  var PREFS_KEY = 'tb_focus_prefs_v1';
  var RING_LEN = 603.19;
  var MIN_PRESETS = [15, 25, 45, 60];
  var BREAK_PRESETS = [0, 3, 5, 10];

  var S = {
    run: null,          // 当前这段（running / paused / done / abandoned）
    break: null,        // 休息倒计时
    after: null,        // 刚保存完、准备休息时的信息
    stats: null,
    sources: [],
    source: null,       // 准备阶段选中的目标动作
    pickedMin: 25,
    breakMin: 5,
    notify: false,
    nextStep: '',
    view: 'setup',
    saveBusy: false,
    deepLink: false,
    sourcesLoaded: false
  };

  function $(id) { return document.getElementById(id); }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  function clean(s, n) {
    var t = String(s == null ? '' : s).replace(/\s+/g, ' ').replace(/^\s+|\s+$/g, '');
    return n && t.length > n ? t.slice(0, n) : t;
  }

  function pad(n) { return (n < 10 ? '0' : '') + n; }

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

  function clamp(n, lo, hi) { return Math.max(lo, Math.min(hi, n)); }

  function uid() {
    return Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
  }

  function toast(msg, type) {
    var el = $('toast');
    if (!el) { return; }
    el.textContent = msg || '';
    el.className = 'toast on' + (type === 'err' ? ' err' : '');
    clearTimeout(toast._t);
    toast._t = setTimeout(function () { el.className = 'toast'; }, 2800);
  }

  /* ---------------- 本地状态 ---------------- */
  function readJSON(key) {
    try { return JSON.parse(localStorage.getItem(key) || 'null'); }
    catch (e) { return null; }
  }

  function writeJSON(key, val) {
    try {
      if (val == null) { localStorage.removeItem(key); }
      else { localStorage.setItem(key, JSON.stringify(val)); }
    } catch (e) { /* 隐私模式下写不进去：页面仍然可以正常计时 */ }
  }

  function saveActive() { writeJSON(ACTIVE_KEY, S.run); }
  function clearActive() { writeJSON(ACTIVE_KEY, null); S.run = null; }
  function clearBreak() { writeJSON(BREAK_KEY, null); S.break = null; }

  function loadPrefs() {
    var p = readJSON(PREFS_KEY) || {};
    S.pickedMin = MIN_PRESETS.indexOf(+p.min) >= 0 ? +p.min : clamp(+p.min || 25, 5, 180);
    S.breakMin = BREAK_PRESETS.indexOf(+p.break) >= 0 ? +p.break : 5;
    S.notify = !!p.notify;
  }

  function savePrefs() {
    writeJSON(PREFS_KEY, { min: S.pickedMin, break: S.breakMin, notify: S.notify });
  }

  /* ---------------- 声音 / 通知 ---------------- */
  var actx = null;

  function unlockAudio() {
    try {
      var Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) { return; }
      if (!actx) { actx = new Ctx(); }
      if (actx.state === 'suspended') { actx.resume().catch(function () {}); }
    } catch (e) { /* 没有 Web Audio 就安静地计时 */ }
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
    if (!S.notify || !('Notification' in window) || Notification.permission !== 'granted') { return; }
    if (!document.hidden) { return; }
    try { new Notification(title, { body: body || '', tag: 'tb-focus' }); }
    catch (e) { /* 某些浏览器要求 Service Worker，不弹就算了 */ }
  }

  /* ---------------- 时长选择 ---------------- */
  function renderMinChips() {
    var box = $('minChips');
    if (!box) { return; }
    box.innerHTML = MIN_PRESETS.map(function (m) {
      return '<button type="button" class="chip' + (m === S.pickedMin ? ' on' : '') +
        '" data-min="' + m + '">' + m + ' 分钟</button>';
    }).join('');
    Array.prototype.forEach.call(box.querySelectorAll('[data-min]'), function (b) {
      b.onclick = function () {
        if (S.run || S.break) { return; }
        S.pickedMin = +b.getAttribute('data-min');
        $('customMin').value = S.pickedMin;
        renderMinChips();
        savePrefs();
      };
    });
    var custom = $('customMin');
    if (custom) { custom.value = S.pickedMin; }
  }

  function renderBreakChips() {
    var box = $('breakChips');
    if (!box) { return; }
    box.innerHTML = BREAK_PRESETS.map(function (m) {
      return '<button type="button" class="chip' + (m === S.breakMin ? ' on' : '') +
        '" data-break="' + m + '">' + (m ? m + ' 分钟' : '不休息') + '</button>';
    }).join('');
    Array.prototype.forEach.call(box.querySelectorAll('[data-break]'), function (b) {
      b.onclick = function () {
        if (S.run || S.break) { return; }
        S.breakMin = +b.getAttribute('data-break');
        renderBreakChips();
        savePrefs();
      };
    });
  }

  /* ---------------- 目标来源 ---------------- */
  function setSource(item) {
    S.source = item || null;
    if (item) {
      $('taskIn').value = item.task || '';
      $('intentIn').value = item.out || '';
      S.pickedMin = clamp(+item.min || 25, 5, 180);
      renderMinChips();
      $('setupMsg').textContent = '已从目标拆解器带入，结束保存后会写回这条动作。';
    }
    renderChip();
    renderSources();
  }

  function clearSource(silent) {
    var had = !!(S.run ? S.run.source : S.source);
    S.source = null;
    if (S.run && S.run.source) {
      S.run.source = null;
      saveActive();
    }
    renderChip();
    renderSources();
    if (S.view === 'done' && S.run) { renderWriteback(); }
    if (had && !silent) {
      $('setupMsg').textContent = '已取消关联，这段会按自由专注记录。';
    }
  }

  function renderChip() {
    var chip = $('srcChip');
    var src = S.run ? S.run.source : S.source;
    if (!chip) { return; }
    if (!src) { chip.hidden = true; return; }
    chip.hidden = false;
    $('srcChipText').textContent = '关联：' + (src.goal_title || src.task || '目标动作');
    var btn = $('clearSourceBtn');
    if (btn) { btn.hidden = S.view === 'break'; }
  }

  function renderSources() {
    var box = $('sourceList');
    if (!box) { return; }
    if (!S.sourcesLoaded) {
      box.innerHTML = '<div class="source-empty">正在读取目标拆解器…</div>';
      return;
    }
    if (!S.sources.length) {
      box.innerHTML = '<div class="source-empty">今天没有到期动作。可以直接自由专注，或者去 ' +
        '<a href="/goal-split">目标拆解器</a> 先拆一条。</div>';
      return;
    }
    var cur = S.source || (S.run && S.run.source) || {};
    box.innerHTML = S.sources.map(function (it) {
      var on = cur && String(cur.rid) === String(it.rid) && String(cur.aid) === String(it.aid);
      var meta = it.goal_title || '目标动作';
      if (it.overdue > 0) { meta += ' · 逾期 ' + it.overdue + ' 天'; }
      else if (it.on) { meta += ' · ' + it.on; }
      else { meta += ' · 没写日期'; }
      return '<button type="button" class="source-item' + (on ? ' on' : '') +
        '" data-rid="' + esc(it.rid) + '" data-aid="' + esc(it.aid) + '">' +
        '<b>' + esc(it.task) + '</b><i>' + it.min + ' 分钟</i>' +
        '<span>' + esc(meta) + '</span>' +
        '</button>';
    }).join('');
    Array.prototype.forEach.call(box.querySelectorAll('[data-rid]'), function (b) {
      b.onclick = function () {
        if (S.run || S.break) { toast('先结束现在这一段，再换任务。'); return; }
        var rid = b.getAttribute('data-rid'), aid = b.getAttribute('data-aid');
        var found = null;
        S.sources.forEach(function (it) {
          if (String(it.rid) === String(rid) && String(it.aid) === String(aid)) { found = it; }
        });
        if (found) { setSource(found); }
      };
    });
  }

  function loadSources() {
    fetch('/focus/api/sources', { credentials: 'same-origin' })
      .then(function (r) { return r.json().catch(function () { return null; }); })
      .then(function (d) {
        S.sourcesLoaded = true;
        S.sources = (d && d.ok && d.items) ? d.items : [];
        // 深链带过来的那条可能在别的日期，列表里没有也保留；这里只补上目标标题
        if (S.source && S.source.rid && S.source.aid && !S.source.goal_title) {
          S.sources.forEach(function (it) {
            if (String(it.rid) === String(S.source.rid) && String(it.aid) === String(S.source.aid)) {
              S.source.goal_title = it.goal_title;
              S.source.task = S.source.task || it.task;
            }
          });
        }
        renderSources();
        renderChip();
      })
      .catch(function () {
        S.sourcesLoaded = true;
        S.sources = [];
        renderSources();
      });
  }

  /* ---------------- 统计 ---------------- */
  function renderStats(st) {
    if (!st) { return; }
    S.stats = st;
    $('todayMin').textContent = st.today_minutes || 0;
    $('todayCount').textContent = (st.today_completed || 0) + ' 段完成';
    $('todayAbandon').textContent = (st.today_abandoned || 0) + ' 段中断';
    $('weekMin').textContent = st.week_minutes || 0;
    $('weekCount').textContent = st.week_completed || 0;
    $('streak').textContent = st.streak || 0;
    $('todayLabel').textContent = st.today || '';

    var days = st.days || [];
    var max = Math.max(1, st.max_minutes || 1);
    $('weekBars').innerHTML = days.map(function (d) {
      var h = d.minutes > 0 ? Math.max(7, Math.round(d.minutes * 100 / max)) : 0;
      return '<div class="bar-day' + (d.today ? ' today' : '') + (d.minutes > 0 ? '' : ' empty') +
        '" title="' + esc(d.date) + ' · ' + d.minutes + ' 分钟">' +
        '<i style="height:' + h + '%"></i><span>' + esc(String(d.label || '').replace('周', '')) + '</span></div>';
    }).join('');

    var recent = st.recent || [];
    $('recentCount').textContent = st.total_sessions ? '共 ' + st.total_sessions + ' 段' : '';
    if (!recent.length) {
      $('recentList').innerHTML = '<div class="empty-mini">还没有记录。第一段保存之后，这里会长出东西。</div>';
    } else {
      $('recentList').innerHTML = recent.map(function (s) {
        var bad = s.status === 'abandoned';
        var href = (s.source_tool === 'goal-split' && s.source_id) ? '/goal-split?r=' + s.source_id : '';
        var tag = href ? 'a' : 'div';
        var when = String(s.created_at || '').slice(5, 16) || '';
        var sub = when + ' · ' + (bad ? '中断' : '完成') + ' · ' + (s.actual_min || 0) + ' 分钟';
        if (s.source_title) { sub += ' · ' + s.source_title; }
        return '<' + tag + ' class="rec"' + (href ? ' href="' + esc(href) + '"' : '') + '>' +
          '<span class="rec-dot' + (bad ? ' bad' : '') + '"></span>' +
          '<span class="rec-main"><b>' + esc(s.task || '（没写内容）') + '</b>' +
          '<span>' + esc(sub) + '</span></span>' +
          '<span class="rec-min">' + (s.actual_min || 0) + '′</span>' +
          '</' + tag + '>';
      }).join('');
    }

    var nextCard = $('nextCard');
    if (st.last_next_step) {
      nextCard.hidden = false;
      $('nextStepText').textContent = st.last_next_step;
      S.nextStep = st.last_next_step;
    } else {
      nextCard.hidden = true;
    }
  }

  function loadStats() {
    fetch('/focus/api/status', { credentials: 'same-origin' })
      .then(function (r) { return r.json().catch(function () { return null; }); })
      .then(function (d) { if (d && d.ok) { renderStats(d.stats); } })
      .catch(function () {});
  }

  /* ---------------- 视图切换 ---------------- */
  function setView(name) {
    S.view = name;
    var map = { setup: 'setupView', run: 'runView', done: 'doneView', break: 'breakView' };
    Object.keys(map).forEach(function (k) { $(map[k]).hidden = k !== name; });
    renderChip();
  }

  function setPhase(label, sub) {
    $('phaseLabel').textContent = label || '';
    $('phaseSub').textContent = sub || '';
  }

  function renderSetup() {
    setView('setup');
    setPhase('准备开始', '选一段时间，坐下就是赢一半。');
    $('setupMsg').textContent = '';
    $('taskIn').value = '';
    $('intentIn').value = '';
    S.source = null;
    renderChip();
    renderSources();
    document.title = '专注台';
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

  function drawTimer() {
    var run = S.run;
    if (!run) { return; }
    var left = remainingMs(run);
    $('timerText').textContent = fmt(left);
    var state = $('timerState');
    if (run.status === 'paused') {
      state.textContent = '已暂停';
      state.classList.add('paused');
    } else {
      state.textContent = '专注中';
      state.classList.remove('paused');
    }
    var total = run.plannedMs || 1;
    var offset = RING_LEN * (1 - left / total);
    $('ringFg').style.strokeDashoffset = offset.toFixed(2);
  }

  function renderInterruptions() {
    var run = S.run;
    if (!run) { return; }
    var list = run.interruptions || [];
    $('intrCount').textContent = list.length ? ('打断 ' + list.length + ' 次') : '';
    $('intrList').innerHTML = list.map(function (it) {
      var at = String(it.at || '').slice(11, 16);
      return '<div class="intr-item"><b>' + esc(at || '—') + '</b><span>' + esc(it.text) + '</span></div>';
    }).join('');
  }

  function renderRun() {
    var run = S.run;
    if (!run) { return; }
    setView('run');
    setPhase(run.status === 'paused' ? '已暂停' : '专注中',
      run.status === 'paused' ? '计时停住了，点「继续」接着走。' : '这段时间只做这一件事。');
    $('runTask').textContent = run.task;
    var intent = $('runIntent');
    intent.textContent = run.intent || '';
    intent.hidden = !run.intent;
    $('runMeta').textContent = run.plannedMin + ' 分钟 · 开始于 ' + hhmm(run.startedAt);
    $('pauseBtn').textContent = run.status === 'paused' ? '继续' : '暂停';
    drawTimer();
    renderInterruptions();
    document.title = fmt(remainingMs(run)) + (run.status === 'paused' ? ' · 已暂停' : '') + ' · ' + run.task;
  }

  function tick() {
    if (S.run && S.run.status === 'running') {
      var left = remainingMs(S.run);
      drawTimer();
      document.title = fmt(left) + ' · ' + S.run.task;
      if (left <= 0) { completeRun(); return; }
    }
    if (S.break) {
      var bleft = Math.max(0, S.break.ms - (Date.now() - S.break.startedAt));
      $('breakTimer').textContent = fmt(bleft);
      document.title = '休息 ' + fmt(bleft) + ' · 专注台';
      if (bleft <= 0) { breakDone(); }
    }
  }

  function startRun() {
    if (S.run || S.break) { return; }
    var task = clean($('taskIn').value, 120);
    if (!task) {
      toast('先写这段时间要做什么。', 'err');
      $('taskIn').focus();
      return;
    }
    var planned = clamp(parseInt($('customMin').value, 10) || S.pickedMin, 5, 180);
    S.pickedMin = planned;
    renderMinChips();
    savePrefs();
    if (S.source && task !== S.source.task) {
      clearSource(true);
      $('setupMsg').textContent = '任务改过了，已取消和目标动作的关联。';
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
      intent: clean($('intentIn').value, 200),
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
    history.replaceState(null, '', '/focus');
    unlockAudio();
    renderRun();
    toast('开始了。这段时间只做这一件事。');
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
    renderRun();
  }

  function completeRun() {
    var run = S.run;
    if (!run || run.status !== 'running') { return; }
    run.status = 'done';
    run.actualMs = run.plannedMs;
    run.actualMin = run.plannedMin;
    run.completedAt = Date.now();
    saveActive();
    chime('done');
    maybeNotify('专注结束', run.task);
    enterDone();
  }

  function finishEarly() {
    var run = S.run;
    if (!run || (run.status !== 'running' && run.status !== 'paused')) { return; }
    var ms = Math.min(elapsedMs(run), run.plannedMs);
    if (ms < 10000 && !confirm('还不到 10 秒，确定要记成完成吗？')) { return; }
    run.status = 'done';
    run.actualMs = ms;
    run.actualMin = Math.max(1, Math.round(ms / 60000));
    run.completedAt = Date.now();
    saveActive();
    chime('done');
    enterDone();
  }

  function abandonRun() {
    var run = S.run;
    if (!run || (run.status !== 'running' && run.status !== 'paused')) { return; }
    if (!confirm('放弃这一段？已经坐过的时间会保留，但不会把动作标成完成。')) { return; }
    run.status = 'abandoned';
    run.actualMs = elapsedMs(run);
    run.actualMin = Math.max(0, Math.round(run.actualMs / 60000));
    run.completedAt = Date.now();
    saveActive();
    enterDone();
  }

  function addInterruption() {
    var run = S.run;
    if (!run) { return; }
    var input = $('intrIn');
    var text = clean(input.value, 80);
    if (!text) { input.focus(); return; }
    run.interruptions = run.interruptions || [];
    run.interruptions.push({ text: text, at: toSql(Date.now()) });
    input.value = '';
    $('intrForm').hidden = true;
    saveActive();
    renderInterruptions();
    toast('记下了。计时没有停。');
  }

  /* ---------------- 完成 / 保存 ---------------- */
  function actualOf(run) {
    if (run.actualMin != null) { return run.actualMin; }
    return Math.max(0, Math.round((run.actualMs || 0) / 60000));
  }

  function renderWriteback() {
    var run = S.run;
    if (!run) { return; }
    var box = $('writebackBox');
    var src = run.source;
    if (!src || !src.rid || !src.aid) { box.hidden = true; return; }
    box.hidden = false;
    var actual = actualOf(run);
    var text;
    if (run.status === 'abandoned') {
      text = '这段按中断记录。' + (actual > 0
        ? '已经坐过的 ' + actual + ' 分钟仍会写回目标，但不会把动作标成完成。'
        : '不到 1 分钟，不会写回目标。');
    } else {
      text = '会写回「' + (src.goal_title || '目标') + '」里的这条动作：实际投入 +' + actual + ' 分钟。';
    }
    $('wbText').textContent = text;
    var mark = $('markDone');
    mark.disabled = run.status === 'abandoned';
    if (run.status === 'abandoned') { mark.checked = false; }
  }

  function enterDone() {
    var run = S.run;
    if (!run) { return; }
    var actual = actualOf(run);
    var bad = run.status === 'abandoned';
    setView('done');
    setPhase(bad ? '这一段中断了' : '这一段结束了',
      bad ? '中断也记一笔，下一次好判断是时间不够还是被别的事拉走。' : '把刚才实际发生的事记一句，下一段会更好开始。');
    $('doneIco').textContent = bad ? '!' : '✓';
    $('doneIco').className = 'done-ico' + (bad ? ' bad' : '');
    $('doneTitle').textContent = bad ? '先停在这里' : '这一段结束了';
    $('doneSub').textContent = bad
      ? '已经坐过 ' + actual + ' 分钟。保存后会按中断记，不打勾。'
      : (run.actualMin >= run.plannedMin ? '完整坐满 ' + run.plannedMin + ' 分钟。' : '提前完成，实际 ' + actual + ' 分钟。');
    $('doneActual').textContent = actual + ' 分钟';
    $('donePlanned').textContent = run.plannedMin + ' 分钟';
    $('doneIntr').textContent = (run.interruptions || []).length + ' 次';
    $('noteIn').value = run.note || '';
    $('nextIn').value = run.nextStep || '';
    $('saveMsg').textContent = '';
    $('saveSessionBtn').disabled = false;

    if (run.source && run.source.rid && run.source.aid) {
      $('writebackBox').hidden = false;
      $('markDone').checked = !bad && actual >= run.plannedMin * 0.8;
    } else {
      $('writebackBox').hidden = true;
    }
    renderWriteback();
    document.title = bad ? '这一段中断了 · 专注台' : '这一段结束了 · 专注台';
  }

  function saveSession() {
    var run = S.run;
    if (!run || S.saveBusy) { return; }
    S.saveBusy = true;
    $('saveSessionBtn').disabled = true;
    $('saveMsg').textContent = '保存中…';
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
      note: $('noteIn').value || '',
      next_step: $('nextIn').value || '',
      started_at: toSql(run.startedAt),
      ended_at: toSql(run.completedAt || Date.now()),
      client_id: run.id,
      mark_done: !!(run.source && run.source.rid && $('markDone').checked && status === 'completed')
    };
    fetch('/focus/api/sessions', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    }).then(function (r) {
      return r.json().catch(function () { return { ok: false, message: '服务返回看不懂的内容（HTTP ' + r.status + '）' }; });
    }).then(function (d) {
      S.saveBusy = false;
      $('saveSessionBtn').disabled = false;
      if (!d || !d.ok) {
        $('saveMsg').textContent = (d && d.message) || '没存上，再试一次。';
        toast((d && d.message) || '没存上，再试一次。', 'err');
        return;
      }
      var wb = d.writeback || null;
      if (d.stats) { renderStats(d.stats); }
      var savedNext = (d.item && d.item.next_step) || payload.next_step || '';
      S.nextStep = savedNext;
      clearActive();
      showSaved(run, actual, wb, !!d.duplicate);
      loadSources();
    }).catch(function () {
      S.saveBusy = false;
      $('saveSessionBtn').disabled = false;
      $('saveMsg').textContent = '网络断了，再点一次保存。';
      toast('网络断了，再点一次保存。', 'err');
    });
  }

  function showSaved(run, actual, wb, duplicate) {
    S.after = {
      breakMin: run.breakMin || 0,
      source: run.source || null,
      nextStep: run.nextStep || ''
    };
    S.source = null;
    setView('break');
    setPhase('已保存', '这段已经进记录，下一段可以更小一点。');
    $('breakIco').textContent = '✓';
    $('breakTitle').textContent = '这一段已经保存';
    $('breakSub').textContent = actual + ' 分钟已经记下。' +
      (run.breakMin > 0 ? '休息完再回来。' : '可以再来一段，也可以收工。');
    $('breakTimer').hidden = !(run.breakMin > 0);
    $('breakTimer').textContent = fmt((run.breakMin || 0) * 60000);
    $('breakStartBtn').hidden = !(run.breakMin > 0);
    $('breakStartBtn').textContent = '开始休息 ' + (run.breakMin || 0) + ' 分钟';
    $('breakSkipBtn').textContent = run.nextStep ? '按下一步再来一段' : '再来一段';
    var goal = $('afterGoalLink');
    if (run.source && run.source.rid) {
      goal.hidden = false;
      goal.href = '/goal-split?r=' + encodeURIComponent(run.source.rid);
    } else {
      goal.hidden = true;
    }
    var hint = duplicate ? '这段之前已经保存过，没有重复记账。' : '已记 ' + actual + ' 分钟。';
    if (wb && wb.ok) {
      hint += ' 已写回目标：实际投入 ' + (wb.actual || 0) + ' 分钟' +
        (wb.done ? '，动作已标完成。' : '。');
    } else if (wb && wb.ok === false) {
      hint += ' 目标没写成：' + (wb.message || '目标可能已经被删了') + '。';
    }
    if (run.nextStep) { hint += ' 下一段：' + run.nextStep; }
    $('savedHint').textContent = hint;
    document.title = '已保存 · 专注台';
  }

  function discardSession() {
    if (!confirm('丢掉这一段的记录？还没保存，丢掉就没了。')) { return; }
    clearActive();
    resetSetup(true);
    loadStats();
    toast('丢掉了。这一段没有进记录。');
  }

  /* ---------------- 休息 ---------------- */
  function startBreak() {
    if (S.break) { return; }
    var mins = S.after ? S.after.breakMin : 0;
    if (!mins) { goNext(true); return; }
    S.break = { startedAt: Date.now(), ms: mins * 60000 };
    writeJSON(BREAK_KEY, S.break);
    unlockAudio();
    S.view = 'break';
    setView('break');
    setPhase('休息中', '计时在走，离开屏幕也行。');
    $('breakIco').textContent = '☕';
    $('breakTitle').textContent = '休息中';
    $('breakSub').textContent = '离开屏幕，站起来走两步。休息不算浪费，是下一段的本钱。';
    $('breakTimer').hidden = false;
    $('breakStartBtn').hidden = true;
    $('breakSkipBtn').textContent = '跳过休息';
    $('afterGoalLink').hidden = true;
    $('savedHint').textContent = '';
    document.title = '休息 ' + fmt(S.break.ms) + ' · 专注台';
  }

  function breakDone() {
    if (!S.break) { return; }
    clearBreak();
    chime('break');
    maybeNotify('休息结束', '回来吧，下一段从一个更小的地方开始。');
    S.view = 'break';
    setView('break');
    setPhase('休息结束', '回来吧，下一段从一个更小的地方开始。');
    $('breakIco').textContent = '☀';
    $('breakTitle').textContent = '休息结束';
    $('breakSub').textContent = '回来吧，下一段从一个更小的地方开始。';
    $('breakTimer').hidden = false;
    $('breakTimer').textContent = '00:00';
    $('breakStartBtn').hidden = true;
    $('breakSkipBtn').textContent = '再来一段';
    $('savedHint').textContent = S.nextStep ? ('下一段：' + S.nextStep) : '';
    document.title = '休息结束 · 专注台';
  }

  function skipBreak() {
    var running = !!S.break;
    clearBreak();
    if (running) { toast('跳过休息，直接开始下一段。'); }
    goNext(true);
  }

  /* ---------------- 回到准备 ---------------- */
  function resetSetup(prefillNext) {
    var next = prefillNext && S.nextStep ? S.nextStep : '';
    S.source = null;
    S.after = null;
    setView('setup');
    setPhase('准备开始', '选一段时间，坐下就是赢一半。');
    $('taskIn').value = next;
    $('intentIn').value = '';
    $('setupMsg').textContent = next ? '下一步已经填好，直接开始就行。' : '';
    $('intrForm').hidden = true;
    $('intrList').innerHTML = '';
    renderMinChips();
    renderBreakChips();
    renderChip();
    renderSources();
    document.title = '专注台';
    if (next) { $('taskIn').focus(); }
    else { window.scrollTo({ top: 0, behavior: 'smooth' }); }
  }

  function goNext(prefillNext) {
    clearActive();
    clearBreak();
    resetSetup(prefillNext);
  }

  function useNext() {
    if (S.run || S.break) { toast('先结束现在这一段。'); return; }
    var text = $('nextStepText').textContent || '';
    if (!text) { return; }
    setView('setup');
    $('taskIn').value = text;
    $('intentIn').value = '';
    S.source = null;
    renderChip();
    renderSources();
    $('taskIn').focus();
    toast('下一步填好了，选个时长就能开始。');
  }

  /* ---------------- 载入未完成状态 ---------------- */
  function loadActive() {
    var o = readJSON(ACTIVE_KEY);
    if (!o || !o.startedAt || !o.plannedMs || !o.task) {
      try { localStorage.removeItem(ACTIVE_KEY); } catch (e) {}
      return false;
    }
    S.run = o;
    if (o.status === 'done' || o.status === 'abandoned') {
      if (o.actualMin == null) {
        o.actualMin = o.status === 'abandoned'
          ? Math.max(0, Math.round((o.actualMs || 0) / 60000))
          : o.plannedMin;
      }
      enterDone();
      toast('上次那段还没保存，已经帮你找回来了。');
      return true;
    }
    if (o.status === 'paused') {
      renderRun();
      return true;
    }
    // running：如果离开期间已经超过计划，直接按自然完成收尾
    if (remainingMs(o) <= 0) {
      completeRun();
      toast('上次那段其实已经到时间了，按完成算。');
      return true;
    }
    renderRun();
    toast('上次那段还在，接着走。');
    return true;
  }

  function loadBreak() {
    var b = readJSON(BREAK_KEY);
    if (!b || !b.startedAt || !b.ms) {
      try { localStorage.removeItem(BREAK_KEY); } catch (e) {}
      return false;
    }
    var left = b.ms - (Date.now() - b.startedAt);
    if (left <= 0) {
      clearBreak();
      toast('上一段休息已经结束了。');
      return false;
    }
    S.break = b;
    S.after = { breakMin: Math.round(b.ms / 60000), source: null, nextStep: S.nextStep };
    startBreakViewFromStorage();
    return true;
  }

  function startBreakViewFromStorage() {
    setView('break');
    setPhase('休息中', '计时在走，离开屏幕也行。');
    $('breakIco').textContent = '☕';
    $('breakTitle').textContent = '休息中';
    $('breakSub').textContent = '离开屏幕，站起来走两步。休息不算浪费，是下一段的本钱。';
    $('breakTimer').hidden = false;
    $('breakStartBtn').hidden = true;
    $('breakSkipBtn').textContent = '跳过休息';
    $('afterGoalLink').hidden = true;
    $('savedHint').textContent = '';
    tick();
  }

  /* ---------------- 深链 ---------------- */
  function applyDeepLink() {
    var q;
    try { q = new URLSearchParams(location.search); }
    catch (e) { return; }
    var task = clean(q.get('task'), 120);
    var out = clean(q.get('out') || q.get('intent'), 200);
    var min = parseInt(q.get('min'), 10);
    var rid = parseInt(q.get('rid'), 10);
    var aid = clean(q.get('aid'), 24);
    if (min >= 5 && min <= 180) { S.pickedMin = min; }
    if (task) { $('taskIn').value = task; }
    if (out) { $('intentIn').value = out; }
    if (task || out || rid || aid) {
      S.deepLink = true;
      $('setupMsg').textContent = '已经从上一个工具带过来了，确认一下就能开始。';
    }
    if (rid && aid) {
      S.source = {
        rid: rid,
        aid: aid,
        task: task,
        out: out,
        min: min >= 5 && min <= 180 ? min : S.pickedMin,
        goal_title: clean(q.get('goal'), 120)
      };
      renderChip();
    }
    renderMinChips();
  }

  /* ---------------- 事件绑定 ---------------- */
  function bind() {
    $('startBtn').onclick = startRun;
    $('pauseBtn').onclick = togglePause;
    $('finishBtn').onclick = finishEarly;
    $('abandonBtn').onclick = abandonRun;
    $('intrBtn').onclick = function () {
      var f = $('intrForm');
      f.hidden = !f.hidden;
      if (!f.hidden) { $('intrIn').focus(); }
    };
    $('intrSave').onclick = addInterruption;
    $('intrIn').addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); addInterruption(); }
    });
    $('saveSessionBtn').onclick = saveSession;
    $('discardBtn').onclick = discardSession;
    $('breakStartBtn').onclick = startBreak;
    $('breakSkipBtn').onclick = skipBreak;
    $('nextUseBtn').onclick = useNext;
    $('srcRefresh').onclick = function () { loadSources(); loadStats(); toast('刷新了今天的目标动作。'); };
    $('clearSourceBtn').onclick = function () { clearSource(false); };
    $('taskIn').addEventListener('input', function () {
      if (!S.source) { return; }
      var value = clean($('taskIn').value, 120);
      // 深链只带了目标、没带动作文字时，第一次输入就把动作文字补上，别一打字就断开关联
      if (!S.source.task) { S.source.task = value; return; }
      if (value !== S.source.task) {
        clearSource(true);
        if (S.view === 'setup') { $('setupMsg').textContent = '任务改过了，已取消和目标动作的关联。'; }
      }
    });
    $('customMin').addEventListener('change', function () {
      var n = clamp(parseInt(this.value, 10) || 25, 5, 180);
      S.pickedMin = n;
      this.value = n;
      renderMinChips();
      savePrefs();
    });
    $('notifyToggle').addEventListener('change', function () {
      var on = this.checked;
      if (!on) { S.notify = false; savePrefs(); return; }
      if (!('Notification' in window)) {
        this.checked = false;
        toast('这个浏览器不支持系统通知。', 'err');
        return;
      }
      if (Notification.permission === 'denied') {
        this.checked = false;
        toast('浏览器已经拒绝通知，去地址栏旁边改一下权限。', 'err');
        return;
      }
      if (Notification.permission === 'granted') {
        S.notify = true; savePrefs(); return;
      }
      Notification.requestPermission().then(function (p) {
        S.notify = p === 'granted';
        $('notifyToggle').checked = S.notify;
        savePrefs();
        if (!S.notify) { toast('没拿到通知权限，响铃仍然会在页面里响。'); }
      });
    });
    $('noteIn').addEventListener('input', function () {
      if (S.run) { S.run.note = this.value; saveActive(); }
    });
    $('nextIn').addEventListener('input', function () {
      if (S.run) { S.run.nextStep = this.value; saveActive(); }
    });

    document.addEventListener('visibilitychange', function () {
      if (!document.hidden) { tick(); }
    });
    window.addEventListener('pageshow', function (e) {
      if (e.persisted) { tick(); }
    });
    document.addEventListener('pointerdown', unlockAudio, { passive: true });
  }

  function init() {
    loadPrefs();
    renderMinChips();
    renderBreakChips();
    var nt = $('notifyToggle');
    if (nt) { nt.checked = S.notify; }
    bind();

    var active = loadActive();
    if (!active) {
      applyDeepLink();
      var broke = loadBreak();
      if (!broke) {
        setView('setup');
        document.title = '专注台';
      }
    } else {
      // 有正在进行/待保存的段时，旧的休息状态没有意义
      clearBreak();
    }

    loadStats();
    loadSources();
    setInterval(tick, 250);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
