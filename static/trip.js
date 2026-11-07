/* 出行攻略 /trip
   ------------------------------------------------------------
   高德负责事实（路线 / 天气 / POI），模型负责挑点和写理由。
   前端只做三件事：收集条件、展示服务端算好的时间线、按需加载高德地图。
   地图的 JS Key 和安全密钥由 /trip/api/status 下发；Web 服务 Key 永远不出服务端。 */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };
  var S = {
    status: null,
    plan: null,
    savedId: null,
    busy: false,
    stageTimer: null,
    map: null,
    mapMarkers: [],
    amapPromise: null,
    originCoords: null,
    history: []
  };
  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (ch) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
    });
  }

  function today() {
    var d = new Date();
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' +
      String(d.getDate()).padStart(2, '0');
  }

  function toast(text) {
    var el = $('toast');
    if (!el) { return; }
    el.textContent = text;
    el.classList.add('show');
    clearTimeout(el._timer);
    el._timer = setTimeout(function () { el.classList.remove('show'); }, 2600);
  }

  async function api(url, options) {
    var opts = options || {};
    opts.headers = Object.assign({ 'Content-Type': 'application/json' }, opts.headers || {});
    var resp = await fetch(url, opts);
    var data = await resp.json().catch(function () { return {}; });
    if (!resp.ok || data.ok === false) {
      throw new Error(data.message || ('请求失败（' + resp.status + '）'));
    }
    return data;
  }

  // ---------------- 配置状态 ----------------
  function applyStatus(data) {
    S.status = data || {};
    var pill = $('statusPill');
    var txt = $('statusTxt');
    var banner = $('configBanner');
    var ico = $('bannerIco');
    var body = $('bannerText');
    var readyMap = !!S.status.map_ready;
    var readyAmap = !!S.status.amap_ready;
    var readyAi = !!S.status.ai_ready;

    if (readyAmap && (readyMap || readyAi)) {
      pill.className = 'pill ' + (readyMap && readyAi ? 'ok' : 'warn');
      txt.textContent = readyMap && readyAi ? '高德 + 地图已就绪' : '路线可用 · 部分配置待补';
    } else if (!readyAmap) {
      pill.className = 'pill warn';
      txt.textContent = '演示模式 · 未配置高德';
    } else {
      pill.className = 'pill warn';
      txt.textContent = '高德已就绪';
    }

    if (readyAmap && readyMap && readyAi) {
      banner.hidden = true;
      return;
    }

    banner.hidden = false;
    banner.className = 'banner ' + (readyAmap ? '' : 'warn');
    ico.textContent = readyAmap ? 'ⓘ' : '⚠';
    if (!readyAmap) {
      body.innerHTML = '<b>现在是演示模式。</b>还没配置高德 Web 服务 Key ' +
        '<code>AMAP_WEB_KEY</code>，生成出来的地点、路线和天气都是示例，只用来预览界面。' +
        '填好 Key 后，同一套页面会直接换成真实查询。';
    } else if (!readyMap) {
      body.innerHTML = '<b>路线可以生成，但结果页暂时不显示地图。</b>要显示地图，需要在 <code>.env</code> ' +
        '里补上 <code>AMAP_JS_KEY</code> 和 <code>AMAP_SECURITY_CODE</code>，并把域名加进高德 JS API 的白名单。';
    } else if (!readyAi) {
      body.innerHTML = '<b>高德已就绪，模型还没配置。</b>现在会按高德评分自动排一条基础行程；' +
        '配置模型 Key 后，AI 会参与挑点和写理由。';
    }
  }

  async function loadStatus() {
    try {
      var data = await api('/trip/api/status');
      applyStatus(data);
    } catch (err) {
      $('statusPill').className = 'pill warn';
      $('statusTxt').textContent = '状态未知';
    }
    if (S.plan) { renderMap(S.plan); }
  }

  // ---------------- 表单 ----------------
  function selectedChips(container) {
    if (!container) { return []; }
    return Array.prototype.map.call(
      container.querySelectorAll('.chip.on'),
      function (btn) { return btn.getAttribute('data-pref') || ''; }
    ).filter(Boolean);
  }

  function bindChipGroup(id, single, attr) {
    var box = $(id);
    if (!box) { return; }
    box.addEventListener('click', function (event) {
      var btn = event.target.closest('.chip');
      if (!btn || !box.contains(btn)) { return; }
      if (single) {
        Array.prototype.forEach.call(box.querySelectorAll('.chip'), function (chip) {
          var on = chip === btn;
          chip.classList.toggle('on', on);
          chip.setAttribute('aria-pressed', on ? 'true' : 'false');
        });
        return;
      }
      if (!btn.classList.contains('on') && selectedChips(box).length >= 3) {
        toast('偏好最多选 3 个');
        return;
      }
      var on = !btn.classList.contains('on');
      btn.classList.toggle('on', on);
      btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  }

  function currentForm() {
    var box = $('durationChips').querySelector('.chip.on');
    var modeBox = $('modeChips').querySelector('.chip.on');
    return {
      origin: $('tripOrigin').value.trim(),
      destination: $('tripDest').value.trim(),
      date: $('tripDate').value || today(),
      start_time: $('tripStart').value || '08:30',
      duration: box ? box.getAttribute('data-duration') : 'half',
      mode: modeBox ? modeBox.getAttribute('data-mode') : 'driving',
      prefs: selectedChips($('prefChips')),
      origin_lng: S.originCoords ? S.originCoords.lng : null,
      origin_lat: S.originCoords ? S.originCoords.lat : null,
      origin_coordsys: S.originCoords ? (S.originCoords.sys || 'gcj02') : 'gcj02'
    };
  }

  function setSingleChip(containerId, attr, value) {
    var box = $(containerId);
    if (!box) { return; }
    Array.prototype.forEach.call(box.querySelectorAll('.chip'), function (chip) {
      var on = chip.getAttribute(attr) === value;
      chip.classList.toggle('on', on);
      chip.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  }

  function setPrefs(prefs) {
    var list = Array.isArray(prefs) ? prefs : [];
    var box = $('prefChips');
    if (!box) { return; }
    Array.prototype.forEach.call(box.querySelectorAll('.chip'), function (chip) {
      var on = list.indexOf(chip.getAttribute('data-pref')) >= 0;
      chip.classList.toggle('on', on);
      chip.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  }

  function fillForm(request) {
    request = request || {};
    if (request.origin) { $('tripOrigin').value = request.origin; }
    if (request.destination) { $('tripDest').value = request.destination; }
    if (request.date) { $('tripDate').value = request.date; }
    if (request.start_time) { $('tripStart').value = request.start_time; }
    setSingleChip('durationChips', 'data-duration', request.duration || 'full');
    setSingleChip('modeChips', 'data-mode', request.mode || 'driving');
    setPrefs(request.prefs || []);
    S.originCoords = null;
    $('locateMsg').textContent = '';
  }

  function locate() {
    var msg = $('locateMsg');
    if (!navigator.geolocation) {
      msg.className = 'msg err';
      msg.textContent = '这个浏览器不支持定位，直接手填起点也可以。';
      return;
    }
    if (!window.isSecureContext) {
      msg.className = 'msg err';
      msg.textContent = '定位需要 HTTPS 或 localhost；在线上域名打开才能用。';
      return;
    }
    msg.className = 'msg';
    msg.textContent = '正在定位…';
    var btn = $('locateBtn');
    btn.disabled = true;
    navigator.geolocation.getCurrentPosition(function (pos) {
      btn.disabled = false;
      // 浏览器给的是 WGS84；标上 sys，服务端先转 GCJ-02 再查路线。
      S.originCoords = {
        lng: Number(pos.coords.longitude.toFixed(6)),
        lat: Number(pos.coords.latitude.toFixed(6)),
        sys: 'gps'
      };
      $('tripOrigin').value = '我的位置';
      msg.className = 'msg ok';
      msg.textContent = '已用当前位置，生成时会换成具体地址。';
    }, function (err) {
      btn.disabled = false;
      msg.className = 'msg err';
      msg.textContent = err && err.code === 1 ? '定位权限被拒绝了，手填起点就行。' : '没定位成功，手填起点吧。';
    }, { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 });
  }

  // ---------------- 生成 ----------------
  function setView(view) {
    $('emptyBox').hidden = view !== 'empty';
    $('loadingBox').hidden = view !== 'loading';
    $('planBox').hidden = view !== 'plan';
  }

  function setStage(index) {
    var rows = $('loadingBox').querySelectorAll('.load-row');
    Array.prototype.forEach.call(rows, function (row) {
      var n = Number(row.getAttribute('data-stage'));
      row.classList.toggle('done', n < index);
      row.classList.toggle('active', n === index);
    });
  }

  function startLoading() {
    clearInterval(S.stageTimer);
    setStage(0);
    S.stageTimer = setInterval(function () {
      var active = $('loadingBox').querySelector('.load-row.active');
      if (!active) { return; }
      var next = Number(active.getAttribute('data-stage')) + 1;
      if (next <= 3) { setStage(next); }
    }, 3200);
  }

  function scrollResult() {
    if (window.innerWidth < 900) {
      $('result').scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }

  async function generate() {
    if (S.busy) { return; }
    var form = currentForm();
    var formMsg = $('formMsg');
    formMsg.className = 'msg';
    formMsg.textContent = '';
    if (!form.destination) {
      formMsg.className = 'msg err';
      formMsg.textContent = '目的地还没填。';
      $('tripDest').focus();
      return;
    }
    if (!form.origin && !form.origin_lng) {
      formMsg.className = 'msg err';
      formMsg.textContent = '起点还没填，或者点右边的 ◎ 用当前位置。';
      $('tripOrigin').focus();
      return;
    }

    S.busy = true;
    $('planBtn').disabled = true;
    $('planBtn').textContent = '正在排…';
    S.plan = null;
    S.savedId = null;
    setView('loading');
    startLoading();
    scrollResult();

    try {
      var data = await api('/trip/api/plan', {
        method: 'POST',
        body: JSON.stringify(form)
      });
      clearInterval(S.stageTimer);
      setStage(4);
      S.plan = data.plan || null;
      S.savedId = null;
      if (!S.plan) { throw new Error('没有拿到行程。'); }
      renderPlan(S.plan);
      setView('plan');
      toast(S.plan.demo ? '演示行程已生成，配置高德 Key 后是真实结果' : '行程排好了');
    } catch (err) {
      clearInterval(S.stageTimer);
      var msg = err && err.message ? err.message : '生成失败，稍后再试。';
      formMsg.className = 'msg err';
      formMsg.textContent = msg;
      $('emptyBox').innerHTML = '<div class="big">⚠️</div><h2>这条路线没排出来</h2>' +
        '<p>' + esc(msg) + '</p><p style="margin-top:6px">换个更具体的地名，或把出发时间提前一点再试。</p>';
      setView('empty');
      toast(msg);
      scrollResult();
    } finally {
      S.busy = false;
      $('planBtn').disabled = false;
      $('planBtn').textContent = '帮我排一条';
    }
  }

  // ---------------- 结果渲染 ----------------
  function modeLabel(mode) {
    return ({ driving: '驾车', transit: '公交', walking: '步行', riding: '骑行' })[mode] || mode || '';
  }

  function durationLabel(duration) {
    return duration === 'full' ? '一天' : '半天';
  }

  function weatherIcon(day) {
    if (!day) { return '🌤️'; }
    if (day.indexOf('雨') >= 0 || day.indexOf('雷') >= 0) { return '🌧️'; }
    if (day.indexOf('雪') >= 0) { return '❄️'; }
    if (day.indexOf('云') >= 0 || day.indexOf('阴') >= 0) { return '☁️'; }
    return '☀️';
  }

  function routeHtml(plan) {
    var route = plan.route || {};
    var steps = Array.isArray(route.steps) ? route.steps : [];
    var stepsHtml = '';
    if (steps.length) {
      stepsHtml = '<details class="route-steps"><summary>看路线步骤（' + steps.length + '）</summary><ol>' +
        steps.map(function (step) {
          return '<li>' + esc(step.text || '') +
            (step.detail ? ' <span class="mini">' + esc(step.detail) + '</span>' : '') + '</li>';
        }).join('') + '</ol></details>';
    }
    return '<div class="routebox">' +
      '<div class="route-top">' +
        '<span class="route-mode">' + esc(route.label || modeLabel(route.mode)) + '</span>' +
        '<span class="route-km">' + esc(route.distance_text || '') + ' · 约 ' +
          esc(route.duration_min || 0) + ' 分钟</span>' +
        (route.estimated ? '<span class="badge warn">直线估算</span>' : '') +
        (route.cost ? '<span class="badge n">参考费用 ' + esc(route.cost) + '</span>' : '') +
      '</div>' +
      '<div class="route-note">' + esc(plan.route_tip || '') + '</div>' +
      stepsHtml +
    '</div>';
  }

  function weatherHtml(weather) {
    if (!weather) { return ''; }
    return '<div class="weather">' +
      '<span style="font-size:17px">' + weatherIcon(weather.day) + '</span>' +
      '<span><b>' + esc(weather.day || '') + '</b> ' + esc(weather.week || '') + '</span>' +
      '<span>' + esc(weather.night_temp || '?') + '° / ' + esc(weather.day_temp || '?') + '°</span>' +
      (weather.wind ? '<span>' + esc(weather.wind) + '风 ' + esc(weather.power || '') + '级</span>' : '') +
      '<span class="mini">夜间 / 白天</span>' +
    '</div>';
  }

  function itemHtml(item, index) {
    var poi = item.poi || {};
    var hop = '';
    if (index > 0) {
      hop = '<div class="tl-hop">↳ 从 ' + esc(item.from_prev || '上一站') + ' 过来约 ' +
        esc(item.travel_min || 0) + ' 分钟' +
        (item.travel_text ? ' · ' + esc(item.travel_text) : '') + '</div>';
    }
    var facts = [];
    if (poi.tag) { facts.push(poi.tag); }
    if (poi.rating) { facts.push('高德评分 ' + poi.rating); }
    if (poi.cost) { facts.push('参考人均 ¥' + poi.cost); }
    if (poi.distance_m) { facts.push('离目的地约 ' + (poi.distance_m >= 1000 ?
      (poi.distance_m / 1000).toFixed(1) + ' 公里' : poi.distance_m + ' 米')); }
    return '<li class="tl-item ' + (poi.kind === 'food' ? 'food' : '') + '">' + hop +
      '<div class="tl-card">' +
        '<div class="tl-top">' +
          '<span class="tl-time">' + esc(item.start) + '–' + esc(item.end) + '</span>' +
          '<span class="tl-name">' + esc(poi.name || '未命名地点') + '</span>' +
          '<span class="tl-stay">停留 ' + esc(item.stay_min) + ' 分钟</span>' +
        '</div>' +
        (poi.address ? '<div class="tl-addr">' + esc(poi.address) + '</div>' : '') +
        (item.why ? '<div class="tl-why">' + esc(item.why) + '</div>' : '') +
        (item.tip ? '<div class="tl-tip">' + esc(item.tip) + '</div>' : '') +
        (facts.length ? '<div class="tl-facts">' + facts.map(function (f) {
          return '<span>' + esc(f) + '</span>';
        }).join('') + '</div>' : '') +
      '</div>' +
    '</li>';
  }

  function alternativesHtml(plan) {
    var alts = Array.isArray(plan.alternatives) ? plan.alternatives : [];
    var skipped = Array.isArray(plan.skipped) ? plan.skipped : [];
    if (!alts.length && !skipped.length) { return ''; }
    var cards = [];
    var seen = {};
    alts.forEach(function (alt) {
      var poi = alt.poi || {};
      var key = poi.id || poi.name;
      if (key && seen[key]) { return; }
      if (key) { seen[key] = true; }
      cards.push('<div class="alt"><b>' + esc(poi.name || '') + '</b><p>' +
        esc(alt.why || '') + '</p></div>');
    });
    skipped.forEach(function (item) {
      var poi = item.poi || {};
      var key = poi.id || poi.name;
      if (key && seen[key]) { return; }
      if (key) { seen[key] = true; }
      cards.push('<div class="alt"><b>' + esc(poi.name || '') +
        '</b><p>这次时间不够没排进去。' + esc(item.why || '') + '</p></div>');
    });
    return '<div class="sec-h">备选与没排上的<span class="sp">时间不够时可以替换其中一站</span></div>' +
      '<div class="alt-list">' + cards.join('') + '</div>';
  }

  function warningsHtml(plan) {
    var warnings = (Array.isArray(plan.warnings) ? plan.warnings : []).filter(function (w) {
      return String(w || '').indexOf('当前是演示行程') !== 0;
    });
    if (!warnings.length) { return ''; }
    return '<div class="sec-h">出发前看一眼</div><ul class="warn-list">' +
      warnings.map(function (w) { return '<li>' + esc(w) + '</li>'; }).join('') + '</ul>';
  }

  function renderPlan(plan) {
    var request = plan.request || {};
    var destination = plan.destination || {};
    var origin = plan.origin || {};
    var stats = plan.stats || {};
    var items = Array.isArray(plan.items) ? plan.items : [];
    var date = request.date || '';
    var title = '去' + (destination.name || request.destination || '目的地');
    var badges = [];
    if (plan.demo) { badges.push('<span class="badge warn">演示数据</span>'); }
    badges.push('<span class="badge ' + (plan.ai_used ? 'ok' : 'n') + '">' +
      (plan.ai_used ? 'AI 编排' : '按高德评分自动排') + '</span>');
    if (plan.route && plan.route.estimated) {
      badges.push('<span class="badge warn">路线为估算</span>');
    }

    var demoBanner = plan.demo
      ? '<div class="banner warn" style="margin:12px 0 0"><span class="ico">⚠</span><div>' +
        '<b>这是演示行程。</b>当前没有配置 <code>AMAP_WEB_KEY</code>，地点、路线、天气都不是实时查询结果，' +
        '只用于预览页面效果。配置 Key 后重新生成即可。</div></div>'
      : '';

    var html = '<article class="card">' +
      '<div class="plan-head"><h2>' + esc(title) + '</h2><div class="badges">' +
        badges.join('') + '</div></div>' +
      '<div class="meta">' +
        (date ? '<span>' + esc(date) + '</span>' : '') +
        '<span>' + esc(request.start_time || '') + ' 出发</span>' +
        '<span>' + esc(durationLabel(request.duration)) + '</span>' +
        '<span>' + esc(modeLabel(request.mode)) + '</span>' +
        (origin.name ? '<span>从 ' + esc(origin.name) + ' 出发</span>' : '') +
        '<span>' + esc(stats.stops || items.length) + ' 站</span>' +
        (stats.end_time ? '<span>预计 ' + esc(stats.end_time) + ' 结束</span>' : '') +
      '</div>' +
      demoBanner +
      '<p class="summary">' + esc(plan.summary || '') + '</p>' +
      routeHtml(plan) +
      weatherHtml(plan.weather) +
      '<div class="map-wrap" id="mapWrap" hidden>' +
        '<div id="tripMap" aria-label="行程地图"></div>' +
        '<div class="map-note" id="mapNote">加载地图中…</div>' +
      '</div>' +
      '<div class="sec-h">时间线<span class="sp">' +
        (stats.travel_min ? '站间移动约 ' + esc(stats.travel_min) + ' 分钟' : '') + '</span></div>' +
      '<ol class="tl">' + items.map(itemHtml).join('') + '</ol>' +
      warningsHtml(plan) +
      alternativesHtml(plan) +
      '<div class="actions">' +
        '<button class="btn primary sm" id="saveBtn" type="button">保存这条行程</button>' +
        '<button class="btn sm" id="exportBtn" type="button">导出 Markdown</button>' +
        '<span class="sp"></span>' +
        '<button class="btn ghost sm" id="editBtn" type="button">调整条件</button>' +
      '</div>' +
    '</article>';

    $('planBox').innerHTML = html;
    $('saveBtn').addEventListener('click', savePlan);
    $('exportBtn').addEventListener('click', exportPlan);
    $('editBtn').addEventListener('click', function () {
      $('tripOrigin').scrollIntoView({ behavior: 'smooth', block: 'center' });
      setTimeout(function () {
        if ($('tripDest')) { $('tripDest').focus(); }
      }, 300);
    });
    markSaved(false);
    renderMap(plan);
  }

  function markSaved(saved) {
    var btn = $('saveBtn');
    if (!btn) { return; }
    btn.disabled = !!saved;
    btn.textContent = saved ? '已保存' : '保存这条行程';
  }

  // ---------------- 地图 ----------------
  function loadAmap() {
    if (window.AMap) { return Promise.resolve(); }
    if (S.amapPromise) { return S.amapPromise; }
    var status = S.status || {};
    if (!status.map_ready || !status.js_key || !status.security_code) {
      return Promise.reject(new Error('没有配置地图 Key'));
    }
    S.amapPromise = new Promise(function (resolve, reject) {
      window._AMapSecurityConfig = { securityJsCode: status.security_code };
      var script = document.createElement('script');
      script.async = true;
      script.src = 'https://webapi.amap.com/maps?v=2.0&key=' + encodeURIComponent(status.js_key);
      script.onload = function () {
        if (window.AMap) { resolve(); } else { reject(new Error('地图脚本没有初始化成功')); }
      };
      script.onerror = function () { reject(new Error('地图脚本加载失败')); };
      document.head.appendChild(script);
      setTimeout(function () {
        if (!window.AMap) { reject(new Error('地图加载超时')); }
      }, 12000);
    });
    return S.amapPromise;
  }

  function pin(position, cssClass, text, title) {
    var content = document.createElement('div');
    content.className = 'amap-pin ' + cssClass;
    content.textContent = text;
    return new AMap.Marker({
      position: position,
      content: content,
      offset: new AMap.Pixel(-13, -13),
      title: title || ''
    });
  }

  function renderMap(plan) {
    var wrap = $('mapWrap');
    if (!wrap) { return; }
    if (!S.status || !S.status.map_ready) {
      wrap.hidden = true;
      return;
    }
    wrap.hidden = false;
    var note = $('mapNote');
    note.textContent = '加载地图中…';

    loadAmap().then(function () {
      if (S.map) {
        S.map.destroy();
        S.map = null;
        S.mapMarkers = [];
      }
      var origin = plan.origin || {};
      var destination = plan.destination || {};
      var items = Array.isArray(plan.items) ? plan.items : [];
      if (destination.lng == null || destination.lat == null) {
        note.textContent = '目的地没有坐标，画不了地图。';
        return;
      }
      var map = new AMap.Map('tripMap', {
        zoom: 12,
        center: [destination.lng, destination.lat],
        resizeEnable: true,
        viewMode: '2D'
      });
      S.map = map;

      var markers = [];
      var path = [];
      if (origin.lng != null && origin.lat != null) {
        path.push([origin.lng, origin.lat]);
        markers.push(pin([origin.lng, origin.lat], 'origin', '起', origin.name || '起点'));
      }
      items.forEach(function (item, index) {
        var poi = item.poi || {};
        if (poi.lng == null || poi.lat == null) { return; }
        path.push([poi.lng, poi.lat]);
        markers.push(pin([poi.lng, poi.lat], '', String(index + 1), poi.name || ''));
      });
      path.push([destination.lng, destination.lat]);
      markers.push(pin([destination.lng, destination.lat], 'dest', '终', destination.name || '目的地'));

      map.add(markers);
      S.mapMarkers = markers;
      if (path.length > 1) {
        map.add(new AMap.Polyline({
          path: path,
          strokeColor: '#0E7490',
          strokeWeight: 4,
          strokeOpacity: 0.72,
          strokeStyle: 'dashed',
          zIndex: 10
        }));
      }
      map.setFitView(markers, false, [36, 36, 36, 36]);
      note.textContent = '连线只表示站点顺序，不是实际道路轨迹；路线以上方高德规划为准。';
    }).catch(function (err) {
      note.textContent = '地图没加载出来：' + (err && err.message ? err.message : '未知原因') +
        '。可以先看时间线和路线卡片。';
    });
  }

  // ---------------- 保存 / 导出 ----------------
  async function savePlan() {
    if (!S.plan || S.savedId) { return; }
    var btn = $('saveBtn');
    btn.disabled = true;
    btn.textContent = '保存中…';
    try {
      var data = await api('/trip/api/plans', {
        method: 'POST',
        body: JSON.stringify({ plan: S.plan })
      });
      S.savedId = data.id;
      markSaved(true);
      toast('已保存，可在左侧历史行程里回看');
      loadHistory();
    } catch (err) {
      btn.disabled = false;
      btn.textContent = '保存这条行程';
      toast(err.message || '保存失败');
    }
  }

  function buildMarkdown(plan) {
    var request = plan.request || {};
    var destination = plan.destination || {};
    var origin = plan.origin || {};
    var route = plan.route || {};
    var lines = [];
    lines.push('# ' + (origin.name || request.origin || '起点') + ' → ' +
      (destination.name || request.destination || '目的地') + ' 出行攻略');
    lines.push('');
    lines.push('- 日期：' + (request.date || ''));
    lines.push('- 出发：' + (request.start_time || '') + '，' +
      durationLabel(request.duration) + '，' + modeLabel(request.mode));
    if (plan.weather) {
      lines.push('- 天气：' + (plan.weather.day || '') + '，' +
        (plan.weather.night_temp || '?') + '~' + (plan.weather.day_temp || '?') + '℃');
    }
    if (plan.demo) { lines.push('- **演示数据：未配置高德 Key，以下内容不是真实查询结果**'); }
    lines.push('');
    lines.push('> ' + (plan.summary || ''));
    lines.push('');
    lines.push('## 怎么去');
    lines.push('');
    lines.push('- ' + (route.label || modeLabel(route.mode)) + '：' +
      (route.distance_text || '') + '，约 ' + (route.duration_min || 0) + ' 分钟' +
      (route.estimated ? '（直线估算）' : ''));
    if (plan.route_tip) { lines.push('- ' + plan.route_tip); }
    if (Array.isArray(route.steps) && route.steps.length) {
      route.steps.forEach(function (step) {
        lines.push('  - ' + (step.text || '') + (step.detail ? '（' + step.detail + '）' : ''));
      });
    }
    lines.push('');
    lines.push('## 时间线');
    lines.push('');
    (plan.items || []).forEach(function (item, index) {
      var poi = item.poi || {};
      lines.push((index + 1) + '. **' + item.start + '–' + item.end + ' ' + (poi.name || '') +
        '**（停留 ' + item.stay_min + ' 分钟）');
      if (poi.address) { lines.push('   - 地址：' + poi.address); }
      if (item.why) { lines.push('   - ' + item.why); }
      if (item.tip) { lines.push('   - 提示：' + item.tip); }
      if (index > 0 && item.travel_min) {
        lines.push('   - 从上一站过来约 ' + item.travel_min + ' 分钟');
      }
    });
    lines.push('');
    if (Array.isArray(plan.warnings) && plan.warnings.length) {
      lines.push('## 出发前确认');
      lines.push('');
      plan.warnings.forEach(function (w) { lines.push('- ' + w); });
      lines.push('');
    }
    if (Array.isArray(plan.alternatives) && plan.alternatives.length) {
      lines.push('## 备选');
      lines.push('');
      plan.alternatives.forEach(function (alt) {
        lines.push('- ' + ((alt.poi && alt.poi.name) || '') + '：' + (alt.why || ''));
      });
      lines.push('');
    }
    return lines.join('\n');
  }

  function exportPlan() {
    if (!S.plan) { return; }
    var md = buildMarkdown(S.plan);
    var dest = (S.plan.destination && S.plan.destination.name) || '目的地';
    var date = (S.plan.request && S.plan.request.date) || today();
    var name = '出行攻略 ' + dest + ' ' + date + '.md';
    if (window.TBExport && typeof window.TBExport.download === 'function') {
      var ok = window.TBExport.download(md, name);
      toast(ok === false ? '下载没成功，换个浏览器试试' : '已导出 Markdown');
      return;
    }
    try {
      var blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
      var link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = name;
      link.click();
      setTimeout(function () { URL.revokeObjectURL(link.href); }, 1000);
      toast('已导出 Markdown');
    } catch (err) {
      toast('导出失败');
    }
  }

  // ---------------- 历史 ----------------
  function renderHistory(items) {
    S.history = items || [];
    $('histCount').textContent = S.history.length ? S.history.length + ' 条' : '';
    if (!S.history.length) {
      $('histList').innerHTML = '<div class="mini">还没有保存的行程。生成一条后点「保存这条行程」。</div>';
      return;
    }
    $('histList').innerHTML = S.history.map(function (item) {
      return '<div class="hist-item">' +
        '<div class="hist-main">' +
          '<div class="hist-t">' + esc(item.title || '未命名行程') + '</div>' +
          '<div class="hist-s">' + esc(item.summary || '') + '</div>' +
          '<div class="mini" style="margin-top:3px">' + esc(item.date || '') +
            (item.stops ? ' · ' + esc(item.stops) + ' 站' : '') + '</div>' +
        '</div>' +
        '<div class="hist-a">' +
          '<button class="lnk" type="button" data-open="' + esc(item.id) + '">打开</button>' +
          '<button class="lnk danger" type="button" data-del="' + esc(item.id) + '" title="删除">删</button>' +
        '</div>' +
      '</div>';
    }).join('');
  }

  async function loadHistory() {
    try {
      var data = await api('/trip/api/plans');
      renderHistory(data.items || []);
    } catch (err) {
      $('histList').innerHTML = '<div class="mini">历史记录没读出来：' + esc(err.message || '') + '</div>';
    }
  }

  async function openSaved(id) {
    try {
      var data = await api('/trip/api/plans/' + encodeURIComponent(id));
      S.plan = data.plan || null;
      S.savedId = data.id;
      if (!S.plan) { throw new Error('这条记录里没有行程。'); }
      fillForm(data.request || S.plan.request || {});
      renderPlan(S.plan);
      setView('plan');
      markSaved(true);
      scrollResult();
      toast('已打开保存的行程');
    } catch (err) {
      toast(err.message || '打开失败');
    }
  }

  async function deleteSaved(id) {
    if (!window.confirm('删除这条行程？删除后不能恢复。')) { return; }
    try {
      await api('/trip/api/plans/' + encodeURIComponent(id), { method: 'DELETE' });
      if (String(S.savedId) === String(id)) {
        S.savedId = null;
        markSaved(false);
      }
      toast('已删除');
      loadHistory();
    } catch (err) {
      toast(err.message || '删除失败');
    }
  }

  // ---------------- 绑定 ----------------
  function bind() {
    $('tripDate').value = today();

    bindChipGroup('durationChips', true, 'data-duration');
    bindChipGroup('modeChips', true, 'data-mode');
    bindChipGroup('prefChips', false, 'data-pref');

    $('planBtn').addEventListener('click', generate);
    $('locateBtn').addEventListener('click', locate);

    $('tripOrigin').addEventListener('input', function () {
      if (S.originCoords) {
        S.originCoords = null;
        $('locateMsg').textContent = '';
      }
    });

    [$('tripOrigin'), $('tripDest')].forEach(function (input) {
      input.addEventListener('keydown', function (event) {
        if (event.key === 'Enter') {
          event.preventDefault();
          generate();
        }
      });
    });

    document.querySelectorAll('.examples button').forEach(function (btn) {
      btn.addEventListener('click', function () {
        $('tripOrigin').value = btn.getAttribute('data-origin') || '';
        $('tripDest').value = btn.getAttribute('data-dest') || '';
        setSingleChip('modeChips', 'data-mode', btn.getAttribute('data-mode') || 'driving');
        setSingleChip('durationChips', 'data-duration', btn.getAttribute('data-duration') || 'half');
        S.originCoords = null;
        generate();
      });
    });

    $('histList').addEventListener('click', function (event) {
      var open = event.target.closest('[data-open]');
      if (open) { openSaved(open.getAttribute('data-open')); return; }
      var del = event.target.closest('[data-del]');
      if (del) { deleteSaved(del.getAttribute('data-del')); }
    });
  }

  // 给「我的地点」用：把一个收藏地点填进起点 / 终点。
  // 起点会同时带上坐标，避免同名地点解析到别处；终点仍交给服务端解析，
  // 所以优先用地址（比「家」「公司」这类名字更容易解析到唯一位置）。
  window.TBTrip = {
    setPlace: function (kind, place) {
      if (!place) { return; }
      var name = place.name || place.address || '';
      if (kind === 'origin') {
        $('tripOrigin').value = name;
        if (place.lng != null && place.lat != null) {
          S.originCoords = { lng: Number(place.lng), lat: Number(place.lat), sys: 'gcj02' };
        } else {
          S.originCoords = null;
        }
        $('locateMsg').className = 'msg ok';
        $('locateMsg').textContent = '已从「我的地点」选择：' + name;
      } else {
        $('tripDest').value = place.address || name;
      }
      $('formMsg').className = 'msg';
      $('formMsg').textContent = '';
    }
  };

  function init() {
    bind();
    loadStatus();
    loadHistory();

    var rid = new URLSearchParams(window.location.search).get('r');
    if (rid) { openSaved(rid); }
  }

  init();
})();
