(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };
  var canvas = $('imageCanvas');
  var ctx = canvas.getContext('2d');
  var stageEl = $('stage');
  var canvasStage = $('canvasStage');
  var fileInput = $('file');
  var drop = $('drop');
  var msg = $('msg');
  var out = $('out');
  var spinner = $('spinner');
  var zoomChip = $('zoomChip');
  var selectionInfo = $('selectionInfo');

  var MAX_UP = 1000 * 1024;
  var OK_TYPES = ['image/png', 'image/jpeg', 'image/gif', 'image/bmp', 'image/tiff'];
  var state = {
    image: null,
    prepared: null,
    scale: 1,
    offsetX: 0,
    offsetY: 0,
    tool: 'pan',
    selection: null,
    redactions: [],
    boxes: [],
    selectedBox: -1,
    drag: null,
    pinch: null,
    pointers: new Map(),
    dpr: 1
  };

  function showMsg(text, kind) {
    msg.textContent = text || '';
    msg.className = text ? ('show ' + (kind || 'info')) : '';
  }

  function clearMsg() {
    msg.textContent = '';
    msg.className = '';
  }

  function clamp(value, minimum, maximum) {
    return Math.max(minimum, Math.min(maximum, value));
  }

  function toBlob(target, type, quality) {
    return new Promise(function (resolve) { target.toBlob(resolve, type, quality); });
  }

  function loadImageBlob(blob) {
    return new Promise(function (resolve, reject) {
      var url = URL.createObjectURL(blob);
      var image = new Image();
      image.onload = function () {
        URL.revokeObjectURL(url);
        resolve(image);
      };
      image.onerror = function () {
        URL.revokeObjectURL(url);
        reject(new Error('图片读取失败'));
      };
      image.src = url;
    });
  }

  function prepare(file) {
    var type = (file.type || '').toLowerCase();
    if (OK_TYPES.indexOf(type) >= 0 && file.size <= MAX_UP) {
      return Promise.resolve({ blob: file, name: file.name || 'screenshot.png' });
    }
    return loadImageBlob(file).then(function (image) {
      var work = document.createElement('canvas');
      var workCtx = work.getContext('2d');
      var scale = 1;
      var tries = [
        { type: 'image/png' },
        { type: 'image/jpeg', quality: 0.92 },
        { type: 'image/jpeg', quality: 0.8 },
        { type: 'image/jpeg', quality: 0.65 }
      ];
      function attempt(index) {
        if (index >= tries.length) {
          scale *= 0.7;
          if (scale < 0.15) return null;
          return attempt(0);
        }
        var spec = tries[index];
        work.width = Math.max(1, Math.round(image.naturalWidth * scale));
        work.height = Math.max(1, Math.round(image.naturalHeight * scale));
        workCtx.fillStyle = '#fff';
        workCtx.fillRect(0, 0, work.width, work.height);
        workCtx.drawImage(image, 0, 0, work.width, work.height);
        return toBlob(work, spec.type, spec.quality).then(function (blob) {
          if (blob && blob.size <= MAX_UP) {
            return {
              blob: blob,
              name: spec.type === 'image/png' ? 'screenshot.png' : 'screenshot.jpg'
            };
          }
          return attempt(index + 1);
        });
      }
      return attempt(0);
    });
  }

  function stageSize() {
    var rect = canvas.getBoundingClientRect();
    return { width: Math.max(1, rect.width), height: Math.max(1, rect.height) };
  }

  function resizeCanvas() {
    var size = stageSize();
    var nextDpr = Math.min(window.devicePixelRatio || 1, 2);
    var width = Math.round(size.width * nextDpr);
    var height = Math.round(size.height * nextDpr);
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    state.dpr = nextDpr;
    draw();
  }

  function fitToWindow() {
    if (!state.image) return;
    var size = stageSize();
    var nextScale = Math.min(
      (size.width - 28) / state.image.naturalWidth,
      (size.height - 28) / state.image.naturalHeight
    );
    state.scale = clamp(nextScale, 0.01, 20);
    state.offsetX = (size.width - state.image.naturalWidth * state.scale) / 2;
    state.offsetY = (size.height - state.image.naturalHeight * state.scale) / 2;
    draw();
  }

  function screenPoint(event) {
    var rect = canvas.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  }

  function screenToImage(point) {
    return {
      x: (point.x - state.offsetX) / state.scale,
      y: (point.y - state.offsetY) / state.scale
    };
  }

  function imageToScreen(point) {
    return {
      x: point.x * state.scale + state.offsetX,
      y: point.y * state.scale + state.offsetY
    };
  }

  function normalizedRect(a, b) {
    return {
      x: Math.min(a.x, b.x),
      y: Math.min(a.y, b.y),
      width: Math.abs(a.x - b.x),
      height: Math.abs(a.y - b.y)
    };
  }

  function clampRect(rect) {
    if (!state.image) return rect;
    var x1 = clamp(rect.x, 0, state.image.naturalWidth);
    var y1 = clamp(rect.y, 0, state.image.naturalHeight);
    var x2 = clamp(rect.x + rect.width, 0, state.image.naturalWidth);
    var y2 = clamp(rect.y + rect.height, 0, state.image.naturalHeight);
    return {
      x: Math.min(x1, x2), y: Math.min(y1, y2),
      width: Math.abs(x2 - x1), height: Math.abs(y2 - y1)
    };
  }

  function draw() {
    if (!canvas.width || !canvas.height) return;
    ctx.setTransform(state.dpr, 0, 0, state.dpr, 0, 0);
    var size = stageSize();
    ctx.clearRect(0, 0, size.width, size.height);
    ctx.fillStyle = '#080d15';
    ctx.fillRect(0, 0, size.width, size.height);
    if (!state.image) return;

    var imageX = state.offsetX;
    var imageY = state.offsetY;
    var imageW = state.image.naturalWidth * state.scale;
    var imageH = state.image.naturalHeight * state.scale;
    ctx.imageSmoothingEnabled = true;
    ctx.drawImage(state.image, imageX, imageY, imageW, imageH);

    state.boxes.forEach(function (box, index) {
      var p = imageToScreen({ x: box.left, y: box.top });
      var selected = index === state.selectedBox;
      ctx.fillStyle = selected ? 'rgba(251,191,36,.2)' : 'rgba(34,211,238,.1)';
      ctx.strokeStyle = selected ? '#fbbf24' : '#22d3ee';
      ctx.lineWidth = selected ? 2.5 : 1.5;
      ctx.fillRect(p.x, p.y, box.width * state.scale, box.height * state.scale);
      ctx.strokeRect(p.x, p.y, box.width * state.scale, box.height * state.scale);
    });

    state.redactions.forEach(function (rect) {
      var p = imageToScreen(rect);
      ctx.fillStyle = '#000';
      ctx.fillRect(p.x, p.y, rect.width * state.scale, rect.height * state.scale);
      ctx.strokeStyle = '#ff6477';
      ctx.lineWidth = 1.5;
      ctx.strokeRect(p.x, p.y, rect.width * state.scale, rect.height * state.scale);
    });

    var selection = state.selection;
    if (state.drag && state.drag.type === 'selection') {
      selection = normalizedRect(state.drag.startImage, state.drag.currentImage);
    }
    if (selection && selection.width > 1 && selection.height > 1) {
      var s = imageToScreen(selection);
      var sw = selection.width * state.scale;
      var sh = selection.height * state.scale;
      ctx.fillStyle = 'rgba(34,211,238,.08)';
      ctx.fillRect(s.x, s.y, sw, sh);
      ctx.setLineDash([7, 5]);
      ctx.strokeStyle = '#67e8f9';
      ctx.lineWidth = 2;
      ctx.strokeRect(s.x, s.y, sw, sh);
      ctx.setLineDash([]);
      ctx.fillStyle = '#d9fbff';
      ctx.font = '700 11px "Microsoft YaHei", sans-serif';
      ctx.fillText('仅识别此区域', s.x + 5, Math.max(14, s.y - 6));
    }

    zoomChip.textContent = Math.round(state.scale * 100) + '%';
  }

  function setTool(tool) {
    state.tool = tool;
    document.querySelectorAll('[data-tool]').forEach(function (button) {
      button.classList.toggle('on', button.dataset.tool === tool);
    });
    canvas.classList.toggle('mode-crop', tool === 'crop');
    canvas.classList.toggle('mode-redact', tool === 'redact');
    canvas.style.cursor = tool === 'pan' ? 'grab' : 'crosshair';
    $('canvasTip').textContent = tool === 'crop'
      ? '在图片上拖出要识别的区域'
      : tool === 'redact'
        ? '拖出区域并涂黑；点击识别时不会上传被打码的内容'
        : '滚轮缩放 · 拖动平移 · 框选后只识别选区';
  }

  function updateSelectionInfo() {
    if (state.selectedBox >= 0 && state.boxes[state.selectedBox]) {
      selectionInfo.textContent = '文字框：' + state.boxes[state.selectedBox].text;
    } else if (state.selection) {
      selectionInfo.textContent = '识别范围：' + Math.round(state.selection.width) + ' × ' +
        Math.round(state.selection.height) + ' 像素';
    } else {
      selectionInfo.textContent = '当前识别范围：整张图片 · 打码 ' + state.redactions.length + ' 处';
    }
  }

  function zoomAt(factor, anchor) {
    if (!state.image) return;
    var before = screenToImage(anchor);
    state.scale = clamp(state.scale * factor, 0.02, 20);
    state.offsetX = anchor.x - before.x * state.scale;
    state.offsetY = anchor.y - before.y * state.scale;
    draw();
  }

  function hitBox(point) {
    for (var i = state.boxes.length - 1; i >= 0; i -= 1) {
      var box = state.boxes[i];
      if (point.x >= box.left && point.x <= box.left + box.width &&
          point.y >= box.top && point.y <= box.top + box.height) return i;
    }
    return -1;
  }

  function pointerDown(event) {
    if (!state.image) return;
    event.preventDefault();
    canvas.focus();
    canvas.setPointerCapture(event.pointerId);
    state.pointers.set(event.pointerId, screenPoint(event));
    if (state.pointers.size === 2) {
      var values = Array.from(state.pointers.values());
      var mid = { x: (values[0].x + values[1].x) / 2, y: (values[0].y + values[1].y) / 2 };
      state.pinch = {
        distance: Math.hypot(values[0].x - values[1].x, values[0].y - values[1].y) || 1,
        scale: state.scale,
        imagePoint: screenToImage(mid)
      };
      state.drag = null;
      return;
    }
    var point = screenPoint(event);
    if (state.tool === 'pan') {
      state.drag = {
        type: 'pan', start: point, originX: state.offsetX, originY: state.offsetY,
        moved: false
      };
    } else {
      var imagePoint = screenToImage(point);
      state.drag = {
        type: 'selection', tool: state.tool, startImage: imagePoint,
        currentImage: imagePoint
      };
    }
  }

  function pointerMove(event) {
    if (!state.pointers.has(event.pointerId)) return;
    state.pointers.set(event.pointerId, screenPoint(event));
    if (state.pointers.size === 2 && state.pinch) {
      var values = Array.from(state.pointers.values());
      var mid = { x: (values[0].x + values[1].x) / 2, y: (values[0].y + values[1].y) / 2 };
      var distance = Math.hypot(values[0].x - values[1].x, values[0].y - values[1].y) || 1;
      state.scale = clamp(state.pinch.scale * distance / state.pinch.distance, 0.02, 20);
      state.offsetX = mid.x - state.pinch.imagePoint.x * state.scale;
      state.offsetY = mid.y - state.pinch.imagePoint.y * state.scale;
      draw();
      return;
    }
    if (!state.drag) return;
    var point = screenPoint(event);
    if (state.drag.type === 'pan') {
      var dx = point.x - state.drag.start.x;
      var dy = point.y - state.drag.start.y;
      if (Math.abs(dx) + Math.abs(dy) > 3) state.drag.moved = true;
      state.offsetX = state.drag.originX + dx;
      state.offsetY = state.drag.originY + dy;
    } else {
      state.drag.currentImage = screenToImage(point);
    }
    draw();
  }

  function pointerUp(event) {
    var point = screenPoint(event);
    state.pointers.delete(event.pointerId);
    if (state.pointers.size < 2) state.pinch = null;
    var drag = state.drag;
    state.drag = null;
    if (!drag) {
      draw();
      return;
    }
    if (drag.type === 'pan' && !drag.moved) {
      var index = hitBox(screenToImage(point));
      if (index >= 0) {
        state.selectedBox = index;
        updateSelectionInfo();
      }
    } else if (drag.type === 'selection') {
      var rect = clampRect(normalizedRect(drag.startImage, drag.currentImage));
      if (rect.width > 3 && rect.height > 3) {
        if (drag.tool === 'crop') {
          state.selection = rect;
          state.boxes = [];
          state.selectedBox = -1;
          showMsg('已框选识别区域。点击“识别文字”只上传这一块。', 'info');
        } else {
          state.redactions.push(rect);
          showMsg('打码区域已添加，识别时将不会上传这部分内容。', 'info');
        }
        updateSelectionInfo();
      }
    }
    draw();
  }

  function pointerCancel(event) {
    state.pointers.delete(event.pointerId);
    if (state.pointers.size < 2) state.pinch = null;
    state.drag = null;
    draw();
  }

  function clearMarks() {
    state.selection = null;
    state.redactions = [];
    state.boxes = [];
    state.selectedBox = -1;
    updateSelectionInfo();
    draw();
  }

  function buildRegionCanvas(region) {
    var rect = clampRect(region || {
      x: 0, y: 0, width: state.image.naturalWidth, height: state.image.naturalHeight
    });
    var work = document.createElement('canvas');
    work.width = Math.max(1, Math.round(rect.width));
    work.height = Math.max(1, Math.round(rect.height));
    var workCtx = work.getContext('2d');
    workCtx.drawImage(
      state.image,
      rect.x, rect.y, rect.width, rect.height,
      0, 0, work.width, work.height
    );
    state.redactions.forEach(function (redaction) {
      var left = Math.max(rect.x, redaction.x);
      var top = Math.max(rect.y, redaction.y);
      var right = Math.min(rect.x + rect.width, redaction.x + redaction.width);
      var bottom = Math.min(rect.y + rect.height, redaction.y + redaction.height);
      if (right > left && bottom > top) {
        workCtx.fillStyle = '#000';
        workCtx.fillRect(left - rect.x, top - rect.y, right - left, bottom - top);
      }
    });
    return { canvas: work, region: rect };
  }

  async function compressForUpload(source) {
    var scale = 1;
    var specs = [
      { type: 'image/png' },
      { type: 'image/jpeg', quality: 0.92 },
      { type: 'image/jpeg', quality: 0.8 },
      { type: 'image/jpeg', quality: 0.65 }
    ];
    for (var round = 0; round < 6; round += 1) {
      var work = document.createElement('canvas');
      work.width = Math.max(1, Math.round(source.width * scale));
      work.height = Math.max(1, Math.round(source.height * scale));
      var workCtx = work.getContext('2d');
      workCtx.fillStyle = '#fff';
      workCtx.fillRect(0, 0, work.width, work.height);
      workCtx.drawImage(source, 0, 0, work.width, work.height);
      for (var i = 0; i < specs.length; i += 1) {
        var blob = await toBlob(work, specs[i].type, specs[i].quality);
        if (blob && blob.size <= MAX_UP) {
          return {
            blob: blob,
            width: work.width,
            height: work.height,
            extension: specs[i].type === 'image/png' ? 'png' : 'jpg'
          };
        }
      }
      scale *= 0.72;
    }
    throw new Error('选区压缩后仍超过 1 MB，请缩小框选范围');
  }

  async function uploadPayload() {
    var built = buildRegionCanvas(state.selection);
    var compressed = await compressForUpload(built.canvas);
    return {
      blob: compressed.blob,
      region: built.region,
      ratioX: compressed.width / built.canvas.width,
      ratioY: compressed.height / built.canvas.height,
      extension: compressed.extension
    };
  }

  async function run() {
    if (!state.image || !state.prepared) {
      showMsg('请先粘贴或选择图片', 'err');
      return;
    }
    clearMsg();
    spinner.classList.add('on');
    $('btnRun').disabled = true;
    $('btnDownloadImage').disabled = true;
    try {
      var payload = await uploadPayload();
      var form = new FormData();
      form.append('file', payload.blob, 'ocr-selection.' + payload.extension);
      var response = await fetch('/ocr/api/recognize', { method: 'POST', body: form });
      var data = await response.json();
      if (!data.ok) {
        showMsg(data.message || '识别失败，请重试', 'err');
        return;
      }
      state.boxes = (data.boxes || []).map(function (box) {
        return {
          text: String(box.text || ''),
          left: payload.region.x + Number(box.left || 0) / payload.ratioX,
          top: payload.region.y + Number(box.top || 0) / payload.ratioY,
          width: Number(box.width || 0) / payload.ratioX,
          height: Number(box.height || 0) / payload.ratioY
        };
      });
      state.selectedBox = -1;
      out.value = data.text || '';
      $('result').hidden = false;
      $('stats').textContent = data.count
        ? (data.count + ' 行 · ' + state.boxes.length + ' 个定位框 · 耗时 ' + data.elapsed + 's')
        : '未识别到文字';
      updateSelectionInfo();
      draw();
      if (!data.count) showMsg('没有识别到文字，换一张更清晰的图片试试', 'info');
    } catch (error) {
      showMsg(error.message || '请求失败，请检查服务是否在运行', 'err');
    } finally {
      spinner.classList.remove('on');
      $('btnRun').disabled = false;
      $('btnDownloadImage').disabled = false;
    }
  }

  async function setFile(file) {
    if (!file) return;
    if (!/^image\//.test(file.type || '')) {
      showMsg('请选择图片文件', 'err');
      return;
    }
    clearMsg();
    $('btnRun').disabled = true;
    try {
      var prepared = await prepare(file);
      if (!prepared) throw new Error('图片太大，压缩不到 1 MB 以内，请裁剪后再试');
      state.image = await loadImageBlob(prepared.blob);
      state.prepared = prepared;
      state.selection = null;
      state.redactions = [];
      state.boxes = [];
      state.selectedBox = -1;
      stageEl.hidden = false;
      $('result').hidden = true;
      out.value = '';
      $('stats').textContent = '';
      setTool('pan');
      updateSelectionInfo();
      requestAnimationFrame(function () {
        resizeCanvas();
        fitToWindow();
      });
      await run();
    } catch (error) {
      showMsg(error.message || '图片读取失败，请换一张试试', 'err');
      $('btnRun').disabled = false;
    }
  }

  function reset() {
    state.image = null;
    state.prepared = null;
    state.selection = null;
    state.redactions = [];
    state.boxes = [];
    state.selectedBox = -1;
    state.pointers.clear();
    state.drag = null;
    state.pinch = null;
    stageEl.hidden = true;
    $('result').hidden = true;
    out.value = '';
    $('stats').textContent = '';
    fileInput.value = '';
    $('btnRun').disabled = false;
    clearMsg();
  }

  async function downloadProcessed() {
    if (!state.image) return;
    try {
      var built = buildRegionCanvas(state.selection);
      var blob = await toBlob(built.canvas, 'image/png');
      if (!blob) throw new Error('图片导出失败');
      var link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = 'ocr-processed-' + Date.now() + '.png';
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(function () { URL.revokeObjectURL(link.href); }, 1500);
      showMsg(state.selection ? '已导出框选区域。' : '已导出处理后的完整图片。', 'info');
    } catch (error) {
      showMsg(error.message || '图片导出失败', 'err');
    }
  }

  function copyText(text, button) {
    function done() {
      var old = button.textContent;
      button.textContent = '已复制';
      setTimeout(function () { button.textContent = old; }, 1500);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done).catch(done);
    } else {
      var area = document.createElement('textarea');
      area.value = text;
      document.body.appendChild(area);
      area.select();
      try { document.execCommand('copy'); } catch (error) {}
      area.remove();
      done();
    }
  }

  function bind() {
    document.querySelectorAll('[data-tool]').forEach(function (button) {
      button.addEventListener('click', function () { setTool(button.dataset.tool); });
    });
    $('toolFit').addEventListener('click', fitToWindow);
    $('btnClearMarks').addEventListener('click', clearMarks);
    $('btnDownloadImage').addEventListener('click', downloadProcessed);
    $('btnRun').addEventListener('click', run);
    $('btnReset').addEventListener('click', reset);
    canvas.addEventListener('pointerdown', pointerDown);
    canvas.addEventListener('pointermove', pointerMove);
    canvas.addEventListener('pointerup', pointerUp);
    canvas.addEventListener('pointercancel', pointerCancel);
    canvas.addEventListener('wheel', function (event) {
      if (!state.image) return;
      event.preventDefault();
      var factor = Math.exp(-event.deltaY * 0.0015);
      zoomAt(factor, screenPoint(event));
    }, { passive: false });
    new ResizeObserver(resizeCanvas).observe(canvasStage);

    drop.addEventListener('click', function () { fileInput.click(); });
    fileInput.addEventListener('change', function () { setFile(fileInput.files[0]); });
    ['dragenter', 'dragover'].forEach(function (name) {
      drop.addEventListener(name, function (event) {
        event.preventDefault();
        drop.classList.add('over');
      });
    });
    ['dragleave', 'dragend'].forEach(function (name) {
      drop.addEventListener(name, function () { drop.classList.remove('over'); });
    });
    drop.addEventListener('drop', function (event) {
      event.preventDefault();
      drop.classList.remove('over');
      setFile(event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0]);
    });
    document.addEventListener('paste', function (event) {
      var items = (event.clipboardData && event.clipboardData.items) || [];
      for (var i = 0; i < items.length; i += 1) {
        if (items[i].type && items[i].type.indexOf('image/') === 0) {
          var file = items[i].getAsFile();
          if (file) {
            event.preventDefault();
            setFile(file);
            return;
          }
        }
      }
    });

    $('btnCopy').addEventListener('click', function () {
      if (out.value) copyText(out.value, $('btnCopy'));
    });
    $('btnSave').addEventListener('click', function () {
      if (!out.value) return;
      var blob = new Blob([out.value], { type: 'text/plain;charset=utf-8' });
      var link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = 'ocr-' + Date.now() + '.txt';
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(function () { URL.revokeObjectURL(link.href); }, 1000);
    });
    $('btnClear').addEventListener('click', function () {
      out.value = '';
      $('stats').textContent = '';
      clearMsg();
    });
  }

  function snapshot() {
    return {
      image: state.image ? {
        width: state.image.naturalWidth,
        height: state.image.naturalHeight
      } : null,
      tool: state.tool,
      scale: Number(state.scale.toFixed(3)),
      selection: state.selection,
      redactions: state.redactions.slice(),
      boxes: state.boxes.slice(),
      selectedBox: state.selectedBox
    };
  }

  bind();
  window.TBOCR = {
    state: state,
    snapshot: snapshot,
    setTool: setTool,
    fit: fitToWindow,
    run: run,
    clearMarks: clearMarks,
    debug: {
      setSelection: function (x, y, width, height) {
        state.selection = clampRect({
          x: Number(x), y: Number(y), width: Number(width), height: Number(height)
        });
        state.boxes = [];
        state.selectedBox = -1;
        updateSelectionInfo();
        draw();
      },
      addRedaction: function (x, y, width, height) {
        state.redactions.push(clampRect({
          x: Number(x), y: Number(y), width: Number(width), height: Number(height)
        }));
        updateSelectionInfo();
        draw();
      }
    }
  };
})();
