/*
 * 宫格切图页签
 * =============
 * 把一张图按「外边距 + 单元格 + 间距」切开，每张单独编码，
 * 最后用自己写的 store-only ZIP 打包下载。
 *
 * 几何关系（单位都是原图像素）：
 *
 *   left = margin
 *   cellW = (W - margin×2 - gap×(cols-1)) / cols
 *   第 c 格的左边界 = margin + c × (cellW + gap)
 *
 * 预览和导出共用同一套公式 —— 如果预览画一条线、导出用另一套算，
 * 用户就会拿到对不齐的九宫格。
 */
import {
  clamp,
  mimeOf,
  extOf,
  toBlob,
  baseName,
  humanSize,
  roundRectPath,
  MAX_EXPORT_PIXELS
} from './canvas.js';
import { createStoreZip } from './zip.js';

export function createGridTab(ctx) {
  const ui = {
    rows: document.getElementById('gridRows'),
    cols: document.getElementById('gridCols'),
    gap: document.getElementById('gridGap'),
    margin: document.getElementById('gridMargin'),
    radius: document.getElementById('gridRadius'),
    square: document.getElementById('gridSquare'),
    background: document.getElementById('gridBg'),
    format: document.getElementById('gridFormat'),
    quality: document.getElementById('gridQuality'),
    run: document.getElementById('gridRun')
  };

  function numberValue(input, low, high, fallback) {
    const value = Number(input.value);
    if (!Number.isFinite(value)) return fallback;
    return clamp(Math.round(value), low, high);
  }

  function qualityValue() {
    const value = Number(ui.quality.value);
    return Number.isFinite(value) ? clamp(value, 0.5, 1) : 0.92;
  }

  function collectConfig() {
    return {
      rows: numberValue(ui.rows, 1, 12, 3),
      cols: numberValue(ui.cols, 1, 12, 3),
      gap: numberValue(ui.gap, 0, 200, 4),
      margin: numberValue(ui.margin, 0, 200, 0),
      radius: numberValue(ui.radius, 0, 200, 0),
      squareCrop: ui.square.checked,
      background: (ui.background.value || '#ffffff').toLowerCase(),
      format: ui.format.value || 'jpeg',
      quality: qualityValue()
    };
  }

  function applyPreset(config) {
    if (!config) return;
    if (Number.isFinite(Number(config.rows))) ui.rows.value = clamp(Math.round(Number(config.rows)), 1, 12);
    if (Number.isFinite(Number(config.cols))) ui.cols.value = clamp(Math.round(Number(config.cols)), 1, 12);
    if (Number.isFinite(Number(config.gap))) ui.gap.value = clamp(Math.round(Number(config.gap)), 0, 200);
    if (Number.isFinite(Number(config.margin))) ui.margin.value = clamp(Math.round(Number(config.margin)), 0, 200);
    if (Number.isFinite(Number(config.radius))) ui.radius.value = clamp(Math.round(Number(config.radius)), 0, 200);
    if (typeof config.squareCrop === 'boolean') ui.square.checked = config.squareCrop;
    if (/^#[0-9a-fA-F]{6}$/.test(String(config.background || ''))) {
      ui.background.value = config.background;
    }
    if (['jpeg', 'webp', 'png'].includes(config.format)) ui.format.value = config.format;
    if (Number.isFinite(Number(config.quality))) {
      ui.quality.value = clamp(Number(config.quality), 0.5, 1).toFixed(2);
    }
    ctx.stage.render();
  }

  /** 计算一次几何，预览和导出都从这里取，避免两边算法漂移。 */
  function geometry() {
    if (!ctx.source) return null;
    const W = ctx.source.canvas.width;
    const H = ctx.source.canvas.height;
    const config = collectConfig();

    // 边距不能把图挤成负数；超过一半就按能放下的最大值算
    const margin = Math.min(config.margin, Math.max(0, Math.floor((Math.min(W, H) - 2) / 2)));
    const availW = W - margin * 2 - config.gap * (config.cols - 1);
    const availH = H - margin * 2 - config.gap * (config.rows - 1);
    const cellW = availW / config.cols;
    const cellH = availH / config.rows;
    const ok = availW > 0 && availH > 0 && cellW >= 2 && cellH >= 2;

    return {
      ...config,
      W,
      H,
      margin,
      left: margin,
      top: margin,
      availW,
      availH,
      cellW,
      cellH,
      ok,
      reason: ok ? '' : '边距或间距太大，单元格已经放不下'
    };
  }

  const overlay = {
    render(g, stage) {
      const geo = geometry();
      if (!geo || !geo.ok) return;
      const s = stage.scale || 1;

      g.save();

      // 可用区域边框
      g.strokeStyle = 'rgba(79, 70, 229, 0.9)';
      g.lineWidth = 1.5 / s;
      g.strokeRect(geo.left, geo.top, geo.availW, geo.availH);

      // 单元格之间的切割线
      g.strokeStyle = 'rgba(255, 255, 255, 0.95)';
      g.lineWidth = 2 / s;
      g.beginPath();
      for (let c = 1; c < geo.cols; c += 1) {
        const x = geo.left + c * (geo.cellW + geo.gap) - geo.gap / 2;
        g.moveTo(x, geo.top);
        g.lineTo(x, geo.top + geo.availH);
      }
      for (let r = 1; r < geo.rows; r += 1) {
        const y = geo.top + r * (geo.cellH + geo.gap) - geo.gap / 2;
        g.moveTo(geo.left, y);
        g.lineTo(geo.left + geo.availW, y);
      }
      g.stroke();

      // 行列号：屏幕上太小就不画，免得糊成一片
      if (geo.cellW * s >= 34 && geo.cellH * s >= 22) {
        const fontSize = 12 / s;
        g.font = `700 ${fontSize}px -apple-system, "PingFang SC", system-ui, sans-serif`;
        g.textAlign = 'center';
        g.textBaseline = 'middle';
        g.shadowColor = 'rgba(0, 0, 0, 0.55)';
        g.shadowBlur = 4 / s;
        g.fillStyle = '#ffffff';
        for (let r = 0; r < geo.rows; r += 1) {
          for (let c = 0; c < geo.cols; c += 1) {
            const cx = geo.left + c * (geo.cellW + geo.gap) + geo.cellW / 2;
            const cy = geo.top + r * (geo.cellH + geo.gap) + geo.cellH / 2;
            g.fillText(`${r + 1}-${c + 1}`, cx, cy);
          }
        }
      }

      g.restore();
    }
  };

  function tick() {
    return new Promise((resolve) => setTimeout(resolve, 0));
  }

  function pad2(value) {
    return String(value).padStart(2, '0');
  }

  async function run() {
    if (!ctx.source) {
      ctx.setStatus('先选一张图片', 'err');
      return;
    }

    const geo = geometry();
    if (!geo || !geo.ok) {
      ctx.setStatus(geo ? geo.reason : '先选一张图片', 'err');
      return;
    }

    const format = geo.format;
    const mime = mimeOf(format);
    const ext = extOf(format);
    const total = geo.rows * geo.cols;
    const base = baseName(ctx.source.name);
    const entries = [];
    const restore = ctx.setBusy(ui.run, '切图中…');
    const token = ctx.newToken();

    try {
      for (let row = 0; row < geo.rows; row += 1) {
        for (let col = 0; col < geo.cols; col += 1) {
          if (ctx.isStale(token)) return;

          // 用浮点边界取整，最后一格贴到可用区域右/下边缘，不留下 1px 缝
          const x = Math.round(geo.left + col * (geo.cellW + geo.gap));
          const xEnd = col === geo.cols - 1
            ? Math.round(geo.left + geo.availW)
            : Math.round(geo.left + (col + 1) * (geo.cellW + geo.gap) - geo.gap);
          const y = Math.round(geo.top + row * (geo.cellH + geo.gap));
          const yEnd = row === geo.rows - 1
            ? Math.round(geo.top + geo.availH)
            : Math.round(geo.top + (row + 1) * (geo.cellH + geo.gap) - geo.gap);

          let sx = x;
          let sy = y;
          let sw = Math.max(1, xEnd - x);
          let sh = Math.max(1, yEnd - y);
          let outW = Math.max(1, Math.round(sw));
          let outH = Math.max(1, Math.round(sh));

          if (geo.squareCrop) {
            // 每格先取中心正方形：九宫格发朋友圈时不会一边大一边小
            const side = Math.min(sw, sh);
            sx = x + (sw - side) / 2;
            sy = y + (sh - side) / 2;
            sw = side;
            sh = side;
            outW = Math.max(1, Math.round(side));
            outH = outW;
          }

          const cap = Math.min(1, Math.sqrt(MAX_EXPORT_PIXELS / (outW * outH)));
          if (cap < 1) {
            outW = Math.max(1, Math.round(outW * cap));
            outH = Math.max(1, Math.round(outH * cap));
          }

          const canvas = document.createElement('canvas');
          canvas.width = outW;
          canvas.height = outH;
          const g = canvas.getContext('2d');
          g.imageSmoothingEnabled = true;
          g.imageSmoothingQuality = 'high';

          // JPEG/WebP 没有透明通道，先铺底色；PNG 保留透明
          if (format !== 'png') {
            g.fillStyle = geo.background;
            g.fillRect(0, 0, outW, outH);
          }

          if (geo.radius > 0) {
            g.beginPath();
            roundRectPath(g, 0, 0, outW, outH, geo.radius);
            g.clip();
          }
          g.drawImage(ctx.source.canvas, sx, sy, sw, sh, 0, 0, outW, outH);

          const blob = await toBlob(canvas, mime, format === 'png' ? undefined : geo.quality);
          if (ctx.isStale(token)) return;

          entries.push({
            name: `${base}_${pad2(row + 1)}-${pad2(col + 1)}.${ext}`,
            data: new Uint8Array(await blob.arrayBuffer())
          });

          if (entries.length % 4 === 0 || entries.length === total) {
            ctx.setStatus(`正在切图 ${entries.length}/${total}…`, 'info');
            await tick();
          }
        }
      }

      if (ctx.isStale(token)) return;
      const zip = createStoreZip(entries);
      const filename = `${base}-宫格-${geo.rows}x${geo.cols}.zip`;
      ctx.deliver(zip, filename, `已切 ${total} 张 · ${humanSize(zip.size)}`, 'grid');
      ctx.setStatus(`已导出 ${total} 张，ZIP ${humanSize(zip.size)}`, 'info');
    } catch (error) {
      if (!ctx.isStale(token)) ctx.setStatus(error.message || '切图失败', 'err');
    } finally {
      restore();
    }
  }

  function refresh() {
    ctx.clearResult('grid');
    if (ctx.source) ctx.stage.render();
  }

  ui.run.addEventListener('click', run);
  [ui.rows, ui.cols, ui.gap, ui.margin, ui.radius, ui.square,
    ui.background, ui.format, ui.quality].forEach((el) => {
    el.addEventListener('input', refresh);
    el.addEventListener('change', refresh);
  });

  return {
    mount() {},
    activate() {
      if (ctx.source) {
        ctx.showImage(ctx.source.canvas);
        ctx.stage.setOverlay(overlay);
      } else {
        ctx.stage.setOverlay(null);
      }
      ctx.stage.render();
    },
    deactivate() {
      ctx.stage.setOverlay(null);
    },
    onSource() {},
    collectConfig,
    applyPreset
  };
}
