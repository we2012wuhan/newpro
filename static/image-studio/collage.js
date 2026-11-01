/*
 * 拼图页签
 * =========
 * 9 张照片 → 一张 3×3 方图。
 *
 * 和「切图」正好相反：
 *   切图 = 一张图 → 9 张切图（ZIP）；
 *   拼图 = 9 张照片 → 一张合成图。
 *
 * 画法只有一条主线：
 *   1. 先按底色铺满整张方图；
 *   2. 再把每个格子算成像素矩形；
 *   3. 每张照片按 cover 方式（等比放大到填满、居中、多出来的裁掉）画进格子；
 *   4. 需要圆角时先 clip 再画。
 *
 * 预览和导出共用同一张 preview canvas —— 屏幕上看到什么，下载的就是什么。
 * 如果预览画一套、导出再算一套，用户迟早会拿到对不齐的图。
 *
 * 照片全程留在内存里，不经过网络；预设只存尺寸、间距这些参数。
 */
import {
  clamp,
  mimeOf,
  extOf,
  toBlob,
  humanSize,
  roundRectPath,
  loadSource
} from './canvas.js';

const SLOT_COUNT = 9;
const COLS = 3;
const SIZES = [1080, 1440, 2048, 3000];
const PHOTO_MAX_PIXELS = 2_000_000;   // 单张照片上限，9 张加起来也可控
const THUMB_SIZE = 128;
const IMAGE_RE = /\.(png|jpe?g|webp|gif|bmp|avif|heic|heif)$/i;

/** 只负责判断「像不像图片」，真正的解码交给 loadSource。 */
function isImageFile(file) {
  if (!file) return false;
  return (file.type && file.type.indexOf('image/') === 0) || IMAGE_RE.test(file.name || '');
}

export function createCollageTab(ctx) {
  const ui = {
    slots: document.getElementById('collageSlots'),
    input: document.getElementById('collageInput'),
    add: document.getElementById('collageAdd'),
    clear: document.getElementById('collageClear'),
    size: document.getElementById('collageSize'),
    gap: document.getElementById('collageGap'),
    margin: document.getElementById('collageMargin'),
    radius: document.getElementById('collageRadius'),
    background: document.getElementById('collageBg'),
    format: document.getElementById('collageFormat'),
    quality: document.getElementById('collageQuality'),
    run: document.getElementById('collageRun')
  };

  const slots = new Array(SLOT_COUNT).fill(null);   // {source, name}
  const cells = [];                                 // {el, thumb}
  const preview = document.createElement('canvas');
  preview.width = 1080;
  preview.height = 1080;

  let previewSize = 1080;
  let loadSeq = 0;        // 批量读取的代号：换一批 / 清空后旧结果作废
  let pendingSlot = null; // 点某个格子打开选择框时，记住要替换哪一格
  let loading = false;

  function numberValue(input, low, high, fallback) {
    const value = Number(input.value);
    if (!Number.isFinite(value)) return fallback;
    return clamp(Math.round(value), low, high);
  }

  function pickSize(value) {
    const size = Math.round(Number(value));
    return SIZES.indexOf(size) >= 0 ? size : 1080;
  }

  function collectConfig() {
    const quality = Number(ui.quality.value);
    return {
      size: pickSize(ui.size.value),
      gap: numberValue(ui.gap, 0, 200, 8),
      margin: numberValue(ui.margin, 0, 200, 0),
      radius: numberValue(ui.radius, 0, 200, 0),
      background: (ui.background.value || '#ffffff').toLowerCase(),
      format: ui.format.value || 'jpeg',
      quality: Number.isFinite(quality) ? clamp(quality, 0.5, 1) : 0.92
    };
  }

  // ---------------------------------------------------------------
  // 几何：预览和导出共用
  // ---------------------------------------------------------------
  function geometry(config) {
    const size = config.size;
    // 边距最多占一半，避免把可用区域挤成负数
    const margin = Math.min(config.margin, Math.max(0, Math.floor((size - 2) / 2)));
    const avail = size - margin * 2;
    const cell = (avail - config.gap * (COLS - 1)) / COLS;
    return {
      size,
      margin,
      cell,
      ok: avail > 0 && cell >= 4
    };
  }

  /**
   * 第 (row, col) 格的像素矩形。
   * 边界四舍五入、最后一行/列直接贴齐，避免相邻格子之间出现半像素缝。
   */
  function cellRect(config, geo, row, col) {
    const step = geo.cell + config.gap;
    const x0 = Math.round(geo.margin + col * step);
    const y0 = Math.round(geo.margin + row * step);
    const x1 = col === COLS - 1 ? config.size - geo.margin : Math.round(geo.margin + (col + 1) * step - config.gap);
    const y1 = row === COLS - 1 ? config.size - geo.margin : Math.round(geo.margin + (row + 1) * step - config.gap);
    return { x: x0, y: y0, w: Math.max(0, x1 - x0), h: Math.max(0, y1 - y0) };
  }

  /** cover：等比放大到填满 rect，居中后多余部分自然被裁掉（配合 clip）。 */
  function drawCover(g, source, rect) {
    const k = Math.max(rect.w / source.width, rect.h / source.height);
    const width = source.width * k;
    const height = source.height * k;
    g.drawImage(
      source,
      rect.x + (rect.w - width) / 2,
      rect.y + (rect.h - height) / 2,
      width,
      height
    );
  }

  function compose() {
    const config = collectConfig();
    const geo = geometry(config);

    if (preview.width !== config.size || preview.height !== config.size) {
      preview.width = config.size;
      preview.height = config.size;
      previewSize = config.size;
    }

    const g = preview.getContext('2d');
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.clearRect(0, 0, config.size, config.size);
    g.imageSmoothingEnabled = true;
    g.imageSmoothingQuality = 'high';

    // JPEG 不支持透明，PNG/WebP 也统一铺底色：空格子要有一致的观感
    g.fillStyle = config.background;
    g.fillRect(0, 0, config.size, config.size);

    if (!geo.ok) return { config, geo };

    slots.forEach((slot, index) => {
      if (!slot) return;
      const rect = cellRect(config, geo, Math.floor(index / COLS), index % COLS);
      if (rect.w <= 0 || rect.h <= 0) return;

      g.save();
      g.beginPath();
      roundRectPath(g, rect.x, rect.y, rect.w, rect.h, config.radius);
      g.clip();
      drawCover(g, slot.source.canvas, rect);
      g.restore();
    });

    return { config, geo };
  }

  // ---------------------------------------------------------------
  // 槽位 UI
  // ---------------------------------------------------------------
  function paintSlot(index) {
    const cell = cells[index];
    if (!cell) return;
    const slot = slots[index];
    cell.el.classList.toggle('has', !!slot);
    cell.el.setAttribute(
      'aria-label',
      slot
        ? `第 ${index + 1} 格，${slot.source.name}，点击更换`
        : `第 ${index + 1} 格，点击选择照片`
    );

    const g = cell.thumb.getContext('2d');
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.clearRect(0, 0, THUMB_SIZE, THUMB_SIZE);
    if (slot) {
      g.imageSmoothingEnabled = true;
      g.imageSmoothingQuality = 'high';
      drawCover(g, slot.source.canvas, { x: 0, y: 0, w: THUMB_SIZE, h: THUMB_SIZE });
    }
  }

  function updateInfo() {
    if (ctx.activeTab !== 'collage') return;
    const filled = slots.filter(Boolean).length;
    const size = collectConfig().size;
    ctx.setImageInfo(`${filled}/${SLOT_COUNT} 张 · ${size} × ${size}`);
  }

  /**
   * 重新合成、清掉旧结果、必要时重新适应窗口。
   * clear=false 用于批量读图过程中，避免每张都闪一下结果条。
   */
  function refresh({ clear = true } = {}) {
    const before = previewSize;
    const result = compose();
    if (clear) ctx.clearResult('collage');
    updateInfo();

    if (ctx.activeTab === 'collage') {
      if (previewSize !== before) ctx.stage.fit();
      else ctx.stage.render();
    }
    return result;
  }

  function openPicker(slotIndex) {
    pendingSlot = slotIndex;
    ui.input.value = '';
    ui.input.click();
  }

  function removeSlot(index) {
    if (!slots[index]) return;
    loadSeq += 1;   // 还在读取的旧批次作废，避免刚删掉又被写回来
    loading = false; // 旧批次的 finally 不会再执行，这里手动收尾
    slots[index] = null;
    paintSlot(index);
    refresh();
    ctx.setStatus(`已移除第 ${index + 1} 张`, 'info');
  }

  function clearSlots() {
    if (!slots.some(Boolean)) return;
    if (!window.confirm('清空已选的 9 张照片？')) return;
    loadSeq += 1;
    loading = false;
    slots.fill(null);
    cells.forEach((cell, index) => paintSlot(index));
    refresh();
    ctx.setStatus('已清空照片', 'info');
  }

  /**
   * 把文件填进槽位。
   * startIndex 有值时，第一张替换该格，其余按顺序填空格；否则从第一个空格开始。
   */
  async function addFiles(files, startIndex) {
    const images = Array.from(files || []).filter(isImageFile);
    if (!images.length) {
      ctx.setStatus('这里只接受图片文件', 'err');
      return;
    }
    if (loading) {
      ctx.setStatus('上一批照片还在读取，稍等一下', 'info');
      return;
    }

    const targets = [];
    if (Number.isInteger(startIndex) && startIndex >= 0 && startIndex < SLOT_COUNT) {
      targets.push(startIndex);
    }
    for (let i = 0; i < SLOT_COUNT && targets.length < images.length; i += 1) {
      if (!slots[i] && targets.indexOf(i) < 0) targets.push(i);
    }
    if (!targets.length) {
      ctx.setStatus('9 格已经满了，点某一格可以单独更换', 'err');
      return;
    }

    const seq = ++loadSeq;
    const count = Math.min(images.length, targets.length);
    const dropped = images.length - count;
    loading = true;
    let added = 0;
    let failed = 0;

    try {
      for (let i = 0; i < count; i += 1) {
        if (seq !== loadSeq) return;
        ctx.setStatus(`正在读取 ${i + 1}/${count}…`, 'info');
        try {
          const source = await loadSource(images[i], { maxPixels: PHOTO_MAX_PIXELS });
          if (seq !== loadSeq) return;
          const index = targets[i];
          slots[index] = { source, name: images[i].name || '照片' };
          paintSlot(index);
          refresh({ clear: false });
          added += 1;
        } catch (error) {
          failed += 1;
        }
      }

      if (seq !== loadSeq) return;
      refresh();
      const parts = [`已添加 ${added} 张照片`];
      if (failed) parts.push(`${failed} 张读取失败`);
      if (dropped > 0) parts.push(`最多 ${SLOT_COUNT} 张，多出的已忽略`);
      ctx.setStatus(parts.join('，'), failed ? 'err' : 'info');
    } finally {
      if (seq === loadSeq) loading = false;
    }
  }

  // ---------------------------------------------------------------
  // 导出
  // ---------------------------------------------------------------
  async function run() {
    if (loading) {
      ctx.setStatus('照片还在读取，稍等一下', 'info');
      return;
    }
    const filled = slots.filter(Boolean).length;
    if (!filled) {
      ctx.setStatus('先选至少一张照片', 'err');
      return;
    }

    const config = collectConfig();
    refresh({ clear: false });

    const restore = ctx.setBusy(ui.run, '生成中…');
    const token = ctx.newToken();
    try {
      const blob = await toBlob(
        preview,
        mimeOf(config.format),
        config.format === 'png' ? undefined : config.quality
      );
      if (ctx.isStale(token)) return;

      const filename = `九宫格拼图-${config.size}.${extOf(config.format)}`;
      const text = `拼图完成：${filled} 张 · ${config.size} × ${config.size} · ${humanSize(blob.size)}`;
      ctx.deliver(blob, filename, text, 'collage');
    } catch (error) {
      if (!ctx.isStale(token)) ctx.setStatus(error.message || '导出失败', 'err');
    } finally {
      restore();
    }
  }

  // ---------------------------------------------------------------
  // 覆盖层：给空格画虚线和序号（不参与导出）
  // ---------------------------------------------------------------
  const overlay = {
    render(g, stage) {
      const config = collectConfig();
      const geo = geometry(config);
      if (!geo.ok) return;
      const scale = stage.scale || 1;

      g.save();
      g.lineWidth = 1.5 / scale;
      g.setLineDash([6 / scale, 4 / scale]);
      g.strokeStyle = 'rgba(79, 70, 229, .85)';
      g.fillStyle = 'rgba(79, 70, 229, .55)';
      g.textAlign = 'center';
      g.textBaseline = 'middle';
      g.font = `600 ${14 / scale}px system-ui, -apple-system, "Segoe UI", sans-serif`;

      slots.forEach((slot, index) => {
        if (slot) return;
        const rect = cellRect(config, geo, Math.floor(index / COLS), index % COLS);
        if (rect.w <= 0 || rect.h <= 0) return;
        g.beginPath();
        roundRectPath(g, rect.x, rect.y, rect.w, rect.h, config.radius);
        g.stroke();
        g.fillText(String(index + 1), rect.x + rect.w / 2, rect.y + rect.h / 2);
      });

      g.restore();
    }
  };

  // ---------------------------------------------------------------
  // 事件绑定
  // ---------------------------------------------------------------
  function mount() {
    for (let i = 0; i < SLOT_COUNT; i += 1) {
      const el = document.createElement('div');
      el.className = 'slot';
      el.dataset.slot = String(i);
      el.setAttribute('role', 'button');
      el.tabIndex = 0;

      const thumb = document.createElement('canvas');
      thumb.className = 'slot-thumb';
      thumb.width = THUMB_SIZE;
      thumb.height = THUMB_SIZE;

      const num = document.createElement('span');
      num.className = 'slot-num';
      num.textContent = String(i + 1);

      const del = document.createElement('button');
      del.type = 'button';
      del.className = 'slot-del';
      del.textContent = '×';
      del.setAttribute('aria-label', `移除第 ${i + 1} 张照片`);
      del.addEventListener('click', (event) => {
        event.stopPropagation();
        removeSlot(i);
      });

      const plus = document.createElement('span');
      plus.className = 'slot-plus';
      plus.textContent = '＋';
      plus.setAttribute('aria-hidden', 'true');

      el.appendChild(thumb);
      el.appendChild(num);
      el.appendChild(del);
      el.appendChild(plus);
      el.addEventListener('click', () => openPicker(i));
      el.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          openPicker(i);
        }
      });

      ui.slots.appendChild(el);
      cells.push({ el, thumb });
      paintSlot(i);   // 每张 canvas 开头都是空的
    }

    ui.input.addEventListener('change', () => {
      const files = Array.from(ui.input.files || []);
      const target = pendingSlot;
      pendingSlot = null;
      ui.input.value = '';
      if (files.length) addFiles(files, target);
    });

    ui.add.addEventListener('click', () => {
      pendingSlot = null;
      ui.input.value = '';
      ui.input.click();
    });
    ui.clear.addEventListener('click', clearSlots);
    ui.run.addEventListener('click', run);

    ['dragenter', 'dragover'].forEach((type) => {
      ui.slots.addEventListener(type, (event) => {
        event.preventDefault();
        if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
        ui.slots.classList.add('drag');
      });
    });
    ui.slots.addEventListener('dragleave', (event) => {
      if (!event.relatedTarget || !ui.slots.contains(event.relatedTarget)) {
        ui.slots.classList.remove('drag');
      }
    });
    ui.slots.addEventListener('drop', (event) => {
      event.preventDefault();
      ui.slots.classList.remove('drag');
      const files = event.dataTransfer ? event.dataTransfer.files : null;
      if (files && files.length) addFiles(files);
      else ctx.setStatus('拖进来的不是图片', 'err');
    });

    // input + change 都听：不同浏览器/输入方式（键盘、上下箭头、取色器）
    // 触发的事件不完全一样，漏一个就会出现「参数改了预览没动」。
    [ui.size, ui.format].forEach((select) => {
      select.addEventListener('input', () => refresh());
      select.addEventListener('change', () => refresh());
    });
    [ui.gap, ui.margin, ui.radius, ui.quality, ui.background].forEach((input) => {
      input.addEventListener('input', () => refresh());
      input.addEventListener('change', () => refresh());
    });

    compose();   // 先把默认底色的预览准备好
  }

  function applyPreset(config) {
    if (!config) return;
    ui.size.value = String(pickSize(config.size));
    if (Number.isFinite(Number(config.gap))) {
      ui.gap.value = clamp(Math.round(Number(config.gap)), 0, 200);
    }
    if (Number.isFinite(Number(config.margin))) {
      ui.margin.value = clamp(Math.round(Number(config.margin)), 0, 200);
    }
    if (Number.isFinite(Number(config.radius))) {
      ui.radius.value = clamp(Math.round(Number(config.radius)), 0, 200);
    }
    if (/^#[0-9a-fA-F]{6}$/.test(String(config.background || ''))) {
      ui.background.value = config.background;
    }
    if (['jpeg', 'webp', 'png'].indexOf(config.format) >= 0) ui.format.value = config.format;
    if (Number.isFinite(Number(config.quality))) {
      ui.quality.value = clamp(Number(config.quality), 0.5, 1).toFixed(2);
    }
    refresh({ clear: false });
  }

  return {
    mount,
    activate() {
      // 先把舞台换成拼图预览，再按最新参数合成
      if (ctx.stage.image !== preview) ctx.stage.setImage(preview);
      ctx.stage.setOverlay(overlay);
      refresh({ clear: false });
      updateInfo();
    },
    deactivate() {
      // 离开页签时清掉舞台，避免没选原图时切到压缩页还看到拼图
      ctx.stage.setOverlay(null);
      ctx.stage.setImage(null);
    },
    addFiles,
    pick() {
      pendingSlot = null;
      ui.input.value = '';
      ui.input.click();
    },
    collectConfig,
    applyPreset,
    hasContent() {
      return slots.some(Boolean);
    },
    emptyPresetHint: '方案已应用，选 9 张照片就能用'
  };
}
