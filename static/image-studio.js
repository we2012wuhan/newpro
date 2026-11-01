/*
 * 图片工坊 · 页面入口
 * ====================
 * 负责把三块拼起来：
 *
 *   1. canvas.js —— 图片解码 + 舞台（缩放/平移/覆盖层）；
 *   2. 四个页签模块 —— 各自收集参数、画覆盖层、导出文件；
 *   3. 预设 —— 把「当前页签的参数」存进 SQLite，换设备也能套用。
 *
 * 图片像素从头到尾只在内存里：这里没有任何上传逻辑，
 * 发给服务器的只有 presetName + kind + config 这些数字和颜色。
 */
import { createStage, loadSource, humanSize, downloadBlob } from './image-studio/canvas.js';
import { createCompressTab } from './image-studio/compress.js';
import { createCropTab } from './image-studio/crop.js';
import { createGridTab } from './image-studio/grid.js';
import { createCollageTab } from './image-studio/collage.js';
import { takeHandoff, stripHandoffParam } from './handoff.js';

const $ = (id) => document.getElementById(id);

const stageEl = $('stage');
const stageWrap = $('stageWrap');
const emptyState = $('emptyState');
const fileInput = $('fileInput');
const changeBtn = $('changeBtn');
const imageInfo = $('imageInfo');
const statusEl = $('status');
const resultBar = $('resultBar');
const resultText = $('resultText');
const downloadBtn = $('downloadBtn');
const viewZoom = $('viewZoom');
const presetName = $('presetName');
const savePresetBtn = $('savePreset');
const savedList = $('savedList');
const savedCount = $('savedCount');

let activeTab = 'compress';
let statusTimer = 0;
let loadSeq = 0;     // 防止「先选的图后解码完」把新图顶掉
let token = 0;       // 耗时任务的代号：换图 / 新任务后旧任务自动作废

const results = {};  // 每个页签各自记住上一次结果，切换页签不丢

const ctx = {
  source: null,
  stage: null,
  get activeTab() { return activeTab; },
  setStatus,
  setResult,
  clearResult,
  deliver,
  setBusy,
  newToken() { return ++token; },
  isStale(t) { return t !== token; },
  invalidateTokens() { token += 1; },
  setImageInfo(text) { imageInfo.textContent = text; },
  showImage(canvas) {
    if (!canvas) {
      ctx.stage.setImage(null);
      return;
    }
    // 同一张画布只重画；换了画布才重新 fit，避免每次切页签都跳回 100%
    if (ctx.stage.image === canvas) ctx.stage.render();
    else ctx.stage.setImage(canvas);
  }
};

ctx.stage = createStage(stageEl, stageWrap);
ctx.stage.onChange = (scale) => {
  viewZoom.textContent = Math.round(scale * 100) + '%';
};

const tabs = {
  compress: createCompressTab(ctx),
  crop: createCropTab(ctx),
  grid: createGridTab(ctx),
  collage: createCollageTab(ctx)
};

Object.values(tabs).forEach((tab) => {
  if (typeof tab.mount === 'function') tab.mount();
});

// ---------------------------------------------------------------
// 状态条 / 结果条
// ---------------------------------------------------------------
function setStatus(message, kind = 'info') {
  if (!message) {
    statusEl.classList.remove('show');
    return;
  }
  statusEl.textContent = message;
  statusEl.className = 'status show ' + (kind === 'err' ? 'err' : 'info');
  clearTimeout(statusTimer);
  statusTimer = setTimeout(() => {
    statusEl.classList.remove('show');
  }, kind === 'err' ? 4200 : 2400);
}

function setResult(result, kind = activeTab) {
  results[kind] = result;
  if (kind !== activeTab) return;
  resultText.textContent = result.text || '已生成文件';
  resultBar.hidden = false;
}

function clearResult(kind = activeTab) {
  delete results[kind];
  if (kind !== activeTab) return;
  resultBar.hidden = true;
  resultText.textContent = '';
}

function deliver(blob, filename, text, kind = activeTab) {
  setResult({ blob, filename, text }, kind);
  try {
    downloadBlob(blob, filename);
    setStatus('已开始下载 ' + filename, 'info');
  } catch (error) {
    setStatus('下载失败：' + (error.message || error), 'err');
  }
}

/** 跑耗时任务时锁按钮，返回一个恢复函数；放在 finally 里调用。 */
function setBusy(button, busyText) {
  const oldText = button.textContent;
  const oldDisabled = button.disabled;
  button.disabled = true;
  button.textContent = busyText;
  return () => {
    button.disabled = oldDisabled;
    button.textContent = oldText;
  };
}

// ---------------------------------------------------------------
// 页签切换
// ---------------------------------------------------------------
function activateTab(name) {
  if (!tabs[name]) return;
  if (tabs[activeTab] && typeof tabs[activeTab].deactivate === 'function') {
    tabs[activeTab].deactivate();
  }

  activeTab = name;
  document.querySelectorAll('.tab').forEach((btn) => {
    const on = btn.dataset.tab === name;
    btn.classList.toggle('on', on);
    btn.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  document.querySelectorAll('.tab-body').forEach((body) => {
    body.hidden = body.id !== 'tab-' + name;
  });
  stageEl.classList.toggle('crop', name === 'crop');

  if (typeof tabs[name].activate === 'function') tabs[name].activate();

  // 共享的空状态 / 图片信息 / 按钮文案由入口统一管理。
  // 拼图有自己的 9 格预览，不需要「把图片拖到这里」的大空状态。
  if (name === 'collage') {
    emptyState.hidden = true;
    changeBtn.textContent = '添加照片';
  } else {
    emptyState.hidden = !!ctx.source;
    imageInfo.textContent = ctx.source ? describeSource(ctx.source) : '还没有图片';
    changeBtn.textContent = '换一张';
  }

  const saved = results[name];
  if (saved) setResult(saved, name);
  else {
    resultBar.hidden = true;
    resultText.textContent = '';
  }
}

document.querySelectorAll('.tab').forEach((btn) => {
  btn.addEventListener('click', () => activateTab(btn.dataset.tab));
});

// ---------------------------------------------------------------
// 载入图片：选择 / 拖放 / 粘贴
// ---------------------------------------------------------------
function pickFile() {
  // 拼图页签有自己的多选入口，不能走单图 loadFile（那会清空九个格子）
  if (activeTab === 'collage') {
    tabs.collage.pick();
    return;
  }
  fileInput.value = '';
  fileInput.click();
}

$('pickBtn').addEventListener('click', pickFile);
changeBtn.addEventListener('click', pickFile);
fileInput.addEventListener('change', () => {
  const file = fileInput.files && fileInput.files[0];
  fileInput.value = '';
  if (file) loadFile(file);
});

function isImageFile(file) {
  if (!file) return false;
  return (file.type && file.type.indexOf('image/') === 0) ||
    /\.(png|jpe?g|webp|gif|bmp|avif|heic|heif)$/i.test(file.name || '');
}

/** 拖进来的可能是一组照片（拼图要用九张），所以返回数组而不是第一张。 */
function imagesFromTransfer(transfer) {
  const out = [];
  if (!transfer) return out;
  const files = transfer.files;
  if (files && files.length) {
    for (const file of files) {
      if (isImageFile(file)) out.push(file);
    }
  }
  if (!out.length) {
    const items = transfer.items;
    if (items) {
      for (const item of items) {
        if (item.kind === 'file' && item.type.indexOf('image/') === 0) {
          const file = item.getAsFile();
          if (file) out.push(file);
        }
      }
    }
  }
  return out;
}

stageWrap.addEventListener('dragenter', (event) => {
  event.preventDefault();
  stageWrap.classList.add('drag');
});
stageWrap.addEventListener('dragover', (event) => {
  event.preventDefault();
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
  stageWrap.classList.add('drag');
});
stageWrap.addEventListener('dragleave', (event) => {
  // relatedTarget 为空通常表示离开了整个窗口
  if (!event.relatedTarget || !stageWrap.contains(event.relatedTarget)) {
    stageWrap.classList.remove('drag');
  }
});
stageWrap.addEventListener('drop', (event) => {
  event.preventDefault();
  stageWrap.classList.remove('drag');
  const files = imagesFromTransfer(event.dataTransfer);
  if (!files.length) {
    setStatus('拖进来的不是图片', 'err');
    return;
  }
  if (activeTab === 'collage') tabs.collage.addFiles(files);
  else loadFile(files[0]);
});

// 只要文件拖到页面任何地方，都不要让浏览器直接打开它
document.addEventListener('dragover', (event) => event.preventDefault());
document.addEventListener('drop', (event) => {
  if (!stageWrap.contains(event.target)) event.preventDefault();
});

document.addEventListener('paste', (event) => {
  const target = event.target;
  if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)) {
    return;   // 在输入框里粘贴文字时不抢
  }
  const items = event.clipboardData && event.clipboardData.items;
  if (!items) return;
  const files = [];
  for (const item of items) {
    if (item.kind === 'file' && item.type.indexOf('image/') === 0) {
      const file = item.getAsFile();
      if (file) files.push(file);
    }
  }
  if (!files.length) return;
  event.preventDefault();
  if (activeTab === 'collage') tabs.collage.addFiles(files);
  else loadFile(files[0]);
});

async function loadFile(file) {
  const seq = ++loadSeq;
  ctx.invalidateTokens();
  setStatus('正在读取图片…', 'info');

  try {
    const source = await loadSource(file);
    if (seq !== loadSeq) return;

    ctx.source = source;
    emptyState.hidden = true;

    resetAllResults();
    ctx.stage.setImage(source.canvas);
    imageInfo.textContent = describeSource(source);

    Object.values(tabs).forEach((tab) => {
      if (typeof tab.onSource === 'function') tab.onSource(source);
    });
    activateTab(activeTab);
    setStatus('已载入 ' + source.name, 'info');
    return true;
  } catch (error) {
    if (seq !== loadSeq) return false;
    setStatus(error.message || '图片读取失败', 'err');
    return false;
  }
}

function describeSource(source) {
  const size = humanSize(source.bytes);
  const scaled = source.width !== source.originalWidth || source.height !== source.originalHeight;
  const extra = scaled
    ? `（原图 ${source.originalWidth}×${source.originalHeight}，已缩到安全尺寸）`
    : '';
  return `${source.width} × ${source.height} · ${size}${extra}`;
}

function resetAllResults() {
  Object.keys(results).forEach((key) => delete results[key]);
  resultBar.hidden = true;
  resultText.textContent = '';
}

// ---------------------------------------------------------------
// 缩放按钮
// ---------------------------------------------------------------
$('fitBtn').addEventListener('click', () => ctx.stage.fit());
$('zoomIn').addEventListener('click', () => ctx.stage.zoom(1.25));
$('zoomOut').addEventListener('click', () => ctx.stage.zoom(1 / 1.25));

downloadBtn.addEventListener('click', () => {
  const result = results[activeTab];
  if (!result) return;
  downloadBlob(result.blob, result.filename);
  setStatus('已开始下载 ' + result.filename, 'info');
});

// ---------------------------------------------------------------
// 预设
// ---------------------------------------------------------------
function summaryOf(row) {
  const payload = row.payload || {};
  const config = payload.config || {};
  if (payload.kind === 'compress') {
    return `压缩 · 目标 ${config.targetKB || 200}KB · ${String(config.format || 'jpeg').toUpperCase()}`;
  }
  if (payload.kind === 'crop') {
    const aspect = config.aspect === 'free' || !config.aspect ? '自由比例' : config.aspect;
    return `裁剪 · ${aspect} · ${config.rotate || 0}°`;
  }
  if (payload.kind === 'grid') {
    return `切图 · ${config.rows || 3}×${config.cols || 3} · ${String(config.format || 'jpeg').toUpperCase()}`;
  }
  if (payload.kind === 'collage') {
    return `拼图 · ${config.size || 1080}px · ${String(config.format || 'jpeg').toUpperCase()}`;
  }
  return '方案';
}

function timeOf(row) {
  const text = String(row.created_at || '');
  return text.length >= 16 ? text.slice(5, 16) : text;
}

function renderPresets(items, max) {
  savedList.innerHTML = '';
  savedCount.textContent = `${items.length}/${max || 30}`;

  if (!items.length) {
    const empty = document.createElement('div');
    empty.className = 'saved-empty';
    empty.textContent = '还没有方案，调好参数后存一个';
    savedList.appendChild(empty);
    return;
  }

  items.forEach((row) => {
    const wrap = document.createElement('div');
    wrap.className = 'saved-item';

    const restore = document.createElement('button');
    restore.type = 'button';
    restore.className = 'saved-restore';
    const title = document.createElement('b');
    title.textContent = row.title || '未命名方案';
    const summary = document.createElement('span');
    summary.textContent = `${summaryOf(row)} · ${timeOf(row)}`;
    restore.appendChild(title);
    restore.appendChild(summary);
    restore.addEventListener('click', () => applyPresetRow(row));

    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'saved-del';
    del.textContent = '×';
    del.setAttribute('aria-label', '删除方案 ' + (row.title || ''));
    del.addEventListener('click', () => deletePreset(row));

    wrap.appendChild(restore);
    wrap.appendChild(del);
    savedList.appendChild(wrap);
  });
}

async function loadPresets() {
  try {
    const res = await fetch('/image-studio/api/presets', {
      credentials: 'same-origin',
      headers: { Accept: 'application/json' }
    });
    const data = await res.json().catch(() => ({}));
    if (res.status === 401) throw new Error('登录已过期，请刷新页面');
    if (!res.ok || !data.ok) throw new Error(data.message || '方案加载失败');
    renderPresets(data.items || [], data.max || 30);
  } catch (error) {
    savedList.innerHTML = '';
    const empty = document.createElement('div');
    empty.className = 'saved-empty';
    empty.textContent = error.message || '方案加载失败';
    savedList.appendChild(empty);
  }
}

function applyPresetRow(row) {
  const payload = row && row.payload;
  if (!payload || payload.v !== 1 || !tabs[payload.kind]) {
    setStatus('这个方案的格式不支持', 'err');
    return;
  }
  const tab = tabs[payload.kind];
  clearResult(payload.kind);
  activateTab(payload.kind);
  tab.applyPreset(payload.config || {});

  // 拼图的「有没有内容」不在 ctx.source 上，而在它自己的九个格子
  const ready = typeof tab.hasContent === 'function' ? tab.hasContent() : !!ctx.source;
  if (!ready) setStatus(tab.emptyPresetHint || '方案已应用，选一张图片就能用', 'info');
  else setStatus('已应用「' + (row.title || '方案') + '」', 'info');
}

async function savePreset() {
  const tab = tabs[activeTab];
  const config = tab && typeof tab.collectConfig === 'function' ? tab.collectConfig() : null;
  if (!config) {
    setStatus('先调好当前页签的参数', 'err');
    return;
  }

  const restore = setBusy(savePresetBtn, '保存中…');
  try {
    const res = await fetch('/image-studio/api/presets', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({
        name: presetName.value.trim(),
        kind: activeTab,
        config,
        v: 1
      })
    });
    const data = await res.json().catch(() => ({}));
    if (res.status === 401) throw new Error('登录已过期，请刷新页面');
    if (!res.ok || !data.ok) throw new Error(data.message || '保存失败');

    presetName.value = '';
    await loadPresets();
    setStatus('方案已保存', 'info');
  } catch (error) {
    setStatus(error.message || '保存失败', 'err');
  } finally {
    restore();
  }
}

async function deletePreset(row) {
  if (!window.confirm(`删除方案「${row.title || '未命名'}」？`)) return;
  try {
    const res = await fetch('/image-studio/api/presets/' + encodeURIComponent(row.id), {
      method: 'DELETE',
      credentials: 'same-origin',
      headers: { Accept: 'application/json' }
    });
    const data = await res.json().catch(() => ({}));
    if (res.status === 401) throw new Error('登录已过期，请刷新页面');
    if (!res.ok || !data.ok) throw new Error(data.message || '删除失败');
    await loadPresets();
    setStatus('方案已删除', 'info');
  } catch (error) {
    setStatus(error.message || '删除失败', 'err');
  }
}

savePresetBtn.addEventListener('click', savePreset);
presetName.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') {
    event.preventDefault();
    savePreset();
  }
});

// ---------------------------------------------------------------
// 深链：/image-studio?r=123 打开并套用某个方案
// ---------------------------------------------------------------
async function applyDeepLink() {
  const params = new URLSearchParams(location.search);
  const id = params.get('r');
  if (!id) return;
  if (!/^\d+$/.test(id)) {
    dropDeepLink();
    return;
  }

  try {
    const res = await fetch('/image-studio/api/presets/' + id, {
      credentials: 'same-origin',
      headers: { Accept: 'application/json' }
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) throw new Error(data.message || '方案不存在');
    applyPresetRow(data.item);
  } catch (error) {
    setStatus('这个方案不存在或已删除', 'err');
    dropDeepLink();
  }
}

function dropDeepLink() {
  const url = new URL(location.href);
  url.searchParams.delete('r');
  history.replaceState(null, '', url.pathname + (url.search ? url.search : '') + url.hash);
}

// ---------------------------------------------------------------
// 从 AI 文生图接力过来
// ---------------------------------------------------------------
// 文生图页面把 Blob 暂存在 IndexedDB，地址栏只带一个 handoff key。
// 这里读出来当成普通文件走 loadFile，等于用户自己拖进来，
// 图片不需要再上传一次。
async function applyHandoff() {
  const key = new URLSearchParams(location.search).get('handoff');
  if (!key) return;
  stripHandoffParam();

  try {
    const record = await takeHandoff(key);
    if (!record || !record.blob) throw new Error('没有找到暂存的图片');
    const type = record.blob.type || 'image/png';
    const name = record.name || 'AI生成图.png';
    const file = new File([record.blob], name, { type });
    const ok = await loadFile(file);
    if (ok) setStatus('已接入 AI 生成图，可以继续处理', 'info');
  } catch (error) {
    setStatus('AI 生成图接入失败，请下载后拖进来', 'err');
  }
}

// ---------------------------------------------------------------
// 启动
// ---------------------------------------------------------------
activateTab('compress');
loadPresets();
applyDeepLink();
applyHandoff();

window.addEventListener('beforeunload', () => {
  if (ctx.stage) ctx.stage.destroy();
});
