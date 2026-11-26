/* 任务看板：三列展示、拖拽 / 按钮换状态、弹窗新建编辑、两步删除。
   所有用户内容都用 textContent 写入，不拼 innerHTML。 */
(function () {
  'use strict';

  var board = document.getElementById('board');
  var newBtn = document.getElementById('newBtn');
  var statEl = document.getElementById('stat');
  var editor = document.getElementById('editor');
  var form = document.getElementById('form');
  var editorTitle = document.getElementById('editorTitle');
  var fTitle = document.getElementById('fTitle');
  var fNote = document.getElementById('fNote');
  var fStatus = document.getElementById('fStatus');
  var fPriority = document.getElementById('fPriority');
  var fDue = document.getElementById('fDue');
  var fErr = document.getElementById('fErr');
  var editorDay = document.getElementById('editorDay');
  var dayPick = document.getElementById('dayPick');
  var dayPrev = document.getElementById('dayPrev');
  var dayNext = document.getElementById('dayNext');
  var dayToday = document.getElementById('dayToday');
  var dayHint = document.getElementById('dayHint');
  var saveBtn = document.getElementById('saveBtn');
  var cancelBtn = document.getElementById('cancelBtn');
  var toastEl = document.getElementById('toast');

  var meta = { statuses: [], priorities: [], titleMax: 120, noteMax: 1000, limit: 500 };
  var tasks = [];
  var dayTasks = [];
  var order = [];
  var editing = null;
  var busy = {};
  var toastTimer = null;
  var selectedDay = initialDay();

  newBtn.disabled = true;
  syncDayUI();

  function api(path, options) {
    var opts = options || {};
    var headers = { Accept: 'application/json' };
    if (opts.body) { headers['Content-Type'] = 'application/json'; }
    return fetch(path, {
      method: opts.method || 'GET',
      headers: headers,
      body: opts.body || null,
      credentials: 'same-origin'
    }).then(function (res) {
      return res.text().then(function (text) {
        var data = null;
        try { data = text ? JSON.parse(text) : {}; } catch (e) { data = null; }
        if (!res.ok || !data || data.ok === false) {
          var msg = data && data.message
            ? data.message
            : (res.status === 401 || res.status === 403
              ? '登录已失效，请刷新页面后重试。'
              : '请求失败（' + res.status + '）');
          throw new Error(msg);
        }
        return data;
      });
    });
  }

  function toast(message, bad) {
    toastEl.textContent = message;
    toastEl.hidden = false;
    toastEl.classList.toggle('bad', !!bad);
    window.requestAnimationFrame(function () { toastEl.classList.add('show'); });
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(function () {
      toastEl.classList.remove('show');
      window.setTimeout(function () { toastEl.hidden = true; }, 200);
    }, 2400);
  }

  function labelOf(key) {
    for (var i = 0; i < meta.statuses.length; i++) {
      if (meta.statuses[i].k === key) { return meta.statuses[i].n; }
    }
    return key;
  }

  function priorityLabel(key) {
    for (var i = 0; i < meta.priorities.length; i++) {
      if (meta.priorities[i].k === key) { return meta.priorities[i].n; }
    }
    return key;
  }

  function fmtDay(d) {
    var m = String(d.getMonth() + 1);
    var day = String(d.getDate());
    if (m.length < 2) { m = '0' + m; }
    if (day.length < 2) { day = '0' + day; }
    return d.getFullYear() + '-' + m + '-' + day;
  }

  function todayStr() { return fmtDay(new Date()); }

  function validDay(text) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) { return false; }
    var d = new Date(text + 'T00:00:00');
    return !isNaN(d.getTime()) && fmtDay(d) === text;
  }

  function initialDay() {
    var fromUrl = '';
    try { fromUrl = new URLSearchParams(window.location.search).get('day') || ''; } catch (e) {}
    return validDay(fromUrl) ? fromUrl : todayStr();
  }

  function shiftDay(day, delta) {
    var d = new Date(day + 'T00:00:00');
    if (isNaN(d.getTime())) { d = new Date(); }
    d.setDate(d.getDate() + delta);
    return fmtDay(d);
  }

  function relativeDay(day) {
    var today = todayStr();
    if (day === today) { return '今天'; }
    if (day === shiftDay(today, -1)) { return '昨天'; }
    if (day === shiftDay(today, 1)) { return '明天'; }
    return '';
  }

  function weekdayOf(day) {
    var names = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
    var d = new Date(day + 'T00:00:00');
    return isNaN(d.getTime()) ? '' : names[d.getDay()];
  }

  function taskDay(task) {
    return task.day || (task.createdAt || '').slice(0, 10);
  }

  function syncDayUI() {
    dayPick.value = selectedDay;
    dayToday.disabled = selectedDay === todayStr();
    var rel = relativeDay(selectedDay);
    var week = weekdayOf(selectedDay);
    dayHint.textContent = rel ? (week ? rel + ' · ' + week : rel) : week;
    try {
      var url = new URL(window.location.href);
      url.searchParams.set('day', selectedDay);
      window.history.replaceState(null, '', url.toString());
    } catch (e) {}
  }

  function setDay(day) {
    selectedDay = validDay(day) ? day : todayStr();
    syncDayUI();
    render();
  }

  function sortTasks(list, status) {
    var rank = { high: 0, normal: 1, low: 2 };
    return list.slice().sort(function (a, b) {
      if (status === 'done') {
        var ta = a.doneAt || a.updatedAt || a.createdAt || '';
        var tb = b.doneAt || b.updatedAt || b.createdAt || '';
        if (ta !== tb) { return ta < tb ? 1 : -1; }
        return (b.id || 0) - (a.id || 0);
      }
      var pa = rank[a.priority] == null ? 1 : rank[a.priority];
      var pb = rank[b.priority] == null ? 1 : rank[b.priority];
      if (pa !== pb) { return pa - pb; }
      var da = a.due || '9999-99-99';
      var db = b.due || '9999-99-99';
      if (da !== db) { return da < db ? -1 : 1; }
      var ua = a.updatedAt || a.createdAt || '';
      var ub = b.updatedAt || b.createdAt || '';
      if (ua !== ub) { return ua < ub ? 1 : -1; }
      return (b.id || 0) - (a.id || 0);
    });
  }

  function setStat() {
    if (!dayTasks.length) {
      statEl.textContent = '这一天还没有任务';
      return;
    }
    var done = 0;
    for (var i = 0; i < dayTasks.length; i++) {
      if (dayTasks[i].status === 'done') { done += 1; }
    }
    statEl.textContent = '共 ' + dayTasks.length + ' 项 · 已完成 ' + done + ' 项';
  }

  function metaChip(text, cls) {
    var el = document.createElement('span');
    el.className = 'chip' + (cls ? ' ' + cls : '');
    el.textContent = text;
    return el;
  }

  function makeCard(task) {
    var card = document.createElement('article');
    card.className = 'task p-' + task.priority + (task.status === 'done' ? ' is-done' : '');
    card.draggable = true;
    card.tabIndex = 0;
    card.dataset.id = String(task.id);
    card.setAttribute('aria-label', task.title + '，' + labelOf(task.status));

    var title = document.createElement('h3');
    title.textContent = task.title;
    card.appendChild(title);

    if (task.note) {
      var note = document.createElement('p');
      note.className = 'note';
      note.textContent = task.note;
      card.appendChild(note);
    }

    var metaRow = document.createElement('div');
    metaRow.className = 'meta';
    if (task.priority === 'high') {
      metaRow.appendChild(metaChip('高优先级', 'pri-high'));
    } else if (task.priority === 'low') {
      metaRow.appendChild(metaChip('低优先级', 'pri-low'));
    }
    if (task.due) {
      var late = task.status !== 'done' && task.due < todayStr();
      var chip = document.createElement('span');
      chip.className = 'chip due' + (late ? ' late' : '');
      var sameYear = task.due.slice(0, 4) === todayStr().slice(0, 4);
      chip.textContent = (late ? '已逾期 ' : '截止 ') + (sameYear ? task.due.slice(5) : task.due);
      chip.title = (late ? '已逾期：' : '截止日期：') + task.due;
      metaRow.appendChild(chip);
    }
    if (metaRow.childNodes.length) { card.appendChild(metaRow); }

    var acts = document.createElement('div');
    acts.className = 'acts';

    var idx = order.indexOf(task.status);
    var left = document.createElement('button');
    left.type = 'button';
    left.className = 'icon-btn';
    left.textContent = '←';
    left.title = '移到上一列';
    left.setAttribute('aria-label', '把「' + task.title + '」移到上一列');
    left.disabled = idx <= 0;
    left.addEventListener('click', function () {
      if (idx > 0) { moveTask(task, order[idx - 1]); }
    });
    acts.appendChild(left);

    var right = document.createElement('button');
    right.type = 'button';
    right.className = 'icon-btn';
    right.textContent = '→';
    right.title = '移到下一列';
    right.setAttribute('aria-label', '把「' + task.title + '」移到下一列');
    right.disabled = idx < 0 || idx >= order.length - 1;
    right.addEventListener('click', function () {
      if (idx >= 0 && idx < order.length - 1) { moveTask(task, order[idx + 1]); }
    });
    acts.appendChild(right);

    var edit = document.createElement('button');
    edit.type = 'button';
    edit.className = 'icon-btn';
    edit.textContent = '✎';
    edit.title = '编辑';
    edit.setAttribute('aria-label', '编辑「' + task.title + '」');
    edit.addEventListener('click', function () { openEditor(task); });
    acts.appendChild(edit);

    var del = document.createElement('button');
    del.type = 'button';
    del.className = 'icon-btn';
    del.textContent = '×';
    del.title = '删除';
    del.setAttribute('aria-label', '删除「' + task.title + '」');
    del.addEventListener('click', function () { askDelete(del, task); });
    acts.appendChild(del);

    card.appendChild(acts);

    card.addEventListener('dragstart', function (e) {
      if (!e.dataTransfer) { return; }
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', String(task.id));
      card.classList.add('dragging');
    });
    card.addEventListener('dragend', function () {
      card.classList.remove('dragging');
    });
    card.addEventListener('keydown', function (e) {
      if (e.target === card && e.key === 'Enter') {
        e.preventDefault();
        openEditor(task);
      }
    });

    return card;
  }

  function makeColumn(def) {
    var col = document.createElement('section');
    col.className = 'col status-' + def.k;

    var head = document.createElement('div');
    head.className = 'col-head';

    var dot = document.createElement('span');
    dot.className = 'dot';
    dot.setAttribute('aria-hidden', 'true');
    head.appendChild(dot);

    var name = document.createElement('span');
    name.className = 'col-title';
    name.textContent = def.n;
    head.appendChild(name);

    var list = sortTasks(dayTasks.filter(function (t) { return t.status === def.k; }), def.k);

    var count = document.createElement('span');
    count.className = 'count';
    count.textContent = String(list.length);
    head.appendChild(count);

    var add = document.createElement('button');
    add.type = 'button';
    add.className = 'col-add';
    add.textContent = '＋';
    add.title = '在「' + def.n + '」新建任务';
    add.setAttribute('aria-label', '在「' + def.n + '」新建任务');
    add.addEventListener('click', function () { openEditor(null, def.k); });
    head.appendChild(add);

    col.appendChild(head);

    var body = document.createElement('div');
    body.className = 'col-body';
    body.dataset.status = def.k;

    if (list.length) {
      for (var i = 0; i < list.length; i++) {
        body.appendChild(makeCard(list[i]));
      }
    } else {
      var empty = document.createElement('div');
      empty.className = 'empty';
      empty.textContent = '暂无任务';
      body.appendChild(empty);
    }

    body.addEventListener('dragover', function (e) {
      e.preventDefault();
      if (e.dataTransfer) { e.dataTransfer.dropEffect = 'move'; }
      body.classList.add('dragover');
    });
    body.addEventListener('dragleave', function (e) {
      if (!body.contains(e.relatedTarget)) { body.classList.remove('dragover'); }
    });
    body.addEventListener('drop', function (e) {
      e.preventDefault();
      body.classList.remove('dragover');
      var id = parseInt(e.dataTransfer ? e.dataTransfer.getData('text/plain') : '', 10);
      if (!id) { return; }
      var task = null;
      for (var j = 0; j < tasks.length; j++) {
        if (tasks[j].id === id) { task = tasks[j]; break; }
      }
      if (task && task.status !== def.k) { moveTask(task, def.k); }
    });

    col.appendChild(body);
    return col;
  }

  function render() {
    dayTasks = tasks.filter(function (t) { return taskDay(t) === selectedDay; });
    board.textContent = '';
    for (var i = 0; i < meta.statuses.length; i++) {
      board.appendChild(makeColumn(meta.statuses[i]));
    }
    setStat();
  }

  function replaceTask(item) {
    for (var i = 0; i < tasks.length; i++) {
      if (tasks[i].id === item.id) {
        tasks[i] = item;
        return;
      }
    }
  }

  function moveTask(task, status) {
    if (!task || busy[task.id] || !status || task.status === status) { return; }
    var prev = task.status;
    busy[task.id] = true;
    task.status = status;
    render();
    api('/kanban/api/tasks/' + task.id, {
      method: 'PATCH',
      body: JSON.stringify({ patch: { status: status } })
    }).then(function (data) {
      delete busy[task.id];
      replaceTask(data.item);
      render();
      toast('已移到「' + labelOf(status) + '」');
    }).catch(function (err) {
      delete busy[task.id];
      task.status = prev;
      render();
      toast(err.message, true);
    });
  }

  function showFormError(message) {
    fErr.textContent = message;
    fErr.hidden = false;
  }

  function fillSelects() {
    fStatus.textContent = '';
    meta.statuses.forEach(function (item) {
      var opt = document.createElement('option');
      opt.value = item.k;
      opt.textContent = item.n;
      fStatus.appendChild(opt);
    });
    fPriority.textContent = '';
    meta.priorities.forEach(function (item) {
      var opt = document.createElement('option');
      opt.value = item.k;
      opt.textContent = item.n;
      fPriority.appendChild(opt);
    });
  }

  function openEditor(task, presetStatus) {
    if (!meta.statuses.length) {
      toast('还在加载，稍等一下。', true);
      return;
    }
    editing = task || null;
    editorTitle.textContent = task ? '编辑任务' : '新建任务';
    fTitle.value = task ? task.title : '';
    fNote.value = task ? (task.note || '') : '';
    fStatus.value = task ? task.status : (presetStatus || 'todo');
    fPriority.value = task ? task.priority : 'normal';
    fDue.value = task ? (task.due || '') : '';
    editorDay.hidden = !!task;
    editorDay.textContent = task ? '' : '任务会记在 ' + selectedDay + '。';
    fErr.hidden = true;
    fErr.textContent = '';
    saveBtn.disabled = false;
    saveBtn.textContent = '保存';
    if (!editor.open) { editor.showModal(); }
    window.setTimeout(function () { fTitle.focus(); fTitle.select(); }, 30);
  }

  function askDelete(btn, task) {
    if (btn.dataset.armed !== '1') {
      btn.dataset.armed = '1';
      btn.classList.add('confirm');
      btn.textContent = '确认删除';
      btn.title = '再点一次删除';
      window.clearTimeout(btn._t);
      btn._t = window.setTimeout(function () {
        if (!btn.isConnected) { return; }
        btn.dataset.armed = '';
        btn.classList.remove('confirm');
        btn.textContent = '×';
        btn.title = '删除';
      }, 3000);
      return;
    }
    window.clearTimeout(btn._t);
    btn.disabled = true;
    api('/kanban/api/tasks/' + task.id, { method: 'DELETE' }).then(function () {
      tasks = tasks.filter(function (t) { return t.id !== task.id; });
      render();
      toast('已删除');
    }).catch(function (err) {
      btn.disabled = false;
      toast(err.message, true);
    });
  }

  function showBoardError(message) {
    board.textContent = '';
    var box = document.createElement('div');
    box.className = 'board-error';
    var text = document.createElement('div');
    text.textContent = message || '加载失败。';
    box.appendChild(text);
    var retry = document.createElement('button');
    retry.type = 'button';
    retry.className = 'btn sm';
    retry.style.marginTop = '10px';
    retry.textContent = '重试';
    retry.addEventListener('click', function () { load(); });
    box.appendChild(retry);
    board.appendChild(box);
  }

  function load() {
    board.textContent = '';
    var loading = document.createElement('div');
    loading.className = 'loading';
    loading.textContent = '加载中……';
    board.appendChild(loading);
    api('/kanban/api/tasks').then(function (data) {
      meta.statuses = Array.isArray(data.statuses) ? data.statuses : [];
      meta.priorities = Array.isArray(data.priorities) ? data.priorities : [];
      meta.titleMax = data.titleMax || 120;
      meta.noteMax = data.noteMax || 1000;
      meta.limit = data.limit || 500;
      tasks = Array.isArray(data.items) ? data.items : [];
      order = meta.statuses.map(function (s) { return s.k; });
      fTitle.maxLength = meta.titleMax;
      fNote.maxLength = meta.noteMax;
      fillSelects();
      newBtn.disabled = false;
      syncDayUI();
      render();
    }).catch(function (err) {
      showBoardError(err.message);
    });
  }

  newBtn.addEventListener('click', function () { openEditor(null, 'todo'); });
  dayPick.addEventListener('change', function () { setDay(dayPick.value); });
  dayPrev.addEventListener('click', function () { setDay(shiftDay(selectedDay, -1)); });
  dayNext.addEventListener('click', function () { setDay(shiftDay(selectedDay, 1)); });
  dayToday.addEventListener('click', function () { setDay(todayStr()); });
  cancelBtn.addEventListener('click', function () { editor.close(); });
  editor.addEventListener('close', function () { editing = null; });
  editor.addEventListener('click', function (e) {
    if (e.target === editor) { editor.close(); }
  });

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var title = fTitle.value.trim();
    if (!title) {
      showFormError('给任务起个名字。');
      fTitle.focus();
      return;
    }
    if (title.length > meta.titleMax) {
      showFormError('标题太长了，控制在 ' + meta.titleMax + ' 个字以内。');
      fTitle.focus();
      return;
    }
    if (fNote.value.length > meta.noteMax) {
      showFormError('备注太长了，控制在 ' + meta.noteMax + ' 个字以内。');
      fNote.focus();
      return;
    }
    var payload = {
      title: title,
      note: fNote.value,
      status: fStatus.value,
      priority: fPriority.value,
      due: fDue.value || ''
    };
    var wasEditing = !!editing;
    if (!wasEditing) { payload.day = selectedDay; }
    saveBtn.disabled = true;
    saveBtn.textContent = '保存中…';
    fErr.hidden = true;

    var request = editing
      ? api('/kanban/api/tasks/' + editing.id, {
        method: 'PATCH',
        body: JSON.stringify({ patch: payload })
      })
      : api('/kanban/api/tasks', {
        method: 'POST',
        body: JSON.stringify(payload)
      });

    request.then(function (data) {
      if (wasEditing) { replaceTask(data.item); }
      else { tasks.unshift(data.item); }
      editor.close();
      render();
      toast(wasEditing ? '已保存' : '已新建');
    }).catch(function (err) {
      saveBtn.disabled = false;
      saveBtn.textContent = '保存';
      showFormError(err.message);
    });
  });

  load();
})();
