import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';

const TEMPLATES = {
  tray: {
    name: '桌面收纳盒',
    desc: '带分格的桌面收纳盒，可调整格子数量和壁厚。',
    slug: 'desk-organizer',
    defaults: {
      width: 180,
      depth: 130,
      height: 70,
      wall: 2.4,
      cols: 2,
      rows: 1
    },
    fields: [
      { key: 'width', label: '宽度', min: 80, max: 300, step: 5, unit: 'mm' },
      { key: 'depth', label: '深度', min: 60, max: 240, step: 5, unit: 'mm' },
      { key: 'height', label: '高度', min: 30, max: 180, step: 5, unit: 'mm' },
      { key: 'wall', label: '壁厚', min: 1.2, max: 8, step: 0.2, unit: 'mm' },
      { key: 'cols', label: '列数', min: 1, max: 4, step: 1, unit: '格' },
      { key: 'rows', label: '行数', min: 1, max: 3, step: 1, unit: '格' }
    ],
    build: buildTray
  },
  stand: {
    name: '手机支架',
    desc: '带前挡边和充电留孔的手机支架，后靠背角度可调。',
    slug: 'phone-stand',
    defaults: {
      width: 90,
      depth: 110,
      height: 105,
      angle: 16,
      lip: 14,
      cable: 28,
      thickness: 3
    },
    fields: [
      { key: 'width', label: '宽度', min: 60, max: 160, step: 5, unit: 'mm' },
      { key: 'depth', label: '底座深度', min: 70, max: 180, step: 5, unit: 'mm' },
      { key: 'height', label: '靠背高度', min: 70, max: 150, step: 5, unit: 'mm' },
      { key: 'angle', label: '后仰角度', min: 5, max: 35, step: 1, unit: '°' },
      { key: 'lip', label: '前挡边高', min: 8, max: 30, step: 1, unit: 'mm' },
      { key: 'cable', label: '充电孔宽', min: 0, max: 60, step: 2, unit: 'mm' },
      { key: 'thickness', label: '结构厚度', min: 2, max: 6, step: 0.5, unit: 'mm' }
    ],
    build: buildStand
  },
  hook: {
    name: '洞洞板挂钩',
    desc: '螺丝固定的实用挂钩，适合洞洞板、柜门和墙面。',
    slug: 'wall-hook',
    defaults: {
      width: 60,
      plateHeight: 90,
      reach: 55,
      plateThickness: 6,
      hookThickness: 8,
      tipHeight: 14,
      screwDiameter: 4.5
    },
    fields: [
      { key: 'width', label: '底板宽度', min: 30, max: 120, step: 5, unit: 'mm' },
      { key: 'plateHeight', label: '底板高度', min: 50, max: 180, step: 5, unit: 'mm' },
      { key: 'reach', label: '挂钩伸出', min: 25, max: 100, step: 5, unit: 'mm' },
      { key: 'plateThickness', label: '底板厚度', min: 4, max: 12, step: 0.5, unit: 'mm' },
      { key: 'hookThickness', label: '挂钩厚度', min: 5, max: 15, step: 0.5, unit: 'mm' },
      { key: 'tipHeight', label: '上翘高度', min: 8, max: 30, step: 1, unit: 'mm' },
      { key: 'screwDiameter', label: '螺丝孔直径', min: 3, max: 8, step: 0.5, unit: 'mm' }
    ],
    build: buildHook
  },
  custom: {
    name: '自由绘制',
    desc: '在毫米网格上画闭合轮廓，添加圆孔，再设置拉伸厚度。',
    slug: 'custom-model',
    defaults: {
      extrusion: 6,
      closed: true,
      outline: [
        [-50, -55], [50, -55], [50, -45], [-38, -45], [-38, 30], [-50, 30]
      ],
      holes: [
        { x: -44, y: 18, r: 2.4 },
        { x: 44, y: -50, r: 2.4 }
      ]
    },
    fields: [
      { key: 'extrusion', label: '拉伸厚度', min: 1, max: 40, step: 0.5, unit: 'mm' }
    ],
    build: buildCustom
  }
};

const state = {
  key: 'tray',
  values: {},
  geometry: null,
  stats: null,
  color: '#55b8aa'
};

Object.entries(TEMPLATES).forEach(([key, template]) => {
  state.values[key] = JSON.parse(JSON.stringify(template.defaults));
});

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
const sketchCtx = sketchCanvas.getContext('2d');
const sketchHintEl = document.getElementById('sketchHint');
const sketchPositionEl = document.getElementById('sketchPosition');
const paneSketchBtn = document.getElementById('paneSketch');
const paneModelBtn = document.getElementById('paneModel');
const canvasBadge = document.getElementById('canvasBadge');
const viewToolsEl = document.querySelector('.view-tools');

let renderer;
let scene;
let camera;
let controls;
let modelGroup;
let grid;
let buildTimer = 0;
let currentView = 'iso';
let savedItems = [];
let currentPane = 'model';
let sketchMode = 'outline';
let dragIndex = -1;
let holeDraft = null;
let sketchHistory = [];

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, Number(value)));
}

function setStatus(text, kind = 'info') {
  statusEl.textContent = text || '';
  statusEl.className = 'status' + (text ? ' show ' + kind : '');
}

function boxGeometry(width, height, depth, x, y, z, rotateX = 0) {
  const geometry = new THREE.BoxGeometry(
    Math.max(0.1, width),
    Math.max(0.1, height),
    Math.max(0.1, depth)
  );
  if (rotateX) geometry.rotateX(rotateX);
  geometry.translate(x, y, z);
  if (geometry.index) {
    const nonIndexed = geometry.toNonIndexed();
    geometry.dispose();
    return nonIndexed;
  }
  return geometry;
}

function mergeParts(parts) {
  const nonIndexed = parts.map((geometry) => {
    if (!geometry.index) return geometry;
    const copy = geometry.toNonIndexed();
    geometry.dispose();
    return copy;
  });
  const merged = mergeGeometries(nonIndexed, false);
  nonIndexed.forEach((geometry) => geometry.dispose());
  if (!merged) throw new Error('模型网格合并失败');
  merged.computeVertexNormals();
  merged.computeBoundingBox();
  return merged;
}

function buildTray(values) {
  const width = values.width;
  const depth = values.depth;
  const height = values.height;
  const wall = Math.min(values.wall, Math.min(width, depth) / 4);
  const cols = Math.round(values.cols);
  const rows = Math.round(values.rows);
  const parts = [
    boxGeometry(width, wall, depth, 0, wall / 2, 0),
    boxGeometry(wall, height, depth, -(width - wall) / 2, height / 2, 0),
    boxGeometry(wall, height, depth, (width - wall) / 2, height / 2, 0),
    boxGeometry(width - wall * 2, height, wall, 0, height / 2, -(depth - wall) / 2),
    boxGeometry(width - wall * 2, height, wall, 0, height / 2, (depth - wall) / 2)
  ];

  const innerWidth = width - wall * 2;
  const innerDepth = depth - wall * 2;
  for (let i = 1; i < cols; i += 1) {
    const x = -innerWidth / 2 + (innerWidth / cols) * i;
    parts.push(boxGeometry(wall, height - wall, innerDepth, x, wall + (height - wall) / 2, 0));
  }
  for (let i = 1; i < rows; i += 1) {
    const z = -innerDepth / 2 + (innerDepth / rows) * i;
    parts.push(boxGeometry(innerWidth, height - wall, wall, 0, wall + (height - wall) / 2, z));
  }

  return {
    geometry: mergeParts(parts),
    warning: wall < 1.6 ? '壁厚小于 1.6 mm，打印和承力可能偏弱。' : ''
  };
}

function buildStand(values) {
  const width = values.width;
  const depth = values.depth;
  const height = values.height;
  const base = values.thickness;
  const lip = Math.min(values.lip, height * 0.4);
  const backThickness = Math.max(2.4, values.thickness);
  const gap = Math.min(values.cable, width - 12);
  const angle = THREE.MathUtils.degToRad(values.angle);
  const parts = [
    boxGeometry(width, base, depth, 0, base / 2, 0)
  ];

  if (gap <= 0.1) {
    parts.push(boxGeometry(width, lip, base, 0, base + lip / 2, -(depth - base) / 2));
  } else {
    const sideWidth = Math.max(4, (width - gap) / 2);
    const x = gap / 2 + sideWidth / 2;
    for (const side of [-1, 1]) {
      parts.push(boxGeometry(sideWidth, lip, base, side * x, base + lip / 2, -(depth - base) / 2));
    }
  }

  const centerY = base + Math.cos(angle) * height / 2;
  const centerZ = depth - backThickness / 2 - Math.sin(angle) * height / 2;
  parts.push(boxGeometry(width, height, backThickness, 0, centerY, centerZ, -angle));

  return {
    geometry: mergeParts(parts),
    warning: gap > width - 16 ? '充电孔接近支架总宽，边缘可能过薄。' : ''
  };
}

function buildHook(values) {
  const width = values.width;
  const height = values.plateHeight;
  const plateDepth = values.plateThickness;
  const screwRadius = values.screwDiameter / 2;
  const holeY = [height * 0.25, height * 0.75];
  const shape = new THREE.Shape();
  shape.moveTo(-width / 2, 0);
  shape.lineTo(width / 2, 0);
  shape.lineTo(width / 2, height);
  shape.lineTo(-width / 2, height);
  shape.closePath();
  for (const y of holeY) {
    const hole = new THREE.Path();
    hole.absarc(0, y, screwRadius, 0, Math.PI * 2, true);
    shape.holes.push(hole);
  }
  const plateGeometry = new THREE.ExtrudeGeometry(shape, {
    depth: plateDepth,
    bevelEnabled: false,
    steps: 1
  });
  const plate = plateGeometry.index ? plateGeometry.toNonIndexed() : plateGeometry;
  if (plate !== plateGeometry) plateGeometry.dispose();

  const armY = height * 0.46;
  const armWidth = Math.max(18, width * 0.72);
  const reach = values.reach;
  const hookT = values.hookThickness;
  const tipH = values.tipHeight;
  const arm = boxGeometry(
    armWidth,
    hookT,
    reach,
    0,
    armY,
    plateDepth + reach / 2
  );
  const tip = boxGeometry(
    armWidth,
    tipH,
    hookT,
    0,
    armY + tipH / 2 - hookT / 2,
    plateDepth + reach - hookT / 2
  );

  return {
    geometry: mergeParts([plate, arm, tip]),
    warning: screwRadius * 2 > width * 0.22 ? '螺丝孔相对底板偏大，请确认边缘强度。' : ''
  };
}

function polygonArea(points) {
  let area = 0;
  for (let i = 0; i < points.length; i += 1) {
    const a = points[i];
    const b = points[(i + 1) % points.length];
    area += a[0] * b[1] - b[0] * a[1];
  }
  return Math.abs(area) / 2;
}

function orientation(a, b, c) {
  return (b[1] - a[1]) * (c[0] - b[0]) - (b[0] - a[0]) * (c[1] - b[1]);
}

function segmentsIntersect(a, b, c, d) {
  const abC = orientation(a, b, c);
  const abD = orientation(a, b, d);
  const cdA = orientation(c, d, a);
  const cdB = orientation(c, d, b);
  return ((abC > 0 && abD < 0) || (abC < 0 && abD > 0)) &&
    ((cdA > 0 && cdB < 0) || (cdA < 0 && cdB > 0));
}

function outlineSelfIntersects(points) {
  const count = points.length;
  for (let i = 0; i < count; i += 1) {
    const a = points[i];
    const b = points[(i + 1) % count];
    for (let j = i + 1; j < count; j += 1) {
      if (j === i || j === (i + 1) % count || (j + 1) % count === i) continue;
      const c = points[j];
      const d = points[(j + 1) % count];
      if (segmentsIntersect(a, b, c, d)) return true;
    }
  }
  return false;
}

function pointInPolygon(point, polygon) {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const xi = polygon[i][0];
    const yi = polygon[i][1];
    const xj = polygon[j][0];
    const yj = polygon[j][1];
    const crosses = ((yi > point[1]) !== (yj > point[1])) &&
      (point[0] < ((xj - xi) * (point[1] - yi)) / ((yj - yi) || 1e-9) + xi);
    if (crosses) inside = !inside;
  }
  return inside;
}

function buildCustom(values) {
  if (!values.closed || !Array.isArray(values.outline) || values.outline.length < 3) {
    throw new Error('请先画完并闭合外轮廓');
  }
  const outline = values.outline.map((point) => [Number(point[0]), Number(point[1])]);
  if (outlineSelfIntersects(outline)) throw new Error('轮廓发生自相交，请撤回交叉的点');
  if (polygonArea(outline) < 1) throw new Error('轮廓面积太小，请至少画出 1 mm² 的形状');

  const shape = new THREE.Shape();
  shape.moveTo(outline[0][0], outline[0][1]);
  for (let i = 1; i < outline.length; i += 1) {
    shape.lineTo(outline[i][0], outline[i][1]);
  }
  shape.closePath();

  let ignoredHoles = 0;
  (values.holes || []).forEach((hole) => {
    if (!pointInPolygon([hole.x, hole.y], outline)) {
      ignoredHoles += 1;
      return;
    }
    const path = new THREE.Path();
    path.absarc(hole.x, hole.y, Math.max(0.5, hole.r), 0, Math.PI * 2, true);
    shape.holes.push(path);
  });

  const geometry = new THREE.ExtrudeGeometry(shape, {
    depth: values.extrusion,
    bevelEnabled: false,
    curveSegments: 48,
    steps: 1
  });
  const result = geometry.index ? geometry.toNonIndexed() : geometry;
  if (result !== geometry) geometry.dispose();
  result.translate(0, 0, -values.extrusion / 2);
  result.computeVertexNormals();
  result.computeBoundingBox();
  return {
    geometry: result,
    warning: ignoredHoles ? `${ignoredHoles} 个圆孔落在轮廓外，已忽略。` : ''
  };
}

function disposeSceneModel() {
  if (!modelGroup) return;
  modelGroup.traverse((child) => {
    if (child.geometry) child.geometry.dispose();
    if (child.material) {
      if (Array.isArray(child.material)) child.material.forEach((item) => item.dispose());
      else child.material.dispose();
    }
  });
  scene.remove(modelGroup);
  modelGroup = null;
}

function createModel() {
  const template = TEMPLATES[state.key];
  const built = template.build(state.values[state.key]);
  const geometry = built.geometry;
  const material = new THREE.MeshStandardMaterial({
    color: state.color,
    roughness: 0.42,
    metalness: 0.04,
    side: THREE.DoubleSide
  });
  const mesh = new THREE.Mesh(geometry, material);
  mesh.castShadow = true;
  mesh.receiveShadow = true;

  const edgeMaterial = new THREE.LineBasicMaterial({
    color: document.documentElement.getAttribute('data-theme') === 'dark'
      ? 0xd8fff9 : 0x1f4b47,
    transparent: true,
    opacity: 0.19
  });
  const edges = new THREE.LineSegments(new THREE.EdgesGeometry(geometry, 28), edgeMaterial);

  disposeSceneModel();
  modelGroup = new THREE.Group();
  modelGroup.add(mesh, edges);
  scene.add(modelGroup);

  const bounds = geometry.boundingBox.clone();
  state.geometry = geometry;
  state.stats = {
    width: bounds.max.x - bounds.min.x,
    height: bounds.max.y - bounds.min.y,
    depth: bounds.max.z - bounds.min.z,
    triangles: geometry.attributes.position.count / 3
  };
  statsEl.textContent =
    `${state.stats.width.toFixed(0)} × ${state.stats.height.toFixed(0)} × ` +
    `${state.stats.depth.toFixed(0)} mm · ${state.stats.triangles.toLocaleString('zh-CN')} 面`;

  updateGrid(bounds);
  fitView(currentView);
  updateExportButtons();
  if (built.warning) setStatus(built.warning, 'err');
  else setStatus('模型已更新，可以旋转检查或直接下载。', 'info');
}

function scheduleBuild() {
  window.clearTimeout(buildTimer);
  buildTimer = window.setTimeout(() => {
    try {
      createModel();
    } catch (error) {
      setStatus(`生成失败：${error.message || error}`, 'err');
    }
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
      const value = clamp(raw, field.min, field.max);
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

function selectTemplate(key) {
  if (!TEMPLATES[key]) return;
  state.key = key;
  designNameEl.value = '';
  currentView = 'iso';
  document.querySelectorAll('.tpl').forEach((button) => {
    button.classList.toggle('on', button.dataset.template === key);
  });
  document.querySelectorAll('.vbtn').forEach((button) => {
    button.classList.toggle('on', button.dataset.view === 'iso');
  });
  renderFields();
  if (key === 'custom') {
    sketchHistory = [];
    setPane('sketch');
    drawSketch();
    if (state.values.custom.closed) createModel();
    else clearSceneModel();
  } else {
    setPane('model');
    createModel();
  }
  updateExportButtons();
}

function updateGrid(bounds) {
  if (grid) {
    scene.remove(grid);
    grid.geometry.dispose();
    grid.material.dispose();
  }
  const span = Math.max(
    bounds.max.x - bounds.min.x,
    bounds.max.z - bounds.min.z,
    80
  ) * 2.2;
  grid = new THREE.GridHelper(span, 20, 0x6f918e, 0xaec5c2);
  grid.position.y = Math.min(0, bounds.min.y) - 0.15;
  grid.material.transparent = true;
  grid.material.opacity = document.documentElement.getAttribute('data-theme') === 'dark'
    ? 0.28 : 0.34;
  scene.add(grid);
}

function resizeModelViewport() {
  if (!renderer || !camera) return;
  const width = host.clientWidth;
  const height = host.clientHeight;
  if (!width || !height) return;
  renderer.setSize(width, height, false);
  camera.aspect = width / height;
  camera.updateProjectionMatrix();
}

function clearSceneModel() {
  disposeSceneModel();
  state.geometry = null;
  state.stats = null;
  statsEl.textContent = '轮廓未闭合';
  updateExportButtons();
}

function updateExportButtons() {
  const invalidCustom = state.key === 'custom' &&
    (!state.values.custom.closed || !state.values.custom.outline.length);
  const disabled = !state.geometry || invalidCustom;
  document.getElementById('downloadStl').disabled = disabled;
  document.getElementById('download3mf').disabled = disabled;
  saveDesignBtn.disabled = disabled;
}

function cloneDefaults(key) {
  return JSON.parse(JSON.stringify(TEMPLATES[key].defaults));
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
  document.querySelector('.viewer-title').textContent = showingSketch ? '2D 轮廓草图' : '实时预览';
  if (showingSketch) {
    resizeSketchCanvas();
  } else {
    resizeModelViewport();
    fitView(currentView);
  }
}

function sketchMetrics() {
  const width = Math.max(1, sketchCanvas.clientWidth);
  const height = Math.max(1, sketchCanvas.clientHeight);
  const scale = Math.min(width / 240, height / 180) * 0.92;
  return { width, height, scale, ox: width / 2, oy: height / 2 };
}

function worldToSketch(point, metrics = sketchMetrics()) {
  return [
    metrics.ox + point[0] * metrics.scale,
    metrics.oy - point[1] * metrics.scale
  ];
}

function sketchToWorld(clientX, clientY) {
  const rect = sketchCanvas.getBoundingClientRect();
  const metrics = sketchMetrics();
  return [
    (clientX - rect.left - metrics.ox) / metrics.scale,
    -(clientY - rect.top - metrics.oy) / metrics.scale
  ];
}

function pushSketchHistory() {
  const custom = state.values.custom;
  sketchHistory.push(JSON.stringify({
    outline: custom.outline,
    holes: custom.holes,
    closed: custom.closed
  }));
  if (sketchHistory.length > 50) sketchHistory.shift();
}

function restoreSketchSnapshot(snapshot) {
  const parsed = JSON.parse(snapshot);
  state.values.custom.outline = parsed.outline || [];
  state.values.custom.holes = parsed.holes || [];
  state.values.custom.closed = !!parsed.closed;
}

function resizeSketchCanvas() {
  if (state.key !== 'custom' || currentPane !== 'sketch') return;
  const rect = sketchCanvas.getBoundingClientRect();
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const width = Math.max(1, Math.round(rect.width * dpr));
  const height = Math.max(1, Math.round(rect.height * dpr));
  if (sketchCanvas.width !== width || sketchCanvas.height !== height) {
    sketchCanvas.width = width;
    sketchCanvas.height = height;
  }
  sketchCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
  drawSketch();
}

function drawSketch() {
  if (!sketchCtx) return;
  const custom = state.values.custom;
  const metrics = sketchMetrics();
  const ctx = sketchCtx;
  const styles = getComputedStyle(document.documentElement);
  const line = styles.getPropertyValue('--line').trim() || '#dfe7ea';
  const muted = styles.getPropertyValue('--muted').trim() || '#7d899e';
  const acc = styles.getPropertyValue('--acc').trim() || '#0f9f8f';
  ctx.clearRect(0, 0, metrics.width, metrics.height);

  ctx.lineWidth = 1;
  for (let x = -120; x <= 120; x += 10) {
    const sx = metrics.ox + x * metrics.scale;
    ctx.strokeStyle = x === 0 ? acc : line;
    ctx.globalAlpha = x === 0 ? 0.65 : 0.5;
    ctx.beginPath();
    ctx.moveTo(sx, 0);
    ctx.lineTo(sx, metrics.height);
    ctx.stroke();
  }
  for (let y = -90; y <= 90; y += 10) {
    const sy = metrics.oy - y * metrics.scale;
    ctx.strokeStyle = y === 0 ? acc : line;
    ctx.globalAlpha = y === 0 ? 0.65 : 0.5;
    ctx.beginPath();
    ctx.moveTo(0, sy);
    ctx.lineTo(metrics.width, sy);
    ctx.stroke();
  }
  ctx.globalAlpha = 1;

  if (custom.outline.length) {
    ctx.beginPath();
    custom.outline.forEach((point, index) => {
      const [x, y] = worldToSketch(point, metrics);
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    if (custom.closed) {
      ctx.closePath();
      (custom.holes || []).forEach((hole) => {
        const [x, y] = worldToSketch([hole.x, hole.y], metrics);
        ctx.moveTo(x + hole.r * metrics.scale, y);
        ctx.arc(x, y, hole.r * metrics.scale, 0, Math.PI * 2, true);
      });
      ctx.fillStyle = state.color + '55';
      ctx.fill('evenodd');
      ctx.strokeStyle = acc;
      ctx.lineWidth = 2;
      ctx.stroke();
    } else {
      ctx.strokeStyle = acc;
      ctx.lineWidth = 2;
      ctx.stroke();
    }
  }

  custom.outline.forEach((point, index) => {
    const [x, y] = worldToSketch(point, metrics);
    ctx.beginPath();
    ctx.arc(x, y, index === 0 ? 5.5 : 4, 0, Math.PI * 2);
    ctx.fillStyle = index === 0 ? acc : '#ffffff';
    ctx.fill();
    ctx.strokeStyle = acc;
    ctx.lineWidth = 1.5;
    ctx.stroke();
  });

  (custom.holes || []).forEach((hole) => {
    const [x, y] = worldToSketch([hole.x, hole.y], metrics);
    ctx.beginPath();
    ctx.arc(x, y, Math.max(3, hole.r * metrics.scale), 0, Math.PI * 2);
    ctx.strokeStyle = muted;
    ctx.lineWidth = 1.5;
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(x, y, 1.5, 0, Math.PI * 2);
    ctx.fillStyle = muted;
    ctx.fill();
  });

  if (holeDraft) {
    const [x, y] = worldToSketch([holeDraft.x, holeDraft.y], metrics);
    ctx.beginPath();
    ctx.arc(x, y, Math.max(2, holeDraft.r * metrics.scale), 0, Math.PI * 2);
    ctx.setLineDash([5, 4]);
    ctx.strokeStyle = acc;
    ctx.lineWidth = 1.5;
    ctx.stroke();
    ctx.setLineDash([]);
  }
}

function setSketchMode(mode) {
  if (mode === 'hole' && !state.values.custom.closed) {
    setStatus('请先闭合外轮廓，再添加圆孔。', 'err');
    return;
  }
  sketchMode = mode;
  document.getElementById('sketchOutline').classList.toggle('on', mode === 'outline');
  document.getElementById('sketchHole').classList.toggle('on', mode === 'hole');
  sketchHintEl.textContent = mode === 'hole'
    ? '在轮廓内按下并拖动，松开即可添加圆孔。'
    : '依次点击网格画外轮廓，点回第一个点即可闭合。';
}

function closeCustomOutline() {
  const custom = state.values.custom;
  if (custom.outline.length < 3) {
    setStatus('至少需要 3 个点才能闭合轮廓。', 'err');
    return;
  }
  if (outlineSelfIntersects(custom.outline)) {
    setStatus('轮廓有交叉，请撤回并重新画。', 'err');
    return;
  }
  pushSketchHistory();
  custom.closed = true;
  drawSketch();
  setSketchMode('hole');
  scheduleBuild();
  setPane('model');
  setStatus('轮廓已闭合，3D 模型正在生成。', 'info');
}

function undoCustomSketch() {
  if (!sketchHistory.length) {
    setStatus('没有可以撤回的草图操作。', 'err');
    return;
  }
  restoreSketchSnapshot(sketchHistory.pop());
  drawSketch();
  if (state.values.custom.closed) scheduleBuild();
  else clearSceneModel();
  setStatus('已撤回上一笔。', 'info');
}

function clearCustomSketch() {
  pushSketchHistory();
  state.values.custom.outline = [];
  state.values.custom.holes = [];
  state.values.custom.closed = false;
  sketchMode = 'outline';
  setSketchMode('outline');
  clearSceneModel();
  drawSketch();
  setSketchMode('outline');
  setStatus('草图已清空。先点击网格画出至少 3 个点。', 'info');
}

function nearestSketchPoint(point) {
  const metrics = sketchMetrics();
  const outline = state.values.custom.outline;
  for (let i = 0; i < outline.length; i += 1) {
    const dx = outline[i][0] - point[0];
    const dy = outline[i][1] - point[1];
    if (Math.hypot(dx, dy) * metrics.scale <= 10) return i;
  }
  return -1;
}

function handleSketchPointerDown(event) {
  if (state.key !== 'custom' || currentPane !== 'sketch') return;
  event.preventDefault();
  const custom = state.values.custom;
  const point = sketchToWorld(event.clientX, event.clientY);
  if (sketchMode === 'hole') {
    if (!custom.closed) return;
    pushSketchHistory();
    holeDraft = { x: round2(point[0]), y: round2(point[1]), r: 0 };
    sketchCanvas.setPointerCapture(event.pointerId);
    return;
  }

  if (!custom.closed && custom.outline.length >= 3) {
    const first = worldToSketch(custom.outline[0]);
    const rect = sketchCanvas.getBoundingClientRect();
    const sx = event.clientX - rect.left;
    const sy = event.clientY - rect.top;
    if (Math.hypot(first[0] - sx, first[1] - sy) <= 11) {
      closeCustomOutline();
      return;
    }
  }

  const vertex = nearestSketchPoint(point);
  if (vertex >= 0) {
    pushSketchHistory();
    dragIndex = vertex;
    sketchCanvas.setPointerCapture(event.pointerId);
    return;
  }
  if (custom.closed) {
    setStatus('轮廓已经闭合。可以添加圆孔，或清空重画。', 'info');
    return;
  }
  pushSketchHistory();
  custom.outline.push([round2(point[0]), round2(point[1])]);
  drawSketch();
  setStatus(`已添加第 ${custom.outline.length} 个点。`, 'info');
}

function handleSketchPointerMove(event) {
  if (state.key !== 'custom' || currentPane !== 'sketch') return;
  const point = sketchToWorld(event.clientX, event.clientY);
  sketchPositionEl.textContent = `X ${round2(point[0])} · Y ${round2(point[1])} mm`;
  if (dragIndex >= 0) {
    state.values.custom.outline[dragIndex] = [round2(point[0]), round2(point[1])];
    drawSketch();
    return;
  }
  if (holeDraft) {
    holeDraft.r = Math.hypot(point[0] - holeDraft.x, point[1] - holeDraft.y);
    drawSketch();
  }
}

function handleSketchPointerUp(event) {
  if (dragIndex >= 0) {
    dragIndex = -1;
    scheduleBuild();
    return;
  }
  if (holeDraft) {
    const hole = holeDraft;
    holeDraft = null;
    if (hole.r >= 0.5) {
      state.values.custom.holes.push({
        x: hole.x,
        y: hole.y,
        r: round2(Math.min(hole.r, 100))
      });
      drawSketch();
      scheduleBuild();
      setStatus('圆孔已添加，3D 模型正在更新。', 'info');
    } else {
      sketchHistory.pop();
      drawSketch();
    }
  }
  if (sketchCanvas.hasPointerCapture(event.pointerId)) {
    sketchCanvas.releasePointerCapture(event.pointerId);
  }
}

function round2(value) {
  return Math.round(Number(value) * 100) / 100;
}

function fitView(view = 'iso') {
  if (!modelGroup || !state.geometry) return;
  const bounds = state.geometry.boundingBox;
  const center = bounds.getCenter(new THREE.Vector3());
  const size = bounds.getSize(new THREE.Vector3());
  const distance = Math.max(size.x, size.y, size.z, 60) * 2.25;
  controls.target.copy(center);
  camera.up.set(0, 1, 0);

  if (view === 'front') {
    camera.position.set(center.x, center.y, center.z + distance);
  } else if (view === 'top') {
    camera.up.set(0, 0, -1);
    camera.position.set(center.x, center.y + distance, center.z + 0.01);
  } else {
    camera.position.set(
      center.x + distance * 0.7,
      center.y + distance * 0.55,
      center.z + distance * 0.8
    );
  }
  camera.near = Math.max(0.1, distance / 800);
  camera.far = distance * 20;
  camera.updateProjectionMatrix();
  controls.update();
}

function triangleNormal(a, b, c) {
  const ab = new THREE.Vector3().subVectors(b, a);
  const ac = new THREE.Vector3().subVectors(c, a);
  return new THREE.Vector3().crossVectors(ab, ac).normalize();
}

function makeStlBytes(geometry) {
  const source = geometry.index ? geometry.toNonIndexed() : geometry;
  const positions = source.attributes.position;
  const triangles = Math.floor(positions.count / 3);
  const buffer = new ArrayBuffer(84 + triangles * 50);
  const view = new DataView(buffer);
  const header = new TextEncoder().encode('Toolbox 3D Lab');
  new Uint8Array(buffer, 0, header.length).set(header);
  view.setUint32(80, triangles, true);
  let offset = 84;
  const a = new THREE.Vector3();
  const b = new THREE.Vector3();
  const c = new THREE.Vector3();

  for (let i = 0; i < triangles; i += 1) {
    const index = i * 3;
    a.fromBufferAttribute(positions, index);
    b.fromBufferAttribute(positions, index + 1);
    c.fromBufferAttribute(positions, index + 2);
    const normal = triangleNormal(a, b, c);
    for (const value of [normal.x, normal.y, normal.z]) {
      view.setFloat32(offset, value, true);
      offset += 4;
    }
    for (const point of [a, b, c]) {
      for (const value of [point.x, point.y, point.z]) {
        view.setFloat32(offset, value, true);
        offset += 4;
      }
    }
    view.setUint16(offset, 0, true);
    offset += 2;
  }
  if (source !== geometry) source.dispose();
  return new Uint8Array(buffer);
}

let crcTable = null;
function crc32(data) {
  if (!crcTable) {
    crcTable = new Uint32Array(256);
    for (let i = 0; i < 256; i += 1) {
      let value = i;
      for (let j = 0; j < 8; j += 1) {
        value = (value & 1) ? (0xedb88320 ^ (value >>> 1)) : (value >>> 1);
      }
      crcTable[i] = value >>> 0;
    }
  }
  let crc = 0xffffffff;
  for (const byte of data) crc = crcTable[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

function zipStore(files) {
  const encoder = new TextEncoder();
  const entries = files.map((file) => {
    const name = encoder.encode(file.name);
    return { ...file, name, crc: crc32(file.data), offset: 0 };
  });
  let localSize = 0;
  for (const entry of entries) localSize += 30 + entry.name.length + entry.data.length;
  const centralSize = entries.reduce((sum, entry) => sum + 46 + entry.name.length, 0);
  const result = new Uint8Array(localSize + centralSize + 22);
  const view = new DataView(result.buffer);
  let offset = 0;

  for (const entry of entries) {
    entry.offset = offset;
    view.setUint32(offset, 0x04034b50, true); offset += 4;
    view.setUint16(offset, 20, true); offset += 2;
    view.setUint16(offset, 0x0800, true); offset += 2;
    view.setUint16(offset, 0, true); offset += 2;
    view.setUint16(offset, 0, true); offset += 2;
    view.setUint16(offset, 0, true); offset += 2;
    view.setUint32(offset, entry.crc, true); offset += 4;
    view.setUint32(offset, entry.data.length, true); offset += 4;
    view.setUint32(offset, entry.data.length, true); offset += 4;
    view.setUint16(offset, entry.name.length, true); offset += 2;
    view.setUint16(offset, 0, true); offset += 2;
    result.set(entry.name, offset); offset += entry.name.length;
    result.set(entry.data, offset); offset += entry.data.length;
  }

  const centralOffset = offset;
  for (const entry of entries) {
    view.setUint32(offset, 0x02014b50, true); offset += 4;
    view.setUint16(offset, 20, true); offset += 2;
    view.setUint16(offset, 20, true); offset += 2;
    view.setUint16(offset, 0x0800, true); offset += 2;
    view.setUint16(offset, 0, true); offset += 2;
    view.setUint16(offset, 0, true); offset += 2;
    view.setUint16(offset, 0, true); offset += 2;
    view.setUint32(offset, entry.crc, true); offset += 4;
    view.setUint32(offset, entry.data.length, true); offset += 4;
    view.setUint32(offset, entry.data.length, true); offset += 4;
    view.setUint16(offset, entry.name.length, true); offset += 2;
    view.setUint16(offset, 0, true); offset += 2;
    view.setUint16(offset, 0, true); offset += 2;
    view.setUint16(offset, 0, true); offset += 2;
    view.setUint16(offset, 0, true); offset += 2;
    view.setUint32(offset, 0, true); offset += 4;
    view.setUint32(offset, entry.offset, true); offset += 4;
    result.set(entry.name, offset); offset += entry.name.length;
  }

  view.setUint32(offset, 0x06054b50, true); offset += 4;
  view.setUint16(offset, 0, true); offset += 2;
  view.setUint16(offset, 0, true); offset += 2;
  view.setUint16(offset, entries.length, true); offset += 2;
  view.setUint16(offset, entries.length, true); offset += 2;
  view.setUint32(offset, centralSize, true); offset += 4;
  view.setUint32(offset, centralOffset, true); offset += 4;
  view.setUint16(offset, 0, true);
  return result;
}

function make3mfBytes(geometry) {
  const source = geometry.index ? geometry.toNonIndexed() : geometry;
  const positions = source.attributes.position;
  const count = positions.count;
  const vertices = [];
  const triangles = [];
  for (let i = 0; i < count; i += 1) {
    vertices.push(
      `<vertex x="${positions.getX(i).toFixed(4)}" y="${positions.getY(i).toFixed(4)}" z="${positions.getZ(i).toFixed(4)}"/>`
    );
  }
  for (let i = 0; i < count; i += 3) {
    triangles.push(`<triangle v1="${i}" v2="${i + 1}" v3="${i + 2}"/>`);
  }
  if (source !== geometry) source.dispose();

  const model = `<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter" xml:lang="zh-CN" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
  <metadata name="Title">${TEMPLATES[state.key].name}</metadata>
  <resources>
    <object id="1" type="model">
      <mesh>
        <vertices>${vertices.join('')}</vertices>
        <triangles>${triangles.join('')}</triangles>
      </mesh>
    </object>
  </resources>
  <build><item objectid="1"/></build>
</model>`;
  const contentTypes = `<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
</Types>`;
  const rels = `<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>`;
  const encoder = new TextEncoder();
  return zipStore([
    { name: '[Content_Types].xml', data: encoder.encode(contentTypes) },
    { name: '_rels/.rels', data: encoder.encode(rels) },
    { name: '3D/3dmodel.model', data: encoder.encode(model) }
  ]);
}

function exportBytes(format) {
  if (!state.geometry) throw new Error('模型还没有准备好');
  return format === '3mf' ? make3mfBytes(state.geometry) : makeStlBytes(state.geometry);
}

function download(format) {
  try {
    const bytes = exportBytes(format);
    const blob = new Blob([bytes], {
      type: format === '3mf' ? 'model/3mf' : 'model/stl'
    });
    const stamp = new Date().toISOString().slice(0, 10).replaceAll('-', '');
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = `${TEMPLATES[state.key].slug}-${stamp}.${format}`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(link.href), 2000);
    setStatus(`${format.toUpperCase()} 已生成，正在下载。`, 'info');
  } catch (error) {
    setStatus(`导出失败：${error.message || error}`, 'err');
  }
}

function templateLabel(key) {
  return TEMPLATES[key] ? TEMPLATES[key].name : '未知模板';
}

function restoreDesign(item) {
  const data = item && item.payload ? item.payload : {};
  const model = data.model;
  if (!TEMPLATES[model] || !data.params) {
    setStatus('这条参数记录已经无法恢复。', 'err');
    return;
  }
  state.key = model;
  state.values[model] = { ...cloneDefaults(model), ...data.params };
  if (model === 'custom') {
    state.values[model].outline = Array.isArray(data.params.outline)
      ? data.params.outline.map((point) => [Number(point[0]), Number(point[1])]) : [];
    state.values[model].holes = Array.isArray(data.params.holes)
      ? data.params.holes.map((hole) => ({
        x: Number(hole.x), y: Number(hole.y), r: Number(hole.r)
      })) : [];
    state.values[model].closed = data.params.closed !== false;
  }
  state.color = data.color || '#55b8aa';
  colorEl.value = state.color;
  designNameEl.value = item.title || '';
  document.querySelectorAll('.tpl').forEach((button) => {
    button.classList.toggle('on', button.dataset.template === model);
  });
  renderFields();
  sketchHistory = [];
  if (model === 'custom') {
    setPane('sketch');
    drawSketch();
    if (state.values.custom.closed) createModel();
    else clearSceneModel();
  } else {
    setPane('model');
    createModel();
  }
  setStatus(`已恢复「${item.title || '未命名参数'}」。`, 'info');
  document.querySelector('.panel').scrollTo({ top: 0, behavior: 'smooth' });
}

function renderSaved() {
  savedListEl.innerHTML = '';
  savedCountEl.textContent = savedItems.length ? `${savedItems.length} 条` : '';
  if (!savedItems.length) {
    const empty = document.createElement('div');
    empty.className = 'saved-empty';
    empty.textContent = '还没有保存的参数。调整好以后点上面的「保存」。';
    savedListEl.appendChild(empty);
    return;
  }
  savedItems.forEach((item) => {
    const data = item.payload || {};
    const row = document.createElement('div');
    row.className = 'saved-item';

    const restore = document.createElement('button');
    restore.type = 'button';
    restore.className = 'saved-restore';
    const title = document.createElement('b');
    title.textContent = item.title || '未命名参数';
    const meta = document.createElement('span');
    meta.textContent = `${templateLabel(data.model)} · ${String(item.created_at || '').slice(5, 16)}`;
    restore.appendChild(title);
    restore.appendChild(meta);
    restore.addEventListener('click', () => restoreDesign(item));

    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'saved-del';
    remove.textContent = '×';
    remove.title = '删除这组参数';
    remove.setAttribute('aria-label', `删除 ${item.title || '未命名参数'}`);
    remove.addEventListener('click', async () => {
      if (!window.confirm(`删除「${item.title || '未命名参数'}」？`)) return;
      try {
        const response = await fetch(`/3d-lab/api/designs/${item.id}`, { method: 'DELETE' });
        const result = await response.json();
        if (!response.ok || !result.ok) throw new Error(result.message || '删除失败');
        savedItems = savedItems.filter((saved) => saved.id !== item.id);
        renderSaved();
        setStatus('参数记录已删除。', 'info');
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
    setStatus(`读取参数记录失败：${error.message || error}`, 'err');
  }
}

async function saveDesign() {
  if (!state.geometry || (state.key === 'custom' && !state.values.custom.closed)) {
    setStatus('请先完成并闭合 3D 轮廓再保存。', 'err');
    return;
  }
  saveDesignBtn.disabled = true;
  try {
    const response = await fetch('/3d-lab/api/designs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: designNameEl.value.trim(),
        model: state.key,
        params: state.values[state.key],
        color: state.color
      })
    });
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.message || '保存失败');
    await loadDesigns();
    setStatus('参数已保存到作品库，换设备登录也能恢复。', 'info');
  } catch (error) {
    setStatus(`保存失败：${error.message || error}`, 'err');
  } finally {
    saveDesignBtn.disabled = false;
  }
}

function initScene() {
  scene = new THREE.Scene();
  camera = new THREE.PerspectiveCamera(36, 1, 0.1, 10000);
  camera.position.set(220, 170, 250);

  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  host.appendChild(renderer.domElement);

  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.075;
  controls.minDistance = 30;
  controls.maxDistance = 1800;
  controls.maxPolarAngle = Math.PI * 0.92;
  controls.target.set(0, 35, 0);

  const hemi = new THREE.HemisphereLight(0xffffff, 0x55716e, 2.2);
  scene.add(hemi);
  const keyLight = new THREE.DirectionalLight(0xffffff, 2.8);
  keyLight.position.set(180, 300, 220);
  keyLight.castShadow = true;
  keyLight.shadow.mapSize.set(1024, 1024);
  keyLight.shadow.camera.left = -300;
  keyLight.shadow.camera.right = 300;
  keyLight.shadow.camera.top = 300;
  keyLight.shadow.camera.bottom = -300;
  scene.add(keyLight);
  const fill = new THREE.DirectionalLight(0x9be9df, 1.0);
  fill.position.set(-220, 120, -160);
  scene.add(fill);

  const resize = () => {
    resizeModelViewport();
  };
  new ResizeObserver(resize).observe(host);
  new ResizeObserver(resizeSketchCanvas).observe(sketchCanvas);
  resize();

  renderer.setAnimationLoop(() => {
    controls.update();
    renderer.render(scene, camera);
  });
}

function bindUI() {
  document.querySelectorAll('.tpl').forEach((button) => {
    button.addEventListener('click', () => selectTemplate(button.dataset.template));
  });
  document.querySelectorAll('.vbtn').forEach((button) => {
    button.addEventListener('click', () => {
      currentView = button.dataset.view;
      document.querySelectorAll('.vbtn').forEach((item) => {
        item.classList.toggle('on', item === button);
      });
      fitView(currentView);
    });
  });
  paneSketchBtn.addEventListener('click', () => setPane('sketch'));
  paneModelBtn.addEventListener('click', () => setPane('model'));
  document.getElementById('sketchOutline').addEventListener('click', () => setSketchMode('outline'));
  document.getElementById('sketchHole').addEventListener('click', () => setSketchMode('hole'));
  document.getElementById('closeOutline').addEventListener('click', closeCustomOutline);
  document.getElementById('undoSketch').addEventListener('click', undoCustomSketch);
  document.getElementById('clearSketch').addEventListener('click', clearCustomSketch);
  sketchCanvas.addEventListener('pointerdown', handleSketchPointerDown);
  sketchCanvas.addEventListener('pointermove', handleSketchPointerMove);
  sketchCanvas.addEventListener('pointerup', handleSketchPointerUp);
  sketchCanvas.addEventListener('pointercancel', handleSketchPointerUp);
  sketchCanvas.addEventListener('contextmenu', (event) => {
    event.preventDefault();
    if (state.key === 'custom' && currentPane === 'sketch') undoCustomSketch();
  });
  colorEl.addEventListener('input', () => {
    state.color = colorEl.value;
    if (modelGroup) {
      const mesh = modelGroup.children.find((child) => child.isMesh);
      if (mesh) mesh.material.color.set(state.color);
    }
  });
  document.getElementById('downloadStl').addEventListener('click', () => download('stl'));
  document.getElementById('download3mf').addEventListener('click', () => download('3mf'));
  saveDesignBtn.addEventListener('click', saveDesign);
  document.getElementById('resetParams').addEventListener('click', () => {
    state.values[state.key] = cloneDefaults(state.key);
    sketchHistory = [];
    renderFields();
    currentView = 'iso';
    if (state.key === 'custom') {
      setPane('sketch');
      drawSketch();
    } else {
      setPane('model');
    }
    createModel();
  });
  new MutationObserver(() => {
    if (!state.geometry) return;
    updateGrid(state.geometry.boundingBox);
    if (modelGroup) {
      const edges = modelGroup.children.find((child) => child.isLineSegments);
      if (edges) {
        edges.material.color.set(
          document.documentElement.getAttribute('data-theme') === 'dark'
            ? 0xd8fff9 : 0x1f4b47
        );
      }
    }
    if (currentPane === 'sketch') drawSketch();
  }).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
}

async function init() {
  try {
    initScene();
    bindUI();
    renderFields();
    createModel();
    loadingEl.hidden = true;
    loadDesigns();
    window.TB3D = {
      templates: Object.keys(TEMPLATES),
      state,
      rebuild: createModel,
      exportBytes,
      setTemplate: selectTemplate,
      setPane,
      drawSketch,
      closeOutline: closeCustomOutline
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
