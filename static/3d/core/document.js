// 3D 作品文档模型 v2：
//   { version: 2, bodies: [ template | sketch ] }
// 旧的 v1 记录（{ model, params, color }）会在加载时自动迁移。

import {
  clamp,
  deepClone,
  round2,
  uid,
  MAX_COORD,
  MAX_ARC_CENTER
} from './geomath.js';

export const DOC_VERSION = 2;
export const MAX_OUTLINES = 20;
export const MAX_CIRCLES = 60;
export const MAX_VERTICES = 400;
export const TEMPLATE_KEYS = ['tray', 'stand', 'hook'];

function cleanColor(value) {
  const color = String(value || '').trim().toLowerCase();
  return /^#[0-9a-f]{6}$/.test(color) ? color : '#55b8aa';
}

function cleanId(value, prefix) {
  const text = String(value || '').trim().slice(0, 40);
  return text || uid(prefix);
}

function cleanCoordinate(value, limit = MAX_COORD) {
  const number = Number(value);
  if (!Number.isFinite(number)) return null;
  return round2(clamp(number, -limit, limit));
}

function cleanPoint(point) {
  if (!Array.isArray(point) || point.length !== 2) return null;
  const x = cleanCoordinate(point[0]);
  const y = cleanCoordinate(point[1]);
  return x === null || y === null ? null : [x, y];
}

function cleanSegment(segment) {
  if (segment?.type === 'arc') {
    const cx = Number(segment.cx);
    const cy = Number(segment.cy);
    const r = Number(segment.r);
    if (
      Number.isFinite(cx) && Number.isFinite(cy) && Number.isFinite(r) &&
      r > 0 && r <= MAX_ARC_CENTER &&
      Math.abs(cx) <= MAX_ARC_CENTER && Math.abs(cy) <= MAX_ARC_CENTER
    ) {
      return {
        type: 'arc',
        cx: round2(cx),
        cy: round2(cy),
        r: round2(r),
        clockwise: !!segment.clockwise
      };
    }
  }
  return { type: 'line' };
}

function cleanOutline(raw, usedIds) {
  if (!raw || typeof raw !== 'object') return null;
  const vertices = [];
  const rawVertices = Array.isArray(raw.vertices) ? raw.vertices.slice(0, MAX_VERTICES) : [];
  for (const point of rawVertices) {
    const cleaned = cleanPoint(point);
    if (cleaned) vertices.push(cleaned);
  }
  if (!vertices.length) return null;

  let closed = !!raw.closed;
  if (closed && vertices.length < 3) closed = false;

  const edgeCount = closed ? vertices.length : Math.max(0, vertices.length - 1);
  const rawSegments = Array.isArray(raw.segments) ? raw.segments : [];
  const segments = [];
  for (let i = 0; i < edgeCount; i += 1) {
    segments.push(cleanSegment(rawSegments[i]));
  }

  let id = cleanId(raw.id, 'outline');
  while (usedIds.has(id)) id = uid('outline');
  usedIds.add(id);

  return {
    id,
    role: raw.role === 'hole' ? 'hole' : 'outer',
    closed,
    vertices,
    segments
  };
}

function cleanCircle(raw, usedIds) {
  if (!raw || typeof raw !== 'object') return null;
  const x = cleanCoordinate(raw.x);
  const y = cleanCoordinate(raw.y);
  const radius = Number(raw.r);
  if (x === null || y === null || !Number.isFinite(radius)) return null;
  if (radius < 0.5 || radius > 200) return null;

  let id = cleanId(raw.id, 'hole');
  while (usedIds.has(id)) id = uid('hole');
  usedIds.add(id);

  return { id, x, y, r: round2(radius) };
}

export function emptySketch() {
  return { outlines: [], circles: [] };
}

export function createDefaultSketch() {
  const vertices = [
    [-50, -55], [50, -55], [50, -45], [-38, -45], [-38, 30], [-50, 30]
  ];
  return {
    outlines: [{
      id: uid('outer'),
      role: 'outer',
      closed: true,
      vertices,
      segments: vertices.map(() => ({ type: 'line' }))
    }],
    circles: [
      { id: uid('hole'), x: -44, y: 18, r: 2.4 },
      { id: uid('hole'), x: 44, y: -50, r: 2.4 }
    ]
  };
}

/**
 * 容错整理草图：去掉非法点、补齐线段、限制数量。
 * 会返回全新的对象，不修改输入。
 */
export function normalizeSketch(raw) {
  const usedIds = new Set();
  const outlines = [];
  const circles = [];

  const rawOutlines = Array.isArray(raw?.outlines) ? raw.outlines.slice(0, MAX_OUTLINES) : [];
  for (const item of rawOutlines) {
    const outline = cleanOutline(item, usedIds);
    if (outline) outlines.push(outline);
  }

  const rawCircles = Array.isArray(raw?.circles) ? raw.circles.slice(0, MAX_CIRCLES) : [];
  for (const item of rawCircles) {
    const circle = cleanCircle(item, usedIds);
    if (circle) circles.push(circle);
  }

  return { outlines, circles };
}

export function sketchHasClosedOuter(sketch) {
  return (sketch?.outlines || []).some((outline) =>
    outline.closed && outline.role === 'outer' && (outline.vertices?.length || 0) >= 3);
}

function normalizeTemplateParams(raw) {
  const params = {};
  if (!raw || typeof raw !== 'object') return params;
  for (const [key, value] of Object.entries(raw)) {
    const number = Number(value);
    if (Number.isFinite(number)) params[key] = number;
  }
  return params;
}

function normalizeBody(raw, index) {
  if (!raw || typeof raw !== 'object') return null;
  const color = cleanColor(raw.color);
  const id = cleanId(raw.id, 'body');

  if (raw.kind === 'template') {
    const template = String(raw.template || '');
    if (!TEMPLATE_KEYS.includes(template)) return null;
    return {
      id: id || `body-${index + 1}`,
      kind: 'template',
      template,
      params: normalizeTemplateParams(raw.params),
      color
    };
  }

  if (raw.kind === 'sketch') {
    return {
      id: id || `body-${index + 1}`,
      kind: 'sketch',
      extrusion: round2(clamp(Number(raw.extrusion) || 6, 1, 40)),
      sketch: normalizeSketch(raw.sketch),
      color
    };
  }

  return null;
}

function migrateV1(payload) {
  const model = String(payload.model || '');
  const color = cleanColor(payload.color);
  const params = payload.params && typeof payload.params === 'object' ? payload.params : {};

  if (model === 'custom') {
    const vertices = [];
    for (const point of Array.isArray(params.outline) ? params.outline : []) {
      const cleaned = cleanPoint(point);
      if (cleaned) vertices.push(cleaned);
    }
    const closed = params.closed !== false && vertices.length >= 3;
    const sketch = emptySketch();
    if (vertices.length) {
      sketch.outlines.push({
        id: uid('outer'),
        role: 'outer',
        closed,
        vertices,
        segments: Array(closed ? vertices.length : Math.max(0, vertices.length - 1))
          .fill(0)
          .map(() => ({ type: 'line' }))
      });
    }
    for (const hole of Array.isArray(params.holes) ? params.holes : []) {
      const circle = cleanCircle({
        x: hole?.x,
        y: hole?.y,
        r: hole?.r
      }, new Set());
      if (circle) sketch.circles.push(circle);
    }
    return {
      version: DOC_VERSION,
      bodies: [{
        id: 'body-1',
        kind: 'sketch',
        color,
        extrusion: round2(clamp(Number(params.extrusion) || 6, 1, 40)),
        sketch: normalizeSketch(sketch)
      }]
    };
  }

  if (TEMPLATE_KEYS.includes(model)) {
    return {
      version: DOC_VERSION,
      bodies: [{
        id: 'body-1',
        kind: 'template',
        template: model,
        params: normalizeTemplateParams(params),
        color
      }]
    };
  }

  return null;
}

/** 把保存记录里的 payload 转成 v2 文档；无法识别时返回 null。 */
export function payloadToDocument(payload) {
  if (!payload || typeof payload !== 'object') return null;

  if (payload.version === DOC_VERSION && Array.isArray(payload.bodies) && payload.bodies.length) {
    const bodies = payload.bodies
      .slice(0, 6)
      .map((body, index) => normalizeBody(body, index))
      .filter(Boolean);
    return bodies.length ? { version: DOC_VERSION, bodies } : null;
  }

  return migrateV1(payload);
}

/** 把当前界面状态整理成 v2 文档，用于保存。 */
export function serializeDocument(state) {
  if (state.key === 'custom') {
    return {
      version: DOC_VERSION,
      bodies: [{
        id: 'body-1',
        kind: 'sketch',
        color: cleanColor(state.color),
        extrusion: round2(clamp(Number(state.values?.custom?.extrusion) || 6, 1, 40)),
        sketch: normalizeSketch(state.sketch)
      }]
    };
  }

  return {
    version: DOC_VERSION,
    bodies: [{
      id: 'body-1',
      kind: 'template',
      color: cleanColor(state.color),
      template: state.key,
      params: deepClone(state.values?.[state.key] || {})
    }]
  };
}

export function documentLabel(payload, templateNames = {}) {
  if (payload?.version === DOC_VERSION) {
    const body = payload.bodies?.[0];
    if (!body) return '3D 作品';
    if (body.kind === 'template') return templateNames[body.template] || '3D 作品';
    return '自由绘制';
  }
  return templateNames[payload?.model] || '3D 作品';
}
