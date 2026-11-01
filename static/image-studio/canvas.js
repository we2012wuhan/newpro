/*
 * 图片工坊 · Canvas 公共层
 * ========================
 * 这一层不关心「压缩 / 裁剪 / 切图」具体要做什么，
 * 只负责三件所有页签都会用到的事：
 *
 *   1. 把用户选的文件变成一张可以用 drawImage 画的图（顺便处理 EXIF 方向和超大图）；
 *   2. 管好舞台：显示尺寸、设备像素比、缩放、平移、鼠标/触摸；
 *   3. 把一张 canvas 导出成 Blob 并触发下载。
 *
 * 读这个文件时可以先记住一个区别：
 *   - 显示用的 canvas 尺寸 = CSS 尺寸 × devicePixelRatio，所以高分屏不糊；
 *   - 导出用的 canvas 尺寸 = 图片真实像素，和屏幕无关。
 *   两者混在一起，导出结果就会莫名其妙变成两倍大或一半大。
 */

export const MAX_FILE_BYTES = 50 * 1024 * 1024;      // 单个文件上限 50MB
export const MAX_SOURCE_PIXELS = 40 * 1000 * 1000;  // 源图最多 4000 万像素
export const MAX_EXPORT_PIXELS = 16 * 1000 * 1000;  // 导出画布最多 1600 万像素（照顾 iOS）

const MIME = {
  jpeg: 'image/jpeg',
  webp: 'image/webp',
  png: 'image/png'
};

export function clamp(value, low, high) {
  return Math.min(high, Math.max(low, value));
}

export function mimeOf(format) {
  return MIME[format] || MIME.jpeg;
}

export function extOf(format) {
  return format === 'jpeg' ? 'jpg' : (format || 'png');
}

/** 人类可读的体积：小文件用 B，常规图片用 KB，大图用 MB。 */
export function humanSize(bytes) {
  const n = Number(bytes) || 0;
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
  return (n / 1024 / 1024).toFixed(2) + ' MB';
}

/** 去掉扩展名并清理文件名里的非法字符，中文保留。 */
export function baseName(name, fallback = '图片') {
  let text = String(name || '').replace(/\.[^./\\]+$/, '');
  // 去掉 Windows 文件名非法字符，中文和空格保留
  text = text.replace(/[\\/:*?"<>|]/g, '').trim();
  return (text || fallback).slice(0, 60);
}

/** 只缩不放：最长边超过 maxEdge 时等比缩小。 */
export function fitDimensions(width, height, maxEdge) {
  const w = Math.max(1, Math.round(width));
  const h = Math.max(1, Math.round(height));
  if (!maxEdge || maxEdge <= 0) return { width: w, height: h };
  const k = Math.min(1, maxEdge / Math.max(w, h));
  return {
    width: Math.max(1, Math.round(w * k)),
    height: Math.max(1, Math.round(h * k))
  };
}

/**
 * canvas.toBlob 是回调式的，这里包成 Promise。
 * 返回 null 通常意味着画布太大或格式不支持 —— 必须显式报错，
 * 不能悄悄返回一个空文件。
 */
export function toBlob(canvas, type, quality) {
  return new Promise((resolve, reject) => {
    try {
      canvas.toBlob((blob) => {
        if (!blob) {
          reject(new Error('导出失败：图片可能太大，或浏览器不支持这种格式'));
          return;
        }
        // 浏览器不支持目标格式时会回退成 PNG，这里明确拒绝，免得用户拿到错误的后缀
        if (type && blob.type && blob.type !== type) {
          reject(new Error('当前浏览器不支持导出为 ' + type.split('/')[1].toUpperCase()));
          return;
        }
        resolve(blob);
      }, type, quality);
    } catch (error) {
      reject(new Error('导出失败：' + (error.message || error)));
    }
  });
}

/** 用 <a download> 触发下载；URL 用完要撤销，否则一直占内存。 */
export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename || 'image';
  a.rel = 'noopener';
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

/**
 * 把 File 解码成一张离屏 canvas。
 *
 * 为什么要先画到 canvas 上，而不是直接留着 ImageBitmap？
 *   - 超大图可以在这里统一缩到安全尺寸，后面每个页签都不用再防；
 *   - canvas 能在所有页签之间反复 drawImage，生命周期更好管。
 *
 * EXIF 方向：手机照片常常把「怎么转」写在 EXIF 里，而不是真的转像素。
 * createImageBitmap 的 imageOrientation:'from-image' 会让浏览器先摆正；
 * 老浏览器不支持时退回 <img>，现代浏览器解码 <img> 时也会自动应用 EXIF。
 *
 * maxPixels 默认 4000 万；拼图一次要留 9 张，可以传更小的值控制内存。
 */
export async function loadSource(file, { maxPixels = MAX_SOURCE_PIXELS } = {}) {
  if (!file) throw new Error('没有选择图片');
  const looksImage = (file.type && file.type.indexOf('image/') === 0) ||
    /\.(png|jpe?g|webp|gif|bmp|avif|heic|heif)$/i.test(file.name || '');
  if (!looksImage) throw new Error('这不是图片文件');
  if (file.size > MAX_FILE_BYTES) {
    throw new Error('图片超过 50MB，先压缩一下再试');
  }

  let bitmap = null;
  let img = null;
  let url = '';

  try {
    if (typeof createImageBitmap === 'function') {
      try {
        bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
      } catch (error) {
        bitmap = null;   // Safari 老版本等：退回 <img>
      }
    }

    if (!bitmap) {
      url = URL.createObjectURL(file);
      img = await loadImageElement(url);
    }

    const rawWidth = bitmap ? bitmap.width : img.naturalWidth;
    const rawHeight = bitmap ? bitmap.height : img.naturalHeight;
    if (!rawWidth || !rawHeight) throw new Error('读不到图片尺寸');

    // 源图太大时先等比缩到上限以内，避免后面每步都卡
    const budget = Math.max(1, Number(maxPixels) || MAX_SOURCE_PIXELS);
    const ratio = Math.min(1, Math.sqrt(budget / (rawWidth * rawHeight)));
    const width = Math.max(1, Math.round(rawWidth * ratio));
    const height = Math.max(1, Math.round(rawHeight * ratio));

    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext('2d');
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(bitmap || img, 0, 0, width, height);

    return {
      canvas,
      width,
      height,
      originalWidth: rawWidth,
      originalHeight: rawHeight,
      bytes: file.size,
      name: file.name || '图片',
      file   // 压缩时可以判断「原图本来就不超标」，直接原样给回用户
    };
  } catch (error) {
    throw new Error('这张图片无法读取：' + (error.message || '格式可能不受支持'));
  } finally {
    if (bitmap) bitmap.close();
    if (url) URL.revokeObjectURL(url);
  }
}

function loadImageElement(url) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error('浏览器解不开这个格式'));
    img.src = url;
  });
}

/** 复制一张 canvas。旋转/翻转都从副本开始，保证原图不被改坏。 */
export function cloneCanvas(source) {
  const out = document.createElement('canvas');
  out.width = source.width;
  out.height = source.height;
  out.getContext('2d').drawImage(source, 0, 0);
  return out;
}

/**
 * 顺时针旋转 90 / 180 / 270 度。
 * 90 和 270 度时宽高互换，所以新画布要交换 width/height。
 */
export function rotateCanvas(source, degrees) {
  const deg = ((Number(degrees) || 0) % 360 + 360) % 360;
  if (deg === 0) return cloneCanvas(source);

  const swap = deg === 90 || deg === 270;
  const out = document.createElement('canvas');
  out.width = swap ? source.height : source.width;
  out.height = swap ? source.width : source.height;

  const ctx = out.getContext('2d');
  ctx.translate(out.width / 2, out.height / 2);
  ctx.rotate(deg * Math.PI / 180);
  ctx.drawImage(source, -source.width / 2, -source.height / 2);
  return out;
}

/** axis='x' 水平翻转，axis='y' 垂直翻转。 */
export function flipCanvas(source, axis) {
  const out = document.createElement('canvas');
  out.width = source.width;
  out.height = source.height;
  const ctx = out.getContext('2d');
  ctx.translate(axis === 'x' ? out.width : 0, axis === 'y' ? out.height : 0);
  ctx.scale(axis === 'x' ? -1 : 1, axis === 'y' ? -1 : 1);
  ctx.drawImage(source, 0, 0);
  return out;
}

/** 圆角路径；老浏览器没有 roundRect 时手写一段。 */
export function roundRectPath(ctx, x, y, width, height, radius) {
  const r = Math.max(0, Math.min(radius, width / 2, height / 2));
  if (!r) {
    ctx.rect(x, y, width, height);
    return;
  }
  if (typeof ctx.roundRect === 'function') {
    ctx.roundRect(x, y, width, height, r);
    return;
  }
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + width - r, y);
  ctx.quadraticCurveTo(x + width, y, x + width, y + r);
  ctx.lineTo(x + width, y + height - r);
  ctx.quadraticCurveTo(x + width, y + height, x + width - r, y + height);
  ctx.lineTo(x + r, y + height);
  ctx.quadraticCurveTo(x, y + height, x, y + height - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

/**
 * 舞台控制器
 * ----------
 * 一个 <canvas> 负责显示原图 + 当前页签的覆盖层（裁剪框、网格线）。
 * 状态只有三个数：scale（缩放）、ox/oy（图片左上角在屏幕上的位置）。
 * 所有坐标换算都围绕这三个数：
 *
 *   屏幕坐标 = 图片坐标 × scale + offset
 *   图片坐标 = (屏幕坐标 - offset) / scale
 *
 * 覆盖层通过 overlay 对象注册：
 *   overlay.render(ctx, stage)  —— 在图片坐标系里画
 *   overlay.down/move/up(point) —— 返回 true 表示「这次拖拽归我」，舞台就不再平移
 */
export function createStage(canvas, wrap) {
  const ctx = canvas.getContext('2d');
  const abort = new AbortController();
  const signal = abort.signal;

  let image = null;
  let overlay = null;
  let scale = 1;
  let ox = 0;
  let oy = 0;
  let viewWidth = 1;
  let viewHeight = 1;
  let dpr = 1;
  let lastFitScale = 1;
  let raf = 0;
  let onChange = null;   // 缩放变化时通知页面更新百分比

  const pointers = new Map();
  let drag = null;      // {type:'pan'|'overlay', pointerId, ...}
  let pinch = null;

  function measure() {
    const rect = wrap.getBoundingClientRect();
    viewWidth = Math.max(1, rect.width);
    viewHeight = Math.max(1, rect.height);
    dpr = Math.min(window.devicePixelRatio || 1, 2);

    const backW = Math.round(viewWidth * dpr);
    const backH = Math.round(viewHeight * dpr);
    if (canvas.width !== backW || canvas.height !== backH) {
      canvas.width = backW;     // 改 width/height 会清空画布，所以下面必须重画
      canvas.height = backH;
    }
    canvas.style.width = viewWidth + 'px';
    canvas.style.height = viewHeight + 'px';
  }

  function schedule() {
    if (raf) return;
    raf = requestAnimationFrame(() => {
      raf = 0;
      render();
    });
  }

  function render() {
    measure();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // 先按 DPR 缩放，之后所有绘制都用 CSS 像素思考
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    if (!image) return;

    ctx.save();
    ctx.translate(ox, oy);
    ctx.scale(scale, scale);
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(image, 0, 0);
    if (overlay && typeof overlay.render === 'function') {
      overlay.render(ctx, api);
    }
    ctx.restore();

    if (onChange) onChange(scale);
  }

  function fit() {
    if (!image) {
      scale = 1; ox = 0; oy = 0;
      render();
      return;
    }
    const pad = 24;
    const sx = (viewWidth - pad * 2) / image.width;
    const sy = (viewHeight - pad * 2) / image.height;
    scale = clamp(Math.min(sx, sy), 0.02, 8);
    ox = (viewWidth - image.width * scale) / 2;
    oy = (viewHeight - image.height * scale) / 2;
    lastFitScale = scale;
    render();
  }

  function zoomAt(factor, cx, cy) {
    if (!image) return;
    const next = clamp(scale * factor, 0.02, 20);
    const k = next / scale;
    ox = cx - (cx - ox) * k;
    oy = cy - (cy - oy) * k;
    scale = next;
    schedule();
  }

  function pointOf(event) {
    const rect = canvas.getBoundingClientRect();
    const cx = event.clientX - rect.left;
    const cy = event.clientY - rect.top;
    return {
      cx,
      cy,
      x: (cx - ox) / scale,
      y: (cy - oy) / scale,
      scale,
      event
    };
  }

  // ---------------- 指针：单指拖拽 / 双指捏合 ----------------
  canvas.addEventListener('pointerdown', (event) => {
    if (!image) return;
    event.preventDefault();
    try { canvas.setPointerCapture(event.pointerId); } catch (error) { /* 忽略 */ }
    pointers.set(event.pointerId, pointOf(event));

    if (pointers.size === 1) {
      const point = pointOf(event);
      if (event.button === 0 && overlay && typeof overlay.down === 'function' && overlay.down(point)) {
        drag = { type: 'overlay', pointerId: event.pointerId };
      } else {
        drag = {
          type: 'pan',
          pointerId: event.pointerId,
          startX: point.cx,
          startY: point.cy,
          startOx: ox,
          startOy: oy
        };
      }
    } else if (pointers.size === 2) {
      const [a, b] = Array.from(pointers.values());
      pinch = {
        distance: Math.hypot(a.cx - b.cx, a.cy - b.cy) || 1,
        scale,
        ox,
        oy,
        midX: (a.cx + b.cx) / 2,
        midY: (a.cy + b.cy) / 2
      };
      drag = null;
      if (overlay && typeof overlay.cancel === 'function') overlay.cancel();
    }
  }, { signal });

  canvas.addEventListener('pointermove', (event) => {
    if (!pointers.has(event.pointerId)) return;
    event.preventDefault();
    pointers.set(event.pointerId, pointOf(event));

    if (pinch && pointers.size >= 2) {
      const [a, b] = Array.from(pointers.values());
      const distance = Math.hypot(a.cx - b.cx, a.cy - b.cy) || 1;
      const midX = (a.cx + b.cx) / 2;
      const midY = (a.cy + b.cy) / 2;
      const next = clamp(pinch.scale * (distance / pinch.distance), 0.02, 20);
      const k = next / pinch.scale;
      ox = midX - (pinch.midX - pinch.ox) * k;
      oy = midY - (pinch.midY - pinch.oy) * k;
      scale = next;
      schedule();
      return;
    }

    if (!drag || drag.pointerId !== event.pointerId) return;
    const point = pointOf(event);
    if (drag.type === 'pan') {
      ox = drag.startOx + (point.cx - drag.startX);
      oy = drag.startOy + (point.cy - drag.startY);
      schedule();
    } else if (drag.type === 'overlay' && overlay && typeof overlay.move === 'function') {
      overlay.move(point);
    }
  }, { signal });

  function endPointer(event) {
    pointers.delete(event.pointerId);
    if (pointers.size < 2) pinch = null;
    if (drag && drag.pointerId === event.pointerId) {
      if (drag.type === 'overlay' && overlay && typeof overlay.up === 'function') {
        overlay.up(pointOf(event));
      }
      drag = null;
    }
  }

  canvas.addEventListener('pointerup', endPointer, { signal });
  canvas.addEventListener('pointercancel', endPointer, { signal });
  canvas.addEventListener('lostpointercapture', endPointer, { signal });

  canvas.addEventListener('wheel', (event) => {
    if (!image) return;
    event.preventDefault();
    const point = pointOf(event);
    // deltaY > 0 是向下滚，通常代表缩小
    zoomAt(Math.exp(-event.deltaY * 0.0015), point.cx, point.cy);
  }, { passive: false, signal });

  canvas.addEventListener('dblclick', () => fit(), { signal });

  const observer = new ResizeObserver(() => {
    measure();
    // 用户没手动缩放过，就继续铺满窗口；手动缩放过就保持当前比例
    if (Math.abs(scale - lastFitScale) < 0.0001) fit();
    else render();
  });
  observer.observe(wrap);

  const api = {
    get scale() { return scale; },
    get image() { return image; },
    set onChange(fn) { onChange = typeof fn === 'function' ? fn : null; },
    get viewWidth() { return viewWidth; },
    get viewHeight() { return viewHeight; },
    setImage(next, options = {}) {
      image = next || null;
      if (image && options.fit !== false) fit();
      else render();
    },
    setOverlay(next) {
      overlay = next || null;
      schedule();
    },
    fit,
    zoom(factor) { zoomAt(factor, viewWidth / 2, viewHeight / 2); },
    render: schedule,
    screenToImage(cx, cy) {
      return { x: (cx - ox) / scale, y: (cy - oy) / scale };
    },
    destroy() {
      abort.abort();
      observer.disconnect();
      if (raf) cancelAnimationFrame(raf);
    }
  };

  measure();
  return api;
}
