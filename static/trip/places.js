/* 我的地点 /trip#places
   ------------------------------------------------------------
   列表和 CRUD 走自己的 SQLite；地址解析和 POI 搜索才调用高德。
   搜索是显式按钮触发，不做输入即搜 —— 个人 Key 的 POI 配额最紧。 */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };
  var S = {
    loaded: false,
    places: [],
    editing: null,
    picked: null,
    category: 'other',
    filterCategory: '',
    q: '',
    status: null,
    quota: null,
    map: null,
    markers: [],
    searchMarker: null,
    searchPoint: null,
    mapLoading: false,
    searchItems: [],
    sheetOpen: false,
    busy: false,
    loadSeq: 0,
    filterTimer: null,
    tool: '',
    pick: null,
    pickMarker: null,
    pickSeq: 0,
    pickTarget: '',
    measurePoints: [],
    measureMarkers: [],
    measureLine: null,
    measureOptions: [],
    routeStart: null,
    routeEnd: null,
    routeMode: 'driving',
    routeMarkers: [],
    routeLine: null,
    routeResult: null,
    routeSeq: 0,
    markerClickAt: 0
  };

  var CAT_NAMES = { home: '家', work: '公司', food: '餐饮', scenic: '景点', other: '其他' };

  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (ch) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
    });
  }

  function toast(text) {
    var el = $('toast');
    if (!el) { return; }
    el.textContent = text;
    el.classList.add('show');
    clearTimeout(el._timer);
    el._timer = setTimeout(function () { el.classList.remove('show'); }, 2600);
  }

  function setMsg(el, text, kind) {
    if (!el) { return; }
    el.className = 'msg' + (kind ? ' ' + kind : '');
    el.textContent = text || '';
  }

  function isMobile() {
    return window.innerWidth <= 900;
  }

  function resizeMap() {
    if (!S.map) { return; }
    try { S.map.resize(); } catch (err) { /* 地图还没准备好时忽略 */ }
  }

  function openAddBox() {
    var box = $('addBox');
    if (!box) { return; }
    box.open = true;
    setTimeout(function () {
      try { box.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); } catch (err) { /* 忽略 */ }
    }, 40);
  }

  function setSheetLabel() {
    var btn = $('sheetToggle');
    if (!btn) { return; }
    btn.textContent = S.sheetOpen ? '收起' : '搜索';
    btn.setAttribute('aria-expanded', S.sheetOpen ? 'true' : 'false');
  }

  function openSheet(focus) {
    var panel = $('sidePanel');
    if (!panel || !isMobile()) { return; }
    S.sheetOpen = true;
    panel.classList.add('open');
    document.body.classList.add('trip-sheet-open');
    setSheetLabel();
    setTimeout(resizeMap, 260);
    if (focus !== false) {
      setTimeout(function () {
        if (S.sheetOpen && $('placeAddress')) { $('placeAddress').focus(); }
      }, 280);
    }
  }

  function closeSheet() {
    var panel = $('sidePanel');
    if (!panel) { return; }
    S.sheetOpen = false;
    panel.classList.remove('open');
    document.body.classList.remove('trip-sheet-open');
    setSheetLabel();
    setTimeout(resizeMap, 260);
  }

  function toggleSheet() {
    if (S.sheetOpen) { closeSheet(); } else { openSheet(); }
  }

  function panelCollapsed() {
    return document.body.classList.contains('trip-panel-collapsed');
  }

  function setPanelCollapsed(collapsed) {
    document.body.classList.toggle('trip-panel-collapsed', collapsed);
    setTimeout(function () {
      resizeMap();
      fitAll();
    }, 260);
  }

  function clearSearchMarker() {
    removeOverlay(S.searchMarker);
    S.searchMarker = null;
    S.searchPoint = null;
    renderMeasureOptions();
  }

  function fitAll() {
    if (!S.map || !S.markers.length) { return; }
    // setFitView 的 avoid 顺序是「上、下、左、右」，不是上右下左
    var avoid = isMobile()
      ? [130, 170, 30, 30]
      : (panelCollapsed() ? [110, 150, 50, 100] : [110, 150, 440, 100]);
    window.TBMapCore.fit(S.map, S.markers, avoid);
  }

  function fitOverlays(overlays) {
    if (!S.map || !overlays || !overlays.length) { return; }
    var avoid = isMobile()
      ? [130, 170, 30, 30]
      : (panelCollapsed() ? [110, 150, 50, 100] : [110, 150, 440, 100]);
    try { S.map.setFitView(overlays, false, avoid); } catch (err) { /* 地图未就绪时忽略 */ }
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

  // ---------------- 表单 ----------------
  function setCategory(value) {
    S.category = CAT_NAMES[value] ? value : 'other';
    var box = $('placeCatChips');
    if (!box) { return; }
    Array.prototype.forEach.call(box.querySelectorAll('.chip'), function (chip) {
      var on = chip.getAttribute('data-cat') === S.category;
      chip.classList.toggle('on', on);
      chip.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  }

  function setFilterCategory(value) {
    S.filterCategory = CAT_NAMES[value] ? value : '';
    var box = $('placeFilterCats');
    if (box) {
      Array.prototype.forEach.call(box.querySelectorAll('.chip'), function (chip) {
        var on = (chip.getAttribute('data-cat') || '') === S.filterCategory;
        chip.classList.toggle('on', on);
        chip.setAttribute('aria-pressed', on ? 'true' : 'false');
      });
    }
  }

  function tagsToText(tags) {
    return (Array.isArray(tags) ? tags : []).join('，');
  }

  function textToTags(text) {
    return String(text || '').replace(/，/g, ',').split(',')
      .map(function (tag) { return tag.trim(); })
      .filter(function (tag, index, list) { return tag && list.indexOf(tag) === index; })
      .slice(0, 8);
  }

  function resetForm() {
    S.editing = null;
    S.picked = null;
    $('placeName').value = '';
    $('placeAddress').value = '';
    $('placeTags').value = '';
    $('placeNote').value = '';
    setCategory('other');
    setMsg($('placeFormMsg'), '');
    setMsg($('placeSearchMsg'), '');
    $('placeSearchResults').hidden = true;
    $('placeSearchResults').innerHTML = '';
    $('placeEditHint').textContent = '';
    $('placeSaveBtn').textContent = '保存地点';
    if ($('addBox')) { $('addBox').open = false; }
    clearSearchMarker();
    clearPick();
  }

  function fillForm(place) {
    S.editing = place.id;
    S.picked = null;
    $('placeName').value = place.name || '';
    $('placeAddress').value = place.address || '';
    $('placeTags').value = tagsToText(place.tags);
    $('placeNote').value = place.note || '';
    setCategory(place.category || 'other');
    setMsg($('placeFormMsg'), '');
    setMsg($('placeSearchMsg'), '');
    $('placeSearchResults').hidden = true;
    $('placeEditHint').textContent = '正在编辑：' + (place.name || '未命名');
    $('placeSaveBtn').textContent = '保存修改';
    openAddBox();
  }

  // ---------------- 地图工具：取点 / 测距 / 路线 ----------------
  var TOOL_BUTTONS = { pick: 'toolPick', measure: 'toolMeasure', route: 'toolRoute' };

  function distanceMeters(a, b) {
    var R = 6371008.8;
    var lng1 = a[0] * Math.PI / 180;
    var lat1 = a[1] * Math.PI / 180;
    var lng2 = b[0] * Math.PI / 180;
    var lat2 = b[1] * Math.PI / 180;
    var dlng = lng2 - lng1;
    var dlat = lat2 - lat1;
    var h = Math.sin(dlat / 2) * Math.sin(dlat / 2) +
      Math.cos(lat1) * Math.cos(lat2) * Math.sin(dlng / 2) * Math.sin(dlng / 2);
    return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
  }

  function formatDistance(meters) {
    meters = Math.max(0, Number(meters) || 0);
    if (meters < 1000) { return Math.round(meters) + ' 米'; }
    return (meters / 1000).toFixed(2) + ' 公里';
  }

  function removeOverlay(overlay) {
    if (S.map && overlay) {
      try { S.map.remove(overlay); } catch (err) { /* 已经移除时忽略 */ }
    }
  }

  function removeOverlays(overlays) {
    (overlays || []).forEach(function (overlay) { removeOverlay(overlay); });
  }

  function updateToolButtons() {
    Object.keys(TOOL_BUTTONS).forEach(function (name) {
      var btn = $(TOOL_BUTTONS[name]);
      if (!btn) { return; }
      var on = S.tool === name;
      btn.classList.toggle('on', on);
      btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  }

  function setTool(name, force) {
    if (!force && name && S.tool === name) { name = ''; }
    S.tool = name || '';
    updateToolButtons();
    if ($('pickCard')) { $('pickCard').hidden = S.tool !== 'pick' || !S.pick; }
    if ($('routeCard')) { $('routeCard').hidden = S.tool !== 'route'; }
    if ($('measureCard')) { $('measureCard').hidden = S.tool !== 'measure'; }
    if (S.tool === 'route') { renderRouteEndpoints(); }
    if (S.tool === 'measure') { renderMeasureCard(); }
    if (name) {
      var hints = {
        pick: '取点模式：点地图选择位置。',
        measure: '测距模式：点地图上的地点标记，或从测距卡片选择。',
        route: '路线模式：先点起点，再点终点。'
      };
      toast(hints[name] || '');
      // 测距时打开面板，方便先从已知地点里选点；取点和路线需要完整地图。
      if (isMobile()) {
        if (name === 'measure') { openSheet(false); }
        else { closeSheet(); }
      }
    }
  }

  function clearPick() {
    removeOverlay(S.pickMarker);
    S.pickMarker = null;
    S.pick = null;
    S.pickSeq += 1;
    if ($('pickCard')) { $('pickCard').hidden = true; }
    renderMeasureOptions();
  }

  function clearMeasureLine() {
    removeOverlay(S.measureLine);
    S.measureLine = null;
  }

  function drawMeasureLine() {
    clearMeasureLine();
    if (!S.map || S.measurePoints.length < 2) { return; }
    S.measureLine = new window.AMap.Polyline({
      path: S.measurePoints.map(function (point) { return [point.lng, point.lat]; }),
      strokeColor: '#d97706',
      strokeWeight: 5,
      strokeOpacity: 0.9,
      strokeStyle: 'solid',
      lineJoin: 'round',
      zIndex: 120
    });
    S.map.add(S.measureLine);
  }

  function clearMeasure() {
    clearMeasureLine();
    removeOverlays(S.measureMarkers);
    S.measureMarkers = [];
    S.measurePoints = [];
    setMsg($('measureMsg'), '');
    renderMeasureCard();
  }

  function measureTotalMeters() {
    var total = 0;
    for (var i = 1; i < S.measurePoints.length; i += 1) {
      var prev = S.measurePoints[i - 1];
      var point = S.measurePoints[i];
      total += distanceMeters([prev.lng, prev.lat], [point.lng, point.lat]);
    }
    return total;
  }

  function updateMeasureBadge() {
    var el = $('measureTotal');
    if (!el) { return; }
    if (!S.measurePoints.length) {
      el.hidden = true;
      el.textContent = '';
      return;
    }
    el.hidden = false;
    el.textContent = '总距离 ' + formatDistance(measureTotalMeters());
  }

  function collectMeasureOptions() {
    var out = [];
    function add(option) {
      if (!option) { return; }
      var lng = Number(option.lng);
      var lat = Number(option.lat);
      if (!isFinite(lng) || !isFinite(lat)) { return; }
      var key = lng.toFixed(5) + ',' + lat.toFixed(5);
      for (var i = 0; i < out.length; i += 1) {
        if (out[i].key === key) { return; }
      }
      out.push({
        key: key,
        lng: lng,
        lat: lat,
        name: option.name || '',
        address: option.address || '',
        label: option.label || option.name || option.address || '地图点',
        sub: option.sub || '',
        placeId: option.placeId || null,
        kind: option.kind || 'point'
      });
    }
    S.places.forEach(function (place) {
      if (place.lng == null || place.lat == null) { return; }
      add({
        lng: place.lng,
        lat: place.lat,
        name: place.name || '',
        address: place.address || '',
        label: place.name || place.address || '未命名地点',
        sub: place.address || '',
        placeId: place.id,
        kind: 'place'
      });
    });
    if (S.searchPoint) {
      add(Object.assign({}, S.searchPoint, {
        label: S.searchPoint.name || '当前搜索结果',
        sub: S.searchPoint.address || '',
        kind: 'search'
      }));
    }
    if (S.pick) {
      add(Object.assign({}, S.pick, {
        label: S.pick.name || '当前取点',
        sub: S.pick.address || '',
        kind: 'pick'
      }));
    }
    function addEndpoint(endpoint, fallback) {
      if (!endpoint) { return; }
      add({
        lng: endpoint.lng,
        lat: endpoint.lat,
        name: endpoint.name || fallback,
        address: endpoint.address || '',
        label: endpoint.name || fallback,
        sub: endpoint.address || '',
        placeId: endpoint.placeId || null,
        kind: 'route'
      });
    }
    addEndpoint(S.routeStart, '路线起点');
    addEndpoint(S.routeEnd, '路线终点');
    return out;
  }

  function renderMeasureOptions() {
    var select = $('measurePointSelect');
    if (!select) { return; }
    S.measureOptions = collectMeasureOptions();
    var html = '<option value="">选择已标记的地点…</option>';
    S.measureOptions.forEach(function (point, index) {
      html += '<option value="' + index + '">' + esc(point.label) +
        (point.sub ? ' · ' + esc(point.sub) : '') + '</option>';
    });
    select.innerHTML = html;
  }

  function renderMeasureCard() {
    updateMeasureBadge();
    renderMeasureOptions();
    var summary = $('measureSummary');
    if (summary) {
      if (S.measurePoints.length) {
        summary.hidden = false;
        summary.innerHTML = '总距离 <b>' + esc(formatDistance(measureTotalMeters())) + '</b>' +
          '<span class="mini">' + S.measurePoints.length + ' 个点</span>';
      } else {
        summary.hidden = true;
        summary.innerHTML = '';
      }
    }
    var list = $('measureList');
    if (!list) { return; }
    if (!S.measurePoints.length) {
      list.innerHTML = '<div class="mini">还没有测距点。点地图上的地点标记，或从上面选一个。</div>';
      return;
    }
    list.innerHTML = S.measurePoints.map(function (point, index) {
      var segText = '起点';
      if (index) {
        var prev = S.measurePoints[index - 1];
        segText = formatDistance(distanceMeters([prev.lng, prev.lat], [point.lng, point.lat]));
      }
      return '<div class="measure-item">' +
        '<span class="measure-num">' + (index + 1) + '</span>' +
        '<div><b>' + esc(point.name || '临时点') + '</b>' +
        (point.address ? '<div class="mini">' + esc(point.address) + '</div>' : '') +
        '</div>' +
        '<span class="measure-dist">' + esc(segText) + '</span>' +
      '</div>';
    }).join('');
  }

  function addMeasurePoint(lng, lat, info) {
    if (!S.map) { return; }
    info = info || {};
    var point = {
      lng: Number(lng),
      lat: Number(lat),
      name: String(info.name || info.address || '').trim(),
      address: String(info.address || '').trim(),
      placeId: info.placeId || null,
      kind: info.kind || 'temp'
    };
    if (!isFinite(point.lng) || !isFinite(point.lat)) { return; }
    for (var i = 0; i < S.measurePoints.length; i += 1) {
      if (Math.abs(S.measurePoints[i].lng - point.lng) < 1e-7 &&
          Math.abs(S.measurePoints[i].lat - point.lat) < 1e-7) {
        toast('这个位置已经加过了。');
        return;
      }
    }
    if (!point.name) {
      point.name = '临时点 ' + (S.measurePoints.length + 1);
      point.address = point.lng.toFixed(5) + ', ' + point.lat.toFixed(5);
      point.kind = 'temp';
    }
    S.measurePoints.push(point);
    var marker = window.TBMapCore.pin(point.lng, point.lat, String(S.measurePoints.length),
      'measure', point.name);
    marker._tbPoint = Object.assign({}, point, { kind: 'measure' });
    marker.on('click', function () {
      if (handleMarkerClick(marker._tbPoint)) { return; }
      S.markerClickAt = Date.now();
      var html = '<div style="font-size:13px;line-height:1.7;max-width:230px">' +
        '<b>' + esc(point.name || '测距点') + '</b>' +
        (point.address ? '<div style="color:#666;font-size:12px">' + esc(point.address) + '</div>' : '') +
        '</div>';
      window.TBMapCore.infoWindow(html).open(S.map, [point.lng, point.lat]);
    });
    S.map.add(marker);
    S.measureMarkers.push(marker);
    setMsg($('measureMsg'), '');
    drawMeasureLine();
    renderMeasureCard();
    if (point.kind === 'temp') {
      toast('已加入临时点；点地点标记可以直接用它的名称测距。');
    } else {
      toast('已加入测距：' + point.name);
    }
  }

  function removeLastMeasurePoint() {
    if (!S.measurePoints.length) { return; }
    S.measurePoints.pop();
    removeOverlay(S.measureMarkers.pop());
    drawMeasureLine();
    renderMeasureCard();
  }

  function onMeasureSelectChange(select) {
    if (!select.value) { return; }
    var index = Number(select.value);
    select.value = '';
    var point = S.measureOptions[index];
    if (point) { addMeasurePoint(point.lng, point.lat, point); }
  }

  function showPickCard() {
    if ($('pickCard')) { $('pickCard').hidden = false; }
  }

  function doPick(lng, lat, info) {
    if (!S.map) { return; }
    clearPick();
    info = info || {};
    S.pick = {
      lng: lng,
      lat: lat,
      name: info.name || '',
      address: info.address || '',
      city: info.city || '',
      district: info.district || '',
      adcode: info.adcode || ''
    };
    var pointInfo = Object.assign({ kind: 'pick' }, S.pick, {
      placeId: info.placeId || null
    });
    S.pickMarker = window.TBMapCore.pin(lng, lat, '取', 'pick', S.pick.name || '地图取点');
    S.pickMarker._tbPoint = pointInfo;
    S.pickMarker.on('click', function () {
      if (handleMarkerClick(pointInfo)) { return; }
      showPickCard();
    });
    S.map.add(S.pickMarker);
    if ($('pickCoord')) {
      $('pickCoord').textContent = '坐标：' + lng.toFixed(6) + ', ' + lat.toFixed(6);
    }
    if ($('pickAddress')) {
      $('pickAddress').textContent = S.pick.address || S.pick.name || '正在解析地址…';
    }
    showPickCard();
    renderMeasureOptions();
    if (isMobile()) { openSheet(false); }
    // 从已标记的地点取点时不重复消耗反查配额。
    if (S.pick.name || S.pick.address) { return; }
    var seq = ++S.pickSeq;
    api('/trip/api/places/regeo?lng=' + encodeURIComponent(lng.toFixed(6)) +
        '&lat=' + encodeURIComponent(lat.toFixed(6))).then(function (data) {
      if (seq !== S.pickSeq || !S.pick) { return; }
      var place = data.place || {};
      S.pick = Object.assign(S.pick, place);
      if ($('pickAddress')) {
        $('pickAddress').textContent = place.address || place.name ||
          '这个位置没解析出地址，仍可保存或设为端点。';
      }
      renderMeasureOptions();
      refreshQuota();
    }).catch(function (err) {
      if (seq !== S.pickSeq) { return; }
      if ($('pickAddress')) {
        $('pickAddress').textContent = '地址解析失败：' + (err.message || '未知原因') +
          '。仍可保存或设为端点。';
      }
    });
  }

  function usePickAsPlace() {
    if (!S.pick) { return; }
    S.picked = S.pick;
    $('placeAddress').value = S.pick.address || '';
    if (!$('placeName').value.trim()) {
      $('placeName').value = S.pick.name || '地图选点';
    }
    openAddBox();
    toast('已填入取点坐标，补一个名称就能保存。');
  }

  function pickToRoute(which) {
    if (!S.pick) { return; }
    var point = {
      lng: Number(S.pick.lng),
      lat: Number(S.pick.lat),
      name: S.pick.name || S.pick.address || '',
      address: S.pick.address || ''
    };
    setTool('route', true);
    setRouteEndpoint(which, point);
    clearPick();
    // 收起抽屉，让地图露出来点另一个端点；算完路线会自动再打开。
    if (isMobile()) { closeSheet(); }
  }

  function routeEndpointFromPoint(point) {
    if (!point || point.lng == null || point.lat == null) { return null; }
    var lng = Number(point.lng);
    var lat = Number(point.lat);
    if (!isFinite(lng) || !isFinite(lat)) { return null; }
    return {
      lng: lng,
      lat: lat,
      name: point.name || '',
      address: point.address || '',
      placeId: point.placeId || null
    };
  }

  function matchSavedPlace(lng, lat) {
    var best = null;
    var bestDist = Infinity;
    S.places.forEach(function (place) {
      if (place.lng == null || place.lat == null) { return; }
      var dist = distanceMeters([lng, lat], [Number(place.lng), Number(place.lat)]);
      if (dist < bestDist) {
        bestDist = dist;
        best = place;
      }
    });
    // 点在地图上离已收藏地点 30 米以内时，直接复用这个地点
    return best && bestDist <= 30 ? best : null;
  }

  function renderRouteSelect(id, endpoint, placeholder) {
    var select = $(id);
    if (!select) { return; }
    // 端点对应的收藏地点可能被当前筛选过滤掉了，这时按地图点显示。
    var matched = endpoint && endpoint.placeId != null ? findPlace(endpoint.placeId) : null;
    var html = '<option value="__map__">' + esc(placeholder) + '</option>';
    if (endpoint && !matched) {
      html += '<option value="__point__" selected>地图点 ' +
        endpoint.lng.toFixed(5) + ', ' + endpoint.lat.toFixed(5) + '</option>';
    }
    S.places.forEach(function (place) {
      if (place.lng == null || place.lat == null) { return; }
      var selected = matched && String(matched.id) === String(place.id) ? ' selected' : '';
      html += '<option value="place:' + esc(place.id) + '"' + selected + '>' +
        esc(place.name || place.address || '未命名地点') + '</option>';
    });
    select.innerHTML = html;
    if (!endpoint) { select.value = '__map__'; }
  }

  function renderRouteEndpoints() {
    if (!S.map) { return; }
    removeOverlays(S.routeMarkers);
    S.routeMarkers = [];
    function addMarker(endpoint, text, cls, title) {
      if (!endpoint) { return; }
      var marker = window.TBMapCore.pin(endpoint.lng, endpoint.lat, text, cls, title);
      marker._tbPoint = Object.assign({ kind: 'route' }, endpoint);
      marker.on('click', function () {
        if (handleMarkerClick(marker._tbPoint)) { return; }
        var html = '<div style="font-size:13px;line-height:1.7;max-width:230px">' +
          '<b>' + esc(title || '路线端点') + '</b>' +
          (endpoint.address ? '<div style="color:#666;font-size:12px">' +
            esc(endpoint.address) + '</div>' : '') +
          '</div>';
        window.TBMapCore.infoWindow(html).open(S.map, [endpoint.lng, endpoint.lat]);
      });
      S.map.add(marker);
      S.routeMarkers.push(marker);
    }
    addMarker(S.routeStart, '起', 'route-a', (S.routeStart && S.routeStart.name) || '路线起点');
    addMarker(S.routeEnd, '终', 'route-b', (S.routeEnd && S.routeEnd.name) || '路线终点');
    renderRouteSelect('routeStartSelect', S.routeStart, '在地图上点选起点');
    renderRouteSelect('routeEndSelect', S.routeEnd, '在地图上点选终点');
    renderMeasureOptions();
  }

  function clearRouteLine() {
    removeOverlay(S.routeLine);
    S.routeLine = null;
  }

  function hideRouteResult() {
    if ($('routeResult')) {
      $('routeResult').hidden = true;
      $('routeResult').innerHTML = '';
    }
  }

  function resetRouteButton() {
    var btn = $('routeCalcBtn');
    if (btn) {
      btn.disabled = false;
      btn.textContent = '算路线';
    }
  }

  function clearRoute(keepEndpoints) {
    clearRouteLine();
    removeOverlays(S.routeMarkers);
    S.routeMarkers = [];
    S.routeResult = null;
    S.routeSeq += 1;
    hideRouteResult();
    resetRouteButton();
    setMsg($('routeMsg'), '');
    if (!keepEndpoints) {
      S.routeStart = null;
      S.routeEnd = null;
    }
    renderRouteEndpoints();
  }

  function clearTools() {
    clearPick();
    clearMeasure();
    clearRoute(false);
    clearSearchMarker();
    try {
      if (S.map && S.map.clearInfoWindow) { S.map.clearInfoWindow(); }
    } catch (err) { /* 忽略 */ }
    S.tool = '';
    S.pickTarget = '';
    if ($('pickCard')) { $('pickCard').hidden = true; }
    if ($('routeCard')) { $('routeCard').hidden = true; }
    if ($('measureCard')) { $('measureCard').hidden = true; }
    updateToolButtons();
    toast('已清除地图上的临时点、测距和路线。');
  }

  function setRouteEndpoint(which, point) {
    var endpoint = routeEndpointFromPoint(point);
    if (!endpoint) { return; }
    if (!endpoint.placeId) {
      var matched = matchSavedPlace(endpoint.lng, endpoint.lat);
      if (matched) {
        endpoint.placeId = matched.id;
        if (!endpoint.name) { endpoint.name = matched.name || ''; }
      }
    }
    if (which === 'start') { S.routeStart = endpoint; }
    else { S.routeEnd = endpoint; }
    S.pickTarget = '';
    clearRouteLine();
    S.routeResult = null;
    S.routeSeq += 1;
    hideRouteResult();
    resetRouteButton();
    setMsg($('routeMsg'), '');
    renderRouteEndpoints();
    if (S.routeStart && S.routeEnd) {
      calculateRoute();
    } else {
      var missing = S.routeStart ? '终点' : '起点';
      setMsg($('routeMsg'), '再点地图选择' + missing + '，或从下拉里选已收藏地点。');
      toast('再点地图选择' + missing + '。');
    }
  }

  function handleRouteClick(lng, lat, info) {
    var point = { lng: lng, lat: lat };
    if (info) {
      point.name = info.name || '';
      point.address = info.address || '';
      point.placeId = info.placeId || null;
    }
    if (S.pickTarget === 'start' || S.pickTarget === 'end') {
      var target = S.pickTarget;
      setRouteEndpoint(target, point);
      return;
    }
    if (!S.routeStart || !S.routeEnd) {
      setRouteEndpoint(S.routeStart ? 'end' : 'start', point);
      return;
    }
    // 起终点都选好后再次点地图：从头开始选一条新路线
    S.routeStart = null;
    S.routeEnd = null;
    clearRouteLine();
    setRouteEndpoint('start', point);
    toast('已重新开始选点。');
  }

  function drawRoute(route) {
    if (!S.map) { return; }
    clearRouteLine();
    var path = Array.isArray(route.polyline) ? route.polyline.filter(function (point) {
      return Array.isArray(point) && point.length === 2 &&
        isFinite(point[0]) && isFinite(point[1]);
    }) : [];
    if (!path.length && S.routeStart && S.routeEnd) {
      path = [[S.routeStart.lng, S.routeStart.lat], [S.routeEnd.lng, S.routeEnd.lat]];
    }
    if (!path.length) { return; }
    S.routeLine = new window.AMap.Polyline({
      path: path,
      strokeColor: route.estimated ? '#d97706' : '#0E7490',
      strokeWeight: 6,
      strokeOpacity: 0.9,
      strokeStyle: route.estimated ? 'dashed' : 'solid',
      lineJoin: 'round',
      zIndex: 120
    });
    S.map.add(S.routeLine);
    fitOverlays(S.routeMarkers.concat([S.routeLine]));
  }

  function renderRouteResult(route) {
    var box = $('routeResult');
    if (!box) { return; }
    var steps = Array.isArray(route.steps) ? route.steps : [];
    var html = '<div class="route-summary">' +
      '<span>距离 <b>' + esc(route.distance_text || formatDistance(route.distance_m)) + '</b></span>' +
      '<span>预计 <b>' + esc(route.duration_min || '—') + ' 分钟</b></span>' +
      '<span>' + esc(route.label || '') + '</span>' +
      '</div>';
    if (route.estimated) {
      html += '<div class="route-warn">' +
        esc(route.note || '下面是直线估算，不是实际路线。') + '</div>';
    }
    if (steps.length) {
      html += '<ol class="route-steps">' + steps.slice(0, 10).map(function (step) {
        var text = step.text || '';
        if (step.detail) { text += '（' + step.detail + '）'; }
        if (step.distance) { text += ' · ' + formatDistance(step.distance); }
        return '<li>' + esc(text) + '</li>';
      }).join('') + '</ol>';
    }
    box.innerHTML = html;
    box.hidden = false;
  }

  function calculateRoute() {
    if (!S.routeStart || !S.routeEnd) {
      setMsg($('routeMsg'), '先把起点和终点都选好。', 'err');
      toast('先把起点和终点都选好。');
      return;
    }
    var seq = ++S.routeSeq;
    var btn = $('routeCalcBtn');
    if (btn) { btn.disabled = true; btn.textContent = '计算中…'; }
    setMsg($('routeMsg'), '正在向高德请求路线…');
    hideRouteResult();
    api('/trip/api/route', {
      method: 'POST',
      body: JSON.stringify({
        mode: S.routeMode,
        origin: {
          lng: S.routeStart.lng,
          lat: S.routeStart.lat,
          name: S.routeStart.name || ''
        },
        destination: {
          lng: S.routeEnd.lng,
          lat: S.routeEnd.lat,
          name: S.routeEnd.name || ''
        }
      })
    }).then(function (data) {
      if (seq !== S.routeSeq) { return; }
      S.routeResult = data.route || {};
      drawRoute(S.routeResult);
      renderRouteResult(S.routeResult);
      setMsg($('routeMsg'), S.routeResult.estimated
        ? '高德没有返回实际路线，地图上是直线，距离和耗时仅为估算。'
        : '', S.routeResult.estimated ? 'err' : '');
      if (isMobile()) { openSheet(false); }
    }).catch(function (err) {
      if (seq !== S.routeSeq) { return; }
      setMsg($('routeMsg'), err.message || '路线计算失败。', 'err');
    }).then(function () {
      if (seq !== S.routeSeq) { return; }
      if (btn) { btn.disabled = false; btn.textContent = '算路线'; }
    });
  }

  function setRouteMode(mode) {
    S.routeMode = mode;
    var box = $('routeModes');
    if (box) {
      Array.prototype.forEach.call(box.querySelectorAll('.chip'), function (chip) {
        var on = chip.getAttribute('data-mode') === mode;
        chip.classList.toggle('on', on);
        chip.setAttribute('aria-pressed', on ? 'true' : 'false');
      });
    }
    if (S.routeStart && S.routeEnd && S.tool === 'route') { calculateRoute(); }
  }

  function onRouteSelectChange(which, select) {
    var value = select.value;
    if (value === '__map__') {
      if (which === 'start') { S.routeStart = null; }
      else { S.routeEnd = null; }
      S.pickTarget = which;
      clearRouteLine();
      S.routeSeq += 1;
      hideRouteResult();
      resetRouteButton();
      renderRouteEndpoints();
      setMsg($('routeMsg'), '点地图选择' + (which === 'start' ? '起点' : '终点') + '。');
      if (isMobile()) { closeSheet(); }
      return;
    }
    if (value === '__point__') { return; }
    if (value.indexOf('place:') === 0) {
      var place = findPlace(value.slice(6));
      if (place) {
        setRouteEndpoint(which, {
          lng: place.lng,
          lat: place.lat,
          name: place.name,
          address: place.address,
          placeId: place.id
        });
      }
    }
  }

  function swapRouteEndpoints() {
    if (!S.routeStart || !S.routeEnd) {
      setMsg($('routeMsg'), '先把起点和终点都选好。', 'err');
      return;
    }
    var tmp = S.routeStart;
    S.routeStart = S.routeEnd;
    S.routeEnd = tmp;
    clearRouteLine();
    S.routeResult = null;
    S.routeSeq += 1;
    hideRouteResult();
    renderRouteEndpoints();
    calculateRoute();
  }

  function resetRouteSelection() {
    S.pickTarget = 'start';
    clearRoute(false);
    setMsg($('routeMsg'), '点地图选择新的起点。');
    toast('点地图选择新的起点。');
    if (isMobile()) { closeSheet(); }
  }

  function handleMarkerClick(point) {
    if (!point) { return false; }
    S.markerClickAt = Date.now();
    if (S.tool === 'measure') {
      if (point.kind === 'measure') { return false; }
      addMeasurePoint(point.lng, point.lat, point);
      return true;
    }
    if (S.tool === 'route') {
      if (point.kind === 'route') { return true; }
      handleRouteClick(point.lng, point.lat, point);
      return true;
    }
    if (S.tool === 'pick') {
      doPick(point.lng, point.lat, point);
      return true;
    }
    return false;
  }

  function onMapClick(event) {
    // 点标记时有些浏览器也会顺带触发地图 click，这里挡掉重复落点。
    if (Date.now() - S.markerClickAt < 300) { return; }
    if (isMobile() && S.sheetOpen) { closeSheet(); }
    if (!event || !event.lnglat) { return; }
    var lng = event.lnglat.getLng();
    var lat = event.lnglat.getLat();
    if (!isFinite(lng) || !isFinite(lat)) { return; }
    if (S.tool === 'pick') { doPick(lng, lat); }
    else if (S.tool === 'measure') {
      setMsg($('measureMsg'), '请点地图上的地点标记，或从测距卡片里选一个已标记的地点。', 'err');
      toast('测距请选已标记的地点，不要点空白处。');
    }
    else if (S.tool === 'route') { handleRouteClick(lng, lat); }
  }

  // ---------------- 搜索高德 ----------------
  function renderSearchResults(items) {
    var box = $('placeSearchResults');
    if (!box) { return; }
    S.searchItems = Array.isArray(items) ? items : [];
    if (!S.searchItems.length) {
      box.hidden = true;
      box.innerHTML = '';
      return;
    }
    box.hidden = false;
    box.innerHTML = S.searchItems.map(function (item, index) {
      return '<button class="search-result" type="button" data-index="' + index + '">' +
        '<b>' + esc(item.name || '未命名地点') + '</b>' +
        '<span>' + esc(item.address || '') +
          (item.district || item.city ? ' · ' + esc(item.city || '') + esc(item.district || '') : '') +
        '</span>' +
      '</button>';
    }).join('');
  }

  function showSearchOnMap(item) {
    if (!S.map || !window.TBMapCore || item.lng == null || item.lat == null) { return; }
    clearSearchMarker();
    var marker = window.TBMapCore.pin(item.lng, item.lat, '搜', 'search', item.name || '');
    var html = '<div style="font-size:13px;line-height:1.7;max-width:230px">' +
      '<b>' + esc(item.name || '搜索结果') + '</b>' +
      (item.address ? '<div style="color:#666;font-size:12px">' + esc(item.address) + '</div>' : '') +
      '</div>';
    S.searchPoint = {
      lng: Number(item.lng),
      lat: Number(item.lat),
      name: item.name || '',
      address: item.address || '',
      city: item.city || '',
      district: item.district || '',
      adcode: item.adcode || '',
      kind: 'search'
    };
    marker._tbPoint = S.searchPoint;
    marker.on('click', function () {
      if (handleMarkerClick(S.searchPoint)) { return; }
      window.TBMapCore.infoWindow(html).open(S.map, [Number(item.lng), Number(item.lat)]);
    });
    S.map.add(marker);
    S.searchMarker = marker;
    renderMeasureOptions();
    try {
      S.map.setZoomAndCenter(16, [Number(item.lng), Number(item.lat)]);
      window.TBMapCore.infoWindow(html).open(S.map, [Number(item.lng), Number(item.lat)]);
    } catch (err) { /* 地图还没准备好时忽略 */ }
    if ($('placesMapNote')) {
      $('placesMapNote').textContent = '已定位：' + (item.name || item.address || '搜索结果') +
        '。可以保存为我的地点。';
    }
  }

  function pickSearchResult(index) {
    var item = S.searchItems[index];
    if (!item) { return; }
    S.picked = item;
    if (!$('placeName').value.trim()) { $('placeName').value = item.name || ''; }
    $('placeAddress').value = item.address || item.name || '';
    var text = (item.type || '') + (item.name || '');
    if (!S.editing) {
      if (item.type && item.type.indexOf('餐') >= 0) { setCategory('food'); }
      else if (text.indexOf('风景') >= 0 || text.indexOf('公园') >= 0 ||
               text.indexOf('景区') >= 0 || text.indexOf('景点') >= 0) { setCategory('scenic'); }
    }
    $('placeSearchResults').hidden = true;
    setMsg($('placeSearchMsg'), '已选中：' + (item.name || item.address || ''), 'ok');
    showSearchOnMap(item);
    openAddBox();
    if (isMobile()) {
      setTimeout(closeSheet, 180);
    } else {
      $('placeName').focus();
    }
  }

  async function searchPlaces() {
    if (S.busy) { return; }
    var q = $('placeAddress').value.trim();
    if (!q) {
      setMsg($('placeSearchMsg'), '先填地址或地点名，再点搜索。', 'err');
      $('placeAddress').focus();
      return;
    }
    S.busy = true;
    var btn = $('placeSearchBtn');
    btn.disabled = true;
    btn.textContent = '搜索中…';
    setMsg($('placeSearchMsg'), '正在问高德，第一次会慢一点。');
    try {
      var data = await api('/trip/api/places/search?q=' + encodeURIComponent(q));
      renderSearchResults(data.items || []);
      setMsg($('placeSearchMsg'),
        (data.items || []).length ? '点一个结果选中它。' : '没搜到，换个更具体的名字试试。',
        (data.items || []).length ? '' : 'err');
      refreshQuota();
    } catch (err) {
      renderSearchResults([]);
      setMsg($('placeSearchMsg'), err.message || '搜索失败', 'err');
    } finally {
      S.busy = false;
      btn.disabled = false;
      btn.textContent = '搜索';
    }
  }

  // ---------------- 保存 / 删除 ----------------
  async function savePlace() {
    if (S.busy) { return; }
    var name = $('placeName').value.trim();
    var address = $('placeAddress').value.trim();
    var payload = {
      name: name,
      address: address,
      category: S.category,
      tags: textToTags($('placeTags').value),
      note: $('placeNote').value.trim()
    };
    if (S.picked && (!address || address === (S.picked.address || '') || address === (S.picked.name || ''))) {
      payload.lng = S.picked.lng;
      payload.lat = S.picked.lat;
      payload.city = S.picked.city || '';
      payload.district = S.picked.district || '';
      payload.adcode = S.picked.adcode || '';
    }
    if (!address && !S.picked) {
      setMsg($('placeFormMsg'), '填一个地址，或先搜索并选中一个地点。', 'err');
      $('placeAddress').focus();
      return;
    }

    var editing = S.editing;
    S.busy = true;
    var btn = $('placeSaveBtn');
    btn.disabled = true;
    btn.textContent = editing ? '保存中…' : '添加中…';
    setMsg($('placeFormMsg'), '正在保存…');
    try {
      var data;
      if (editing) {
        data = await api('/trip/api/places/' + encodeURIComponent(editing), {
          method: 'PATCH',
          body: JSON.stringify(payload)
        });
      } else {
        data = await api('/trip/api/places', {
          method: 'POST',
          body: JSON.stringify(payload)
        });
      }
      toast(editing ? '地点已更新' : '地点已收藏');
      resetForm();
      await loadPlaces();
      if (data.place && data.place.id) { focusMapOnPlace(data.place); }
      if (isMobile()) { closeSheet(); }
      refreshQuota();
    } catch (err) {
      setMsg($('placeFormMsg'), err.message || '保存失败', 'err');
    } finally {
      S.busy = false;
      btn.disabled = false;
      btn.textContent = editing ? '保存修改' : '保存地点';
    }
  }

  async function deletePlace(id, name) {
    if (!window.confirm('删除「' + (name || '这个地点') + '」？删除后不能恢复。')) { return; }
    try {
      await api('/trip/api/places/' + encodeURIComponent(id), { method: 'DELETE' });
      if (String(S.editing) === String(id)) { resetForm(); }
      toast('已删除');
      loadPlaces();
    } catch (err) {
      toast(err.message || '删除失败');
    }
  }

  // ---------------- 列表 ----------------
  function renderList() {
    var box = $('placeList');
    if (!box) { return; }
    $('placeCount').textContent = S.places.length ? S.places.length + ' 个' : '';
    if (!S.places.length) {
      box.innerHTML = '<div class="mini">' +
        (S.q || S.filterCategory ? '没有符合筛选条件的地点。' : '还没有收藏地点。先在上面搜索一个地址。') +
        '</div>';
      return;
    }
    box.innerHTML = S.places.map(function (place) {
      var tags = Array.isArray(place.tags) && place.tags.length
        ? ' · ' + place.tags.map(esc).join(' / ') : '';
      return '<div class="place-item" data-id="' + esc(place.id) + '">' +
        '<div class="place-top">' +
          '<span class="place-name">' + esc(place.name || '未命名地点') + '</span>' +
          '<span class="place-cat">' + esc(CAT_NAMES[place.category] || '其他') + '</span>' +
        '</div>' +
        (place.address ? '<div class="place-addr">' + esc(place.address) + '</div>' : '') +
        (place.note ? '<div class="place-note">' + esc(place.note) + '</div>' : '') +
        (tags ? '<div class="place-addr">' + tags + '</div>' : '') +
        '<div class="place-actions">' +
          '<button class="lnk" type="button" data-act="edit">编辑</button>' +
          '<button class="lnk danger" type="button" data-act="del">删除</button>' +
        '</div>' +
      '</div>';
    }).join('');
  }

  function findPlace(id) {
    for (var i = 0; i < S.places.length; i += 1) {
      if (String(S.places[i].id) === String(id)) { return S.places[i]; }
    }
    return null;
  }

  async function loadPlaces() {
    var seq = ++S.loadSeq;
    try {
      var query = '/trip/api/places?limit=300';
      if (S.q) { query += '&q=' + encodeURIComponent(S.q); }
      if (S.filterCategory) { query += '&category=' + encodeURIComponent(S.filterCategory); }
      var data = await api(query);
      if (seq !== S.loadSeq) { return; }
      S.places = data.items || [];
      renderList();
      renderMap();
    } catch (err) {
      if (seq !== S.loadSeq) { return; }
      $('placeList').innerHTML = '<div class="mini">地点没读出来：' + esc(err.message || '') + '</div>';
    }
  }

  // ---------------- 地图 ----------------
  function renderQuota(quota) {
    S.quota = quota || null;
    var card = $('quotaCard');
    var box = $('quotaList');
    if (!card || !box || !quota || !quota.services) { return; }
    card.hidden = false;
    $('quotaHint').textContent = quota.guard ? '达到自设上限会暂停调用' : '配额保护已关闭';
    var order = ['poi', 'geocode', 'regeo', 'route', 'weather', 'convert'];
    box.innerHTML = order.map(function (key) {
      var item = quota.services[key];
      if (!item) { return ''; }
      var full = item.cap > 0 && item.used >= item.cap;
      return '<div class="quota-row ' + (full ? 'full' : '') + '">' +
        '<span>' + esc(item.label) + '</span>' +
        '<span><b>' + esc(item.used) + '</b> / ' + (item.cap || '不限') + '</span>' +
      '</div>';
    }).join('');
  }

  function renderStatus(data) {
    var pill = $('statusPill');
    var txt = $('statusTxt');
    var banner = $('configBanner');
    if (!pill || !txt || !banner) { return; }
    if (!data) {
      pill.className = 'pill warn';
      txt.textContent = '配置状态未知';
      banner.className = 'banner warn';
      banner.hidden = false;
      $('bannerIco').textContent = '⚠';
      $('bannerText').innerHTML = '高德配置状态没读到，刷新页面再试；已收藏的地点仍可查看。';
      return;
    }
    if (data.amap_ready && data.map_ready) {
      pill.className = 'pill ok';
      txt.textContent = '高德已就绪';
      banner.hidden = true;
      return;
    }
    pill.className = 'pill warn';
    txt.textContent = data.amap_ready ? '可搜索 · 地图未配置' : '高德未配置';
    banner.className = 'banner warn';
    banner.hidden = false;
    $('bannerIco').textContent = '⚠';
    $('bannerText').innerHTML = data.amap_ready
      ? '没有配置 <code>AMAP_JS_KEY</code> / <code>AMAP_SECURITY_CODE</code>，地图先不显示；搜索和地点列表仍然可用。'
      : '没有配置 <code>AMAP_WEB_KEY</code>，暂时不能搜索新地址；已收藏的地点仍然可以查看。';
  }

  async function refreshQuota() {
    try {
      var data = await api('/trip/api/map/status');
      S.status = data;
      renderStatus(data);
      renderQuota(data.quota);
      if (data.place_count != null) { $('placeMapHint').textContent = data.place_count + ' 个地点'; }
    } catch (err) { /* 配额只是附加信息，不打断主流程 */ }
  }

  async function loadStatus() {
    try {
      S.status = await api('/trip/api/map/status');
      renderStatus(S.status);
      renderQuota(S.status.quota);
      if (S.status.place_count != null) {
        $('placeMapHint').textContent = S.status.place_count + ' 个地点';
      }
    } catch (err) {
      S.status = null;
      renderStatus(null);
    }
    renderMap();
  }

  function focusMapOnPlace(place) {
    if (!S.map || place.lng == null || place.lat == null) { return; }
    try {
      S.map.setZoomAndCenter(14, [Number(place.lng), Number(place.lat)]);
    } catch (err) { /* 地图还没准备好时忽略 */ }
  }

  function categoryClass(category) {
    return 'cat-' + (CAT_NAMES[category] ? category : 'other');
  }

  function renderMap() {
    var note = $('placesMapNote');
    if (!note) { return; }
    if (!S.status || !S.status.map_ready) {
      note.textContent = S.status
        ? '没有配置高德 JS API Key / 安全密钥，地图先不显示；地点列表仍然可用。'
        : '地图状态还没读到。';
      return;
    }
    if (S.mapLoading) { return; }
    if (!window.TBMapCore) {
      note.textContent = '地图模块没加载出来，刷新页面再试。';
      return;
    }
    S.mapLoading = true;
    window.TBMapCore.load(S.status).then(function () {
      if (!S.map) {
        S.map = new window.AMap.Map('placesMap', {
          zoom: 12,
          center: [120.15, 30.25],
          resizeEnable: true,
          viewMode: '2D'
        });
        S.map.on('click', onMapClick);
        if ($('mapTools')) { $('mapTools').hidden = false; }
        try {
          if (window.AMap.ToolBar) {
            S.map.addControl(new window.AMap.ToolBar({
              position: { right: '16px', bottom: '145px' }
            }));
          }
        } catch (err) { /* 控件加载失败不影响地图 */ }
        setTimeout(resizeMap, 80);
      }
      window.TBMapCore.clear(S.map, S.markers);
      S.markers = S.places.filter(function (place) {
        return place.lng != null && place.lat != null;
      }).map(function (place) {
        var marker = window.TBMapCore.pin(
          place.lng, place.lat, '•', categoryClass(place.category), place.name || '');
        marker._tbPoint = {
          lng: Number(place.lng),
          lat: Number(place.lat),
          name: place.name || '',
          address: place.address || '',
          placeId: place.id,
          kind: 'place'
        };
        marker.on('click', function () {
          if (handleMarkerClick(marker._tbPoint)) { return; }
          var html = '<div style="font-size:13px;line-height:1.7;max-width:230px">' +
            '<b>' + esc(place.name || '') + '</b>' +
            (place.address ? '<div style="color:#666;font-size:12px">' + esc(place.address) + '</div>' : '') +
            (place.note ? '<div style="font-size:12px">' + esc(place.note) + '</div>' : '') +
            '</div>';
          window.TBMapCore.infoWindow(html).open(S.map, [Number(place.lng), Number(place.lat)]);
        });
        return marker;
      });
      if (S.markers.length) {
        S.map.add(S.markers);
        fitAll();
        note.textContent = S.markers.length + ' 个地点，标记颜色对应分类。';
      } else {
        note.textContent = '还没有地点。在面板里搜索地址并保存后，地图会显示标记。';
      }
      $('placeMapHint').textContent = S.places.length + ' 个地点';
      renderRouteEndpoints();
    }).catch(function (err) {
      note.textContent = '地图没加载出来：' + (err && err.message ? err.message : '未知原因') +
        '。地点列表不受影响。';
    }).then(function () {
      S.mapLoading = false;
    });
  }

  // ---------------- 事件 ----------------
  function onShow() {
    if (!S.loaded) {
      S.loaded = true;
      loadStatus();
      loadPlaces();
    } else if (S.map) {
      setTimeout(function () {
        try { S.map.resize(); } catch (err) { /* 忽略 */ }
      }, 80);
    }
  }

  function bind() {
    var catBox = $('placeCatChips');
    if (catBox) {
      catBox.addEventListener('click', function (event) {
        var chip = event.target.closest('.chip');
        if (chip && catBox.contains(chip)) { setCategory(chip.getAttribute('data-cat')); }
      });
    }

    var filterBox = $('placeFilterCats');
    if (filterBox) {
      filterBox.addEventListener('click', function (event) {
        var chip = event.target.closest('.chip');
        if (chip && filterBox.contains(chip)) {
          setFilterCategory(chip.getAttribute('data-cat') || '');
          loadPlaces();
        }
      });
    }

    $('placeSearchBtn').addEventListener('click', searchPlaces);
    $('placeSaveBtn').addEventListener('click', savePlace);
    $('placeResetBtn').addEventListener('click', resetForm);
    if ($('sheetToggle')) { $('sheetToggle').addEventListener('click', toggleSheet); }
    if ($('sheetClose')) { $('sheetClose').addEventListener('click', closeSheet); }
    if ($('panelCollapse')) {
      $('panelCollapse').addEventListener('click', function () { setPanelCollapsed(true); });
    }
    if ($('panelExpand')) {
      $('panelExpand').addEventListener('click', function () { setPanelCollapsed(false); });
    }
    if ($('fitBtn')) {
      $('fitBtn').addEventListener('click', function () {
        fitAll();
        if (isMobile()) { closeSheet(); }
      });
    }

    [['toolPick', 'pick'], ['toolMeasure', 'measure'], ['toolRoute', 'route']].forEach(function (pair) {
      var btn = $(pair[0]);
      if (btn) {
        btn.addEventListener('click', function () { setTool(pair[1]); });
      }
    });
    if ($('toolClear')) {
      $('toolClear').addEventListener('click', clearTools);
    }
    if ($('pickClose')) {
      $('pickClose').addEventListener('click', function () { clearPick(); });
    }
    if ($('pickSavePlace')) {
      $('pickSavePlace').addEventListener('click', usePickAsPlace);
    }
    if ($('pickSetStart')) {
      $('pickSetStart').addEventListener('click', function () { pickToRoute('start'); });
    }
    if ($('pickSetEnd')) {
      $('pickSetEnd').addEventListener('click', function () { pickToRoute('end'); });
    }
    if ($('measureClose')) {
      $('measureClose').addEventListener('click', function () { setTool(''); });
    }
    if ($('measurePointSelect')) {
      $('measurePointSelect').addEventListener('change', function () {
        onMeasureSelectChange(this);
      });
    }
    if ($('measureUndo')) {
      $('measureUndo').addEventListener('click', removeLastMeasurePoint);
    }
    if ($('measureClearBtn')) {
      $('measureClearBtn').addEventListener('click', function () {
        clearMeasure();
        toast('测距已清空。');
      });
    }
    if ($('routeStartSelect')) {
      $('routeStartSelect').addEventListener('change', function () {
        onRouteSelectChange('start', this);
      });
    }
    if ($('routeEndSelect')) {
      $('routeEndSelect').addEventListener('change', function () {
        onRouteSelectChange('end', this);
      });
    }
    if ($('routeModes')) {
      $('routeModes').addEventListener('click', function (event) {
        var chip = event.target.closest('[data-mode]');
        if (chip && $('routeModes').contains(chip)) {
          setRouteMode(chip.getAttribute('data-mode'));
        }
      });
    }
    if ($('routeCalcBtn')) {
      $('routeCalcBtn').addEventListener('click', calculateRoute);
    }
    if ($('routeSwapBtn')) {
      $('routeSwapBtn').addEventListener('click', swapRouteEndpoints);
    }
    if ($('routeResetBtn')) {
      $('routeResetBtn').addEventListener('click', resetRouteSelection);
    }
    window.addEventListener('resize', function () { resizeMap(); });
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape' && S.sheetOpen) { closeSheet(); }
    });

    $('placeAddress').addEventListener('keydown', function (event) {
      if (event.key === 'Enter') {
        event.preventDefault();
        searchPlaces();
      }
    });

    $('placeSearchResults').addEventListener('click', function (event) {
      var btn = event.target.closest('[data-index]');
      if (btn) { pickSearchResult(Number(btn.getAttribute('data-index'))); }
    });

    $('placeFilter').addEventListener('input', function () {
      S.q = $('placeFilter').value.trim();
      clearTimeout(S.filterTimer);
      S.filterTimer = setTimeout(loadPlaces, 260);
    });

    $('placeList').addEventListener('click', function (event) {
      var btn = event.target.closest('[data-act]');
      if (!btn) { return; }
      var holder = btn.closest('[data-id]');
      var place = holder ? findPlace(holder.getAttribute('data-id')) : null;
      if (!place) { return; }
      var act = btn.getAttribute('data-act');
      if (act === 'edit') { fillForm(place); }
      else if (act === 'del') { deletePlace(place.id, place.name); }
    });
  }

  function init() {
    if (!$('panel-places')) { return; }
    bind();
    resetForm();
    onShow();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
