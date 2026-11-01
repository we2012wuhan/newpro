/*
 * 裁剪页签
 * =========
 * 这里有两个坐标系，先分清楚：
 *
 *   - 「工作图」：已经按旋转/翻转摆正的那张 canvas，裁剪框的坐标都相对它；
 *   - 「屏幕」：stage 缩放、平移后才显示出来的样子。
 *
 * 裁剪框 rect 永远存工作图像素（x, y, w, h）。用户拖鼠标时，
 * 鼠标位置通过 point.x / point.y 换算回图片坐标，再修改 rect。
 *
 * 旋转/翻转不是改 rect 而是重建工作图；重建之前，先把 rect 用同样的
 * 变换搬过去，用户就不会看到框突然跑掉。
 */
import {
  clamp,
  mimeOf,
  extOf,
  toBlob,
  baseName,
  humanSize,
  rotateCanvas,
  flipCanvas,
  roundRectPath,
  MAX_EXPORT_PIXELS
} from './canvas.js';

const MIN_SIZE = 24;   // 选区最短边（图片像素）

export function createCropTab(ctx) {
  const ui = {
    aspect: document.getElementById('cropAspect'),
    rotate: document.getElementById('cropRotate'),
    flipX: document.getElementById('cropFlipX'),
    flipY: document.getElementById('cropFlipY'),
    format: document.getElementById('cropFormat'),
    quality: document.getElementById('cropQuality'),
    edge: document.getElementById('cropEdge'),
    reset: document.getElementById('cropReset'),
    run: document.getElementById('cropRun')
  };

  let work = null;          // 旋转/翻转后的工作图
  let rect = null;          // 裁剪框，工作图坐标
  let lockRatio = 0;        // 0 表示自由比例
  let lastRotate = 0;
  let lastFlipX = false;
  let lastFlipY = false;
  let drag = null;

  // -------------------------------------------------------------
  // 比例与选区
  // -------------------------------------------------------------
  function ratioOf(aspect) {
    if (!aspect || aspect === 'free') return 0;
    const parts = String(aspect).split(':');
    const a = Number(parts[0]);
    const b = Number(parts[1]);
    if (!a || !b) return 0;
    return a / b;
  }

  function fitAspectInImage(ratio) {
    const W = work.width;
    const H = work.height;
    let w = W;
    let h = w / ratio;
    if (h > H) {
      h = H;
      w = h * ratio;
    }
    return { x: (W - w) / 2, y: (H - h) / 2, w, h };
  }

  function resetSelection() {
    if (!work) {
      rect = null;
      return;
    }
    rect = lockRatio
      ? fitAspectInImage(lockRatio)
      : { x: 0, y: 0, w: work.width, h: work.height };
  }

  function keepInside(r) {
    const W = work.width;
    const H = work.height;
    const w = clamp(r.w, Math.min(MIN_SIZE, W), W);
    const h = clamp(r.h, Math.min(MIN_SIZE, H), H);
    return {
      x: clamp(r.x, 0, W - w),
      y: clamp(r.y, 0, H - h),
      w,
      h
    };
  }

  // -------------------------------------------------------------
  // 覆盖层：框外遮罩、三分线、8 个手柄、尺寸角标
  // -------------------------------------------------------------
  const overlay = {
    render(g, stage) {
      if (!work || !rect) return;
      const s = stage.scale || 1;
      const W = work.width;
      const H = work.height;
      const r = rect;

      // 框外压暗：上、下、左、右四块，比 evenodd 路径更好懂
      g.save();
      g.fillStyle = 'rgba(8, 12, 24, 0.55)';
      g.fillRect(0, 0, W, r.y);
      g.fillRect(0, r.y + r.h, W, H - (r.y + r.h));
      g.fillRect(0, r.y, r.x, r.h);
      g.fillRect(r.x + r.w, r.y, W - (r.x + r.w), r.h);

      // 三分线
      g.strokeStyle = 'rgba(255, 255, 255, 0.42)';
      g.lineWidth = 1 / s;
      g.beginPath();
      for (let i = 1; i <= 2; i += 1) {
        const x = r.x + r.w * i / 3;
        const y = r.y + r.h * i / 3;
        g.moveTo(x, r.y);
        g.lineTo(x, r.y + r.h);
        g.moveTo(r.x, y);
        g.lineTo(r.x + r.w, y);
      }
      g.stroke();

      // 边框：先白后紫，深浅背景都看得清
      g.strokeStyle = 'rgba(255, 255, 255, 0.9)';
      g.lineWidth = 3 / s;
      g.strokeRect(r.x, r.y, r.w, r.h);
      g.strokeStyle = '#4f46e5';
      g.lineWidth = 1.5 / s;
      g.strokeRect(r.x, r.y, r.w, r.h);

      // 8 个手柄
      const hs = 10 / s;
      const half = hs / 2;
      const points = [
        [r.x, r.y], [r.x + r.w / 2, r.y], [r.x + r.w, r.y],
        [r.x + r.w, r.y + r.h / 2], [r.x + r.w, r.y + r.h],
        [r.x + r.w / 2, r.y + r.h], [r.x, r.y + r.h], [r.x, r.y + r.h / 2]
      ];
      g.fillStyle = '#ffffff';
      g.strokeStyle = '#4f46e5';
      g.lineWidth = 1.5 / s;
      points.forEach(([px, py]) => {
        g.beginPath();
        g.rect(px - half, py - half, hs, hs);
        g.fill();
        g.stroke();
      });

      // 尺寸角标：优先放在框上方，贴边时挪到框内
      const label = `${Math.round(r.w)} × ${Math.round(r.h)}`;
      const fontSize = 12 / s;
      const pad = 6 / s;
      g.font = `600 ${fontSize}px -apple-system, "PingFang SC", system-ui, sans-serif`;
      const textWidth = g.measureText(label).width;
      const boxW = textWidth + pad * 2;
      const boxH = fontSize * 1.5;
      let lx = r.x + 4 / s;
      let ly = r.y - boxH - 5 / s;
      if (ly < 0) ly = r.y + 5 / s;
      lx = Math.min(lx, W - boxW - 2 / s);
      if (lx < r.x) lx = r.x;
      g.fillStyle = 'rgba(17, 24, 39, 0.84)';
      g.beginPath();
      roundRectPath(g, lx, ly, boxW, boxH, 6 / s);
      g.fill();
      g.fillStyle = '#ffffff';
      g.textBaseline = 'middle';
      g.fillText(label, lx + pad, ly + boxH / 2);
      g.restore();
    },

    down(point) {
      if (!work || !rect) return false;
      // 图片外的按下交给舞台平移；图片内才算操作裁剪框
      if (point.x < 0 || point.y < 0 || point.x > work.width || point.y > work.height) {
        return false;
      }
      const handle = handleAt(point);
      drag = { handle, start: point, startRect: { ...rect } };

      if (handle === 'new') {
        const x = clamp(point.x, 0, work.width);
        const y = clamp(point.y, 0, work.height);
        drag.anchor = { x, y };
        rect = { x, y, w: 0, h: 0 };
        ctx.stage.render();
      }
      return true;
    },

    move(point) {
      if (!drag || !work) return;
      const startRect = drag.startRect;

      if (drag.handle === 'new') {
        rect = fromAnchor(drag.anchor, point, lockRatio);
      } else if (drag.handle === 'move') {
        rect = keepInside({
          x: startRect.x + (point.x - drag.start.x),
          y: startRect.y + (point.y - drag.start.y),
          w: startRect.w,
          h: startRect.h
        });
      } else if (lockRatio) {
        rect = resizeLocked(drag.handle, point, startRect, lockRatio);
      } else {
        rect = resizeFree(drag.handle, point, startRect);
      }
      ctx.stage.render();
    },

    up() {
      if (!drag) return;
      if (drag.handle === 'new' && (rect.w < 4 || rect.h < 4)) {
        rect = drag.startRect;   // 只是点了一下，没有真的拖出框
      }
      if (rect) rect = keepInside(rect);
      drag = null;
      ctx.stage.render();
    },

    cancel() {
      drag = null;
    }
  };

  function handleAt(p) {
    const pad = 11 / (p.scale || 1);
    const r = rect;
    const nearLeft = Math.abs(p.x - r.x) <= pad;
    const nearRight = Math.abs(p.x - (r.x + r.w)) <= pad;
    const nearTop = Math.abs(p.y - r.y) <= pad;
    const nearBottom = Math.abs(p.y - (r.y + r.h)) <= pad;
    const inY = p.y >= r.y - pad && p.y <= r.y + r.h + pad;
    const inX = p.x >= r.x - pad && p.x <= r.x + r.w + pad;

    if (inY && nearLeft) return nearTop ? 'nw' : (nearBottom ? 'sw' : 'w');
    if (inY && nearRight) return nearTop ? 'ne' : (nearBottom ? 'se' : 'e');
    if (inX && nearTop) return 'n';
    if (inX && nearBottom) return 's';
    if (p.x > r.x && p.x < r.x + r.w && p.y > r.y && p.y < r.y + r.h) return 'move';
    return 'new';
  }

  function fromAnchor(anchor, point, ratio) {
    const W = work.width;
    const H = work.height;
    const px = clamp(point.x, 0, W);
    const py = clamp(point.y, 0, H);
    const dx = px - anchor.x;
    const dy = py - anchor.y;
    let w = Math.abs(dx);
    let h = Math.abs(dy);

    if (ratio) {
      if (w / Math.max(h, 0.001) > ratio) h = w / ratio;
      else w = h * ratio;
    }

    const dirX = dx < 0 ? -1 : 1;
    const dirY = dy < 0 ? -1 : 1;
    const maxW = dirX > 0 ? W - anchor.x : anchor.x;
    const maxH = dirY > 0 ? H - anchor.y : anchor.y;
    const k = Math.min(1, maxW / Math.max(w, 0.001), maxH / Math.max(h, 0.001));
    w *= k;
    h *= k;

    return {
      x: dirX > 0 ? anchor.x : anchor.x - w,
      y: dirY > 0 ? anchor.y : anchor.y - h,
      w,
      h
    };
  }

  function resizeFree(handle, point, startRect) {
    const W = work.width;
    const H = work.height;
    const dx = point.x - drag.start.x;
    const dy = point.y - drag.start.y;
    let left = startRect.x;
    let top = startRect.y;
    let right = startRect.x + startRect.w;
    let bottom = startRect.y + startRect.h;

    if (handle.includes('w')) left = clamp(startRect.x + dx, 0, right - MIN_SIZE);
    if (handle.includes('e')) right = clamp(startRect.x + startRect.w + dx, left + MIN_SIZE, W);
    if (handle.includes('n')) top = clamp(startRect.y + dy, 0, bottom - MIN_SIZE);
    if (handle.includes('s')) bottom = clamp(startRect.y + startRect.h + dy, top + MIN_SIZE, H);

    return { x: left, y: top, w: right - left, h: bottom - top };
  }

  function resizeLocked(handle, point, startRect, ratio) {
    const W = work.width;
    const H = work.height;

    if (handle === 'n' || handle === 's') {
      const anchorY = handle === 's' ? startRect.y : startRect.y + startRect.h;
      let h = Math.max(MIN_SIZE, Math.abs(point.y - anchorY));
      let w = h * ratio;
      const maxH = handle === 's' ? H - startRect.y : startRect.y + startRect.h;
      if (h > maxH) { h = maxH; w = h * ratio; }
      if (w > W) { w = W; h = w / ratio; }
      const cx = startRect.x + startRect.w / 2;
      return {
        x: clamp(cx - w / 2, 0, W - w),
        y: handle === 's' ? startRect.y : startRect.y + startRect.h - h,
        w,
        h
      };
    }

    if (handle === 'e' || handle === 'w') {
      const anchorX = handle === 'e' ? startRect.x : startRect.x + startRect.w;
      let w = Math.max(MIN_SIZE, Math.abs(point.x - anchorX));
      let h = w / ratio;
      const maxW = handle === 'e' ? W - startRect.x : startRect.x + startRect.w;
      if (w > maxW) { w = maxW; h = w / ratio; }
      if (h > H) { h = H; w = h * ratio; }
      const cy = startRect.y + startRect.h / 2;
      return {
        x: handle === 'e' ? startRect.x : startRect.x + startRect.w - w,
        y: clamp(cy - h / 2, 0, H - h),
        w,
        h
      };
    }

    // 四个角：对角固定，按鼠标到对角的距离定大小
    const anchorX = handle.includes('w') ? startRect.x + startRect.w : startRect.x;
    const anchorY = handle.includes('n') ? startRect.y + startRect.h : startRect.y;
    let w = Math.abs(point.x - anchorX);
    let h = Math.abs(point.y - anchorY);
    if (w / Math.max(h, 0.001) > ratio) h = w / ratio;
    else w = h * ratio;
    if (w < MIN_SIZE) { w = MIN_SIZE; h = w / ratio; }
    if (h < MIN_SIZE) { h = MIN_SIZE; w = h * ratio; }

    const maxW = handle.includes('w') ? anchorX : W - anchorX;
    const maxH = handle.includes('n') ? anchorY : H - anchorY;
    const k = Math.min(1, maxW / w, maxH / h);
    w *= k;
    h *= k;

    return {
      x: handle.includes('w') ? anchorX - w : anchorX,
      y: handle.includes('n') ? anchorY - h : anchorY,
      w,
      h
    };
  }

  // -------------------------------------------------------------
  // 旋转 / 翻转：先搬 rect，再重建工作图
  // -------------------------------------------------------------
  function rotateRect(delta) {
    if (!rect) return;
    const W = work.width;
    const H = work.height;
    const r = rect;
    if (delta === 90) {
      rect = { x: H - (r.y + r.h), y: r.x, w: r.h, h: r.w };
    } else if (delta === 180) {
      rect = { x: W - (r.x + r.w), y: H - (r.y + r.h), w: r.w, h: r.h };
    } else if (delta === 270) {
      rect = { x: r.y, y: W - (r.x + r.w), w: r.h, h: r.w };
    }
  }

  function rebuildAfterTransform() {
    if (!work) return;
    ctx.showImage(work);
    ctx.stage.setOverlay(overlay);
    ctx.stage.render();
  }

  function onRotateChange() {
    const next = Number(ui.rotate.value) || 0;
    if (!work) {
      lastRotate = next;
      return;
    }
    const delta = ((next - lastRotate) % 360 + 360) % 360;
    if (delta) {
      rotateRect(delta);
      work = rotateCanvas(work, delta);
      // 旋转会把矩形的宽高互换；锁了比例就重新铺一个符合比例的框
      rect = lockRatio ? fitAspectInImage(lockRatio) : keepInside(rect);
      rebuildAfterTransform();
    }
    lastRotate = next;
  }

  function onFlipChange(axis) {
    const checked = axis === 'x' ? ui.flipX.checked : ui.flipY.checked;
    const previous = axis === 'x' ? lastFlipX : lastFlipY;
    if (checked === previous) return;

    if (work && rect) {
      if (axis === 'x') rect.x = work.width - (rect.x + rect.w);
      else rect.y = work.height - (rect.y + rect.h);
      work = flipCanvas(work, axis);
      rect = keepInside(rect);
      rebuildAfterTransform();
    }

    if (axis === 'x') lastFlipX = checked;
    else lastFlipY = checked;
  }

  function onAspectChange() {
    lockRatio = ratioOf(ui.aspect.value);
    if (!work) return;
    if (lockRatio) rect = fitAspectInImage(lockRatio);
    else rect = keepInside(rect);
    ctx.stage.render();
  }

  // -------------------------------------------------------------
  // 参数 / 导出
  // -------------------------------------------------------------
  function qualityValue() {
    const value = Number(ui.quality.value);
    return Number.isFinite(value) ? clamp(value, 0.5, 1) : 0.92;
  }

  function edgeValue() {
    const value = Number(ui.edge.value);
    return Number.isFinite(value) ? clamp(Math.round(value), 0, 12000) : 0;
  }

  function collectConfig() {
    return {
      aspect: ui.aspect.value || 'free',
      rotate: Number(ui.rotate.value) || 0,
      flipX: ui.flipX.checked,
      flipY: ui.flipY.checked,
      format: ui.format.value || 'jpeg',
      quality: qualityValue(),
      maxEdge: edgeValue()
    };
  }

  /**
   * 按当前的旋转/翻转控件，从原图重建工作图。
   * 用「绝对状态」重建而不是叠加增量，方案套用到新图片时才不会算错。
   */
  function buildWorkFromSource() {
    const rotate = Number(ui.rotate.value) || 0;
    const flipX = ui.flipX.checked;
    const flipY = ui.flipY.checked;
    lastRotate = rotate;
    lastFlipX = flipX;
    lastFlipY = flipY;

    if (!ctx.source) {
      work = null;
      rect = null;
      return;
    }

    let canvas = ctx.source.canvas;
    if (rotate) canvas = rotateCanvas(canvas, rotate);
    if (flipX) canvas = flipCanvas(canvas, 'x');
    if (flipY) canvas = flipCanvas(canvas, 'y');
    work = canvas;
    rect = lockRatio
      ? fitAspectInImage(lockRatio)
      : { x: 0, y: 0, w: work.width, h: work.height };
  }

  function applyPreset(config) {
    if (!config) return;

    if (['free', '1:1', '4:3', '3:4', '16:9', '9:16'].includes(config.aspect)) {
      ui.aspect.value = config.aspect;
    }
    if ([0, 90, 180, 270].includes(Number(config.rotate))) {
      ui.rotate.value = String(Number(config.rotate));
    }
    ui.flipX.checked = config.flipX === true;
    ui.flipY.checked = config.flipY === true;
    if (['jpeg', 'webp', 'png'].includes(config.format)) ui.format.value = config.format;
    if (Number.isFinite(Number(config.quality))) {
      ui.quality.value = clamp(Number(config.quality), 0.5, 1).toFixed(2);
    }
    if (Number.isFinite(Number(config.maxEdge))) {
      ui.edge.value = clamp(Math.round(Number(config.maxEdge)), 0, 12000);
    }

    lockRatio = ratioOf(ui.aspect.value);
    // 方案里没有裁剪框：从原图重新做绝对变换，再给一个默认选区
    buildWorkFromSource();

    if (ctx.activeTab === 'crop') {
      if (work) {
        ctx.showImage(work);
        ctx.stage.setOverlay(overlay);
        ctx.stage.render();
      } else {
        ctx.stage.setOverlay(null);
      }
    }
  }

  async function run() {
    const source = ctx.source;
    if (!source || !work || !rect) {
      ctx.setStatus('先选一张图片，再拖出裁剪框', 'err');
      return;
    }

    const config = collectConfig();
    const format = config.format;
    const restore = ctx.setBusy(ui.run, '导出中…');
    const token = ctx.newToken();

    try {
      const sx = clamp(rect.x, 0, work.width);
      const sy = clamp(rect.y, 0, work.height);
      const sw = clamp(rect.w, 1, work.width - sx);
      const sh = clamp(rect.h, 1, work.height - sy);

      let outW = Math.max(1, Math.round(sw));
      let outH = Math.max(1, Math.round(sh));

      if (config.maxEdge > 0) {
        const k = Math.min(1, config.maxEdge / Math.max(outW, outH));
        outW = Math.max(1, Math.round(outW * k));
        outH = Math.max(1, Math.round(outH * k));
      }

      const cap = Math.min(1, Math.sqrt(MAX_EXPORT_PIXELS / (outW * outH)));
      outW = Math.max(1, Math.round(outW * cap));
      outH = Math.max(1, Math.round(outH * cap));

      const canvas = document.createElement('canvas');
      canvas.width = outW;
      canvas.height = outH;
      const g = canvas.getContext('2d');
      g.imageSmoothingEnabled = true;
      g.imageSmoothingQuality = 'high';
      if (format === 'jpeg') {
        g.fillStyle = '#ffffff';
        g.fillRect(0, 0, outW, outH);
      }
      g.drawImage(work, sx, sy, sw, sh, 0, 0, outW, outH);

      const mime = mimeOf(format);
      const blob = await toBlob(canvas, mime, format === 'png' ? undefined : config.quality);
      if (ctx.isStale(token)) return;

      const filename = `${baseName(source.name)}-裁剪.${extOf(format)}`;
      ctx.deliver(blob, filename,
        `裁剪完成：${outW} × ${outH} · ${humanSize(blob.size)}`, 'crop');
      ctx.setStatus(`已裁剪 ${outW} × ${outH}`, 'info');
    } catch (error) {
      if (!ctx.isStale(token)) ctx.setStatus(error.message || '裁剪失败', 'err');
    } finally {
      restore();
    }
  }

  // -------------------------------------------------------------
  // 事件绑定与生命周期
  // -------------------------------------------------------------
  ui.aspect.addEventListener('change', onAspectChange);
  ui.rotate.addEventListener('change', onRotateChange);
  ui.flipX.addEventListener('change', () => onFlipChange('x'));
  ui.flipY.addEventListener('change', () => onFlipChange('y'));
  ui.reset.addEventListener('click', () => {
    if (!work) return;
    resetSelection();
    ctx.stage.render();
  });
  ui.run.addEventListener('click', run);

  return {
    mount() {},
    activate() {
      if (work) {
        ctx.showImage(work);
        ctx.stage.setOverlay(overlay);
        ctx.stage.render();
      } else {
        ctx.stage.setOverlay(null);
      }
    },
    deactivate() {
      drag = null;
      ctx.stage.setOverlay(null);
    },
    onSource() {
      // 换图时沿用当前旋转/翻转设置：用户可能刚套用了「横转竖」方案
      lockRatio = ratioOf(ui.aspect.value);
      buildWorkFromSource();
    },
    collectConfig,
    applyPreset
  };
}
