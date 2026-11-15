// 2D 草图编辑器：缩放/平移、吸附、线/圆弧、圆孔、多边形孔、圆角、撤销重做、SVG 导入。
//
// 草图数据：
//   { outlines: [{ id, role:'outer'|'hole', closed, vertices:[[x,y],...],
//                  segments:[{type:'line'} | {type:'arc', cx, cy, r, clockwise}] }],
//     circles: [{ id, x, y, r }] }
// segments[i] 连接 vertices[i] -> vertices[(i+1)%n]。

import {
  clamp,
  round2,
  uid,
  distance,
  flattenOutline,
  flattenCircle,
  pointInPolygon,
  polygonArea,
  polylineLength,
  outlineSelfIntersects,
  nearestPointOnSegment,
  filletCorner,
  isArcEndpoint,
  sketchBounds,
  snapToGrid,
  normalizeSegments,
  edgeCountOf,
  MAX_COORD
} from './geomath.js';
import {
  normalizeSketch,
  emptySketch,
  sketchHasClosedOuter,
  MAX_OUTLINES,
  MAX_VERTICES
} from './document.js';

const MIN_SCALE = 0.4;
const MAX_SCALE = 80;
const HIT_PX = 9;
const VERTEX_HIT_PX = 10;
const MAX_HISTORY = 60;
const SVG_NS = 'http://www.w3.org/2000/svg';

function numberOrNull(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function findOutline(sketch, outlineId) {
  return (sketch.outlines || []).find((outline) => outline.id === outlineId) || null;
}

function findCircle(sketch, circleId) {
  return (sketch.circles || []).find((circle) => circle.id === circleId) || null;
}

export function createSketchEditor({ layer, canvas, getColor, onCommit, onStatus }) {
  const ctx = canvas.getContext('2d');
  const state = {
    sketch: emptySketch(),
    tool: 'outline',
    snap: true,
    gridSize: 5,
    selection: null,
    hover: null,
    activeOutlineId: null,
    drag: null,
    draft: null,
    pinch: null,
    pointers: new Map(),
    spaceDown: false,
    history: [],
    future: [],
    view: { scale: 3, ox: 0, oy: 0, ready: false },
    lastHoverScreen: null
  };

  const els = {
    hint: document.getElementById('sketchHint'),
    position: document.getElementById('sketchPosition'),
    props: document.getElementById('sketchProps'),
    propTitle: document.getElementById('propTitle'),
    propVertexRow: document.getElementById('propVertexRow'),
    propCircleRow: document.getElementById('propCircleRow'),
    propOutlineRow: document.getElementById('propOutlineRow'),
    propX: document.getElementById('propX'),
    propY: document.getElementById('propY'),
    propCX: document.getElementById('propCX'),
    propCY: document.getElementById('propCY'),
    propCR: document.getElementById('propCR'),
    propRole: document.getElementById('propRole'),
    propMeta: document.getElementById('propMeta'),
    propNote: document.getElementById('propNote'),
    propDelete: document.getElementById('propDelete'),
    propClose: document.getElementById('propClose'),
    propApply: document.getElementById('propApply'),
    closeOutline: document.getElementById('closeOutline'),
    deleteItem: document.getElementById('deleteSketchItem'),
    undo: document.getElementById('undoSketch'),
    redo: document.getElementById('redoSketch'),
    clear: document.getElementById('clearSketch'),
    snapToggle: document.getElementById('snapToggle'),
    gridSize: document.getElementById('gridSize'),
    fit: document.getElementById('fitSketch'),
    importSvg: document.getElementById('importSvg'),
    svgFile: document.getElementById('svgFile'),
    filletRadius: document.getElementById('filletRadius'),
    tools: {
      select: document.getElementById('sketchSelect'),
      outline: document.getElementById('sketchOutline'),
      hole: document.getElementById('sketchHole'),
      circle: document.getElementById('sketchCircle'),
      fillet: document.getElementById('sketchFillet')
    }
  };

  function emitStatus(text, kind = 'info') {
    if (typeof onStatus === 'function') onStatus(text, kind);
  }

  function rect() {
    return canvas.getBoundingClientRect();
  }

  function canvasWidth() {
    return Math.max(1, canvas.clientWidth || rect().width);
  }

  function canvasHeight() {
    return Math.max(1, canvas.clientHeight || rect().height);
  }

  function worldToScreen(point) {
    return [
      state.view.ox + Number(point[0]) * state.view.scale,
      state.view.oy - Number(point[1]) * state.view.scale
    ];
  }

  function screenToWorld(x, y) {
    return [
      (Number(x) - state.view.ox) / state.view.scale,
      (state.view.oy - Number(y)) / state.view.scale
    ];
  }

  function localPoint(event) {
    const box = rect();
    return [event.clientX - box.left, event.clientY - box.top];
  }

  function fitView() {
    const width = canvasWidth();
    const height = canvasHeight();
    if (width < 20 || height < 20) {
      // 画布还没显示出来（例如当前在 3D 预览页），等显示后再自适应。
      state.view.ready = false;
      return;
    }
    const bounds = sketchBounds(state.sketch);
    const padding = 38;
    const availableWidth = Math.max(40, width - padding * 2);
    const availableHeight = Math.max(40, height - padding * 2);
    const scale = clamp(
      Math.min(
        availableWidth / Math.max(bounds.width, 10),
        availableHeight / Math.max(bounds.height, 10)
      ),
      MIN_SCALE,
      MAX_SCALE
    );
    state.view.scale = scale;
    state.view.ox = width / 2 - bounds.cx * scale;
    state.view.oy = height / 2 + bounds.cy * scale;
    state.view.width = width;
    state.view.height = height;
    state.view.ready = true;
    draw();
  }

  function resize() {
    const box = rect();
    if (box.width < 2 || box.height < 2) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const width = Math.round(box.width * dpr);
    const height = Math.round(box.height * dpr);
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (!state.view.ready) {
      fitView();
      return;
    }
    if (state.view.width && (state.view.width !== box.width || state.view.height !== box.height)) {
      // 画布尺寸变化时保持当前视野中心，不打断用户正在看的位置。
      state.view.ox += (box.width - state.view.width) / 2;
      state.view.oy += (box.height - state.view.height) / 2;
      state.view.width = box.width;
      state.view.height = box.height;
    }
    draw();
  }

  // ---------------------------------------------------------
  // 撤销 / 重做
  // ---------------------------------------------------------
  function snapshot() {
    return JSON.stringify({
      outlines: state.sketch.outlines,
      circles: state.sketch.circles,
      activeOutlineId: state.activeOutlineId
    });
  }

  function restore(snapshotText) {
    const parsed = JSON.parse(snapshotText);
    state.sketch.outlines = parsed.outlines || [];
    state.sketch.circles = parsed.circles || [];
    state.activeOutlineId = parsed.activeOutlineId || null;
    if (state.activeOutlineId && !findOutline(state.sketch, state.activeOutlineId)) {
      state.activeOutlineId = null;
    }
    state.selection = null;
    state.drag = null;
    state.draft = null;
  }

  function pushHistory() {
    state.history.push(snapshot());
    if (state.history.length > MAX_HISTORY) state.history.shift();
    state.future.length = 0;
    updateHistoryButtons();
  }

  function updateHistoryButtons() {
    if (els.undo) els.undo.disabled = !state.history.length;
    if (els.redo) els.redo.disabled = !state.future.length;
  }

  function ensureDragHistory(drag) {
    if (!drag || drag.historyPushed) return;
    pushHistory();
    drag.historyPushed = true;
  }

  function afterMutation({ rebuild = true } = {}) {
    state.drag = null;
    state.draft = null;
    updateProps();
    updateHistoryButtons();
    draw();
    if (rebuild && typeof onCommit === 'function') onCommit();
  }

  function undo() {
    if (!state.history.length) {
      emitStatus('没有可以撤回的操作。', 'err');
      return;
    }
    state.future.push(snapshot());
    restore(state.history.pop());
    afterMutation();
    emitStatus('已撤回上一步。');
  }

  function redo() {
    if (!state.future.length) {
      emitStatus('没有可以重做的操作。', 'err');
      return;
    }
    state.history.push(snapshot());
    restore(state.future.pop());
    afterMutation();
    emitStatus('已重做。');
  }

  // ---------------------------------------------------------
  // 绘制
  // ---------------------------------------------------------
  function drawTolerance() {
    return clamp(0.28 / state.view.scale, 0.01, 0.08);
  }

  function containedOuters() {
    return state.sketch.outlines
      .filter((outline) => outline.closed && outline.role === 'outer')
      .map((outline) => flattenOutline(outline, drawTolerance()));
  }

  function drawGrid(width, height) {
    const min = screenToWorld(0, height);
    const max = screenToWorld(width, 0);
    let step = Math.max(0.1, state.gridSize);
    while (step * state.view.scale < 7) step *= 2;

    const startX = Math.floor(min[0] / step);
    const endX = Math.ceil(max[0] / step);
    const startY = Math.floor(min[1] / step);
    const endY = Math.ceil(max[1] / step);

    ctx.lineWidth = 1;
    ctx.globalAlpha = 0.38;
    ctx.strokeStyle = getComputedStyle(document.documentElement)
      .getPropertyValue('--line').trim() || '#dfe7ea';
    ctx.beginPath();
    for (let i = startX; i <= endX; i += 1) {
      const x = i * step;
      const sx = state.view.ox + x * state.view.scale;
      ctx.moveTo(sx, 0);
      ctx.lineTo(sx, height);
    }
    for (let i = startY; i <= endY; i += 1) {
      const y = i * step;
      const sy = state.view.oy - y * state.view.scale;
      ctx.moveTo(0, sy);
      ctx.lineTo(width, sy);
    }
    ctx.stroke();

    // 坐标轴
    ctx.globalAlpha = 0.72;
    ctx.strokeStyle = getComputedStyle(document.documentElement)
      .getPropertyValue('--acc').trim() || '#0f9f8f';
    ctx.beginPath();
    const origin = worldToScreen([0, 0]);
    if (origin[0] >= 0 && origin[0] <= width) {
      ctx.moveTo(origin[0], 0);
      ctx.lineTo(origin[0], height);
    }
    if (origin[1] >= 0 && origin[1] <= height) {
      ctx.moveTo(0, origin[1]);
      ctx.lineTo(width, origin[1]);
    }
    ctx.stroke();
    ctx.globalAlpha = 1;
  }

  function tracePolyline(points, closed) {
    if (!points.length) return;
    const first = worldToScreen(points[0]);
    ctx.moveTo(first[0], first[1]);
    for (let i = 1; i < points.length; i += 1) {
      const point = worldToScreen(points[i]);
      ctx.lineTo(point[0], point[1]);
    }
    if (closed) ctx.closePath();
  }

  function draw() {
    const width = canvasWidth();
    const height = canvasHeight();
    if (width < 2 || height < 2) return;
    const styles = getComputedStyle(document.documentElement);
    const lineColor = styles.getPropertyValue('--line').trim() || '#dfe7ea';
    const mutedColor = styles.getPropertyValue('--muted').trim() || '#7d899e';
    const accent = styles.getPropertyValue('--acc').trim() || '#0f9f8f';
    const warn = '#d9822b';
    const color = typeof getColor === 'function' ? (getColor() || '#55b8aa') : '#55b8aa';

    ctx.clearRect(0, 0, width, height);
    drawGrid(width, height);

    const tolerance = drawTolerance();
    const outers = containedOuters();

    // 外形填充 + 孔洞镂空（evenodd）
    ctx.beginPath();
    for (const points of outers) tracePolyline(points, true);
    for (const outline of state.sketch.outlines) {
      if (!outline.closed || outline.role !== 'hole') continue;
      const points = flattenOutline(outline, tolerance);
      if (points.length >= 3 && outers.some((outer) => pointInPolygon(points[0], outer))) {
        tracePolyline(points, true);
      }
    }
    for (const circle of state.sketch.circles) {
      const points = flattenCircle(circle, tolerance);
      if (points.length >= 3 && outers.some((outer) => pointInPolygon([circle.x, circle.y], outer))) {
        tracePolyline(points, true);
      }
    }
    ctx.fillStyle = color + '30';
    ctx.fill('evenodd');

    // 轮廓线
    for (const outline of state.sketch.outlines) {
      const points = flattenOutline(outline, tolerance);
      if (points.length < 2) continue;
      const isSelected = state.selection &&
        (state.selection.kind === 'outline' || state.selection.kind === 'vertex') &&
        state.selection.outlineId === outline.id;
      ctx.beginPath();
      tracePolyline(points, outline.closed);
      ctx.strokeStyle = outline.role === 'hole' ? warn : accent;
      ctx.lineWidth = isSelected ? 2.8 : 1.8;
      if (!outline.closed) ctx.setLineDash([6, 4]);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // 圆孔
    for (const circle of state.sketch.circles) {
      const selected = state.selection?.kind === 'circle' && state.selection.circleId === circle.id;
      const center = worldToScreen([circle.x, circle.y]);
      const radius = Math.max(2, circle.r * state.view.scale);
      ctx.beginPath();
      ctx.arc(center[0], center[1], radius, 0, Math.PI * 2);
      ctx.strokeStyle = selected ? accent : warn;
      ctx.lineWidth = selected ? 2.8 : 1.8;
      ctx.stroke();

      // 圆心 + 半径手柄
      ctx.beginPath();
      ctx.arc(center[0], center[1], 2.2, 0, Math.PI * 2);
      ctx.fillStyle = mutedColor;
      ctx.fill();
      const handle = worldToScreen([circle.x + circle.r, circle.y]);
      ctx.beginPath();
      ctx.arc(handle[0], handle[1], 3.5, 0, Math.PI * 2);
      ctx.fillStyle = accent;
      ctx.fill();
    }

    // 顶点
    for (const outline of state.sketch.outlines) {
      outline.vertices.forEach((vertex, index) => {
        const screen = worldToScreen(vertex);
        const selected = state.selection?.kind === 'vertex' &&
          state.selection.outlineId === outline.id && state.selection.index === index;
        const hovered = state.hover?.kind === 'vertex' &&
          state.hover.outlineId === outline.id && state.hover.index === index;
        const locked = isArcEndpoint(outline, index);
        const firstOpen = !outline.closed && index === 0;
        const radius = selected ? 6.5 : (firstOpen ? 5.5 : 4);
        ctx.beginPath();
        ctx.arc(screen[0], screen[1], radius, 0, Math.PI * 2);
        ctx.fillStyle = selected || hovered ? accent : (locked ? '#ffffff' : '#ffffff');
        ctx.fill();
        ctx.strokeStyle = selected || hovered ? accent : (locked ? mutedColor : accent);
        ctx.lineWidth = selected ? 2.5 : 1.5;
        ctx.stroke();
        if (locked) {
          ctx.beginPath();
          ctx.arc(screen[0], screen[1], radius + 2.5, 0, Math.PI * 2);
          ctx.strokeStyle = mutedColor;
          ctx.lineWidth = 1;
          ctx.stroke();
        }
      });
    }

    // 正在拖出的圆孔
    if (state.draft?.kind === 'circle') {
      const center = worldToScreen([state.draft.x, state.draft.y]);
      const radius = Math.max(2, state.draft.r * state.view.scale);
      ctx.beginPath();
      ctx.arc(center[0], center[1], radius, 0, Math.PI * 2);
      ctx.setLineDash([5, 4]);
      ctx.strokeStyle = accent;
      ctx.lineWidth = 1.6;
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // 吸附/悬停准星
    if (state.hover?.kind === 'snap' && state.hover.point) {
      const screen = worldToScreen(state.hover.point);
      ctx.beginPath();
      ctx.moveTo(screen[0] - 7, screen[1]);
      ctx.lineTo(screen[0] + 7, screen[1]);
      ctx.moveTo(screen[0], screen[1] - 7);
      ctx.lineTo(screen[0], screen[1] + 7);
      ctx.strokeStyle = accent;
      ctx.lineWidth = 1;
      ctx.stroke();
    }

    // 选中轮廓的面积/长度提示
    if (state.selection?.kind === 'outline') {
      const outline = findOutline(state.sketch, state.selection.outlineId);
      if (outline) {
        const points = flattenOutline(outline, tolerance);
        const text = outline.closed
          ? `${polygonArea(points).toFixed(1)} mm²`
          : `${polylineLength(points, false).toFixed(1)} mm`;
        drawLabel(text, points.length ? worldToScreen(points[0]) : [12, 12]);
      }
    }
    if (state.selection?.kind === 'vertex') {
      const outline = findOutline(state.sketch, state.selection.outlineId);
      if (outline) {
        const vertex = outline.vertices[state.selection.index];
        if (vertex) drawLabel(`X ${round2(vertex[0])} · Y ${round2(vertex[1])}`, worldToScreen(vertex));
      }
    }
    if (state.selection?.kind === 'circle') {
      const circle = findCircle(state.sketch, state.selection.circleId);
      if (circle) {
        drawLabel(`R ${round2(circle.r)} mm`, worldToScreen([circle.x + circle.r, circle.y]));
      }
    }

    // 未闭合提示
    if (state.tool !== 'select' && (state.tool === 'outline' || state.tool === 'hole')) {
      const active = state.activeOutlineId ? findOutline(state.sketch, state.activeOutlineId) : null;
      if (active && !active.closed && active.vertices.length >= 3) {
        const first = worldToScreen(active.vertices[0]);
        ctx.beginPath();
        ctx.arc(first[0], first[1], 9, 0, Math.PI * 2);
        ctx.setLineDash([3, 3]);
        ctx.strokeStyle = accent;
        ctx.lineWidth = 1.5;
        ctx.stroke();
        ctx.setLineDash([]);
      }
    }

    ctx.lineWidth = 1;
    ctx.globalAlpha = 1;
  }

  function drawLabel(text, screen) {
    const styles = getComputedStyle(document.documentElement);
    const card = styles.getPropertyValue('--card').trim() || '#ffffff';
    const ink = styles.getPropertyValue('--ink').trim() || '#182337';
    ctx.font = '11px -apple-system,"PingFang SC","Microsoft YaHei",sans-serif';
    const width = ctx.measureText(text).width + 10;
    const x = clamp(screen[0] + 10, 4, canvasWidth() - width - 4);
    const y = clamp(screen[1] - 26, 4, canvasHeight() - 22);
    ctx.globalAlpha = 0.94;
    ctx.fillStyle = card;
    ctx.strokeStyle = styles.getPropertyValue('--line').trim() || '#dfe7ea';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.roundRect(x, y, width, 18, 5);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = ink;
    ctx.globalAlpha = 1;
    ctx.fillText(text, x + 5, y + 13);
  }

  // ---------------------------------------------------------
  // 命中测试 / 吸附
  // ---------------------------------------------------------
  function hitTest(sx, sy) {
    let bestVertex = null;
    for (const outline of state.sketch.outlines) {
      outline.vertices.forEach((vertex, index) => {
        const screen = worldToScreen(vertex);
        const d = Math.hypot(screen[0] - sx, screen[1] - sy);
        if (d <= VERTEX_HIT_PX && (!bestVertex || d < bestVertex.distance)) {
          bestVertex = {
            kind: 'vertex',
            outlineId: outline.id,
            index,
            distance: d
          };
        }
      });
    }
    if (bestVertex) return bestVertex;

    for (const circle of state.sketch.circles) {
      const center = worldToScreen([circle.x, circle.y]);
      const handle = worldToScreen([circle.x + circle.r, circle.y]);
      if (Math.hypot(handle[0] - sx, handle[1] - sy) <= VERTEX_HIT_PX) {
        return { kind: 'circle', circleId: circle.id, part: 'radius' };
      }
      if (Math.hypot(center[0] - sx, center[1] - sy) <= VERTEX_HIT_PX) {
        return { kind: 'circle', circleId: circle.id, part: 'center' };
      }
      const edgeDistance = Math.abs(Math.hypot(center[0] - sx, center[1] - sy) - circle.r * state.view.scale);
      if (edgeDistance <= HIT_PX) {
        return { kind: 'circle', circleId: circle.id, part: 'edge' };
      }
    }

    let bestEdge = null;
    for (const outline of state.sketch.outlines) {
      const points = flattenOutline(outline, drawTolerance());
      if (points.length < 2) continue;
      const edgeCount = outline.closed ? points.length : points.length - 1;
      for (let i = 0; i < edgeCount; i += 1) {
        const a = worldToScreen(points[i]);
        const b = worldToScreen(points[(i + 1) % points.length]);
        const nearest = nearestPointOnSegment([sx, sy], a, b);
        if (nearest.distance <= HIT_PX && (!bestEdge || nearest.distance < bestEdge.distance)) {
          bestEdge = { kind: 'outline', outlineId: outline.id, distance: nearest.distance };
        }
      }
    }
    if (bestEdge) return bestEdge;

    const world = screenToWorld(sx, sy);
    for (const outline of [...state.sketch.outlines].reverse()) {
      if (!outline.closed) continue;
      if (pointInPolygon(world, flattenOutline(outline, drawTolerance()))) {
        return { kind: 'outline', outlineId: outline.id };
      }
    }
    return null;
  }

  function snapPoint(point, {
    anchor = null,
    ortho = false,
    excludeVertex = null,
    excludeCircleId = null
  } = {}) {
    let x = Number(point[0]);
    let y = Number(point[1]);

    if (state.snap) {
      let best = null;
      const screen = worldToScreen([x, y]);
      for (const outline of state.sketch.outlines) {
        outline.vertices.forEach((vertex, index) => {
          if (excludeVertex && outline.id === excludeVertex.outlineId) {
            const count = outline.vertices.length;
            const dragged = excludeVertex.index;
            const adjacent = outline.closed
              ? index === dragged ||
                index === (dragged - 1 + count) % count ||
                index === (dragged + 1) % count
              : Math.abs(index - dragged) <= 1;
            if (adjacent) return;
          }
          const vertexScreen = worldToScreen(vertex);
          const d = Math.hypot(vertexScreen[0] - screen[0], vertexScreen[1] - screen[1]);
          if (d <= VERTEX_HIT_PX && (!best || d < best.distance)) {
            best = { x: vertex[0], y: vertex[1], distance: d };
          }
        });
      }
      for (const circle of state.sketch.circles) {
        if (excludeCircleId && circle.id === excludeCircleId) continue;
        const centerScreen = worldToScreen([circle.x, circle.y]);
        const d = Math.hypot(centerScreen[0] - screen[0], centerScreen[1] - screen[1]);
        if (d <= VERTEX_HIT_PX && (!best || d < best.distance)) {
          best = { x: circle.x, y: circle.y, distance: d };
        }
      }
      if (best) return { point: [best.x, best.y], snap: 'vertex' };
    }

    if (ortho && anchor) {
      const dx = x - Number(anchor[0]);
      const dy = y - Number(anchor[1]);
      if (Math.abs(dx) >= Math.abs(dy)) y = Number(anchor[1]);
      else x = Number(anchor[0]);
    }

    let snap = null;
    if (state.snap) {
      x = snapToGrid(x, state.gridSize);
      y = snapToGrid(y, state.gridSize);
      snap = 'grid';
    }
    return { point: [round2(x), round2(y)], snap };
  }

  // ---------------------------------------------------------
  // 编辑操作
  // ---------------------------------------------------------
  function ensureActiveOutline(role) {
    let outline = state.activeOutlineId ? findOutline(state.sketch, state.activeOutlineId) : null;
    if (outline && (outline.closed || outline.role !== role)) outline = null;
    if (!outline) {
      const open = [...state.sketch.outlines].reverse()
        .find((item) => !item.closed && item.role === role);
      if (open) {
        outline = open;
      } else {
        outline = {
          id: uid(role === 'hole' ? 'hole' : 'outer'),
          role,
          closed: false,
          vertices: [],
          segments: []
        };
        state.sketch.outlines.push(outline);
      }
      state.activeOutlineId = outline.id;
    }
    return outline;
  }

  function addSketchPoint(point, { ortho = false } = {}) {
    const role = state.tool === 'hole' ? 'hole' : 'outer';
    const hasResumable = state.sketch.outlines.some(
      (outline) => !outline.closed && outline.role === role
    );
    if (!hasResumable && state.sketch.outlines.length >= MAX_OUTLINES) {
      emitStatus(`轮廓最多 ${MAX_OUTLINES} 个。`, 'err');
      return;
    }
    const outline = ensureActiveOutline(role);
    if (outline.vertices.length >= MAX_VERTICES) {
      emitStatus(`单个轮廓最多 ${MAX_VERTICES} 个点。`, 'err');
      return;
    }
    const anchor = outline.vertices.length ? outline.vertices[outline.vertices.length - 1] : null;
    const snapped = snapPoint(point, { anchor, ortho });

    if (outline.vertices.length >= 3) {
      const first = worldToScreen(outline.vertices[0]);
      const screen = worldToScreen(snapped.point);
      if (Math.hypot(first[0] - screen[0], first[1] - screen[1]) <= 12) {
        closeActiveOutline();
        return;
      }
    }

    const last = outline.vertices[outline.vertices.length - 1];
    if (last && distance(last, snapped.point) < 1e-6) return;

    pushHistory();
    if (outline.vertices.length > 0) outline.segments.push({ type: 'line' });
    outline.vertices.push(snapped.point);
    afterMutation();
    emitStatus(`已添加第 ${outline.vertices.length} 个点。`);
  }

  function closeActiveOutline() {
    const outline = state.activeOutlineId ? findOutline(state.sketch, state.activeOutlineId) : null;
    if (!outline) {
      emitStatus('请先画一个轮廓。', 'err');
      return;
    }
    if (outline.closed) return;
    if (outline.vertices.length < 3) {
      emitStatus('至少需要 3 个点才能闭合轮廓。', 'err');
      return;
    }
    const points = flattenOutline({ ...outline, closed: true }, drawTolerance());
    if (outlineSelfIntersects(points, true)) {
      emitStatus('轮廓有交叉，请撤回交叉的点再闭合。', 'err');
      return;
    }
    pushHistory();
    outline.closed = true;
    outline.segments = normalizeSegments({ ...outline, closed: true });
    state.activeOutlineId = null;
    afterMutation();
    emitStatus('轮廓已闭合，3D 模型正在更新。');
    if (state.tool === 'outline' || state.tool === 'hole') {
      // 闭合后自动切到选择工具，方便检查/倒圆角。
      setTool('select');
    }
  }

  function addCircle(center, radius) {
    const r = Number(radius);
    if (!Number.isFinite(r) || r < 0.5) return false;
    pushHistory();
    state.sketch.circles.push({
      id: uid('hole'),
      x: round2(center[0]),
      y: round2(center[1]),
      r: round2(clamp(r, 0.5, 200))
    });
    afterMutation();
    emitStatus('圆孔已添加。');
    return true;
  }

  function applyFillet(outlineId, index) {
    const outline = findOutline(state.sketch, outlineId);
    if (!outline) return;
    if (outline.vertices.length >= MAX_VERTICES) {
      emitStatus(`单个轮廓最多 ${MAX_VERTICES} 个点，不能再倒圆角了。`, 'err');
      return;
    }
    const radius = numberOrNull(els.filletRadius?.value) ?? 5;
    const result = filletCorner(outline.vertices, outline.segments, index, radius, outline.closed);
    if (result.error) {
      emitStatus(result.error, 'err');
      return;
    }
    pushHistory();
    outline.vertices = result.vertices;
    outline.segments = result.segments;
    state.selection = { kind: 'outline', outlineId };
    afterMutation();
    emitStatus(`已在顶点 ${index + 1} 倒 R${radius} 圆角。`);
  }

  function deleteSelection() {
    const selection = state.selection;
    if (!selection) {
      emitStatus('请先选中要删除的顶点、圆孔或轮廓。', 'err');
      return;
    }
    if (selection.kind === 'circle') {
      const circle = findCircle(state.sketch, selection.circleId);
      if (!circle) return;
      pushHistory();
      state.sketch.circles = state.sketch.circles.filter((item) => item.id !== circle.id);
      state.selection = null;
      afterMutation();
      emitStatus('圆孔已删除。');
      return;
    }
    if (selection.kind === 'outline') {
      const outline = findOutline(state.sketch, selection.outlineId);
      if (!outline) return;
      pushHistory();
      state.sketch.outlines = state.sketch.outlines.filter((item) => item.id !== outline.id);
      if (state.activeOutlineId === outline.id) state.activeOutlineId = null;
      state.selection = null;
      afterMutation();
      emitStatus('轮廓已删除。');
      return;
    }
    if (selection.kind === 'vertex') {
      const outline = findOutline(state.sketch, selection.outlineId);
      if (!outline) return;
      const index = selection.index;
      if (index < 0 || index >= outline.vertices.length) return;
      if (isArcEndpoint(outline, index)) {
        emitStatus('这个顶点连接着圆弧，请先撤销圆角。', 'err');
        return;
      }
      pushHistory();
      const wasClosed = outline.closed;
      if (wasClosed) {
        outline.vertices.splice(index, 1);
        outline.segments.splice(index, 1);
        if (outline.vertices.length < 3) {
          outline.closed = false;
        }
      } else {
        const lastIndex = outline.vertices.length - 1;
        if (index === 0) {
          outline.vertices.splice(0, 1);
          outline.segments.splice(0, 1);
        } else if (index === lastIndex) {
          outline.vertices.splice(index, 1);
          outline.segments.splice(index - 1, 1);
        } else {
          outline.vertices.splice(index, 1);
          outline.segments.splice(index, 1);
        }
      }
      outline.segments = normalizeSegments(outline);
      if (state.activeOutlineId === outline.id && !outline.closed && !outline.vertices.length) {
        state.sketch.outlines = state.sketch.outlines.filter((item) => item.id !== outline.id);
        state.activeOutlineId = null;
      }
      state.selection = null;
      afterMutation();
      emitStatus('顶点已删除。');
    }
  }

  function removeLastPoint() {
    const outline = state.activeOutlineId ? findOutline(state.sketch, state.activeOutlineId) : null;
    if (!outline || outline.closed || !outline.vertices.length) {
      emitStatus('当前没有正在画的轮廓。', 'err');
      return;
    }
    pushHistory();
    outline.vertices.pop();
    if (outline.segments.length >= outline.vertices.length) outline.segments.pop();
    if (!outline.vertices.length) {
      state.sketch.outlines = state.sketch.outlines.filter((item) => item.id !== outline.id);
      state.activeOutlineId = null;
    }
    afterMutation();
    emitStatus('已撤回上一个点。');
  }

  function clearSketch() {
    if (!state.sketch.outlines.length && !state.sketch.circles.length) return;
    if (!window.confirm('清空当前草图？这一步可以用“撤销”恢复。')) return;
    pushHistory();
    state.sketch = emptySketch();
    state.activeOutlineId = null;
    state.selection = null;
    fitView();
    afterMutation();
    emitStatus('草图已清空。');
  }

  function cancelCurrent() {
    if (state.draft) {
      state.draft = null;
      draw();
      return;
    }
    state.selection = null;
    state.drag = null;
    state.pinch = null;
    updateProps();
    draw();
  }

  // ---------------------------------------------------------
  // 属性面板
  // ---------------------------------------------------------
  function hidePropRows() {
    if (els.propVertexRow) els.propVertexRow.hidden = true;
    if (els.propCircleRow) els.propCircleRow.hidden = true;
    if (els.propOutlineRow) els.propOutlineRow.hidden = true;
    if (els.propNote) els.propNote.hidden = true;
  }

  function updateProps() {
    if (!els.props) return;
    const selection = state.selection;
    if (!selection) {
      els.props.hidden = true;
      return;
    }
    els.props.hidden = false;
    hidePropRows();
    if (els.propDelete) els.propDelete.hidden = false;
    if (els.propApply) els.propApply.hidden = false;

    if (selection.kind === 'vertex') {
      const outline = findOutline(state.sketch, selection.outlineId);
      const vertex = outline?.vertices?.[selection.index];
      if (!outline || !vertex) {
        state.selection = null;
        els.props.hidden = true;
        return;
      }
      const locked = isArcEndpoint(outline, selection.index);
      if (els.propTitle) els.propTitle.textContent = `顶点 #${selection.index + 1}`;
      if (els.propVertexRow) els.propVertexRow.hidden = false;
      if (els.propX) {
        els.propX.value = String(round2(vertex[0]));
        els.propX.disabled = locked;
      }
      if (els.propY) {
        els.propY.value = String(round2(vertex[1]));
        els.propY.disabled = locked;
      }
      if (els.propNote) {
        els.propNote.hidden = !locked;
        els.propNote.textContent = locked
          ? '这个顶点连接着圆弧，先撤销圆角才能直接改坐标。'
          : '';
      }
      return;
    }

    if (selection.kind === 'circle') {
      const circle = findCircle(state.sketch, selection.circleId);
      if (!circle) {
        state.selection = null;
        els.props.hidden = true;
        return;
      }
      if (els.propTitle) els.propTitle.textContent = '圆孔';
      if (els.propCircleRow) els.propCircleRow.hidden = false;
      if (els.propCX) els.propCX.value = String(round2(circle.x));
      if (els.propCY) els.propCY.value = String(round2(circle.y));
      if (els.propCR) els.propCR.value = String(round2(circle.r));
      return;
    }

    if (selection.kind === 'outline') {
      const outline = findOutline(state.sketch, selection.outlineId);
      if (!outline) {
        state.selection = null;
        els.props.hidden = true;
        return;
      }
      if (els.propTitle) {
        els.propTitle.textContent = outline.role === 'hole' ? '孔轮廓' : '外轮廓';
      }
      if (els.propOutlineRow) els.propOutlineRow.hidden = false;
      if (els.propRole) els.propRole.value = outline.role;
      if (els.propMeta) {
        const points = flattenOutline(outline, drawTolerance());
        els.propMeta.textContent = outline.closed
          ? `闭合 · ${polygonArea(points).toFixed(1)} mm²`
          : `未闭合 · ${polylineLength(points, false).toFixed(1)} mm`;
      }
      if (els.propNote) {
        els.propNote.hidden = outline.closed;
        els.propNote.textContent = outline.closed ? '' : '点画布上第一个点或「闭合」按钮完成轮廓。';
      }
    }
  }

  function applyVertexInputs() {
    const selection = state.selection;
    if (selection?.kind !== 'vertex') return;
    const outline = findOutline(state.sketch, selection.outlineId);
    if (!outline) return;
    const x = numberOrNull(els.propX?.value);
    const y = numberOrNull(els.propY?.value);
    if (x === null || y === null) {
      updateProps();
      return;
    }
    if (isArcEndpoint(outline, selection.index)) {
      emitStatus('圆弧端点不能直接改坐标。', 'err');
      updateProps();
      return;
    }
    pushHistory();
    outline.vertices[selection.index] = [
      round2(clamp(x, -MAX_COORD, MAX_COORD)),
      round2(clamp(y, -MAX_COORD, MAX_COORD))
    ];
    afterMutation();
  }

  function applyCircleInputs() {
    const selection = state.selection;
    if (selection?.kind !== 'circle') return;
    const circle = findCircle(state.sketch, selection.circleId);
    if (!circle) return;
    const x = numberOrNull(els.propCX?.value);
    const y = numberOrNull(els.propCY?.value);
    const r = numberOrNull(els.propCR?.value);
    if (x === null || y === null || r === null || r < 0.5) {
      emitStatus('圆孔参数不合法。', 'err');
      updateProps();
      return;
    }
    pushHistory();
    circle.x = round2(clamp(x, -MAX_COORD, MAX_COORD));
    circle.y = round2(clamp(y, -MAX_COORD, MAX_COORD));
    circle.r = round2(clamp(r, 0.5, 200));
    afterMutation();
  }

  function applyRole() {
    const selection = state.selection;
    if (selection?.kind !== 'outline') return;
    const outline = findOutline(state.sketch, selection.outlineId);
    if (!outline || !els.propRole) return;
    const role = els.propRole.value === 'hole' ? 'hole' : 'outer';
    if (outline.role === role) return;
    pushHistory();
    outline.role = role;
    afterMutation();
    emitStatus(role === 'hole' ? '已改为孔轮廓。' : '已改为外轮廓。');
  }

  // ---------------------------------------------------------
  // 工具切换
  // ---------------------------------------------------------
  function setTool(tool) {
    if (!['select', 'outline', 'hole', 'circle', 'fillet'].includes(tool)) return;
    state.tool = tool;
    state.drag = null;
    state.draft = null;
    state.selection = null;
    if (tool === 'outline' || tool === 'hole') {
      const role = tool === 'hole' ? 'hole' : 'outer';
      const open = [...state.sketch.outlines].reverse()
        .find((outline) => !outline.closed && outline.role === role);
      state.activeOutlineId = open?.id || null;
    }
    Object.entries(els.tools).forEach(([key, button]) => {
      if (button) button.classList.toggle('on', key === tool);
    });
    if (els.hint) {
      els.hint.textContent = {
        select: '点击选择顶点、圆孔或轮廓；拖动顶点/圆孔可编辑。滚轮缩放，空格/中键平移。',
        outline: '依次点击画外轮廓；点回第一个点或按「闭合」完成。Shift 正交吸附。',
        hole: '依次点击画多边形孔；点回第一个点或按「闭合」完成。',
        circle: '在画布上按下并拖动，松开即可添加圆孔。',
        fillet: '点击一个顶点，按工具栏里的半径倒圆角。'
      }[tool];
    }
    canvas.style.cursor = tool === 'select' ? 'default' : 'crosshair';
    updateProps();
    draw();
  }

  // ---------------------------------------------------------
  // 指针 / 滚轮 / 键盘
  // ---------------------------------------------------------
  function beginPan(event) {
    state.drag = {
      kind: 'pan',
      startX: event.clientX,
      startY: event.clientY,
      ox: state.view.ox,
      oy: state.view.oy
    };
    canvas.style.cursor = 'grabbing';
  }

  function beginPinch() {
    const points = [...state.pointers.values()];
    if (points.length < 2) return;
    const [p1, p2] = points;
    const box = rect();
    const mid = { x: (p1.x + p2.x) / 2 - box.left, y: (p1.y + p2.y) / 2 - box.top };
    state.pinch = {
      distance: Math.max(1, Math.hypot(p2.x - p1.x, p2.y - p1.y)),
      scale: state.view.scale,
      worldMid: screenToWorld(mid.x, mid.y)
    };
    if (state.drag?.historyPushed && state.history.length) {
      restore(state.history.pop());
      updateHistoryButtons();
      updateProps();
    }
    state.drag = null;
    state.draft = null;
    draw();
  }

  function handlePinch() {
    if (!state.pinch) return;
    const points = [...state.pointers.values()];
    if (points.length < 2) return;
    const [p1, p2] = points;
    const box = rect();
    const mid = { x: (p1.x + p2.x) / 2 - box.left, y: (p1.y + p2.y) / 2 - box.top };
    const scale = clamp(
      state.pinch.scale * (Math.hypot(p2.x - p1.x, p2.y - p1.y) / state.pinch.distance),
      MIN_SCALE,
      MAX_SCALE
    );
    state.view.scale = scale;
    state.view.ox = mid.x - state.pinch.worldMid[0] * scale;
    state.view.oy = mid.y + state.pinch.worldMid[1] * scale;
    draw();
  }

  function onPointerDown(event) {
    if (layer.hidden) return;
    if (event.button !== 0 && event.button !== 1) return;
    event.preventDefault();
    canvas.focus({ preventScroll: true });
    state.pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (canvas.setPointerCapture) canvas.setPointerCapture(event.pointerId);

    if (state.pointers.size === 2) {
      beginPinch();
      return;
    }
    if (event.button === 1 || state.spaceDown) {
      beginPan(event);
      return;
    }

    const [sx, sy] = localPoint(event);
    const world = screenToWorld(sx, sy);

    if (state.tool === 'outline' || state.tool === 'hole') {
      addSketchPoint(world, { ortho: event.shiftKey });
      return;
    }

    if (state.tool === 'circle') {
      const snapped = snapPoint(world);
      state.draft = { kind: 'circle', x: snapped.point[0], y: snapped.point[1], r: 0 };
      draw();
      return;
    }

    if (state.tool === 'fillet') {
      const hit = hitTest(sx, sy);
      if (hit?.kind === 'vertex') {
        applyFillet(hit.outlineId, hit.index);
      } else {
        emitStatus('请点击一个轮廓顶点。', 'err');
      }
      return;
    }

    // select
    const hit = hitTest(sx, sy);
    if (!hit) {
      state.selection = null;
      updateProps();
      draw();
      return;
    }
    if (hit.kind === 'vertex') {
      const outline = findOutline(state.sketch, hit.outlineId);
      if (outline && isArcEndpoint(outline, hit.index)) {
        state.selection = { kind: 'vertex', outlineId: hit.outlineId, index: hit.index };
        updateProps();
        draw();
        emitStatus('圆角端点受约束，可撤销圆角后再改。', 'err');
        return;
      }
      state.selection = { kind: 'vertex', outlineId: hit.outlineId, index: hit.index };
      state.drag = {
        kind: 'vertex',
        outlineId: hit.outlineId,
        index: hit.index,
        historyPushed: false
      };
      updateProps();
      draw();
      return;
    }
    if (hit.kind === 'circle') {
      state.selection = { kind: 'circle', circleId: hit.circleId };
      state.drag = {
        kind: hit.part === 'radius' ? 'circle-radius' : 'circle-center',
        circleId: hit.circleId,
        historyPushed: false
      };
      updateProps();
      draw();
      return;
    }
    state.selection = { kind: 'outline', outlineId: hit.outlineId };
    updateProps();
    draw();
  }

  function onPointerMove(event) {
    if (layer.hidden) return;
    if (state.pointers.has(event.pointerId)) {
      state.pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    }
    if (state.pinch) {
      handlePinch();
      return;
    }

    const [sx, sy] = localPoint(event);
    const world = screenToWorld(sx, sy);
    if (els.position) {
      els.position.textContent = `X ${round2(world[0])} · Y ${round2(world[1])} mm`;
    }

    if (state.drag?.kind === 'pan') {
      state.view.ox = state.drag.ox + (event.clientX - state.drag.startX);
      state.view.oy = state.drag.oy + (event.clientY - state.drag.startY);
      draw();
      return;
    }

    if (state.drag?.kind === 'vertex') {
      const outline = findOutline(state.sketch, state.drag.outlineId);
      if (!outline) return;
      ensureDragHistory(state.drag);
      const previousIndex = (state.drag.index - 1 + outline.vertices.length) % outline.vertices.length;
      const anchor = outline.closed || state.drag.index > 0
        ? outline.vertices[previousIndex]
        : null;
      const snapped = snapPoint(world, {
        anchor,
        ortho: event.shiftKey,
        excludeVertex: { outlineId: state.drag.outlineId, index: state.drag.index }
      });
      outline.vertices[state.drag.index] = snapped.point;
      updateProps();
      draw();
      return;
    }

    if (state.drag?.kind === 'circle-center') {
      const circle = findCircle(state.sketch, state.drag.circleId);
      if (!circle) return;
      ensureDragHistory(state.drag);
      const snapped = snapPoint(world, { excludeCircleId: state.drag.circleId });
      circle.x = snapped.point[0];
      circle.y = snapped.point[1];
      updateProps();
      draw();
      return;
    }

    if (state.drag?.kind === 'circle-radius') {
      const circle = findCircle(state.sketch, state.drag.circleId);
      if (!circle) return;
      ensureDragHistory(state.drag);
      circle.r = round2(clamp(distance([circle.x, circle.y], world), 0.5, 200));
      updateProps();
      draw();
      return;
    }

    if (state.draft?.kind === 'circle') {
      state.draft.r = round2(clamp(distance([state.draft.x, state.draft.y], world), 0, 200));
      draw();
      return;
    }

    // hover / 吸附提示
    if (state.lastHoverScreen) {
      const moved = Math.hypot(state.lastHoverScreen[0] - sx, state.lastHoverScreen[1] - sy);
      if (moved < 2) return;
    }
    state.lastHoverScreen = [sx, sy];
    const hit = hitTest(sx, sy);
    if (hit) {
      state.hover = hit;
    } else if (state.tool === 'outline' || state.tool === 'hole' || state.tool === 'circle') {
      const snapped = snapPoint(world);
      state.hover = snapped.snap ? { kind: 'snap', point: snapped.point } : null;
    } else {
      state.hover = null;
    }
    draw();
  }

  function onPointerUp(event) {
    if (layer.hidden) return;
    state.pointers.delete(event.pointerId);
    if (state.pinch && state.pointers.size < 2) {
      state.pinch = null;
      canvas.style.cursor = state.tool === 'select' ? 'default' : 'crosshair';
      draw();
      return;
    }
    if (state.drag) {
      const drag = state.drag;
      state.drag = null;
      canvas.style.cursor = state.tool === 'select' ? 'default' : 'crosshair';
      if (drag.kind === 'vertex' || drag.kind === 'circle-center' || drag.kind === 'circle-radius') {
        if (drag.historyPushed) afterMutation();
        else {
          updateProps();
          draw();
        }
      }
      return;
    }
    if (state.draft?.kind === 'circle') {
      const draft = state.draft;
      state.draft = null;
      if (draft.r >= 0.5) {
        addCircle([draft.x, draft.y], draft.r);
      } else {
        updateHistoryButtons();
        draw();
      }
    }
  }

  function onPointerCancel(event) {
    state.pointers.delete(event.pointerId);
    state.pinch = null;
    const drag = state.drag;
    state.drag = null;
    state.draft = null;
    canvas.style.cursor = state.tool === 'select' ? 'default' : 'crosshair';
    if (drag?.historyPushed && state.history.length) {
      restore(state.history.pop());
      updateHistoryButtons();
      updateProps();
      afterMutation();
    } else {
      draw();
    }
  }

  function onWheel(event) {
    if (layer.hidden) return;
    event.preventDefault();
    const [sx, sy] = localPoint(event);
    const before = screenToWorld(sx, sy);
    const factor = Math.exp(-event.deltaY * 0.0015);
    const scale = clamp(state.view.scale * factor, MIN_SCALE, MAX_SCALE);
    if (Math.abs(scale - state.view.scale) < 1e-9) return;
    state.view.scale = scale;
    state.view.ox = sx - before[0] * scale;
    state.view.oy = sy + before[1] * scale;
    draw();
  }

  function onContextMenu(event) {
    event.preventDefault();
    if (state.tool === 'outline' || state.tool === 'hole') removeLastPoint();
  }

  function onKeyDown(event) {
    if (layer.hidden) return;
    const tag = String(event.target?.tagName || '').toLowerCase();
    const inField = tag === 'input' || tag === 'select' || tag === 'textarea';

    if (event.key === ' ' && !inField) {
      state.spaceDown = true;
      event.preventDefault();
    }
    if ((event.ctrlKey || event.metaKey) && !inField) {
      const key = event.key.toLowerCase();
      if (key === 'z') {
        event.preventDefault();
        if (event.shiftKey) redo();
        else undo();
        return;
      }
      if (key === 'y') {
        event.preventDefault();
        redo();
        return;
      }
    }
    if (inField) return;
    if (event.key === 'Delete' || event.key === 'Backspace') {
      event.preventDefault();
      deleteSelection();
      return;
    }
    if (event.key === 'Escape') {
      cancelCurrent();
      return;
    }
    if (event.key === 'Enter' && (state.tool === 'outline' || state.tool === 'hole')) {
      closeActiveOutline();
    }
  }

  function onKeyUp(event) {
    if (event.key === ' ') state.spaceDown = false;
  }

  // ---------------------------------------------------------
  // SVG 导入
  // ---------------------------------------------------------
  function svgHost() {
    let host = document.getElementById('tbSvgHost');
    if (!host) {
      host = document.createElementNS(SVG_NS, 'svg');
      host.id = 'tbSvgHost';
      host.setAttribute('aria-hidden', 'true');
      host.style.position = 'absolute';
      host.style.width = '0';
      host.style.height = '0';
      host.style.overflow = 'hidden';
      host.style.pointerEvents = 'none';
      document.body.appendChild(host);
    }
    return host;
  }

  function sampleGeometry(element) {
    const host = svgHost();
    const clone = element.cloneNode(true);
    host.appendChild(clone);
    try {
      if (typeof clone.getTotalLength !== 'function') return null;
      const total = clone.getTotalLength();
      if (!Number.isFinite(total) || total <= 0) return null;
      const count = clamp(Math.ceil(total / 1.5), 16, 500);
      const points = [];
      for (let i = 0; i <= count; i += 1) {
        const point = clone.getPointAtLength((total * i) / count);
        points.push([point.x, point.y]);
      }
      return points;
    } finally {
      clone.remove();
    }
  }

  function splitSubpaths(d) {
    const text = String(d || '');
    const starts = [];
    for (let i = 0; i < text.length; i += 1) {
      const char = text[i];
      if (char === 'M' || char === 'm') starts.push(i);
    }
    if (!starts.length) return [];
    return starts.map((start, index) => text.slice(
      start,
      index + 1 < starts.length ? starts[index + 1] : text.length
    ).trim()).filter(Boolean);
  }

  function makeAbsoluteSubpath(d, previousEnd) {
    if (!d) return d;
    const command = d[0];
    if (command !== 'M' && command !== 'm') return d;
    const numbers = d.slice(1).match(/-?\d*\.?\d+(?:e[-+]?\d+)?/gi) || [];
    const dx = Number(numbers[0]) || 0;
    const dy = Number(numbers[1]) || 0;
    const baseX = command === 'm' ? previousEnd[0] : 0;
    const baseY = command === 'm' ? previousEnd[1] : 0;
    const rest = d.slice(1).replace(
      /^\s*-?\d*\.?\d+(?:e[-+]?\d+)?\s*,?\s*-?\d*\.?\d+(?:e[-+]?\d+)?/i,
      ''
    );
    return `M ${baseX + dx} ${baseY + dy} ${rest}`;
  }

  function extractSvgPolylines(svg) {
    const polylines = [];
    const pathElements = [...svg.querySelectorAll('path')];
    if (pathElements.length) {
      let previousEnd = [0, 0];
      for (const pathElement of pathElements) {
        const d = pathElement.getAttribute('d') || '';
        for (const subpath of splitSubpaths(d)) {
          const absolute = makeAbsoluteSubpath(subpath, previousEnd);
          const holder = document.createElementNS(SVG_NS, 'path');
          holder.setAttribute('d', absolute);
          const points = sampleGeometry(holder);
          if (points && points.length >= 3) {
            polylines.push(points);
            previousEnd = points[points.length - 1];
          }
        }
      }
      return polylines;
    }

    const selector = 'polygon,polyline,rect,circle,ellipse';
    for (const element of svg.querySelectorAll(selector)) {
      const points = sampleGeometry(element);
      if (points && points.length >= 3) polylines.push(points);
    }
    return polylines;
  }

  function importSvgText(text) {
    const parser = new DOMParser();
    const documentNode = parser.parseFromString(text, 'image/svg+xml');
    if (documentNode.querySelector('parsererror')) throw new Error('SVG 解析失败');
    const svg = documentNode.documentElement;
    if (!svg || svg.tagName.toLowerCase() !== 'svg') throw new Error('不是有效的 SVG 文件');

    const polylines = extractSvgPolylines(svg);
    if (!polylines.length) throw new Error('SVG 里没有找到可用的轮廓');

    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    for (const points of polylines) {
      for (const [x, y] of points) {
        if (x < minX) minX = x;
        if (y < minY) minY = y;
        if (x > maxX) maxX = x;
        if (y > maxY) maxY = y;
      }
    }
    const width = Math.max(maxX - minX, 1e-6);
    const height = Math.max(maxY - minY, 1e-6);
    const maxSize = Math.max(width, height);

    const widthAttr = String(svg.getAttribute('width') || '');
    const heightAttr = String(svg.getAttribute('height') || '');
    const mmWidth = /^\s*([\d.]+)\s*mm\s*$/i.exec(widthAttr);
    const mmHeight = /^\s*([\d.]+)\s*mm\s*$/i.exec(heightAttr);
    const target = clamp(mmWidth ? Number(mmWidth[1]) : (mmHeight ? Number(mmHeight[1]) : 100), 10, 400);
    const factor = target / maxSize;
    const cx = minX + width / 2;
    const cy = minY + height / 2;

    const transformed = polylines.map((points) => points
      .map(([x, y]) => [round2((x - cx) * factor), round2(-(y - cy) * factor)]));

    const outers = [];
    const outlines = [];
    for (const points of transformed) {
      if (points.length < 4) continue;
      if (distance(points[0], points[points.length - 1]) < 1e-6) points.pop();
      if (points.length < 3) continue;
      const role = outers.some((outer) => pointInPolygon(points[0], outer)) ? 'hole' : 'outer';
      if (role === 'outer') outers.push(points);
      outlines.push({ role, points });
    }
    if (!outlines.length) throw new Error('SVG 轮廓太小，无法导入');

    const room = Math.max(0, MAX_OUTLINES - state.sketch.outlines.length);
    const accepted = outlines.slice(0, room);
    if (!accepted.length) throw new Error(`轮廓最多 ${MAX_OUTLINES} 个，请先删掉一些。`);

    const valid = accepted.filter((item) => !outlineSelfIntersects(item.points, true));
    if (!valid.length) throw new Error('导入的轮廓有交叉，已跳过');
    pushHistory();
    for (const item of valid) {
      state.sketch.outlines.push({
        id: uid(item.role),
        role: item.role,
        closed: true,
        vertices: item.points,
        segments: item.points.map(() => ({ type: 'line' }))
      });
    }
    state.sketch = normalizeSketch(state.sketch);
    state.activeOutlineId = null;
    state.selection = null;
    fitView();
    afterMutation();
    emitStatus(`已导入 ${valid.length} 条轮廓（最大边 ${target.toFixed(0)} mm）。`);
  }

  async function importSvgFile(file) {
    if (!file) return;
    try {
      const text = await file.text();
      importSvgText(text);
    } catch (error) {
      emitStatus(`SVG 导入失败：${error.message || error}`, 'err');
    }
  }

  // ---------------------------------------------------------
  // 绑定
  // ---------------------------------------------------------
  Object.entries(els.tools).forEach(([tool, button]) => {
    if (button) button.addEventListener('click', () => setTool(tool));
  });
  if (els.closeOutline) els.closeOutline.addEventListener('click', closeActiveOutline);
  if (els.deleteItem) els.deleteItem.addEventListener('click', deleteSelection);
  if (els.undo) els.undo.addEventListener('click', undo);
  if (els.redo) els.redo.addEventListener('click', redo);
  if (els.clear) els.clear.addEventListener('click', clearSketch);
  if (els.fit) els.fit.addEventListener('click', fitView);
  if (els.snapToggle) {
    els.snapToggle.addEventListener('change', () => {
      state.snap = !!els.snapToggle.checked;
      draw();
    });
  }
  if (els.gridSize) {
    els.gridSize.addEventListener('change', () => {
      state.gridSize = clamp(Number(els.gridSize.value) || 5, 0.5, 50);
      draw();
    });
  }
  if (els.importSvg) {
    els.importSvg.addEventListener('click', () => els.svgFile?.click());
  }
  if (els.svgFile) {
    els.svgFile.addEventListener('change', () => {
      const file = els.svgFile.files?.[0];
      importSvgFile(file);
      els.svgFile.value = '';
    });
  }
  if (els.propDelete) els.propDelete.addEventListener('click', deleteSelection);
  if (els.propClose) {
    els.propClose.addEventListener('click', () => {
      state.selection = null;
      updateProps();
      draw();
    });
  }
  if (els.propApply) els.propApply.addEventListener('click', () => {
    if (state.selection?.kind === 'vertex') applyVertexInputs();
    else if (state.selection?.kind === 'circle') applyCircleInputs();
    else if (state.selection?.kind === 'outline') applyRole();
  });
  if (els.propRole) els.propRole.addEventListener('change', applyRole);
  if (els.propX) els.propX.addEventListener('change', applyVertexInputs);
  if (els.propY) els.propY.addEventListener('change', applyVertexInputs);
  if (els.propCX) els.propCX.addEventListener('change', applyCircleInputs);
  if (els.propCY) els.propCY.addEventListener('change', applyCircleInputs);
  if (els.propCR) els.propCR.addEventListener('change', applyCircleInputs);

  canvas.addEventListener('pointerdown', onPointerDown);
  canvas.addEventListener('pointermove', onPointerMove);
  canvas.addEventListener('pointerup', onPointerUp);
  canvas.addEventListener('pointercancel', onPointerCancel);
  canvas.addEventListener('pointerleave', () => {
    if (!state.drag && !state.draft) {
      state.hover = null;
      draw();
    }
  });
  canvas.addEventListener('wheel', onWheel, { passive: false });
  canvas.addEventListener('contextmenu', onContextMenu);
  window.addEventListener('keydown', onKeyDown);
  window.addEventListener('keyup', onKeyUp);
  window.addEventListener('blur', () => { state.spaceDown = false; });

  const observer = new ResizeObserver(resize);
  observer.observe(canvas);

  setTool('outline');
  updateHistoryButtons();
  updateProps();

  return {
    draw,
    resize,
    fitView,
    setTool,
    setSketch(sketch) {
      state.sketch = normalizeSketch(sketch);
      state.activeOutlineId = null;
      state.selection = null;
      state.hover = null;
      state.drag = null;
      state.draft = null;
      state.history = [];
      state.future = [];
      state.view.ready = false;
      fitView();
      updateHistoryButtons();
      updateProps();
    },
    getSketch() {
      return normalizeSketch(state.sketch);
    },
    hasClosedOuter() {
      return sketchHasClosedOuter(state.sketch);
    },
    undo,
    redo,
    deleteSelection,
    closeActiveOutline,
    clearSketch,
    importSvgText,
    importSvgFile,
    destroy() {
      observer.disconnect();
      window.removeEventListener('keydown', onKeyDown);
      window.removeEventListener('keyup', onKeyUp);
    }
  };
}
