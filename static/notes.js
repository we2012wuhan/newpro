(function () {
  'use strict';

  var state = {
    day: todayStr(),
    q: '',
    tag: '',
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
    searchTimer: 0,
    draftTimer: 0,
    draftHintTimer: 0,
    toastTimer: 0
  };

  var HM_WEEKS = 12;

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

  function renderDays(days) {
    var box = $('dayList');
    box.textContent = '';
    days = days || [];
    $('dayCount').textContent = days.length ? days.length + ' 天' : '';
    if (!days.length) {
      box.appendChild(el('div', 'side-empty', '还没有记录，从今天开始吧。'));
      return;
    }
    days.forEach(function (item) {
      var button = el('button', 'day-item' + (item.day === state.day ? ' on' : ''));
      button.type = 'button';
      button.title = dayLong(item.day);
      button.setAttribute('data-day', item.day);
      button.appendChild(el('span', 'd', dayShort(item.day)));
      button.appendChild(el('span', 'n', String(item.count || 0)));
      button.addEventListener('click', function () { selectDay(item.day); });
      box.appendChild(button);
    });
  }

  // 左侧热力图：最近 12 个自然周，记录越多颜色越深。
  function heatLevel(count) {
    if (count >= 5) return 4;
    if (count >= 3) return 3;
    if (count >= 2) return 2;
    if (count >= 1) return 1;
    return 0;
  }

  function renderHeatmap(days) {
    var grid = $('heatmap');
    if (!grid) return;
    var counts = {};
    (days || []).forEach(function (item) {
      if (item && isDay(item.day)) counts[item.day] = Number(item.count || 0);
    });
    var today = todayStr();
    var todayDate = parseDay(today);
    var mondayOffset = todayDate ? (todayDate.getUTCDay() + 6) % 7 : 0;
    var start = shiftDay(today, -(mondayOffset + (HM_WEEKS - 1) * 7));
    grid.textContent = '';
    grid.setAttribute('aria-label', dayShort(start) + ' 至 ' + dayShort(today) + ' 的每日记录热力图');
    for (var i = 0; i < HM_WEEKS * 7; i++) {
      var day = shiftDay(start, i);
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
      cell.title = dayLong(day) + (future ? ' · 还没到' : ' · ' + (count ? count + ' 条记录' : '没有记录'));
      cell.setAttribute('aria-label', cell.title);
      cell.addEventListener('click', function () {
        selectDay(this.getAttribute('data-day'));
      });
      grid.appendChild(cell);
    }
    markActiveHeatmap();
  }

  function markActiveHeatmap() {
    var cells = document.querySelectorAll('.hm-cell[data-day]');
    Array.prototype.forEach.call(cells, function (cell) {
      cell.classList.toggle('is-sel', cell.getAttribute('data-day') === state.day);
    });
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

  function markActiveDay() {
    var items = document.querySelectorAll('.day-item');
    Array.prototype.forEach.call(items, function (item) {
      item.classList.toggle('on', item.getAttribute('data-day') === state.day);
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
    $('searchText').textContent = '正在搜索 ' + parts.join(' + ');
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
      renderDays(data.days || []);
      renderHeatmap(data.days || []);
      renderStreak(data.streak || {});
      renderTags(data.tags || []);
      renderUsage(data);
      $('stat').textContent = data.total ? '共 ' + data.total + ' 条记录' : '';
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
      $('dayTitle').appendChild(el('small', '', state.notes.length + ' 条'));
    }
    $('composerTitle').textContent = (state.day === todayStr() ? '今天' : dayShort(state.day)) + ' · 写一条';
    markActiveDay();
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
        empty.appendChild(el('div', '', '没有找到匹配的记录。'));
        var clear = el('button', 'btn sm ghost', '清除筛选');
        clear.type = 'button';
        clear.style.marginTop = '10px';
        clear.addEventListener('click', clearSearch);
        empty.appendChild(clear);
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
    state.day = day;
    state.q = '';
    state.tag = '';
    $('searchInput').value = '';
    clearQueue();
    loadDraft(day);
    applySearchMode();
    updateURLDay(day);
    updateDayHeader();
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
      return { text: String(data.text || ''), tags: String(data.tags || '') };
    } catch (e) {
      return null;
    }
  }

  function loadDraft(day) {
    var data = readDraft(day);
    $('noteText').value = data ? data.text : '';
    $('tagInput').value = data ? data.tags : '';
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
    try {
      if (!text.trim() && !tags.trim()) {
        localStorage.removeItem(draftKey(state.day));
      } else {
        localStorage.setItem(draftKey(state.day), JSON.stringify({ text: text, tags: tags }));
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
    $('todayBtn').addEventListener('click', function () { selectDay(todayStr()); });
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
    bindEvents();
    $('dayInput').value = state.day;
    updateURLDay(state.day);
    updateDayHeader();
    loadDraft(state.day);
    updateSaveState();
    applySearchMode();
    loadStatus().catch(function (err) { toast(err.message || '状态加载失败。', true); });
    loadNotes();
  }

  init();
})();
