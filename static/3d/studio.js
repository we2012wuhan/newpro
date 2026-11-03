// 3D 小作品实验室入口：把界面、3D 场景、2D 草图和保存记录接在一起。
//
// 模块划分：
//   core/geomath.js   纯几何计算（可单测）
//   core/geometry.js  模板和草图 -> Three.js 几何体
//   core/scene.js     相机、灯光、网格、模型显示
//   core/export.js    STL / 3MF 导出
//   core/document.js  v2 作品文档与旧记录兼容
//   core/sketch.js    2D 草图编辑器

import { TEMPLATES, cloneDefaults, buildSketchBody } from './core/geometry.js';
import { createViewer } from './core/scene.js';
import { exportBytes, downloadBytes } from './core/export.js';
import { createSketchEditor } from './core/sketch.js';
import {
  createDefaultSketch,
  documentLabel,
  normalizeSketch,
  payloadToDocument,
  serializeDocument,
  sketchHasClosedOuter
} from './core/document.js';

const TEMPLATE_NAMES = Object.fromEntries(
  Object.entries(TEMPLATES).map(([key, template]) => [key, template.name])
);

const state = {
  key: 'tray',
  values: {
    tray: cloneDefaults('tray'),
    stand: cloneDefaults('stand'),
    hook: cloneDefaults('hook'),
    custom: cloneDefaults('custom')
  },
  sketch: createDefaultSketch(),
  color: '#55b8aa',
  geometry: null,
  stats: null
};

const host = document.getElementById('canvasHost');
const fieldsEl = document.getElementById('fields');
const statusEl = document.getElementById('status');
const statsEl = document.getElementById('stats');
const loadingEl = document.getElementById('loading');
const colorEl = document.getElementById('modelColor');
const designNameEl = document.getElementById('designName');
const saveDesignBtn = document.getElementById('saveDesign');
const savedListEl = document.getElementById('savedList');
const savedCountEl = document.getElementById('savedCount');
const sketchLayer = document.getElementById('sketchLayer');
const sketchCanvas = document.getElementById('sketchCanvas');
const paneSketchBtn = document.getElementById('paneSketch');
const paneModelBtn = document.getElementById('paneModel');
const canvasBadge = document.getElementById('canvasBadge');
const viewToolsEl = document.querySelector('.view-tools');
const viewerTitleEl = document.querySelector('.viewer-title');
const downloadStlBtn = document.getElementById('downloadStl');
const download3mfBtn = document.getElementById('download3mf');
const panelEl = document.querySelector('.panel');

let viewer = null;
let sketchEditor = null;
let buildTimer = 0;
let currentView = 'iso';
let currentPane = 'model';
let savedItems = [];

function setStatus(text, kind = 'info') {
  statusEl.textContent = text || '';
  statusEl.className = 'status' + (text ? ` show ${kind}` : '');
}

function setStatsText(text) {
  statsEl.textContent = text;
}

function updateExportButtons() {
  const disabled = !state.geometry;
  downloadStlBtn.disabled = disabled;
  download3mfBtn.disabled = disabled;
  saveDesignBtn.disabled = disabled;
}

function updateTemplateButtons() {
  document.querySelectorAll('.tpl').forEach((button) => {
    button.classList.toggle('on', button.dataset.template === state.key);
  });
}

function updateViewButtons() {
  document.querySelectorAll('.vbtn').forEach((button) => {
    button.classList.toggle('on', button.dataset.view === currentView);
  });
}

function buildCurrent() {
  if (state.key === 'custom') {
    return buildSketchBody(state.sketch, state.values.custom.extrusion);
  }
  return TEMPLATES[state.key].build(state.values[state.key]);
}

function applyGeometry(built, { fit = true } = {}) {
  const result = viewer.setGeometry(built.geometry, state.color);
  state.geometry = built.geometry;
  state.stats = result.stats;
  setStatsText(
    `${result.stats.width.toFixed(0)} × ${result.stats.height.toFixed(0)} × ` +
    `${result.stats.depth.toFixed(0)} mm · ${result.stats.triangles.toLocaleString('zh-CN')} 面`
  );
  viewer.updateGrid(result.bounds);
  if (fit) viewer.fitView(currentView);
  updateExportButtons();
  if (built.warning) setStatus(built.warning, 'err');
  else if (currentPane === 'model') setStatus('模型已更新，可以旋转检查或直接下载。', 'info');
}

function clearModelState(label = '轮廓未闭合') {
  if (viewer) viewer.clearModel();
  state.geometry = null;
  state.stats = null;
  setStatsText(label);
  updateExportButtons();
}

function rebuild({ quiet = false } = {}) {
  if (!viewer) return;
  if (state.key === 'custom' && !sketchHasClosedOuter(state.sketch)) {
    clearModelState('轮廓未闭合');
    if (currentPane === 'model') {
      setStatus('请先在 2D 草图里画出并闭合外轮廓。', 'info');
    } else if (quiet) {
      setStatus('先画出闭合外轮廓，3D 预览会自动出现。', 'info');
    }
    return;
  }
  try {
    const built = buildCurrent();
    applyGeometry(built, { fit: currentPane === 'model' });
  } catch (error) {
    clearModelState(state.key === 'custom' ? '轮廓未闭合' : '等待建模');
    setStatus(`生成失败：${error.message || error}`, 'err');
  }
}

function scheduleBuild() {
  window.clearTimeout(buildTimer);
  buildTimer = window.setTimeout(() => {
    if (sketchEditor) state.sketch = sketchEditor.getSketch();
    rebuild();
  }, 35);
}

function fieldMarkup(field, value) {
  return `
    <div class="field" data-key="${field.key}">
      <label for="range-${field.key}">${field.label}</label>
      <input id="number-${field.key}" type="number" value="${value}"
        min="${field.min}" max="${field.max}" step="${field.step}" inputmode="decimal">
      <input id="range-${field.key}" type="range" value="${value}"
        min="${field.min}" max="${field.max}" step="${field.step}">
      <output for="range-${field.key}">${value} ${field.unit}</output>
    </div>`;
}

function renderFields() {
  const template = TEMPLATES[state.key];
  const values = state.values[state.key];
  fieldsEl.innerHTML = template.fields
    .map((field) => fieldMarkup(field, values[field.key]))
    .join('');
  document.getElementById('modelName').textContent = template.name;
  document.getElementById('modelDesc').textContent = template.desc;
  paneSketchBtn.disabled = state.key !== 'custom';
  updateExportButtons();

  template.fields.forEach((field) => {
    const range = document.getElementById(`range-${field.key}`);
    const number = document.getElementById(`number-${field.key}`);
    const output = range.closest('.field').querySelector('output');
    const update = (raw) => {
      const value = Math.min(field.max, Math.max(field.min, Number(raw) || field.min));
      values[field.key] = value;
      range.value = String(value);
      number.value = String(value);
      output.textContent = `${value} ${field.unit}`;
      scheduleBuild();
    };
    range.addEventListener('input', () => update(range.value));
    number.addEventListener('change', () => update(number.value));
    number.addEventListener('blur', () => update(number.value));
  });
}

function setPane(pane) {
  if (pane === 'sketch' && state.key !== 'custom') return;
  currentPane = pane;
  const showingSketch = pane === 'sketch';
  host.hidden = showingSketch;
  sketchLayer.hidden = !showingSketch;
  canvasBadge.hidden = showingSketch;
  viewToolsEl.hidden = showingSketch;
  paneSketchBtn.classList.toggle('on', showingSketch);
  paneModelBtn.classList.toggle('on', !showingSketch);
  viewerTitleEl.textContent = showingSketch ? '2D 轮廓草图' : '实时预览';

  if (showingSketch) {
    if (sketchEditor) {
      sketchEditor.resize();
      sketchEditor.draw();
    }
  } else if (viewer) {
    viewer.resize();
    viewer.fitView(currentView);
  }
}

function selectTemplate(key) {
  if (!TEMPLATES[key]) return;
  state.key = key;
  designNameEl.value = '';
  currentView = 'iso';
  updateTemplateButtons();
  updateViewButtons();
  renderFields();

  if (key === 'custom') {
    setPane('sketch');
    rebuild({ quiet: true });
  } else {
    setPane('model');
    rebuild();
  }
  updateExportButtons();
}

function resetParams() {
  if (state.key === 'custom') {
    if (!window.confirm('恢复默认会替换当前草图，继续？')) return;
    state.values.custom = cloneDefaults('custom');
    state.sketch = createDefaultSketch();
    if (sketchEditor) sketchEditor.setSketch(state.sketch);
    renderFields();
    setPane('sketch');
    rebuild({ quiet: true });
  } else {
    state.values[state.key] = cloneDefaults(state.key);
    currentView = 'iso';
    updateViewButtons();
    renderFields();
    setPane('model');
    rebuild();
  }
  setStatus('已恢复默认参数。', 'info');
}

function download(format) {
  if (!state.geometry) {
    setStatus('模型还没有准备好。', 'err');
    return;
  }
  try {
    const template = TEMPLATES[state.key];
    const title = designNameEl.value.trim() || template.name;
    const bytes = exportBytes(state.geometry, format, title);
    const stamp = new Date().toISOString().slice(0, 10).replaceAll('-', '');
    downloadBytes(
      bytes,
      `${template.slug}-${stamp}.${format}`,
      format === '3mf' ? 'model/3mf' : 'model/stl'
    );
    setStatus(`${format.toUpperCase()} 已生成，正在下载。`, 'info');
  } catch (error) {
    setStatus(`导出失败：${error.message || error}`, 'err');
  }
}

function templateLabel(payload) {
  return documentLabel(payload, TEMPLATE_NAMES);
}

function restoreDesign(item) {
  const doc = payloadToDocument(item?.payload);
  if (!doc || !doc.bodies.length) {
    setStatus('这条作品记录已经无法恢复。', 'err');
    return;
  }
  const body = doc.bodies[0];

  if (body.kind === 'template' && TEMPLATES[body.template]) {
    state.key = body.template;
    state.values[body.template] = { ...cloneDefaults(body.template), ...body.params };
  } else if (body.kind === 'sketch') {
    state.key = 'custom';
    state.values.custom = { extrusion: body.extrusion };
    state.sketch = normalizeSketch(body.sketch);
    if (sketchEditor) sketchEditor.setSketch(state.sketch);
  } else {
    setStatus('这条作品记录已经无法恢复。', 'err');
    return;
  }

  state.color = /^#[0-9a-f]{6}$/i.test(String(body.color || '')) ? body.color : '#55b8aa';
  colorEl.value = state.color;
  designNameEl.value = item.title || '';
  currentView = 'iso';
  updateTemplateButtons();
  updateViewButtons();
  renderFields();

  if (state.key === 'custom') {
    setPane('sketch');
    rebuild({ quiet: true });
  } else {
    setPane('model');
    rebuild();
  }
  if (viewer) viewer.setColor(state.color);
  setStatus(`已恢复「${item.title || '未命名作品'}」。`, 'info');
  panelEl.scrollTo({ top: 0, behavior: 'smooth' });
}

function renderSaved() {
  savedListEl.innerHTML = '';
  savedCountEl.textContent = savedItems.length ? `${savedItems.length} 条` : '';
  if (!savedItems.length) {
    const empty = document.createElement('div');
    empty.className = 'saved-empty';
    empty.textContent = '还没有保存的作品。调整好以后点上面的「保存」。';
    savedListEl.appendChild(empty);
    return;
  }

  savedItems.forEach((item) => {
    const row = document.createElement('div');
    row.className = 'saved-item';

    const restore = document.createElement('button');
    restore.type = 'button';
    restore.className = 'saved-restore';
    const title = document.createElement('b');
    title.textContent = item.title || '未命名作品';
    const meta = document.createElement('span');
    meta.textContent = `${templateLabel(item.payload)} · ${String(item.created_at || '').slice(5, 16)}`;
    restore.appendChild(title);
    restore.appendChild(meta);
    restore.addEventListener('click', () => restoreDesign(item));

    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'saved-del';
    remove.textContent = '×';
    remove.title = '删除这个作品';
    remove.setAttribute('aria-label', `删除 ${item.title || '未命名作品'}`);
    remove.addEventListener('click', async () => {
      if (!window.confirm(`删除「${item.title || '未命名作品'}」？`)) return;
      try {
        const response = await fetch(`/3d-lab/api/designs/${item.id}`, { method: 'DELETE' });
        const result = await response.json();
        if (!response.ok || !result.ok) throw new Error(result.message || '删除失败');
        savedItems = savedItems.filter((saved) => saved.id !== item.id);
        renderSaved();
        setStatus('作品记录已删除。', 'info');
      } catch (error) {
        setStatus(`删除失败：${error.message || error}`, 'err');
      }
    });

    row.append(restore, remove);
    savedListEl.appendChild(row);
  });
}

async function loadDesigns() {
  try {
    const response = await fetch('/3d-lab/api/designs');
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.message || '读取失败');
    savedItems = result.items || [];
    renderSaved();
  } catch (error) {
    savedItems = [];
    renderSaved();
    setStatus(`读取作品记录失败：${error.message || error}`, 'err');
  }
}

async function saveDesign() {
  if (!state.geometry) {
    setStatus('请先完成并闭合 3D 轮廓再保存。', 'err');
    return;
  }
  if (state.key === 'custom' && sketchEditor) {
    state.sketch = sketchEditor.getSketch();
  }
  saveDesignBtn.disabled = true;
  try {
    const documentPayload = serializeDocument(state);
    const response = await fetch('/3d-lab/api/designs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: designNameEl.value.trim(),
        ...documentPayload
      })
    });
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.message || '保存失败');
    await loadDesigns();
    setStatus('作品已保存到作品库，换设备登录也能恢复。', 'info');
  } catch (error) {
    setStatus(`保存失败：${error.message || error}`, 'err');
  } finally {
    updateExportButtons();
  }
}

function bindUI() {
  document.querySelectorAll('.tpl').forEach((button) => {
    button.addEventListener('click', () => selectTemplate(button.dataset.template));
  });

  document.querySelectorAll('.vbtn').forEach((button) => {
    button.addEventListener('click', () => {
      currentView = button.dataset.view;
      updateViewButtons();
      if (viewer) viewer.fitView(currentView);
    });
  });

  paneSketchBtn.addEventListener('click', () => setPane('sketch'));
  paneModelBtn.addEventListener('click', () => setPane('model'));

  colorEl.addEventListener('input', () => {
    state.color = colorEl.value;
    if (viewer) viewer.setColor(state.color);
    if (sketchEditor) sketchEditor.draw();
  });

  downloadStlBtn.addEventListener('click', () => download('stl'));
  download3mfBtn.addEventListener('click', () => download('3mf'));
  saveDesignBtn.addEventListener('click', saveDesign);
  document.getElementById('resetParams').addEventListener('click', resetParams);

  new MutationObserver(() => {
    if (viewer) viewer.refreshTheme();
    if (currentPane === 'sketch' && sketchEditor) sketchEditor.draw();
  }).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
}

async function init() {
  try {
    viewer = createViewer(host);
    sketchEditor = createSketchEditor({
      layer: sketchLayer,
      canvas: sketchCanvas,
      getColor: () => state.color,
      onCommit: () => {
        state.sketch = sketchEditor.getSketch();
        scheduleBuild();
      },
      onStatus: (text, kind) => setStatus(text, kind)
    });
    sketchEditor.setSketch(state.sketch);
    bindUI();
    renderFields();
    rebuild({ quiet: true });
    loadingEl.hidden = true;
    loadDesigns();

    window.TB3D = {
      templates: Object.keys(TEMPLATES),
      state,
      viewer,
      sketch: sketchEditor,
      rebuild: () => rebuild(),
      setTemplate: selectTemplate,
      setPane,
      exportBytes: (format) => exportBytes(state.geometry, format, '3D 作品')
    };
  } catch (error) {
    loadingEl.innerHTML = `<div>3D 场景启动失败<br><small>${error.message || error}</small></div>`;
    setStatus(`3D 场景启动失败：${error.message || error}`, 'err');
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
