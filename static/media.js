/* 影音库前端：目录浏览、音频面板、视频弹窗、收藏与断点续播。
 * 播放地址统一走 /media/stream?path=...，后端负责向 AList 要直链并转发 Range。
 * 浏览器不会拿到 AList 账号密码；收藏和进度只写本地 SQLite。
 */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };
  var tabsEl = $('tabs');
  var listEl = $('list');
  var loadingEl = $('loading');
  var emptyEl = $('emptyState');
  var emptyIcon = $('emptyIcon');
  var emptyTitle = $('emptyTitle');
  var emptyDesc = $('emptyDesc');
  var emptyRetry = $('emptyRetry');
  var crumbsEl = $('crumbs');
  var searchEl = $('search');
  var listTitle = $('listTitle');
  var listMeta = $('listMeta');
  var listHint = $('listHint');
  var banner = $('statusBanner');
  var bannerText = $('bannerText');
  var bannerRetry = $('bannerRetry');
  var rootLabel = $('rootLabel');
  var nowPanel = $('nowPanel');
  var nowIcon = $('nowIcon');
  var nowTitle = $('nowTitle');
  var nowKind = $('nowKind');
  var nowMeta = $('nowMeta');
  var nowFav = $('nowFav');
  var nowClose = $('nowClose');
  var audio = $('audioEl');
  var videoModal = $('videoModal');
  var videoEl = $('videoEl');
  var videoTitle = $('videoTitle');
  var videoMeta = $('videoMeta');
  var videoFav = $('videoFav');
  var videoClose = $('videoClose');
  var toastEl = $('toast');

  var TAB_LABEL = { music: '音乐', video: '视频', favorites: '收藏', recent: '最近播放' };
  var state = {
    tab: 'music',
    path: '/',
    items: [],
    crumbs: [],
    query: '',
    status: null,
    current: null,   // 当前音频
    video: null,     // 当前视频
    truncated: false
  };
  var listSeq = 0;
  var statusSeq = 0;
  var toastTimer = null;
  var lastAudioSave = 0;
  var lastVideoSave = 0;

  function isBrowse() {
    return state.tab === 'music' || state.tab === 'video';
  }

  function streamUrl(path) {
    return '/media/stream?path=' + encodeURIComponent(path);
  }

  function fetchJson(url, options) {
    return fetch(url, options).then(function (resp) {
      return resp.json().catch(function () { return {}; }).then(function (data) {
        if (!resp.ok || !data || data.ok === false) {
          var msg = (data && data.error) || ('请求失败（HTTP ' + resp.status + '）');
          throw new Error(msg);
        }
        return data;
      });
    });
  }

  function showToast(message, kind) {
    if (!toastEl) { return; }
    toastEl.textContent = message;
    toastEl.className = 'toast show' + (kind === 'err' ? ' err' : '');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      toastEl.className = 'toast';
    }, 3200);
  }

  function showBanner(message) {
    bannerText.textContent = message;
    banner.hidden = false;
  }

  function hideBanner() {
    banner.hidden = true;
  }

  function formatSize(bytes) {
    var n = Number(bytes) || 0;
    if (n <= 0) { return ''; }
    if (n < 1024) { return n + ' B'; }
    if (n < 1024 * 1024) { return (n / 1024).toFixed(n < 10 * 1024 ? 1 : 0) + ' KB'; }
    if (n < 1024 * 1024 * 1024) { return (n / 1024 / 1024).toFixed(n < 10 * 1024 * 1024 ? 1 : 0) + ' MB'; }
    return (n / 1024 / 1024 / 1024).toFixed(2) + ' GB';
  }

  function formatDate(value) {
    var s = String(value || '');
    var m = s.match(/\d{4}-\d{2}-\d{2}/);
    return m ? m[0] : '';
  }

  function formatClock(seconds) {
    var s = Math.max(0, Math.floor(Number(seconds) || 0));
    var h = Math.floor(s / 3600);
    var m = Math.floor((s % 3600) / 60);
    var sec = s % 60;
    if (h) {
      return h + ':' + String(m).padStart(2, '0') + ':' + String(sec).padStart(2, '0');
    }
    return m + ':' + String(sec).padStart(2, '0');
  }

  function displayName(item) {
    return item.name || item.title || '未命名';
  }

  function fileMeta(item) {
    if (!item) { return ''; }
    if (item.is_dir) { return '文件夹'; }
    var parts = [];
    var ext = String(item.ext || '').replace('.', '').toUpperCase();
    if (ext) { parts.push(ext); }
    var size = formatSize(item.size);
    if (size) { parts.push(size); }
    var date = formatDate(item.modified);
    if (date) { parts.push(date); }
    var pos = Number(item.position) || 0;
    var dur = Number(item.duration) || 0;
    if (pos > 1) {
      parts.push('看到 ' + formatClock(pos) + (dur > 1 ? ' / ' + formatClock(dur) : ''));
    }
    return parts.join(' · ');
  }

  function iconFor(item) {
    if (!item || item.is_dir) { return '📁'; }
    return item.kind === 'video' ? '🎬' : '🎵';
  }

  function resumeFor(item) {
    var pos = Number(item && item.position) || 0;
    var dur = Number(item && item.duration) || 0;
    if (pos > 3 && (!dur || pos < dur - 5)) { return pos; }
    return 0;
  }

  function syncTabs() {
    Array.prototype.forEach.call(tabsEl.querySelectorAll('.tab'), function (btn) {
      var on = btn.getAttribute('data-tab') === state.tab;
      btn.classList.toggle('on', on);
      btn.setAttribute('aria-current', on ? 'page' : 'false');
    });
  }

  function updateUrl() {
    var params = new URLSearchParams();
    params.set('tab', state.tab);
    if (isBrowse()) { params.set('path', state.path || '/'); }
    try {
      history.replaceState(null, '', location.pathname + '?' + params.toString());
    } catch (e) { /* 某些内嵌浏览器不允许改地址，忽略 */ }
  }

  function readUrl() {
    try {
      var params = new URLSearchParams(location.search);
      var tab = params.get('tab');
      if (TAB_LABEL[tab]) { state.tab = tab; }
      var path = params.get('path');
      if (isBrowse() && path) { state.path = path; }
    } catch (e) { /* 忽略坏参数 */ }
  }

  function showLoading() {
    loadingEl.hidden = false;
    listEl.hidden = true;
    emptyEl.hidden = true;
  }

  function showEmpty(icon, title, desc, retry) {
    loadingEl.hidden = true;
    listEl.hidden = true;
    emptyEl.hidden = false;
    emptyIcon.textContent = icon;
    emptyTitle.textContent = title;
    emptyDesc.textContent = desc || '';
    emptyRetry.hidden = !retry;
  }

  function showSetupEmpty(configured) {
    if (configured) {
      showEmpty('🔌', '影音库还没连上 AList',
        '检查服务器上的 alist / openlist 容器是否在运行，以及 .env 里的 ALIST_URL、账号密码是否正确。', true);
    } else {
      showEmpty('⚙️', '还差一步配置',
        '在 .env 里填上 ALIST_PASSWORD（以及 ALIST_USERNAME / ALIST_URL），重启服务后刷新本页。', true);
    }
    listTitle.textContent = '影音库';
    listMeta.textContent = '';
    listHint.textContent = '';
    crumbsEl.textContent = '';
  }

  function loadStatus(force) {
    var my = ++statusSeq;
    showLoading();
    fetch('/media/api/status', { cache: 'no-store' })
      .then(function (resp) { return resp.json(); })
      .then(function (data) {
        if (my !== statusSeq) { return; }
        state.status = data || {};
        var root = data.root || '/';
        rootLabel.textContent = '根目录 ' + root;
        // ALIST_ROOT 不是 / 时，页面默认路径必须落到这个根下面，
        // 否则第一次列目录就会被后端拒绝。
        if (isBrowse() && (!state.path || state.path === '/' ||
            (root !== '/' && state.path !== root && state.path.indexOf(root + '/') !== 0))) {
          state.path = root;
          updateUrl();
        }
        if (!data.configured) {
          showBanner('还没有配置 ALIST_PASSWORD。\n在 .env 里填上 AList 管理员账号和密码，然后重启服务。');
          showSetupEmpty(false);
          return;
        }
        if (!data.connected) {
          showBanner('影音库连不上 AList：' + (data.error || '请检查容器状态。') +
            '\n检查 docker compose 里的 alist 服务，以及 .env 里的 ALIST_URL / 账号密码。');
          showSetupEmpty(true);
          return;
        }
        hideBanner();
        loadList(!!force);
      })
      .catch(function () {
        if (my !== statusSeq) { return; }
        state.status = { ok: false, configured: false, connected: false };
        showBanner('影音库状态接口请求失败，请确认服务是否在运行。');
        showSetupEmpty(false);
      });
  }

  function loadList(force) {
    if (state.status && state.status.ok === false) {
      showSetupEmpty(!!state.status.configured);
      return;
    }
    if (!state.status) {
      loadStatus(force);
      return;
    }
    var my = ++listSeq;
    showLoading();
    var url;
    if (state.tab === 'favorites') {
      url = '/media/api/favorites';
    } else if (state.tab === 'recent') {
      url = '/media/api/recent';
    } else {
      url = '/media/api/list?path=' + encodeURIComponent(state.path || '/') +
        '&kind=' + encodeURIComponent(state.tab === 'video' ? 'video' : 'music') +
        (force ? '&refresh=1' : '');
    }
    fetchJson(url, { cache: 'no-store' })
      .then(function (data) {
        if (my !== listSeq) { return; }
        state.items = Array.isArray(data.items) ? data.items : [];
        state.truncated = !!data.truncated;
        state.crumbs = Array.isArray(data.crumbs) ? data.crumbs : [];
        if (!isBrowse()) {
          state.crumbs = [{ name: TAB_LABEL[state.tab], path: '' }];
        }
        hideBanner();
        render();
      })
      .catch(function (err) {
        if (my !== listSeq) { return; }
        state.items = [];
        state.truncated = false;
        showBanner(err.message || '读取目录失败。');
        showEmpty('⚠️', '这一次没有读出来', err.message || '请稍后重试。', true);
      });
  }

  function filteredItems() {
    if (!state.query) { return state.items.slice(); }
    var q = state.query.toLowerCase();
    return state.items.filter(function (item) {
      return (item.name + ' ' + (item.title || '')).toLowerCase().indexOf(q) !== -1;
    });
  }

  function render() {
    renderCrumbs();
    var items = filteredItems();
    updateHead(items);
    renderList(items);
  }

  function renderCrumbs() {
    crumbsEl.textContent = '';
    if (!isBrowse()) {
      var flat = document.createElement('span');
      flat.className = 'crumb on';
      flat.textContent = TAB_LABEL[state.tab] || '影音库';
      crumbsEl.appendChild(flat);
      return;
    }
    var crumbs = state.crumbs.length ? state.crumbs : [{ name: '影音库', path: '/' }];
    crumbs.forEach(function (crumb, index) {
      if (index) {
        var sep = document.createElement('span');
        sep.className = 'crumb-sep';
        sep.textContent = '›';
        crumbsEl.appendChild(sep);
      }
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'crumb' + (index === crumbs.length - 1 ? ' on' : '');
      btn.textContent = crumb.name || '/';
      btn.title = crumb.path || '/';
      btn.addEventListener('click', function () {
        if (index === crumbs.length - 1) { return; }
        navigate(crumb.path || '/');
      });
      crumbsEl.appendChild(btn);
    });
  }

  function updateHead(items) {
    var label = TAB_LABEL[state.tab] || '影音库';
    if (isBrowse() && state.crumbs.length) {
      label = state.crumbs[state.crumbs.length - 1].name || label;
    }
    listTitle.textContent = label;
    if (state.query) {
      listMeta.textContent = '匹配 ' + items.length + ' / ' + state.items.length + ' 项';
    } else {
      listMeta.textContent = '共 ' + state.items.length + ' 项';
    }
    if (isBrowse()) {
      listHint.textContent = '只显示音乐和视频，其他文件已隐藏 · 文件夹可继续进入' +
        (state.truncated ? ' · 目录较大，只显示前 500 项' : '');
    } else if (state.tab === 'favorites') {
      listHint.textContent = '点右侧星标可以取消收藏';
    } else {
      listHint.textContent = '按最近播放排序';
    }
  }

  function renderList(items) {
    loadingEl.hidden = true;
    listEl.textContent = '';
    if (!items.length) {
      listEl.hidden = true;
      if (state.query) {
        showEmpty('🔎', '没有匹配项', '当前列表里找不到「' + state.query + '」。', false);
      } else if (state.tab === 'favorites') {
        showEmpty('⭐', '还没有收藏', '播放时点右侧星标，或在播放器里点收藏。', true);
      } else if (state.tab === 'recent') {
        showEmpty('🕘', '还没有播放记录', '播放过的音乐和视频会出现在这里，下次可以从上次的位置继续。', true);
      } else {
        showEmpty('📂', '这个文件夹是空的', '没有找到音乐或视频文件。其他类型的文件不会显示。', true);
      }
      return;
    }
    emptyEl.hidden = true;
    listEl.hidden = false;
    var frag = document.createDocumentFragment();
    items.forEach(function (item) {
      frag.appendChild(buildItem(item));
    });
    listEl.appendChild(frag);
  }

  function buildItem(item) {
    var article = document.createElement('article');
    article.className = 'item ' + (item.is_dir ? 'dir' : (item.kind || 'file'));

    var main = document.createElement('button');
    main.type = 'button';
    main.className = 'item-main';

    var cover = document.createElement('span');
    cover.className = 'cover';
    cover.setAttribute('aria-hidden', 'true');
    cover.textContent = iconFor(item);

    var info = document.createElement('span');
    info.className = 'item-info';
    var title = document.createElement('b');
    title.textContent = displayName(item);
    title.title = item.path || displayName(item);
    if (!item.is_dir && !item.playable) {
      var badge = document.createElement('span');
      badge.className = 'badge';
      badge.textContent = '可能不支持';
      title.appendChild(badge);
    }
    var meta = document.createElement('small');
    meta.textContent = fileMeta(item) || (item.is_dir ? '文件夹' : '');
    info.appendChild(title);
    info.appendChild(meta);

    var pos = Number(item.position) || 0;
    var dur = Number(item.duration) || 0;
    if (pos > 1 && dur > 0) {
      var bar = document.createElement('span');
      bar.className = 'mini-bar';
      var fill = document.createElement('i');
      fill.style.width = Math.max(1, Math.min(100, (pos / dur) * 100)) + '%';
      bar.appendChild(fill);
      info.appendChild(bar);
    }

    var go = document.createElement('span');
    go.className = 'item-go';
    go.textContent = item.is_dir ? '›' : '▶';

    main.appendChild(cover);
    main.appendChild(info);
    main.appendChild(go);
    main.addEventListener('click', function () { openItem(item); });
    article.appendChild(main);

    if (!item.is_dir) {
      var star = document.createElement('button');
      star.type = 'button';
      star.className = 'star' + (item.favorite ? ' on' : '');
      star.textContent = item.favorite ? '★' : '☆';
      star.title = item.favorite ? '取消收藏' : '收藏';
      star.setAttribute('aria-label', star.title);
      star.addEventListener('click', function (event) {
        event.stopPropagation();
        toggleFavorite(item);
      });
      article.appendChild(star);
    }
    return article;
  }

  function navigate(path) {
    state.path = path || '/';
    state.query = '';
    searchEl.value = '';
    updateUrl();
    loadList();
  }

  function openItem(item) {
    if (item.is_dir) {
      navigate(item.path);
      return;
    }
    if (!item.playable) {
      showToast('浏览器可能不支持这个格式，仍会尝试播放。');
    }
    if (item.kind === 'video') {
      openVideo(item);
    } else {
      playAudio(item);
    }
  }

  function syncFavButtons() {
    var audioItem = state.current;
    nowFav.classList.toggle('on', !!(audioItem && audioItem.favorite));
    nowFav.textContent = audioItem && audioItem.favorite ? '★' : '☆';
    nowFav.title = audioItem && audioItem.favorite ? '取消收藏' : '收藏';
    var videoItem = state.video;
    videoFav.classList.toggle('on', !!(videoItem && videoItem.favorite));
    videoFav.textContent = videoItem && videoItem.favorite ? '★ 已收藏' : '☆ 收藏';
  }

  function toggleFavorite(item) {
    if (!item || item.is_dir) { return; }
    var before = !!item.favorite;
    var next = !before;
    item.favorite = next;
    render();
    syncFavButtons();
    fetchJson('/media/api/favorite', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        path: item.path,
        favorite: next,
        title: displayName(item)
      })
    }).then(function (data) {
      item.favorite = !!(data.item && data.item.favorite);
      if (state.tab === 'favorites' && !item.favorite) {
        state.items = state.items.filter(function (row) { return row.path !== item.path; });
      }
      render();
      syncFavButtons();
      showToast(item.favorite ? '已收藏' : '已取消收藏');
    }).catch(function (err) {
      item.favorite = before;
      render();
      syncFavButtons();
      showToast(err.message || '收藏没有保存成功。', 'err');
    });
  }

  // ---------------- 音频 ----------------
  function playAudio(item) {
    if (state.video) { closeVideo(true); }
    if (state.current && state.current.path !== item.path) {
      saveAudio(true);
    }
    state.current = item;
    nowPanel.hidden = false;
    nowIcon.textContent = '🎵';
    nowTitle.textContent = displayName(item);
    nowTitle.title = item.path;
    nowKind.textContent = '音乐';
    nowMeta.textContent = fileMeta(item);
    syncFavButtons();

    var resume = resumeFor(item);
    audio.src = streamUrl(item.path);
    audio.load();
    var onMeta = function () {
      audio.removeEventListener('loadedmetadata', onMeta);
      if (resume > 3) {
        try { audio.currentTime = resume; } catch (e) { /* 还没准备好就忽略 */ }
      }
      audio.play().then(function () {
        if (resume > 3) { showToast('从 ' + formatClock(resume) + ' 继续播放'); }
      }).catch(function () {
        showToast('浏览器阻止了自动播放，请点播放键。', 'err');
      });
    };
    audio.addEventListener('loadedmetadata', onMeta);
  }

  function saveAudio(force, reset) {
    var item = state.current;
    if (!item) { return; }
    var now = Date.now();
    if (!force && now - lastAudioSave < 5000) { return; }
    lastAudioSave = now;
    var dur = isFinite(audio.duration) ? audio.duration : (Number(item.duration) || 0);
    var pos = reset ? 0 : (isFinite(audio.currentTime) ? audio.currentTime : 0);
    if (reset) {
      item.position = 0;
      item.duration = dur || item.duration;
    }
    sendProgress(item, pos, dur, false);
  }

  // ---------------- 视频 ----------------
  function openVideo(item) {
    if (state.current) {
      audio.pause();
      saveAudio(true);
    }
    if (state.video && state.video.path !== item.path) {
      saveVideo(true);
    }
    state.video = item;
    videoModal.hidden = false;
    document.body.style.overflow = 'hidden';
    videoTitle.textContent = displayName(item);
    videoTitle.title = item.path;
    videoMeta.textContent = fileMeta(item);
    syncFavButtons();

    var resume = resumeFor(item);
    videoEl.src = streamUrl(item.path);
    videoEl.load();
    var onMeta = function () {
      videoEl.removeEventListener('loadedmetadata', onMeta);
      if (resume > 3) {
        try { videoEl.currentTime = resume; } catch (e) { /* 忽略 */ }
      }
      videoEl.play().then(function () {
        if (resume > 3) { showToast('从 ' + formatClock(resume) + ' 继续播放'); }
      }).catch(function () {
        showToast('浏览器阻止了自动播放，请点播放键。', 'err');
      });
    };
    videoEl.addEventListener('loadedmetadata', onMeta);
  }

  function closeVideo(save) {
    if (!state.video) { return; }
    if (save) { saveVideo(true); }
    videoEl.pause();
    videoEl.removeAttribute('src');
    videoEl.load();
    state.video = null;
    videoModal.hidden = true;
    document.body.style.overflow = '';
    syncFavButtons();
  }

  function saveVideo(force, reset) {
    var item = state.video;
    if (!item) { return; }
    var now = Date.now();
    if (!force && now - lastVideoSave < 5000) { return; }
    lastVideoSave = now;
    var dur = isFinite(videoEl.duration) ? videoEl.duration : (Number(item.duration) || 0);
    var pos = reset ? 0 : (isFinite(videoEl.currentTime) ? videoEl.currentTime : 0);
    if (reset) {
      item.position = 0;
      item.duration = dur || item.duration;
    }
    sendProgress(item, pos, dur, false);
  }

  function sendProgress(item, position, duration, keepalive) {
    if (!item) { return; }
    try {
      fetch('/media/api/progress', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          path: item.path,
          title: displayName(item),
          kind: item.kind,
          position: Math.max(0, Number(position) || 0),
          duration: Math.max(0, Number(duration) || 0)
        }),
        keepalive: !!keepalive
      }).then(function (resp) { return resp.json(); }).then(function (data) {
        if (data && data.ok && data.item) {
          item.position = data.item.position;
          item.duration = data.item.duration;
        }
      }).catch(function () { /* 进度失败不打扰播放 */ });
    } catch (e) { /* 忽略 */ }
  }

  // ---------------- 事件 ----------------
  tabsEl.addEventListener('click', function (event) {
    var btn = event.target.closest ? event.target.closest('.tab') : null;
    if (!btn) { return; }
    var tab = btn.getAttribute('data-tab');
    if (!TAB_LABEL[tab]) { return; }
    var same = tab === state.tab;
    state.tab = tab;
    state.query = '';
    searchEl.value = '';
    if (isBrowse()) {
      state.path = same && state.path ? state.path : '/';
    }
    syncTabs();
    updateUrl();
    loadList();
  });

  searchEl.addEventListener('input', function () {
    state.query = String(searchEl.value || '').trim().toLowerCase();
    render();
  });

  nowFav.addEventListener('click', function () {
    if (state.current) { toggleFavorite(state.current); }
  });

  nowClose.addEventListener('click', function () {
    if (state.current) { saveAudio(true); }
    audio.pause();
    audio.removeAttribute('src');
    audio.load();
    state.current = null;
    nowPanel.hidden = true;
  });

  videoFav.addEventListener('click', function () {
    if (state.video) { toggleFavorite(state.video); }
  });

  videoClose.addEventListener('click', function () { closeVideo(true); });
  videoModal.addEventListener('click', function (event) {
    if (event.target === videoModal) { closeVideo(true); }
  });

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && !videoModal.hidden) {
      closeVideo(true);
    }
  });

  audio.addEventListener('timeupdate', function () {
    if (!state.current) { return; }
    var dur = isFinite(audio.duration) ? audio.duration : 0;
    if (dur > 0) {
      nowMeta.textContent = formatClock(audio.currentTime) + ' / ' + formatClock(dur) +
        ' · ' + displayName(state.current);
    }
    saveAudio(false);
  });
  audio.addEventListener('pause', function () {
    // 切换音源时 load() 也会触发 pause，但 currentTime 是 0；
    // 不挡掉的话，刚点开的新音频会把原来的续播位置覆盖成 0。
    if (!audio.ended && state.current && audio.readyState > 0 && audio.currentTime > 0.5) {
      saveAudio(true);
    }
  });
  audio.addEventListener('ended', function () {
    if (!state.current) { return; }
    saveAudio(true, true);
    showToast('播放完了');
  });
  audio.addEventListener('error', function () {
    if (state.current) {
      showToast('这个音频没法在浏览器里播放，可能是编码或云盘直链的问题。', 'err');
    }
  });

  videoEl.addEventListener('timeupdate', function () {
    if (!state.video) { return; }
    var dur = isFinite(videoEl.duration) ? videoEl.duration : 0;
    if (dur > 0) {
      videoMeta.textContent = formatClock(videoEl.currentTime) + ' / ' + formatClock(dur) +
        ' · ' + displayName(state.video);
    }
    saveVideo(false);
  });
  videoEl.addEventListener('pause', function () {
    if (!videoEl.ended && state.video && videoEl.readyState > 0 && videoEl.currentTime > 0.5) {
      saveVideo(true);
    }
  });
  videoEl.addEventListener('ended', function () {
    if (!state.video) { return; }
    saveVideo(true, true);
    showToast('播放完了');
  });
  videoEl.addEventListener('error', function () {
    if (state.video) {
      showToast('这个视频没法在浏览器里播放，可能是 MKV / HEVC 编码不支持。', 'err');
    }
  });

  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'hidden') {
      if (state.current) { saveAudio(true); }
      if (state.video) { saveVideo(true); }
    }
  });

  window.addEventListener('pagehide', function () {
    if (state.current) {
      var ad = isFinite(audio.duration) ? audio.duration : (Number(state.current.duration) || 0);
      var ap = isFinite(audio.currentTime) ? audio.currentTime : 0;
      sendProgress(state.current, ap, ad, true);
    }
    if (state.video) {
      var vd = isFinite(videoEl.duration) ? videoEl.duration : (Number(state.video.duration) || 0);
      var vp = isFinite(videoEl.currentTime) ? videoEl.currentTime : 0;
      sendProgress(state.video, vp, vd, true);
    }
  });

  $('refreshBtn').addEventListener('click', function () { loadStatus(true); });
  bannerRetry.addEventListener('click', function () { loadStatus(true); });
  emptyRetry.addEventListener('click', function () {
    if (!state.status || !state.status.ok) { loadStatus(true); } else { loadList(true); }
  });

  // 启动
  readUrl();
  syncTabs();
  updateUrl();
  loadStatus();
})();
