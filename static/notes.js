(function () {
  'use strict';

  var LEVEL_NAMES = ['普通', '重要', '关键'];

  var state = {
    day: todayStr(),
    view: 'notes',
    q: '',
    tag: '',
    level: '',
    status: null,
    limits: {
      contentMax: 10000,
      tagsMax: 10,
      tagMax: 20,
      imagesPerNote: 9,
      imageMaxBytes: 10 * 1024 * 1024,
      totalBytes: 2048 * 1024 * 1024
    },
    notes: [],
    queue: [],
    edit: null,
    light: null,
    saving: false,
    loadSeq: 0,
    hmMonth: '',
    hmSeq: 0,
    searchTimer: 0,
    draftTimer: 0,
    draftHintTimer: 0,
    toastTimer: 0,
    analysis: {
      period: 'day',
      date: todayStr(),
      data: null,
      loading: false,
      generating: false,
      error: '',
      seq: 0
    }
  };

  function $(id) {
    return document.getElementById(id);
  }

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function pad(n) {
    return n < 10 ? '0' + n : String(n);
  }

  function levelName(value) {
    var index = Number(value);
    return LEVEL_NAMES[index] || LEVEL_NAMES[0];
  }

  function syncLevelSelect() {
    var select = $('levelSelect');
    if (select) select.setAttribute('data-level', select.value || '0');
  }

  function todayStr() {
    var d = new Date();
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
  }

  function parseDay(text) {
    var parts = String(text || '').split('-');
    if (parts.length !== 3) return null;
    var y = Number(parts[0]);
    var m = Number(parts[1]);
    var d = Number(parts[2]);
    if (!y || !m || !d) return null;
    return new Date(Date.UTC(y, m - 1, d, 12, 0, 0));
  }

  function dayStr(d) {
    return d.getUTCFullYear() + '-' + pad(d.getUTCMonth() + 1) + '-' + pad(d.getUTCDate());
  }

  function isDay(text) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(String(text || ''))) return false;
    var d = parseDay(text);
    return !!d && dayStr(d) === String(text);
  }

  function shiftDay(text, delta) {
    var d = parseDay(text);
    if (!d) return todayStr();
    d.setUTCDate(d.getUTCDate() + delta);
    return dayStr(d);
  }

  function monthOf(text) {
    var value = String(text || '');
    return /^\d{4}-\d{2}/.test(value) ? value.slice(0, 7) : '';
  }

  function shiftMonth(month, delta) {
    if (!/^\d{4}-\d{2}$/.test(month)) return monthOf(todayStr());
    var total = Number(month.slice(0, 4)) * 12 + Number(month.slice(5, 7)) - 1 + delta;
    var year = Math.floor(total / 12);
    var mon = ((total % 12) + 12) % 12 + 1;
    return year + '-' + pad(mon);
  }

  function monthLabel(month) {
    return Number(month.slice(0, 4)) + '年' + Number(month.slice(5, 7)) + '月';
  }

  function weekdayName(d) {
    return ['日', '一', '二', '三', '四', '五', '六'][d.getUTCDay()];
  }

  function dayLong(text) {
    var d = parseDay(text);
    if (!d) return text || '';
    var now = new Date();
    var label = (d.getUTCMonth() + 1) + '月' + d.getUTCDate() + '日 周' + weekdayName(d);
    if (d.getUTCFullYear() !== now.getFullYear()) label = d.getUTCFullYear() + '年' + label;
    return label;
  }

  function dayShort(text) {
    if (text === todayStr()) return '今天';
    if (text === shiftDay(todayStr(), -1)) return '昨天';
    var d = parseDay(text);
    if (!d) return text || '';
    var now = new Date();
    var prefix = d.getUTCFullYear() === now.getFullYear() ? '' : d.getUTCFullYear() + '/';
    return prefix + pad(d.getUTCMonth() + 1) + '-' + pad(d.getUTCDate());
  }

  function periodName(period) {
    return period === 'week' ? '周' : period === 'month' ? '月' : '日';
  }

  function analysisAnchorLabel(period, anchor) {
    var d = parseDay(anchor);
    if (!d) return dayLong(anchor);
    if (period === 'month') {
      return d.getUTCFullYear() + '年' + (d.getUTCMonth() + 1) + '月';
    }
    if (period === 'week') {
      var start = new Date(d.getTime());
      start.setUTCDate(start.getUTCDate() - ((start.getUTCDay() + 6) % 7));
      var end = new Date(start.getTime());
      end.setUTCDate(end.getUTCDate() + 6);
      var head = start.getUTCFullYear() + '年' +
        (start.getUTCMonth() + 1) + '月' + start.getUTCDate() + '日';
      var tail = end.getUTCFullYear() === start.getUTCFullYear()
        ? (end.getUTCMonth() + 1) + '月' + end.getUTCDate() + '日'
        : end.getUTCFullYear() + '年' + (end.getUTCMonth() + 1) + '月' + end.getUTCDate() + '日';
      return head + ' - ' + tail;
    }
    return dayLong(anchor);
  }

  function shiftPeriod(period, anchor, delta) {
    var d = parseDay(anchor);
    if (!d) return todayStr();
    if (period === 'month') {
      d.setUTCDate(1);
      d.setUTCMonth(d.getUTCMonth() + delta);
      return dayStr(d);
    }
    d.setUTCDate(d.getUTCDate() + (period === 'week' ? delta * 7 : delta));
    return dayStr(d);
  }

  function formatBytes(bytes) {
    bytes = Number(bytes || 0);
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    return (bytes / (1024 * 1024 * 1024)).toFixed(2) + ' GB';
  }

  function toast(message, bad) {
    var box = $('toast');
    if (!box) return;
    box.hidden = false;
    box.textContent = String(message || '');
    box.classList.toggle('bad', !!bad);
    box.classList.add('show');
    clearTimeout(state.toastTimer);
    state.toastTimer = setTimeout(function () {
      box.classList.remove('show');
      setTimeout(function () { box.hidden = true; }, 220);
    }, bad ? 4200 : 2600);
  }

  function api(url, options) {
    var opts = options || {};
    var headers = { 'Accept': 'application/json' };
    if (opts.headers) {
      for (var key in opts.headers) {
        if (Object.prototype.hasOwnProperty.call(opts.headers, key)) headers[key] = opts.headers[key];
      }
    }
    return fetch(url, {
      method: opts.method || 'GET',
      credentials: 'same-origin',
      headers: headers,
      body: opts.body || undefined
    }).then(function (resp) {
      if (resp.status === 401) {
        location.href = '/login';
        throw new Error('登录已失效，请重新登录。');
      }
      return resp.text().then(function (text) {
        var data = {};
        if (text) {
          try { data = JSON.parse(text); } catch (e) { data = {}; }
        }
        if (!resp.ok || !data.ok) {
          var err = new Error(data.message || data.msg || data.detail || '请求失败（HTTP ' + resp.status + '）。');
          err.status = resp.status;
          throw err;
        }
        return data;
      });
    }).catch(function (err) {
      if (err && err.message) throw err;
      throw new Error('网络连接失败，请稍后再试。');
    });
  }

  function uploadRequest(url, method, form, onProgress) {
    return new Promise(function (resolve, reject) {
      var xhr = new XMLHttpRequest();
      xhr.open(method, url, true);
      xhr.timeout = 180000;
      xhr.responseType = 'text';
      if (xhr.upload && onProgress) {
        xhr.upload.onprogress = function (e) {
          if (e.lengthComputable) onProgress(Math.round(e.loaded * 100 / e.total));
        };
      }
      xhr.onload = function () {
        var data = {};
        try { data = JSON.parse(xhr.responseText || '{}'); } catch (e) { data = {}; }
        if (xhr.status >= 200 && xhr.status < 300 && data.ok) {
          resolve(data);
          return;
        }
        if (xhr.status === 401) {
          location.href = '/login';
          reject(new Error('登录已失效，请重新登录。'));
          return;
        }
        reject(new Error(data.message || data.detail || '保存失败（HTTP ' + xhr.status + '）。'));
      };
      xhr.onerror = function () {
        reject(new Error('网络连接失败，请稍后再试。'));
      };
      xhr.ontimeout = function () {
        reject(new Error('上传超时，图片可能太大或网络太慢。'));
      };
      xhr.send(form);
    });
  }

  function updateURLDay(day) {
    try {
      var url = new URL(location.href);
      url.searchParams.set('day', day);
      history.replaceState(null, '', url.pathname + '?' + url.searchParams.toString());
    } catch (e) {
      /* 老浏览器忽略即可 */
    }
  }

  function readURLDay() {
    try {
      var day = new URL(location.href).searchParams.get('day');
      return isDay(day) ? day : '';
    } catch (e) {
      return '';
    }
  }

  function applyLimits(data) {
    if (!data) return;
    state.limits = {
      contentMax: Number(data.contentMax || 10000),
      tagsMax: Number(data.tagsMax || 10),
      tagMax: Number(data.tagMax || 20),
      imagesPerNote: Number(data.imagesPerNote || 9),
      imageMaxBytes: Number(data.imageMaxBytes || 10 * 1024 * 1024),
      totalBytes: Number(data.totalBytes || 2048 * 1024 * 1024)
    };
    $('noteText').maxLength = state.limits.contentMax;
    $('editText').maxLength = state.limits.contentMax;
    $('tagInput').placeholder = '标签：空格或逗号分隔，最多 ' + state.limits.tagsMax + ' 个';
    $('editTags').placeholder = $('tagInput').placeholder;
  }

  function renderUsage(data) {
    var box = $('usage');
    if (!box) return;
    box.textContent = '图片 ' + formatBytes(data.usedBytes) + ' / ' + formatBytes(data.totalBytes);
  }

  // 左侧月历热力图：一个月一个网格，记录越多颜色越深。
  function heatLevel(count) {
    if (count >= 5) return 4;
    if (count >= 3) return 3;
    if (count >= 2) return 2;
    if (count >= 1) return 1;
    return 0;
  }

  function loadHeatmap(month) {
    month = /^\d{4}-\d{2}$/.test(String(month || '')) ? month
      : (state.hmMonth || monthOf(state.day) || monthOf(todayStr()));
    state.hmMonth = month;
    var seq = ++state.hmSeq;
    return api('/notes/api/month?month=' + encodeURIComponent(month)).then(function (data) {
      if (seq !== state.hmSeq) return;
      renderHeatmap(data.days || [], data.month || month);
    }).catch(function () {
      if (seq !== state.hmSeq) return;
      renderHeatmap([], month);
    });
  }

  function renderHeatmap(days, month) {
    var grid = $('heatmap');
    if (!grid) return;
    if (!/^\d{4}-\d{2}$/.test(String(month || ''))) {
      month = state.hmMonth || monthOf(state.day) || monthOf(todayStr());
    }
    state.hmMonth = month;
    var counts = {};
    (days || []).forEach(function (item) {
      if (item && isDay(item.day)) counts[item.day] = Number(item.count || 0);
    });
    var today = todayStr();
    var year = Number(month.slice(0, 4));
    var mon = Number(month.slice(5, 7));
    var lead = (new Date(Date.UTC(year, mon - 1, 1, 12)).getUTCDay() + 6) % 7;
    var total = new Date(Date.UTC(year, mon, 0, 12)).getUTCDate();
    var rows = Math.ceil((lead + total) / 7);

    $('hmMonth').textContent = monthLabel(month);
    var current = monthOf(today);
    $('hmNext').disabled = month >= current;
    $('hmPrev').disabled = month <= '1970-01';

    grid.textContent = '';
    grid.setAttribute('aria-label', monthLabel(month) + '每日记录数量');
    for (var i = 0; i < rows * 7; i++) {
      var num = i - lead + 1;
      if (num < 1 || num > total) {
        var blank = document.createElement('span');
        blank.className = 'hm-cell is-blank';
        grid.appendChild(blank);
        continue;
      }
      var day = month + '-' + pad(num);
      var count = counts[day] || 0;
      var level = heatLevel(count);
      var future = day > today;
      var cell = document.createElement('button');
      cell.type = 'button';
      cell.className = 'hm-cell' + (future ? ' is-future' : '') + (day === today ? ' is-today' : '');
      cell.setAttribute('data-day', day);
      cell.setAttribute('data-level', String(level));
      cell.setAttribute('data-count', String(count));
      cell.disabled = future;
      cell.textContent = String(num);
      cell.title = dayLong(day) + (future ? ' · 还没到' : ' · ' + (count ? count + ' 条记录' : '没有记录'));
      cell.setAttribute('aria-label', cell.title);
      cell.addEventListener('click', function () {
        onHeatmapDay(this.getAttribute('data-day'));
      });
      grid.appendChild(cell);
    }
    markActiveHeatmap();
  }

  function markActiveHeatmap() {
    var active = state.view === 'analysis' ? state.analysis.date : state.day;
    var cells = document.querySelectorAll('.hm-cell[data-day]');
    Array.prototype.forEach.call(cells, function (cell) {
      cell.classList.toggle('is-sel', cell.getAttribute('data-day') === active);
    });
  }

  function onHeatmapDay(day) {
    if (state.view === 'analysis') selectAnalysisDay(day);
    else selectDay(day);
  }

  function renderTags(tags) {
    var box = $('tagCloud');
    box.textContent = '';
    tags = (tags || []).slice();
    if (state.tag && !tags.some(function (item) { return item.name === state.tag; })) {
      tags.unshift({ name: state.tag, count: 0 });
    }
    if (!tags.length) {
      box.appendChild(el('span', 'side-empty', '还没有标签'));
      return;
    }
    tags.forEach(function (item) {
      var button = el('button', 'tag-btn' + (state.tag === item.name ? ' on' : ''), '#' + item.name);
      button.type = 'button';
      button.title = item.count ? '使用 ' + item.count + ' 次' : '筛选这个标签';
      button.addEventListener('click', function () { setTag(item.name); });
      box.appendChild(button);
    });
  }

  function applySearchMode() {
    var on = !!(state.q || state.tag);
    $('layout').classList.toggle('search-mode', on);
    $('searchBar').classList.toggle('show', on);
    $('searchClear').classList.toggle('show', !!on);
    if (!on) {
      $('searchText').textContent = '';
      return;
    }
    var parts = [];
    if (state.q) parts.push('文字 “' + state.q + '”');
    if (state.tag) parts.push('标签 “' + state.tag + '”');
    if (state.level !== '') parts.push('重要程度 “' + levelName(state.level) + '”');
    $('searchText').textContent = '正在搜索 ' + parts.join(' + ');
  }

  function renderLevelFilter() {
    var box = $('levelFilter');
    if (!box) return;
    Array.prototype.forEach.call(box.querySelectorAll('button'), function (button) {
      button.classList.toggle('on', button.getAttribute('data-level') === state.level);
    });
  }

  function setLevel(value) {
    value = String(value == null ? '' : value);
    if (value === state.level) return;
    state.level = value;
    renderLevelFilter();
    loadNotes();
  }

  function renderStreak(streak) {
    var box = $('streak');
    if (!box) return;
    var item = streak || {};
    var current = Number(item.current || 0);
    var longest = Number(item.longest || 0);
    if (current > 0) {
      box.hidden = false;
      box.textContent = '🔥 连续 ' + current + ' 天';
      box.title = '当前连续 ' + current + ' 天' +
        (longest > current ? ' · 最长 ' + longest + ' 天' : '');
    } else if (longest > 0) {
      box.hidden = false;
      box.textContent = '最长 ' + longest + ' 天';
      box.title = '还没有开始新的连续记录 · 最长 ' + longest + ' 天';
    } else {
      box.hidden = true;
      box.textContent = '';
      box.title = '';
    }
    if (box.hidden) box.removeAttribute('aria-label');
    else box.setAttribute('aria-label', box.title || box.textContent);
  }

  function loadStatus() {
    return api('/notes/api/status?today=' + encodeURIComponent(todayStr())).then(function (data) {
      state.status = data;
      applyLimits(data);
      loadHeatmap(state.hmMonth);
      renderStreak(data.streak || {});
      renderTags(data.tags || []);
      renderUsage(data);
    });
  }

  function showLoading() {
    var box = $('timeline');
    box.textContent = '';
    box.appendChild(el('div', 'loading', '加载中……'));
  }

  function showLoadError(message) {
    var box = $('timeline');
    box.textContent = '';
    var wrap = el('div', 'errorbox');
    wrap.appendChild(el('div', '', message || '加载失败。'));
    var retry = el('button', 'btn sm', '重试');
    retry.type = 'button';
    retry.style.marginTop = '8px';
    retry.addEventListener('click', function () { loadNotes(); });
    wrap.appendChild(retry);
    box.appendChild(wrap);
  }

  function loadNotes() {
    var seq = ++state.loadSeq;
    var searching = !!(state.q || state.tag);
    var url = '/notes/api/notes?limit=300';
    if (state.level !== '') url += '&level=' + encodeURIComponent(state.level);
    if (searching) {
      if (state.q) url += '&q=' + encodeURIComponent(state.q);
      if (state.tag) url += '&tag=' + encodeURIComponent(state.tag);
    } else {
      url += '&day=' + encodeURIComponent(state.day);
    }
    showLoading();
    return api(url).then(function (data) {
      if (seq !== state.loadSeq) return;
      state.notes = data.items || [];
      renderNotes();
    }).catch(function (err) {
      if (seq !== state.loadSeq) return;
      state.notes = [];
      showLoadError(err.message || '加载失败。');
      updateDayHeader();
    });
  }

  function updateDayHeader() {
    var search = !!(state.q || state.tag);
    $('dayInput').value = state.day;
    var title = dayLong(state.day);
    $('dayTitle').textContent = title;
    if (!search && state.notes.length) {
      var count = state.notes.length + ' 条';
      if (state.level !== '') count += ' · ' + levelName(state.level);
      $('dayTitle').appendChild(el('small', '', count));
    }
    $('composerTitle').textContent = (state.day === todayStr() ? '今天' : dayShort(state.day)) + ' · 写一条';
    markActiveHeatmap();
  }

  function renderNotes() {
    var box = $('timeline');
    box.textContent = '';
    var search = !!(state.q || state.tag);
    updateDayHeader();
    if (!state.notes.length) {
      var empty = el('div', 'empty');
      if (search) {
        var miss = state.level !== ''
          ? '没有找到「' + levelName(state.level) + '」的匹配记录。'
          : '没有找到匹配的记录。';
        empty.appendChild(el('div', '', miss));
        var clear = el('button', 'btn sm ghost', '清除筛选');
        clear.type = 'button';
        clear.style.marginTop = '10px';
        clear.addEventListener('click', function () {
          state.level = '';
          renderLevelFilter();
          clearSearch();
        });
        empty.appendChild(clear);
      } else if (state.level !== '') {
        empty.appendChild(el('div', '', '这一天没有「' + levelName(state.level) + '」的记录。'));
        var all = el('button', 'btn sm ghost', '显示全部');
        all.type = 'button';
        all.style.marginTop = '10px';
        all.addEventListener('click', function () { setLevel(''); });
        empty.appendChild(all);
      } else {
        empty.appendChild(el('div', '', '这一天还没有记录。'));
        var write = el('button', 'btn sm', '写第一条');
        write.type = 'button';
        write.style.marginTop = '10px';
        write.addEventListener('click', function () { $('noteText').focus(); });
        empty.appendChild(write);
      }
      box.appendChild(empty);
      return;
    }

    var lastDay = '';
    state.notes.forEach(function (note) {
      if (search && note.day !== lastDay) {
        lastDay = note.day;
        box.appendChild(el('div', 'day-group', dayLong(note.day)));
      }
      box.appendChild(noteCard(note));
    });
  }

  function noteCard(note) {
    var card = el('article', 'note');
    card.setAttribute('data-id', String(note.id));
    var head = el('div', 'note-head');
    head.appendChild(el('span', 'note-time', String(note.createdAt || '').slice(11, 16)));
    if (note.updatedAt && note.createdAt && note.updatedAt !== note.createdAt) {
      head.appendChild(el('span', 'note-edited', '已编辑'));
    }
    var importance = Number(note.importance || 0);
    if (importance > 0) {
      head.appendChild(el('span', 'note-level lv' + importance, levelName(importance)));
    }
    var actions = el('div', 'note-actions');
    var edit = el('button', 'act', '编辑');
    edit.type = 'button';
    edit.addEventListener('click', function () { openEdit(note); });
    var remove = el('button', 'act danger', '删除');
    remove.type = 'button';
    remove.addEventListener('click', function () { deleteNote(note, remove); });
    actions.appendChild(edit);
    actions.appendChild(remove);
    head.appendChild(actions);
    card.appendChild(head);

    if (note.content) {
      card.appendChild(el('div', 'note-text', note.content));
    }
    if (note.images && note.images.length) {
      var imgs = el('div', 'note-imgs');
      note.images.forEach(function (image, index) {
        var button = el('button', 'note-img');
        button.type = 'button';
        button.title = '查看大图';
        var img = document.createElement('img');
        img.src = image.url;
        img.alt = '笔记图片 ' + (index + 1);
        img.loading = 'lazy';
        img.decoding = 'async';
        button.appendChild(img);
        button.addEventListener('click', function () { openLightbox(note.images, index); });
        imgs.appendChild(button);
      });
      card.appendChild(imgs);
    }
    if (note.tags && note.tags.length) {
      var tags = el('div', 'note-tags');
      note.tags.forEach(function (tag) {
        var button = el('button', 'note-tag', '#' + tag);
        button.type = 'button';
        button.addEventListener('click', function () { setTag(tag); });
        tags.appendChild(button);
      });
      card.appendChild(tags);
    }
    return card;
  }

  function deleteNote(note, button) {
    if (button.getAttribute('data-armed') !== '1') {
      button.setAttribute('data-armed', '1');
      button.classList.add('confirm');
      button.textContent = '确认删除';
      setTimeout(function () {
        if (button.getAttribute('data-armed') === '1') {
          button.removeAttribute('data-armed');
          button.classList.remove('confirm');
          button.textContent = '删除';
        }
      }, 3200);
      return;
    }
    button.setAttribute('data-armed', '2');
    button.disabled = true;
    button.textContent = '删除中…';
    api('/notes/api/notes/' + note.id, { method: 'DELETE' }).then(function () {
      if (state.light) closeLightbox();
      toast('已删除。');
      return refreshAll();
    }).catch(function (err) {
      button.disabled = false;
      button.removeAttribute('data-armed');
      button.classList.remove('confirm');
      button.textContent = '删除';
      toast(err.message || '删除失败。', true);
    });
  }

  function selectDay(day) {
    if (!isDay(day)) return;
    if (day === state.day && !state.q && !state.tag) return;
    if (state.queue.length && !window.confirm('切换日期会清空还没保存的图片，继续吗？')) {
      $('dayInput').value = state.day;
      return;
    }
    persistDraft();
    var targetMonth = monthOf(day);
    var monthChanged = targetMonth !== state.hmMonth;
    state.day = day;
    state.hmMonth = targetMonth;
    state.q = '';
    state.tag = '';
    $('searchInput').value = '';
    clearQueue();
    loadDraft(day);
    applySearchMode();
    updateURLDay(day);
    updateDayHeader();
    if (monthChanged) loadHeatmap(targetMonth);
    renderTags(state.status ? state.status.tags || [] : []);
    loadNotes();
  }

  function setTag(tag) {
    tag = String(tag || '').trim();
    if (!tag) return;
    if (state.tag === tag) {
      state.tag = '';
    } else {
      state.tag = tag;
      state.q = '';
      $('searchInput').value = '';
    }
    applySearchMode();
    renderTags(state.status ? state.status.tags || [] : []);
    loadNotes();
  }

  function clearSearch() {
    state.q = '';
    state.tag = '';
    $('searchInput').value = '';
    applySearchMode();
    renderTags(state.status ? state.status.tags || [] : []);
    loadNotes();
  }

  function refreshAll() {
    return Promise.all([
      loadStatus().catch(function (err) { toast(err.message || '状态加载失败。', true); }),
      loadNotes()
    ]);
  }

  // ---------------- 草稿 ----------------
  function draftKey(day) {
    return 'tb_notes_draft_' + day;
  }

  function readDraft(day) {
    try {
      var raw = localStorage.getItem(draftKey(day));
      if (!raw) return null;
      var data = JSON.parse(raw);
      if (!data || typeof data !== 'object') return null;
      var level = String(data.level == null ? '0' : data.level);
      if (!/^[0-2]$/.test(level)) level = '0';
      return { text: String(data.text || ''), tags: String(data.tags || ''), level: level };
    } catch (e) {
      return null;
    }
  }

  function loadDraft(day) {
    var data = readDraft(day);
    $('noteText').value = data ? data.text : '';
    $('tagInput').value = data ? data.tags : '';
    $('levelSelect').value = data ? data.level : '0';
    syncLevelSelect();
    if (data && (data.text || data.tags)) {
      showDraftHint('已恢复草稿');
    } else {
      $('draftHint').classList.remove('show');
    }
    updateSaveState();
  }

  function persistDraft() {
    var text = $('noteText').value;
    var tags = $('tagInput').value;
    var level = $('levelSelect').value || '0';
    try {
      if (!text.trim() && !tags.trim()) {
        localStorage.removeItem(draftKey(state.day));
      } else {
        localStorage.setItem(draftKey(state.day), JSON.stringify({ text: text, tags: tags, level: level }));
        showDraftHint('已自动保存草稿');
      }
    } catch (e) {
      /* 本地存储满了也不影响主流程 */
    }
  }

  function scheduleDraft() {
    clearTimeout(state.draftTimer);
    state.draftTimer = setTimeout(persistDraft, 350);
  }

  function removeDraft(day) {
    try { localStorage.removeItem(draftKey(day)); } catch (e) { /* ignore */ }
  }

  function showDraftHint(text) {
    var hint = $('draftHint');
    hint.textContent = text || '已自动保存草稿';
    hint.classList.add('show');
    clearTimeout(state.draftHintTimer);
    state.draftHintTimer = setTimeout(function () {
      hint.classList.remove('show');
    }, 1800);
  }

  // ---------------- 图片处理与队列 ----------------
  function imageTypeAllowed(type) {
    return /^image\/(jpeg|png|webp|gif)$/i.test(String(type || ''));
  }

  function canvasToBlob(canvas, type, quality) {
    return new Promise(function (resolve, reject) {
      if (!canvas.toBlob) {
        reject(new Error('当前浏览器不支持图片压缩，请换个浏览器试试。'));
        return;
      }
      canvas.toBlob(function (blob) {
        if (blob) resolve(blob);
        else reject(new Error('图片处理失败。'));
      }, type, quality);
    });
  }

  function compressImage(file) {
    return new Promise(function (resolve, reject) {
      var type = String(file.type || '');
      if (type === 'image/gif') {
        resolve({ blob: file, name: file.name || 'image.gif' });
        return;
      }
      if (type && !/^image\/(jpeg|png|webp)$/i.test(type)) {
        reject(new Error('不支持这种图片格式，请用 JPEG / PNG / WebP / GIF。'));
        return;
      }
      if (file.size > 60 * 1024 * 1024) {
        reject(new Error('图片太大了，单张请控制在 60MB 以内。'));
        return;
      }
      var url = URL.createObjectURL(file);
      var img = new Image();
      img.onload = function () {
        var width = img.naturalWidth || img.width;
        var height = img.naturalHeight || img.height;
        if (!width || !height) {
          URL.revokeObjectURL(url);
          reject(new Error('图片尺寸读取失败。'));
          return;
        }
        var edge = Math.max(width, height);
        var scale = edge > 2560 ? 2560 / edge : 1;
        var cw = Math.max(1, Math.round(width * scale));
        var ch = Math.max(1, Math.round(height * scale));
        var canvas = document.createElement('canvas');
        canvas.width = cw;
        canvas.height = ch;
        var ctx = canvas.getContext('2d');
        if (!ctx) {
          URL.revokeObjectURL(url);
          reject(new Error('图片处理失败。'));
          return;
        }
        ctx.drawImage(img, 0, 0, cw, ch);
        URL.revokeObjectURL(url);
        var base = String(file.name || 'image').replace(/\.[^.]+$/, '') || 'image';
        canvasToBlob(canvas, 'image/webp', 0.9).then(function (blob) {
          resolve({ blob: blob, name: base + '.webp' });
        }).catch(function () {
          return canvasToBlob(canvas, 'image/jpeg', 0.9).then(function (blob) {
            resolve({ blob: blob, name: base + '.jpg' });
          });
        }).catch(reject);
      };
      img.onerror = function () {
        URL.revokeObjectURL(url);
        reject(new Error('这张图片打不开，可能是 HEIC 或损坏的文件。'));
      };
      img.src = url;
    });
  }

  function queueFiles(files, target, capacity, guard, changed) {
    var list = Array.prototype.slice.call(files || []).filter(Boolean);
    if (!list.length) return;
    if (capacity <= 0) {
      toast('一条笔记最多 ' + state.limits.imagesPerNote + ' 张图。', true);
      return;
    }
    if (list.length > capacity) {
      toast('最多 ' + state.limits.imagesPerNote + ' 张图，只保留前 ' + capacity + ' 张。', true);
      list = list.slice(0, capacity);
    }
    list.forEach(function (file) {
      if (file.size > 60 * 1024 * 1024) {
        toast('图片太大了，单张请控制在 60MB 以内。', true);
        return;
      }
      var item = {
        key: 'q' + Date.now() + Math.random(),
        ready: false,
        blob: null,
        url: '',
        name: file.name || 'image'
      };
      target.push(item);
      changed();
      compressImage(file).then(function (result) {
        if (!guard()) return;
        if (result.blob.size > state.limits.imageMaxBytes) {
          removeTargetItem(target, item);
          toast('压缩后仍然太大，单张图片不能超过 ' + Math.round(state.limits.imageMaxBytes / (1024 * 1024)) + 'MB。', true);
          changed();
          return;
        }
        item.blob = result.blob;
        item.name = result.name;
        item.url = URL.createObjectURL(result.blob);
        item.ready = true;
        changed();
      }).catch(function (err) {
        if (!guard()) return;
        removeTargetItem(target, item);
        toast(err.message || '图片处理失败。', true);
        changed();
      });
    });
  }

  function removeTargetItem(target, item) {
    var index = target.indexOf(item);
    if (index >= 0) target.splice(index, 1);
    if (item.url) URL.revokeObjectURL(item.url);
  }

  function pendingCount(queue) {
    return queue.reduce(function (count, item) { return count + (item.ready ? 0 : 1); }, 0);
  }

  function renderQueue() {
    var box = $('queue');
    box.textContent = '';
    box.hidden = !state.queue.length;
    state.queue.forEach(function (item) {
      var thumb = el('div', 'thumb' + (item.ready ? '' : ' pending-item'));
      if (item.url) {
        var img = document.createElement('img');
        img.src = item.url;
        img.alt = '待上传图片';
        thumb.appendChild(img);
      } else {
        thumb.appendChild(el('div', 'pending', '处理中…'));
      }
      var remove = el('button', 'rm', '×');
      remove.type = 'button';
      remove.title = '移除这张图';
      remove.addEventListener('click', function () {
        removeTargetItem(state.queue, item);
        renderQueue();
        updateSaveState();
      });
      thumb.appendChild(remove);
      box.appendChild(thumb);
    });
  }

  function addComposerFiles(files) {
    var target = state.queue;
    var capacity = state.limits.imagesPerNote - target.length;
    queueFiles(files, target, capacity, function () {
      return state.queue === target;
    }, function () {
      renderQueue();
      updateSaveState();
    });
  }

  function clearQueue() {
    state.queue.forEach(function (item) {
      if (item.url) URL.revokeObjectURL(item.url);
    });
    state.queue = [];
    renderQueue();
    updateSaveState();
  }

  function updateSaveState() {
    var pending = pendingCount(state.queue);
    var hasContent = !!$('noteText').value.trim() || state.queue.length > 0;
    $('saveBtn').disabled = state.saving || pending > 0 || !hasContent;
    if (state.saving) {
      $('saveBtn').textContent = '保存中…';
    } else {
      $('saveBtn').textContent = '保存';
    }
    if (!state.saving) {
      if (pending > 0) $('saveHint').textContent = '处理图片…';
      else $('saveHint').textContent = '';
    }
  }

  function saveNew() {
    if (state.saving) return;
    if (pendingCount(state.queue) > 0) {
      toast('图片还在处理，稍等一下。', true);
      return;
    }
    var text = $('noteText').value.trim();
    var tags = parseTagInput($('tagInput').value);
    if (!text && !state.queue.length) {
      toast('写点什么，或者至少加一张图。', true);
      return;
    }
    var form = new FormData();
    form.append('content', text);
    form.append('day', state.day);
    form.append('tags', JSON.stringify(tags));
    form.append('importance', $('levelSelect').value || '0');
    state.queue.forEach(function (item, index) {
      form.append('images', item.blob, item.name || ('image-' + (index + 1) + '.webp'));
    });
    state.saving = true;
    updateSaveState();
    $('saveBtn').disabled = true;
    uploadRequest('/notes/api/notes', 'POST', form, function (percent) {
      $('saveHint').textContent = '上传中 ' + percent + '%';
    }).then(function () {
      $('noteText').value = '';
      $('tagInput').value = '';
      $('levelSelect').value = '0';
      syncLevelSelect();
      clearQueue();
      removeDraft(state.day);
      $('draftHint').classList.remove('show');
      state.saving = false;
      updateSaveState();
      toast('已保存。');
      $('noteText').focus();
      return refreshAll();
    }).catch(function (err) {
      state.saving = false;
      updateSaveState();
      toast(err.message || '保存失败。', true);
    });
  }

  // ---------------- 标签输入 ----------------
  function parseTagInput(raw) {
    var values = String(raw || '').split(/[\s,，]+/);
    var out = [], seen = {};
    values.forEach(function (item) {
      var tag = String(item || '').replace(/^#+/, '').trim();
      if (!tag) return;
      if (tag.length > state.limits.tagMax) tag = tag.slice(0, state.limits.tagMax);
      var key = tag.toLowerCase();
      if (seen[key]) return;
      seen[key] = true;
      out.push(tag);
    });
    if (out.length > state.limits.tagsMax) {
      out = out.slice(0, state.limits.tagsMax);
      toast('最多加 ' + state.limits.tagsMax + ' 个标签。', true);
    }
    return out;
  }

  function addQuickTag(tag) {
    var input = $('tagInput');
    var tags = parseTagInput(input.value);
    if (tags.indexOf(tag) < 0) tags.push(tag);
    input.value = tags.join(' ');
    scheduleDraft();
    updateSaveState();
    $('noteText').focus();
  }

  // ---------------- 编辑 ----------------
  function openEdit(note) {
    if (state.saving) {
      toast('正在保存新笔记，稍等一下。', true);
      return;
    }
    var ed = {
      note: note,
      existing: (note.images || []).slice(),
      removed: {},
      newItems: [],
      saving: false
    };
    state.edit = ed;
    $('editText').value = note.content || '';
    $('editDay').value = note.day || state.day;
    $('editImportance').value = String(note.importance || 0);
    $('editTags').value = (note.tags || []).join(' ');
    $('editErr').hidden = true;
    $('editErr').textContent = '';
    $('editFileInput').value = '';
    renderEditImages();
    updateEditState();
    $('editor').showModal();
    setTimeout(function () { $('editText').focus(); }, 30);
  }

  function renderEditImages() {
    var ed = state.edit;
    var box = $('editImgs');
    box.textContent = '';
    if (!ed) return;
    ed.existing.forEach(function (image) {
      if (ed.removed[image.id]) return;
      var thumb = el('div', 'thumb');
      var img = document.createElement('img');
      img.src = image.url;
      img.alt = '笔记图片';
      thumb.appendChild(img);
      var remove = el('button', 'rm', '×');
      remove.type = 'button';
      remove.title = '移除这张图';
      remove.addEventListener('click', function () {
        ed.removed[image.id] = true;
        renderEditImages();
        updateEditState();
      });
      thumb.appendChild(remove);
      box.appendChild(thumb);
    });
    ed.newItems.forEach(function (item) {
      var thumb = el('div', 'thumb' + (item.ready ? '' : ' pending-item'));
      if (item.url) {
        var img = document.createElement('img');
        img.src = item.url;
        img.alt = '待上传图片';
        thumb.appendChild(img);
      } else {
        thumb.appendChild(el('div', 'pending', '处理中…'));
      }
      var remove = el('button', 'rm', '×');
      remove.type = 'button';
      remove.title = '移除这张图';
      remove.addEventListener('click', function () {
        removeTargetItem(ed.newItems, item);
        renderEditImages();
        updateEditState();
      });
      thumb.appendChild(remove);
      box.appendChild(thumb);
    });
  }

  function editImageCount() {
    var ed = state.edit;
    if (!ed) return 0;
    var existing = ed.existing.filter(function (image) { return !ed.removed[image.id]; }).length;
    return existing + ed.newItems.length;
  }

  function updateEditState() {
    var ed = state.edit;
    if (!ed) return;
    $('editSave').disabled = ed.saving;
    $('editSave').textContent = ed.saving ? '保存中…' : '保存';
    if (!ed.saving) {
      var pending = pendingCount(ed.newItems);
      if (pending > 0) {
        $('editErr').hidden = false;
        $('editErr').textContent = '图片处理中…';
      } else if ($('editErr').textContent === '图片处理中…') {
        $('editErr').hidden = true;
        $('editErr').textContent = '';
      }
    }
  }

  function addEditFiles(files) {
    var ed = state.edit;
    if (!ed) return;
    var capacity = state.limits.imagesPerNote - editImageCount();
    queueFiles(files, ed.newItems, capacity, function () {
      return state.edit === ed;
    }, function () {
      renderEditImages();
      updateEditState();
    });
  }

  function cleanupEdit() {
    var ed = state.edit;
    if (!ed) return;
    ed.newItems.forEach(function (item) {
      if (item.url) URL.revokeObjectURL(item.url);
    });
    state.edit = null;
  }

  function submitEdit(event) {
    event.preventDefault();
    var ed = state.edit;
    if (!ed || ed.saving) return;
    if (pendingCount(ed.newItems) > 0) {
      $('editErr').hidden = false;
      $('editErr').textContent = '图片还在处理，稍等一下再保存。';
      return;
    }
    var text = $('editText').value.trim();
    var day = $('editDay').value;
    if (!isDay(day)) {
      $('editErr').hidden = false;
      $('editErr').textContent = '请选择有效日期。';
      return;
    }
    var tags = parseTagInput($('editTags').value);
    var remaining = ed.existing.filter(function (image) { return !ed.removed[image.id]; }).length + ed.newItems.length;
    if (!text && remaining === 0) {
      $('editErr').hidden = false;
      $('editErr').textContent = '写点什么，或者至少留一张图。';
      return;
    }
    var removeIds = Object.keys(ed.removed).map(function (id) { return Number(id); });
    var form = new FormData();
    form.append('content', text);
    form.append('day', day);
    form.append('tags', JSON.stringify(tags));
    form.append('importance', $('editImportance').value || '0');
    form.append('removeImages', JSON.stringify(removeIds));
    ed.newItems.forEach(function (item, index) {
      form.append('images', item.blob, item.name || ('image-' + (index + 1) + '.webp'));
    });
    ed.saving = true;
    updateEditState();
    $('editErr').hidden = true;
    uploadRequest('/notes/api/notes/' + ed.note.id, 'PUT', form, function (percent) {
      if (state.edit === ed) {
        $('editErr').hidden = false;
        $('editErr').textContent = '上传中 ' + percent + '%';
      }
    }).then(function () {
      ed.saving = false;
      $('editor').close();
      toast('已更新。');
      return refreshAll();
    }).catch(function (err) {
      ed.saving = false;
      updateEditState();
      $('editErr').hidden = false;
      $('editErr').textContent = err.message || '保存失败。';
    });
  }

  // ---------------- 图片预览 ----------------
  function openLightbox(images, index) {
    if (!images || !images.length) return;
    state.light = {
      images: images.slice(),
      index: Math.max(0, Math.min(Number(index) || 0, images.length - 1))
    };
    renderLightbox();
    $('lightbox').showModal();
  }

  function renderLightbox() {
    var light = state.light;
    if (!light) return;
    var list = light.images;
    $('lightImg').src = list[light.index].url;
    $('lightImg').alt = '笔记图片 ' + (light.index + 1);
    $('lightCount').textContent = list.length > 1 ? (light.index + 1) + ' / ' + list.length : '';
    $('lightPrev').style.visibility = list.length > 1 ? 'visible' : 'hidden';
    $('lightNext').style.visibility = list.length > 1 ? 'visible' : 'hidden';
  }

  function moveLightbox(delta) {
    var light = state.light;
    if (!light || light.images.length < 2) return;
    light.index = (light.index + delta + light.images.length) % light.images.length;
    renderLightbox();
  }

  function closeLightbox() {
    var dialog = $('lightbox');
    if (dialog.open) dialog.close();
    state.light = null;
    $('lightImg').removeAttribute('src');
  }

  // ---------------- AI 分析 ----------------
  function syncAnalysisPeriods() {
    var buttons = document.querySelectorAll('#analysisPeriods button');
    Array.prototype.forEach.call(buttons, function (button) {
      button.classList.toggle('on', button.getAttribute('data-period') === state.analysis.period);
    });
  }

  function resetDeleteButton() {
    var button = $('analysisDelete');
    if (!button) return;
    button.disabled = false;
    button.removeAttribute('data-armed');
    button.classList.remove('confirm');
    button.textContent = '删除结果';
  }

  function analysisText(data) {
    var a = data && data.analysis;
    if (!a) return '';
    var lines = [data.label + ' · ' + (data.periodName || periodName(data.period))];
    if (a.headline) lines.push('', a.headline);
    if (a.summary) lines.push('', a.summary);
    if (a.strengths && a.strengths.length) {
      lines.push('', '【做得好的地方】');
      a.strengths.forEach(function (item) {
        lines.push('- ' + item.title +
          (item.evidence ? '：' + item.evidence : '') +
          (item.day ? '（' + item.day + '）' : ''));
      });
    }
    if (a.gaps && a.gaps.length) {
      lines.push('', '【不足与卡点】');
      a.gaps.forEach(function (item) {
        lines.push('- ' + item.title +
          (item.evidence ? '：' + item.evidence : '') +
          (item.impact ? '（影响：' + item.impact + '）' : ''));
      });
    }
    if (a.improvements && a.improvements.length) {
      lines.push('', '【怎么改进】');
      a.improvements.forEach(function (item) {
        lines.push('- ' + item.action +
          (item.how ? '：' + item.how : '') +
          (item.why ? '（依据：' + item.why + '）' : ''));
      });
    }
    var plan = a.nextPlan || {};
    if (plan.focus || (plan.adjustments && plan.adjustments.length) ||
        (plan.actions && plan.actions.length)) {
      lines.push('', '【下一步计划' + (plan.label ? ' · ' + plan.label : '') + '】');
      if (plan.focus) lines.push('重点：' + plan.focus);
      (plan.adjustments || []).forEach(function (item) { lines.push('- ' + item); });
      (plan.actions || []).forEach(function (item, index) {
        lines.push((index + 1) + '. ' + item.action +
          (item.when ? ' · ' + item.when : '') +
          (item.why ? '（依据：' + item.why + '）' : ''));
      });
    }
    if (a.connections && a.connections.length) {
      lines.push('', '【与过去记录的关联】');
      a.connections.forEach(function (item) {
        lines.push('- ' + (item.currentDay || '') + ' ' + (item.current || '') +
          ' ↔ ' + (item.pastDay || '') + ' ' + (item.past || '') +
          (item.relation ? '；关联：' + item.relation : '') +
          (item.suggestion ? '；可以怎么用：' + item.suggestion : ''));
      });
    }
    if (a.highlights && a.highlights.length) {
      lines.push('', '【值得回看】');
      a.highlights.forEach(function (item) {
        lines.push('- ' + (item.day ? item.day + ' ' : '') + (item.title || '') +
          (item.detail ? '：' + item.detail : ''));
      });
    }
    if (a.themes && a.themes.length) {
      lines.push('', '【主题】');
      a.themes.forEach(function (item) {
        lines.push('- ' + item.name + (item.count ? '（' + item.count + '）' : '') +
          (item.note ? '：' + item.note : ''));
      });
    }
    if (a.blockers && a.blockers.length) {
      lines.push('', '【卡点】');
      a.blockers.forEach(function (item) { lines.push('- ' + item); });
    }
    if (a.patterns && a.patterns.length) {
      lines.push('', '【反复出现的模式】');
      a.patterns.forEach(function (item) { lines.push('- ' + item); });
    }
    if (a.suggestions && a.suggestions.length) {
      lines.push('', '【建议】');
      a.suggestions.forEach(function (item, index) {
        lines.push((index + 1) + '. ' + item.action +
          (item.why ? '（' + item.why + '）' : '') +
          (item.when ? ' · ' + item.when : ''));
      });
    }
    if (a.questions && a.questions.length) {
      lines.push('', '【可以继续想】');
      a.questions.forEach(function (item) { lines.push('- ' + item); });
    }
    return lines.join('\n');
  }

  function analysisStat(label, value) {
    var box = el('div', 'an-stat');
    box.appendChild(el('b', '', value));
    box.appendChild(el('span', '', label));
    return box;
  }

  function analysisSection(title, children) {
    var section = el('div', 'an-section');
    section.appendChild(el('h3', '', title));
    (children || []).forEach(function (node) { section.appendChild(node); });
    return section;
  }

  function analysisList(title, items) {
    if (!items || !items.length) return null;
    var list = el('ul', 'an-list');
    items.forEach(function (item) { list.appendChild(el('li', '', item)); });
    return analysisSection(title, [list]);
  }

  function renderAnalysisBody() {
    var box = $('analysisBody');
    var data = state.analysis.data;
    box.textContent = '';

    if (state.analysis.error && !data) {
      var error = el('div', 'errorbox');
      error.appendChild(el('div', '', state.analysis.error));
      var retry = el('button', 'btn sm', '重试');
      retry.type = 'button';
      retry.style.marginTop = '8px';
      retry.addEventListener('click', loadAnalysis);
      error.appendChild(retry);
      box.appendChild(error);
      return;
    }
    if (state.analysis.loading && !data) {
      box.appendChild(el('div', 'an-loading', '正在读取这段记录……'));
      return;
    }
    if (!data) {
      box.appendChild(el('div', 'an-empty', '选择周期后点「生成分析」。'));
      return;
    }

    if (data.meta && data.meta.stale) {
      var notice = el('div', 'an-notice');
      notice.appendChild(el('span', '', '这期间笔记有新增或修改，当前显示的是上次的分析。'));
      var refresh = el('button', 'btn sm', '重新分析');
      refresh.type = 'button';
      refresh.disabled = !data.aiReady ||
        !!(data.quota && Number(data.quota.remaining || 0) <= 0) ||
        !!(data.stats && !data.stats.textCount);
      refresh.addEventListener('click', function () { generateAnalysis(true); });
      notice.appendChild(refresh);
      box.appendChild(notice);
    }
    if (data.analysis && data.analysis.degraded) {
      box.appendChild(el('div', 'an-notice',
        '模型这次返回的不是标准结构，下面按纯文本展示；可以重新分析一次。'));
    }
    if (data.coverage && data.coverage.truncated) {
      var coverage = data.coverage;
      box.appendChild(el('div', 'an-notice',
        '记录比较多，这次只选了 ' + Number(coverage.included || 0) + ' / ' +
        Number(coverage.total || 0) + ' 条文字记录交给模型；结论可能没覆盖全部内容。'));
    }
    if (data.coverage && data.coverage.history && data.coverage.history.available) {
      var historyCoverage = data.coverage.history;
      box.appendChild(el('div', 'an-notice',
        '关联检索：从 ' + Number(historyCoverage.available || 0) +
        ' 条更早的文字记录中选了 ' + Number(historyCoverage.included || 0) +
        ' 条可能相关的候选交给模型。'));
    }

    if (!data.analysis) {
      var empty = el('div', 'an-empty');
      if (!data.aiReady) {
        empty.appendChild(el('b', '', '服务端还没有配置文本模型'));
        empty.appendChild(el('div', '', data.missingHint ||
          '在服务端配置 DEEPSEEK_API_KEY 后即可使用分析。'));
      } else if (!data.stats || !data.stats.textCount) {
        empty.appendChild(el('b', '', '这个周期还没有文字记录'));
        empty.appendChild(el('div', '', '先写一点内容，再回来生成分析。图片只会统计数量，不读取内容。'));
      } else {
        empty.appendChild(el('b', '', '还没有生成' + periodName(data.period) + '分析'));
        empty.appendChild(el('div', '',
          '生成时会把这段时间的笔记文字发送给模型，图片只统计数量。结果只保存在自己的账号里。'));
        var make = el('button', 'btn primary sm', '生成' + periodName(data.period) + '分析');
        make.type = 'button';
        make.addEventListener('click', function () { generateAnalysis(false); });
        empty.appendChild(make);
      }
      box.appendChild(empty);
      return;
    }

    var a = data.analysis;
    box.appendChild(el('h2', 'an-headline', a.headline || '这段记录的分析'));
    if (a.summary) box.appendChild(el('p', 'an-summary', a.summary));

    var stats = data.stats || {};
    var statsBox = el('div', 'an-stats');
    statsBox.appendChild(analysisStat('条记录', Number(stats.noteCount || 0)));
    statsBox.appendChild(analysisStat('有记录天数', Number(stats.activeDays || 0)));
    statsBox.appendChild(analysisStat('重要 / 关键',
      Number((stats.importance && stats.importance.important) || 0) + ' / ' +
      Number((stats.importance && stats.importance.critical) || 0)));
    statsBox.appendChild(analysisStat('图片', Number(stats.imageCount || 0)));
    box.appendChild(statsBox);

    if (stats.daily && stats.daily.length > 1) {
      var chart = el('div', 'an-chart');
      var max = 1;
      stats.daily.forEach(function (item) { max = Math.max(max, Number(item.count || 0)); });
      stats.daily.forEach(function (item) {
        var count = Number(item.count || 0);
        var bar = el('span', 'an-chart-bar' + (count ? '' : ' zero'));
        bar.style.height = count ? Math.max(8, Math.round(count / max * 100)) + '%' : '2px';
        bar.title = item.day + ' · ' + count + ' 条';
        bar.setAttribute('aria-hidden', 'true');
        chart.appendChild(bar);
      });
      box.appendChild(chart);
    }

    if (stats.tags && stats.tags.length) {
      var tags = el('div', 'an-tags');
      stats.tags.forEach(function (item) {
        tags.appendChild(el('span', 'an-tag', '#' + item.name + ' ' + item.count));
      });
      box.appendChild(tags);
    }

    if (a.strengths && a.strengths.length) {
      var strengths = el('div', '');
      a.strengths.forEach(function (item) {
        var card = el('div', 'an-highlight');
        var head = el('div', 'an-highlight-head');
        if (item.day) head.appendChild(el('span', 'an-day', item.day));
        head.appendChild(el('span', 'an-highlight-title', item.title || '做得好的地方'));
        card.appendChild(head);
        if (item.evidence) card.appendChild(el('div', 'an-highlight-detail', '依据：' + item.evidence));
        strengths.appendChild(card);
      });
      box.appendChild(analysisSection('做得好的地方', [strengths]));
    }

    if (a.gaps && a.gaps.length) {
      var gaps = el('div', '');
      a.gaps.forEach(function (item) {
        var card = el('div', 'an-highlight');
        card.appendChild(el('span', 'an-highlight-title', item.title || '不足或卡点'));
        if (item.evidence) card.appendChild(el('div', 'an-highlight-detail', '依据：' + item.evidence));
        if (item.impact) card.appendChild(el('div', 'an-suggest-why', '影响：' + item.impact));
        gaps.appendChild(card);
      });
      box.appendChild(analysisSection('不足与卡点', [gaps]));
    }

    if (a.improvements && a.improvements.length) {
      var improvements = el('div', '');
      a.improvements.forEach(function (item) {
        var card = el('div', 'an-suggest');
        card.appendChild(el('div', 'an-suggest-action', item.action));
        if (item.how) card.appendChild(el('div', 'an-suggest-how', '做法：' + item.how));
        if (item.why) card.appendChild(el('div', 'an-suggest-why', '依据：' + item.why));
        improvements.appendChild(card);
      });
      box.appendChild(analysisSection('怎么改进', [improvements]));
    }

    var plan = a.nextPlan || {};
    if (plan.focus || (plan.adjustments && plan.adjustments.length) ||
        (plan.actions && plan.actions.length)) {
      var planBox = el('div', '');
      if (plan.focus) {
        var focus = el('div', 'an-next-focus');
        focus.appendChild(el('span', 'an-next-label', plan.label || '下一步'));
        focus.appendChild(document.createTextNode(' ' + plan.focus));
        planBox.appendChild(focus);
      }
      if (plan.adjustments && plan.adjustments.length) {
        var adjustList = el('ul', 'an-list');
        plan.adjustments.forEach(function (item) { adjustList.appendChild(el('li', '', item)); });
        planBox.appendChild(adjustList);
      }
      (plan.actions || []).forEach(function (item) {
        var card = el('div', 'an-suggest');
        var action = el('div', 'an-suggest-action', item.action);
        if (item.when) action.appendChild(el('span', 'an-when', item.when));
        card.appendChild(action);
        if (item.why) card.appendChild(el('div', 'an-suggest-why', '依据：' + item.why));
        planBox.appendChild(card);
      });
      box.appendChild(analysisSection('下一步计划', [planBox]));
    }

    if (a.connections && a.connections.length) {
      var connections = el('div', '');
      a.connections.forEach(function (item) {
        var card = el('div', 'an-conn');
        var head = el('div', 'an-conn-head');
        if (item.currentDay) head.appendChild(el('span', 'an-day', '当前 ' + item.currentDay));
        if (item.pastDay) head.appendChild(el('span', 'an-day', '过去 ' + item.pastDay));
        card.appendChild(head);
        if (item.current) {
          var currentRow = el('div', 'an-conn-row');
          currentRow.appendChild(el('span', 'an-conn-key', '当前'));
          currentRow.appendChild(el('span', 'an-conn-text', item.current));
          card.appendChild(currentRow);
        }
        if (item.past) {
          var pastRow = el('div', 'an-conn-row');
          pastRow.appendChild(el('span', 'an-conn-key', '过去'));
          pastRow.appendChild(el('span', 'an-conn-text', item.past));
          card.appendChild(pastRow);
        }
        if (item.relation) card.appendChild(el('div', 'an-conn-relation', '关联：' + item.relation));
        if (item.suggestion) {
          card.appendChild(el('div', 'an-conn-suggest', '可以怎么用：' + item.suggestion));
        }
        connections.appendChild(card);
      });
      box.appendChild(analysisSection('与过去记录的关联', [connections]));
    }

    if (a.highlights && a.highlights.length) {
      var highlights = el('div', '');
      a.highlights.forEach(function (item) {
        var card = el('div', 'an-highlight');
        var head = el('div', 'an-highlight-head');
        if (item.day) head.appendChild(el('span', 'an-day', item.day));
        if (item.title) head.appendChild(el('span', 'an-highlight-title', item.title));
        var level = Number(item.importance || 0);
        if (level > 0) head.appendChild(el('span', 'note-level lv' + level, levelName(level)));
        card.appendChild(head);
        if (item.detail) card.appendChild(el('div', 'an-highlight-detail', item.detail));
        highlights.appendChild(card);
      });
      box.appendChild(analysisSection('值得回看', [highlights]));
    }

    if (a.themes && a.themes.length) {
      var themes = el('div', '');
      a.themes.forEach(function (item) {
        var row = el('div', 'an-theme');
        row.appendChild(el('span', 'an-theme-name', item.name));
        if (item.count) row.appendChild(el('span', 'an-theme-count', item.count + ' 次'));
        if (item.note) row.appendChild(el('span', 'an-theme-note', item.note));
        themes.appendChild(row);
      });
      box.appendChild(analysisSection('主题', [themes]));
    }

    var blockers = analysisList('卡点', a.blockers);
    if (blockers) box.appendChild(blockers);
    var patterns = analysisList('反复出现的模式', a.patterns);
    if (patterns) box.appendChild(patterns);

    if (a.suggestions && a.suggestions.length) {
      var suggestions = el('div', '');
      a.suggestions.forEach(function (item) {
        var card = el('div', 'an-suggest');
        var action = el('div', 'an-suggest-action', item.action);
        if (item.when) action.appendChild(el('span', 'an-when', item.when));
        card.appendChild(action);
        if (item.why) card.appendChild(el('div', 'an-suggest-why', item.why));
        suggestions.appendChild(card);
      });
      box.appendChild(analysisSection('下一步', [suggestions]));
    }

    var questions = analysisList('可以继续想', a.questions);
    if (questions) box.appendChild(questions);

    box.appendChild(el('div', 'an-foot',
      '分析由模型根据你主动发送的笔记文字生成，可能有偏差；图片只统计数量，没有读取内容。' +
      '结果只保存在自己的账号里，可随时删除。'));
  }

  function renderAnalysis() {
    syncAnalysisPeriods();
    $('analysisDate').value = state.analysis.date;
    var data = state.analysis.data;
    $('analysisTitle').textContent = data && data.label
      ? data.label
      : analysisAnchorLabel(state.analysis.period, state.analysis.date);

    var meta = [];
    if (state.analysis.loading) meta.push('正在读取……');
    if (state.analysis.generating) meta.push('正在阅读记录并分析……');
    if (data && data.stats) {
      meta.push(Number(data.stats.noteCount || 0) + ' 条记录');
      meta.push('有记录 ' + Number(data.stats.activeDays || 0) + ' 天');
    }
    if (data && data.meta && data.meta.partial) {
      meta.push('阶段性分析 · 截至 ' + String(data.meta.asOf || '').slice(5));
    }
    if (data && data.meta && data.meta.generatedAt) {
      meta.push('上次生成 ' + String(data.meta.generatedAt).slice(0, 16));
    }
    if (data && data.quota) {
      meta.push('今日剩余 ' + Number(data.quota.remaining || 0) + ' / ' +
        Number(data.quota.dailyLimit || 0) + ' 次');
    }
    $('analysisMeta').textContent = meta.join(' · ');

    var hasAnalysis = !!(data && data.analysis);
    $('analysisAsk').hidden = !hasAnalysis;
    $('analysisCopy').hidden = !hasAnalysis;
    $('analysisDelete').hidden = !hasAnalysis;
    $('analysisGenerate').textContent = state.analysis.generating
      ? '分析中…' : (hasAnalysis ? '重新分析' : '生成分析');
    $('analysisGenerate').disabled = state.analysis.loading ||
      state.analysis.generating ||
      !!(data && !data.aiReady) ||
      !!(data && data.quota && Number(data.quota.remaining || 0) <= 0) ||
      !!(data && data.stats && !data.stats.textCount);

    document.querySelectorAll('#viewSwitch button').forEach(function (button) {
      var on = button.getAttribute('data-view') === state.view;
      button.classList.toggle('on', on);
      button.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    markActiveHeatmap();
    renderAnalysisBody();
  }

  function loadAnalysis() {
    var seq = ++state.analysis.seq;
    state.analysis.loading = true;
    state.analysis.error = '';
    renderAnalysis();
    var url = '/notes/api/analysis?period=' + encodeURIComponent(state.analysis.period) +
      '&date=' + encodeURIComponent(state.analysis.date);
    return api(url).then(function (data) {
      if (seq !== state.analysis.seq) return;
      state.analysis.data = data;
      state.analysis.loading = false;
      resetDeleteButton();
      renderAnalysis();
    }).catch(function (err) {
      if (seq !== state.analysis.seq) return;
      state.analysis.loading = false;
      state.analysis.data = null;
      state.analysis.error = err.message || '分析加载失败。';
      renderAnalysis();
    });
  }

  function generateAnalysis(force) {
    if (state.analysis.loading || state.analysis.generating) return;
    state.analysis.generating = true;
    state.analysis.error = '';
    renderAnalysis();
    api('/notes/api/analysis', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        period: state.analysis.period,
        date: state.analysis.date,
        force: !!force
      })
    }).then(function (data) {
      state.analysis.generating = false;
      state.analysis.data = data;
      resetDeleteButton();
      renderAnalysis();
      toast((data.meta && data.meta.message) || '分析完成。');
    }).catch(function (err) {
      state.analysis.generating = false;
      renderAnalysis();
      toast(err.message || '分析失败。', true);
    });
  }

  function copyAnalysis() {
    var text = analysisText(state.analysis.data);
    if (!text) return;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () {
        toast('分析已复制。');
      }).catch(function () {
        toast('复制失败，请手动选择文字。', true);
      });
      return;
    }
    var area = document.createElement('textarea');
    area.value = text;
    area.style.position = 'fixed';
    area.style.opacity = '0';
    document.body.appendChild(area);
    area.select();
    try {
      document.execCommand('copy');
      toast('分析已复制。');
    } catch (e) {
      toast('复制失败，请手动选择文字。', true);
    }
    document.body.removeChild(area);
  }

  function askAnalysis() {
    var data = state.analysis.data;
    var a = data && data.analysis;
    if (!a) return;
    if (!window.TBQuickAI || typeof window.TBQuickAI.open !== 'function') {
      toast('全站 AI 助手还没加载好，稍后再试。', true);
      return;
    }
    var lines = ['我有一份' + periodName(data.period) + '的笔记分析（' + data.label + '）：'];
    if (a.headline) lines.push('结论：' + a.headline);
    if (a.summary) lines.push('总结：' + a.summary);
    if (a.strengths && a.strengths.length) {
      lines.push('做得好的地方：');
      a.strengths.forEach(function (item) {
        lines.push('- ' + (item.title || '') + (item.evidence ? '：' + item.evidence : ''));
      });
    }
    if (a.gaps && a.gaps.length) {
      lines.push('不足与卡点：');
      a.gaps.forEach(function (item) {
        lines.push('- ' + (item.title || '') + (item.evidence ? '：' + item.evidence : ''));
      });
    }
    if (a.improvements && a.improvements.length) {
      lines.push('改进方向：');
      a.improvements.forEach(function (item) {
        lines.push('- ' + (item.action || '') + (item.how ? '：' + item.how : ''));
      });
    }
    var plan = a.nextPlan || {};
    if (plan.focus || (plan.actions && plan.actions.length)) {
      lines.push('下一步计划' + (plan.label ? '（' + plan.label + '）' : '') + '：');
      if (plan.focus) lines.push('重点：' + plan.focus);
      (plan.actions || []).forEach(function (item, index) {
        lines.push((index + 1) + '. ' + (item.action || '') +
          (item.when ? '（' + item.when + '）' : ''));
      });
    }
    if (a.connections && a.connections.length) {
      lines.push('与过去记录的关联：');
      a.connections.forEach(function (item) {
        lines.push('- ' + (item.currentDay || '') + ' ↔ ' + (item.pastDay || '') +
          '：' + (item.relation || ''));
      });
    }
    lines.push('请基于这些结论，帮我把下一步排个优先级，指出我可能忽略的地方，' +
      '再补充一些我没注意到的、当前和过去记录之间的联系。');
    window.TBQuickAI.open(lines.join('\n').slice(0, 2500));
  }

  function deleteAnalysis() {
    var data = state.analysis.data;
    if (!data || !data.analysis) return;
    var button = $('analysisDelete');
    if (button.getAttribute('data-armed') !== '1') {
      button.setAttribute('data-armed', '1');
      button.classList.add('confirm');
      button.textContent = '确认删除';
      setTimeout(resetDeleteButton, 3200);
      return;
    }
    button.disabled = true;
    button.textContent = '删除中…';
    var url = '/notes/api/analysis?period=' + encodeURIComponent(state.analysis.period) +
      '&date=' + encodeURIComponent(state.analysis.date);
    api(url, { method: 'DELETE' }).then(function () {
      toast('这份分析已删除。');
      state.analysis.data = null;
      return loadAnalysis();
    }).catch(function (err) {
      resetDeleteButton();
      toast(err.message || '删除失败。', true);
    });
  }

  function setAnalysisPeriod(period) {
    if (period !== 'day' && period !== 'week' && period !== 'month') return;
    if (state.analysis.period === period) return;
    state.analysis.period = period;
    state.analysis.data = null;
    state.analysis.error = '';
    syncAnalysisPeriods();
    loadAnalysis();
  }

  function setAnalysisDate(day) {
    if (!isDay(day)) return;
    if (state.analysis.date === day && state.analysis.data) {
      $('analysisDate').value = day;
      return;
    }
    state.analysis.date = day;
    state.analysis.data = null;
    state.analysis.error = '';
    $('analysisDate').value = day;
    var targetMonth = monthOf(day);
    if (targetMonth !== state.hmMonth) {
      state.hmMonth = targetMonth;
      loadHeatmap(targetMonth);
    }
    markActiveHeatmap();
    loadAnalysis();
  }

  function selectAnalysisDay(day) {
    setAnalysisDate(day);
  }

  function switchView(view) {
    if (view !== 'notes' && view !== 'analysis') return;
    if (state.view === view) return;
    if (state.view === 'notes') persistDraft();
    state.view = view;
    document.body.classList.toggle('view-analysis', view === 'analysis');
    $('analysisView').hidden = view !== 'analysis';
    if (view === 'analysis') {
      $('analysisDate').value = state.analysis.date;
      markActiveHeatmap();
      loadAnalysis();
    } else {
      updateDayHeader();
    }
  }

  // ---------------- 初始化与事件 ----------------
  function bindEvents() {
    $('saveBtn').addEventListener('click', saveNew);
    $('noteText').addEventListener('input', function () {
      scheduleDraft();
      updateSaveState();
    });
    $('tagInput').addEventListener('input', function () {
      scheduleDraft();
      updateSaveState();
    });
    Array.prototype.forEach.call(document.querySelectorAll('#levelFilter button'), function (button) {
      button.addEventListener('click', function () {
        setLevel(button.getAttribute('data-level') || '');
      });
    });
    $('levelSelect').addEventListener('change', function () {
      syncLevelSelect();
      scheduleDraft();
    });
    $('imgBtn').addEventListener('click', function () { $('fileInput').click(); });
    $('fileInput').addEventListener('change', function () {
      addComposerFiles(this.files);
      this.value = '';
    });
    Array.prototype.forEach.call(document.querySelectorAll('.quick button'), function (button) {
      button.addEventListener('click', function () { addQuickTag(button.getAttribute('data-tag')); });
    });

    var composer = $('composer');
    composer.addEventListener('paste', function (event) {
      var files = filesFromEvent(event);
      if (files.length) {
        event.preventDefault();
        addComposerFiles(files);
      }
    });
    ['dragenter', 'dragover'].forEach(function (name) {
      composer.addEventListener(name, function (event) {
        event.preventDefault();
        composer.classList.add('drag');
      });
    });
    ['dragleave', 'drop'].forEach(function (name) {
      composer.addEventListener(name, function (event) {
        event.preventDefault();
        composer.classList.remove('drag');
        if (name === 'drop') {
          addComposerFiles(event.dataTransfer ? event.dataTransfer.files : []);
        }
      });
    });
    composer.addEventListener('keydown', function (event) {
      if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
        event.preventDefault();
        saveNew();
      }
    });

    $('prevDay').addEventListener('click', function () { selectDay(shiftDay(state.day, -1)); });
    $('nextDay').addEventListener('click', function () { selectDay(shiftDay(state.day, 1)); });
    $('hmPrev').addEventListener('click', function () {
      var month = shiftMonth(state.hmMonth || monthOf(todayStr()), -1);
      if (month >= '1970-01') loadHeatmap(month);
    });
    $('hmNext').addEventListener('click', function () {
      var month = shiftMonth(state.hmMonth || monthOf(todayStr()), 1);
      if (month <= monthOf(todayStr())) loadHeatmap(month);
    });
    $('todayBtn').addEventListener('click', function () {
      if (state.view === 'analysis') setAnalysisDate(todayStr());
      else selectDay(todayStr());
    });
    $('dayInput').addEventListener('change', function () {
      if (isDay(this.value)) selectDay(this.value);
      else this.value = state.day;
    });
    $('searchInput').addEventListener('input', function () {
      var value = this.value.trim();
      state.q = value;
      if (value) state.tag = '';
      applySearchMode();
      renderTags(state.status ? state.status.tags || [] : []);
      clearTimeout(state.searchTimer);
      state.searchTimer = setTimeout(loadNotes, 300);
    });
    $('searchClear').addEventListener('click', clearSearch);
    $('searchBack').addEventListener('click', clearSearch);

    $('editForm').addEventListener('submit', submitEdit);
    $('editCancel').addEventListener('click', function () { $('editor').close(); });
    $('editImgBtn').addEventListener('click', function () { $('editFileInput').click(); });
    $('editFileInput').addEventListener('change', function () {
      addEditFiles(this.files);
      this.value = '';
    });
    $('editText').addEventListener('paste', function (event) {
      var files = filesFromEvent(event);
      if (files.length) {
        event.preventDefault();
        addEditFiles(files);
      }
    });
    $('editor').addEventListener('close', cleanupEdit);

    $('lightPrev').addEventListener('click', function () { moveLightbox(-1); });
    $('lightNext').addEventListener('click', function () { moveLightbox(1); });
    $('lightClose').addEventListener('click', closeLightbox);
    $('lightbox').addEventListener('click', function (event) {
      if (event.target === $('lightbox')) closeLightbox();
    });
    $('lightbox').addEventListener('keydown', function (event) {
      if (event.key === 'ArrowLeft') { event.preventDefault(); moveLightbox(-1); }
      if (event.key === 'ArrowRight') { event.preventDefault(); moveLightbox(1); }
    });
    $('lightbox').addEventListener('close', function () {
      state.light = null;
      $('lightImg').removeAttribute('src');
    });

    Array.prototype.forEach.call(document.querySelectorAll('#viewSwitch button'), function (button) {
      button.addEventListener('click', function () {
        switchView(button.getAttribute('data-view'));
      });
    });
    Array.prototype.forEach.call(document.querySelectorAll('#analysisPeriods button'), function (button) {
      button.addEventListener('click', function () {
        setAnalysisPeriod(button.getAttribute('data-period'));
      });
    });
    $('analysisPrev').addEventListener('click', function () {
      setAnalysisDate(shiftPeriod(state.analysis.period, state.analysis.date, -1));
    });
    $('analysisNext').addEventListener('click', function () {
      setAnalysisDate(shiftPeriod(state.analysis.period, state.analysis.date, 1));
    });
    $('analysisToday').addEventListener('click', function () {
      setAnalysisDate(todayStr());
    });
    $('analysisDate').addEventListener('change', function () {
      if (isDay(this.value)) setAnalysisDate(this.value);
      else this.value = state.analysis.date;
    });
    $('analysisGenerate').addEventListener('click', function () {
      generateAnalysis(!!(state.analysis.data && state.analysis.data.analysis));
    });
    $('analysisAsk').addEventListener('click', askAnalysis);
    $('analysisCopy').addEventListener('click', copyAnalysis);
    $('analysisDelete').addEventListener('click', deleteAnalysis);

    window.addEventListener('beforeunload', persistDraft);
    window.addEventListener('popstate', function () {
      var day = readURLDay();
      if (day && day !== state.day) selectDay(day);
    });
  }

  function filesFromEvent(event) {
    var files = [];
    if (event.clipboardData) {
      var items = event.clipboardData.items || [];
      for (var i = 0; i < items.length; i++) {
        if (items[i].kind === 'file' && String(items[i].type || '').indexOf('image/') === 0) {
          var file = items[i].getAsFile();
          if (file) files.push(file);
        }
      }
    }
    if (!files.length && event.dataTransfer && event.dataTransfer.files) {
      files = Array.prototype.slice.call(event.dataTransfer.files).filter(function (file) {
        return String(file.type || '').indexOf('image/') === 0;
      });
    }
    return files;
  }

  function init() {
    state.day = readURLDay() || todayStr();
    state.hmMonth = monthOf(state.day);
    state.analysis.date = state.day;
    bindEvents();
    renderLevelFilter();
    $('dayInput').value = state.day;
    $('analysisDate').value = state.analysis.date;
    updateURLDay(state.day);
    updateDayHeader();
    renderAnalysis();
    loadDraft(state.day);
    updateSaveState();
    applySearchMode();
    loadStatus().catch(function (err) { toast(err.message || '状态加载失败。', true); });
    loadNotes();
  }

  init();
})();
