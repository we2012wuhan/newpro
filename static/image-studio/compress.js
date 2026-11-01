/*
 * 压缩页签
 * =========
 * 目标很朴素：给定「不超过多少 KB」，把图压进去。
 *
 * 浏览器能调的旋钮只有两个：
 *   - quality：JPEG/WebP 的质量，0–1，直接决定体积；
 *   - 尺寸：像素少了，体积基本按平方往下掉。
 *
 * 所以做法是「先调质量，质量到底了再降尺寸」：
 *   1. 在 [0.30, 0.95] 里二分 8 次，找「体积 ≤ 目标」的最高质量；
 *   2. 如果最低质量还是超标，就把长宽各缩到 85%，再来一轮，最多 6 轮；
 *   3. PNG 没有质量参数，直接把 quality 参数忽略，只能降尺寸。
 *
 * 注意：这里所有 canvas 都是「导出用」的，不加 DPR —— 预览模糊没关系，
 * 导出的像素不能因为屏幕是 2 倍屏就变成两倍大。
 */
import {
  clamp,
  fitDimensions,
  mimeOf,
  extOf,
  toBlob,
  baseName,
  humanSize,
  MAX_EXPORT_PIXELS
} from './canvas.js';

const MIN_QUALITY = 0.30;
const MAX_QUALITY = 0.95;
const MIN_EDGE = 32;

export function createCompressTab(ctx) {
  const ui = {
    kb: document.getElementById('compressKB'),
    edge: document.getElementById('compressEdge'),
    format: document.getElementById('compressFormat'),
    background: document.getElementById('compressBg'),
    run: document.getElementById('compressRun')
  };

  function numberValue(input, low, high, fallback) {
    const value = Number(input.value);
    if (!Number.isFinite(value)) return fallback;
    return clamp(Math.round(value), low, high);
  }

  function collectConfig() {
    return {
      targetKB: numberValue(ui.kb, 5, 10240, 200),
      maxEdge: numberValue(ui.edge, 0, 12000, 0),
      format: ui.format.value || 'jpeg',
      background: (ui.background.value || '#ffffff').toLowerCase()
    };
  }

  function applyPreset(config) {
    if (!config) return;
    if (Number.isFinite(Number(config.targetKB))) {
      ui.kb.value = clamp(Math.round(Number(config.targetKB)), 5, 10240);
    }
    if (Number.isFinite(Number(config.maxEdge))) {
      ui.edge.value = clamp(Math.round(Number(config.maxEdge)), 0, 12000);
    }
    if (['jpeg', 'webp', 'png'].includes(config.format)) ui.format.value = config.format;
    if (/^#[0-9a-fA-F]{6}$/.test(String(config.background || ''))) {
      ui.background.value = config.background;
    }
  }

  /** 把原图画到指定尺寸；JPEG 不支持透明，所以先铺底色。 */
  function paint(sourceCanvas, width, height, format, background) {
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(width));
    canvas.height = Math.max(1, Math.round(height));
    const g = canvas.getContext('2d');
    g.imageSmoothingEnabled = true;
    g.imageSmoothingQuality = 'high';
    if (format === 'jpeg') {
      g.fillStyle = background || '#ffffff';
      g.fillRect(0, 0, canvas.width, canvas.height);
    }
    g.drawImage(sourceCanvas, 0, 0, canvas.width, canvas.height);
    return canvas;
  }

  function capPixels(width, height) {
    const ratio = Math.min(1, Math.sqrt(MAX_EXPORT_PIXELS / (width * height)));
    return {
      width: Math.max(1, Math.round(width * ratio)),
      height: Math.max(1, Math.round(height * ratio))
    };
  }

  function tick() {
    return new Promise((resolve) => setTimeout(resolve, 0));
  }

  /**
   * 原图能不能直接交差？
   * canvas 重新编码不一定更小：一张本身只有 2KB 的 PNG，重编码可能变 4KB，
   * 还白白降了分辨率。所以同格式、没要求缩尺寸、原文件已经达标时，原样返回。
   */
  function canReuseOriginal(source, base, format, targetBytes) {
    if (!source.file) return false;
    // 源图在载入时被缩过（超过 4000 万像素），原文件已经不是舞台上的像素
    if (source.width !== source.originalWidth || source.height !== source.originalHeight) return false;
    // 用户要求限定长边，就不能偷懒
    if (base.width !== source.width || base.height !== source.height) return false;

    const name = String(source.name || '').toLowerCase();
    const type = String(source.file.type || '').toLowerCase();
    const sameFormat = name.endsWith('.' + extOf(format)) || type === mimeOf(format);
    return sameFormat && source.file.size <= targetBytes;
  }

  async function run() {
    const source = ctx.source;
    if (!source) {
      ctx.setStatus('先选一张图片', 'err');
      return;
    }

    const config = collectConfig();
    const targetBytes = config.targetKB * 1024;
    const format = config.format;
    const mime = mimeOf(format);
    const restore = ctx.setBusy(ui.run, '压缩中…');
    const token = ctx.newToken();

    try {
      const base = fitDimensions(source.width, source.height, config.maxEdge);

      if (canReuseOriginal(source, base, format, targetBytes)) {
        const filename = `${baseName(source.name)}-压缩-${config.targetKB}KB.${extOf(format)}`;
        const text = `原图 ${humanSize(source.bytes)} 已小于目标 ${config.targetKB}KB，直接使用原图`;
        ctx.deliver(source.file, filename, text, 'compress');
        ctx.setStatus('原图已小于目标，直接使用原图', 'info');
        return;
      }

      let size = capPixels(base.width, base.height);
      let canvas = paint(source.canvas, size.width, size.height, format, config.background);

      let best = null;
      let bestQuality = null;
      let reached = false;

      if (format === 'png') {
        // PNG 是无损格式：quality 参数无效，只能一档一档缩尺寸
        let rounds = 0;
        while (rounds <= 7) {
          const blob = await toBlob(canvas, mime);
          if (ctx.isStale(token)) return;
          best = blob;
          bestQuality = null;
          if (blob.size <= targetBytes) {
            reached = true;
            break;
          }
          if (Math.min(size.width, size.height) <= MIN_EDGE) break;

          size = nextSmaller(size);
          canvas = paint(source.canvas, size.width, size.height, format, config.background);
          rounds += 1;
          await tick();
        }
      } else {
        let rounds = 0;
        while (rounds <= 6) {
          let low = MIN_QUALITY;
          let high = MAX_QUALITY;
          let roundBest = null;
          let roundQuality = null;

          // 二分：体积随质量单调上升，所以每次都能砍掉一半区间
          for (let step = 0; step < 8; step += 1) {
            const quality = (low + high) / 2;
            const blob = await toBlob(canvas, mime, quality);
            if (ctx.isStale(token)) return;
            if (blob.size <= targetBytes) {
              roundBest = blob;
              roundQuality = quality;
              low = quality;        // 还能再高一点
            } else {
              high = quality;       // 太大了，降质量
            }
            await tick();
          }

          if (roundBest) {
            best = roundBest;
            bestQuality = roundQuality;
            reached = true;
            break;
          }

          // 8 次二分不一定正好踩到 0.30，这里补一次「最低质量」作为兜底结果
          const floorBlob = await toBlob(canvas, mime, MIN_QUALITY);
          if (ctx.isStale(token)) return;
          best = floorBlob;
          bestQuality = MIN_QUALITY;
          if (floorBlob.size <= targetBytes) {
            reached = true;
            break;
          }

          if (Math.min(size.width, size.height) <= MIN_EDGE) break;
          size = nextSmaller(size);
          canvas = paint(source.canvas, size.width, size.height, format, config.background);
          rounds += 1;
          await tick();
        }
      }

      if (!best) throw new Error('压缩失败，浏览器没有生成文件');

      const qualityText = bestQuality == null ? '' : ` · 质量 ${bestQuality.toFixed(2)}`;
      const prefix = reached
        ? '压缩完成'
        : `未达到 ${config.targetKB}KB，已导出最小结果`;
      const text = `${prefix}：${humanSize(source.bytes)} → ${humanSize(best.size)} · ` +
        `${size.width} × ${size.height}${qualityText}`;

      const filename = `${baseName(source.name)}-压缩-${config.targetKB}KB.${extOf(format)}`;
      ctx.deliver(best, filename, text, 'compress');

      if (reached) {
        ctx.setStatus(`已压到 ${humanSize(best.size)}`, 'info');
      } else if (format === 'png') {
        ctx.setStatus('PNG 只能靠缩尺寸，已导出最小结果', 'err');
      } else {
        ctx.setStatus('最低质量和最小尺寸仍超标，已导出最小结果', 'err');
      }
    } catch (error) {
      if (!ctx.isStale(token)) ctx.setStatus(error.message || '压缩失败', 'err');
    } finally {
      restore();
    }
  }

  function nextSmaller(size) {
    const ratio = 0.85;
    let width = Math.max(MIN_EDGE, Math.round(size.width * ratio));
    let height = Math.max(MIN_EDGE, Math.round(size.height * ratio));
    const capped = capPixels(width, height);
    width = capped.width;
    height = capped.height;
    return { width, height };
  }

  ui.run.addEventListener('click', run);

  return {
    mount() {},
    activate() {
      if (ctx.source) ctx.showImage(ctx.source.canvas);
      ctx.stage.setOverlay(null);
    },
    deactivate() {},
    onSource() {},
    collectConfig,
    applyPreset
  };
}
