// 纯几何函数：不依赖 Three.js 和 DOM，方便在 Node 里直接做单元测试。
// 草图坐标统一用毫米，二维平面为 XY，y 轴向上。

export const ARC_TOLERANCE = 0.08;
export const MIN_AREA = 1;
export const MAX_COORD = 500;
export const MAX_ARC_CENTER = 2000;

export function clamp(value, min, max) {
  const number = Number(value);
  if (!Number.isFinite(number)) return min;
  return Math.min(max, Math.max(min, number));
}

export function round2(value) {
  return Math.round(Number(value) * 100) / 100;
}

export function deepClone(value) {
  if (typeof structuredClone === 'function') return structuredClone(value);
  return JSON.parse(JSON.stringify(value));
}

let uidCounter = 0;
export function uid(prefix = 'id') {
  uidCounter += 1;
  return `${prefix}_${uidCounter.toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

export function distance(a, b) {
  return Math.hypot(Number(b[0]) - Number(a[0]), Number(b[1]) - Number(a[1]));
}

export function segmentLength(a, b) {
  return distance(a, b);
}

export function polylineLength(points, closed = false) {
  if (!Array.isArray(points) || points.length < 2) return 0;
  let total = 0;
  for (let i = 1; i < points.length; i += 1) total += distance(points[i - 1], points[i]);
  if (closed) total += distance(points[points.length - 1], points[0]);
  return total;
}

export function signedArea(points) {
  if (!Array.isArray(points) || points.length < 3) return 0;
  let area = 0;
  for (let i = 0; i < points.length; i += 1) {
    const a = points[i];
    const b = points[(i + 1) % points.length];
    area += Number(a[0]) * Number(b[1]) - Number(b[0]) * Number(a[1]);
  }
  return area / 2;
}

export function polygonArea(points) {
  return Math.abs(signedArea(points));
}

export function pointInPolygon(point, polygon) {
  if (!Array.isArray(polygon) || polygon.length < 3) return false;
  const x = Number(point[0]);
  const y = Number(point[1]);
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i, i += 1) {
    const xi = Number(polygon[i][0]);
    const yi = Number(polygon[i][1]);
    const xj = Number(polygon[j][0]);
    const yj = Number(polygon[j][1]);
    const crosses = ((yi > y) !== (yj > y)) &&
      (x < ((xj - xi) * (y - yi)) / ((yj - yi) || 1e-9) + xi);
    if (crosses) inside = !inside;
  }
  return inside;
}

export function orientation(a, b, c) {
  return (Number(b[1]) - Number(a[1])) * (Number(c[0]) - Number(b[0])) -
    (Number(b[0]) - Number(a[0])) * (Number(c[1]) - Number(b[1]));
}

export function segmentsIntersect(a, b, c, d) {
  const abC = orientation(a, b, c);
  const abD = orientation(a, b, d);
  const cdA = orientation(c, d, a);
  const cdB = orientation(c, d, b);
  return ((abC > 0 && abD < 0) || (abC < 0 && abD > 0)) &&
    ((cdA > 0 && cdB < 0) || (cdA < 0 && cdB > 0));
}

export function outlineSelfIntersects(points, closed = true) {
  if (!Array.isArray(points) || points.length < 3) return false;
  const count = points.length;
  const segmentCount = closed ? count : count - 1;
  for (let i = 0; i < segmentCount; i += 1) {
    const a = points[i];
    const b = points[(i + 1) % count];
    for (let j = i + 1; j < segmentCount; j += 1) {
      if (closed) {
        if (j === i + 1 || (j + 1) % segmentCount === i) continue;
      } else if (j === i + 1) {
        continue;
      }
      const c = points[j];
      const d = points[(j + 1) % count];
      if (segmentsIntersect(a, b, c, d)) return true;
    }
  }
  return false;
}

export function normalizeAngle(angle) {
  let value = Number(angle);
  while (value <= -Math.PI) value += Math.PI * 2;
  while (value > Math.PI) value -= Math.PI * 2;
  return value;
}

/**
 * 把一段圆弧展开成折线。返回点不包含起点，包含终点。
 * segment: { type:'arc', cx, cy, r, clockwise }
 */
export function arcPoints(start, end, segment, tolerance = ARC_TOLERANCE, maxPoints = 256) {
  const cx = Number(segment.cx);
  const cy = Number(segment.cy);
  const radius = Math.max(1e-3, Number(segment.r));
  if (![cx, cy, radius].every(Number.isFinite)) return [[Number(end[0]), Number(end[1])]];

  const startAngle = Math.atan2(Number(start[1]) - cy, Number(start[0]) - cx);
  const endAngle = Math.atan2(Number(end[1]) - cy, Number(end[0]) - cx);
  let sweep = endAngle - startAngle;
  if (segment.clockwise) {
    while (sweep > 0) sweep -= Math.PI * 2;
    while (sweep < -Math.PI * 2) sweep += Math.PI * 2;
  } else {
    while (sweep < 0) sweep += Math.PI * 2;
    while (sweep > Math.PI * 2) sweep -= Math.PI * 2;
  }
  if (Math.abs(sweep) < 1e-9) return [[Number(end[0]), Number(end[1])]];

  const ratio = 1 - tolerance / radius;
  const step = ratio <= -1 ? Math.PI / 2 : 2 * Math.acos(clamp(ratio, -1, 1));
  const count = clamp(Math.ceil(Math.abs(sweep) / Math.max(step, 1e-3)), 2, maxPoints);
  const points = [];
  for (let i = 1; i <= count; i += 1) {
    if (i === count) {
      points.push([Number(end[0]), Number(end[1])]);
      break;
    }
    const angle = startAngle + sweep * (i / count);
    points.push([cx + Math.cos(angle) * radius, cy + Math.sin(angle) * radius]);
  }
  return points;
}

export function flattenOutline(outline, tolerance = ARC_TOLERANCE) {
  const vertices = Array.isArray(outline?.vertices) ? outline.vertices : [];
  if (!vertices.length) return [];
  const count = vertices.length;
  const closed = !!outline.closed;
  const segments = Array.isArray(outline.segments) ? outline.segments : [];
  const edgeCount = closed ? count : Math.max(0, count - 1);
  const points = [[Number(vertices[0][0]), Number(vertices[0][1])]];

  for (let i = 0; i < edgeCount; i += 1) {
    const start = vertices[i];
    const end = vertices[(i + 1) % count];
    const segment = segments[i] || { type: 'line' };
    if (segment.type === 'arc') {
      const arc = arcPoints(start, end, segment, tolerance);
      for (const point of arc) points.push(point);
    } else {
      points.push([Number(end[0]), Number(end[1])]);
    }
  }

  if (closed && points.length > 1) {
    const first = points[0];
    const last = points[points.length - 1];
    if (Math.hypot(first[0] - last[0], first[1] - last[1]) < 1e-6) points.pop();
  }
  return points;
}

export function flattenCircle(circle, tolerance = ARC_TOLERANCE, maxPoints = 256) {
  const cx = Number(circle.x);
  const cy = Number(circle.y);
  const radius = Math.max(1e-3, Number(circle.r));
  if (![cx, cy, radius].every(Number.isFinite)) return [];
  const ratio = 1 - tolerance / radius;
  const step = ratio <= -1 ? Math.PI / 2 : 2 * Math.acos(clamp(ratio, -1, 1));
  const count = clamp(Math.ceil((Math.PI * 2) / Math.max(step, 1e-3)), 12, maxPoints);
  const points = [];
  for (let i = 0; i < count; i += 1) {
    const angle = (Math.PI * 2 * i) / count;
    points.push([cx + Math.cos(angle) * radius, cy + Math.sin(angle) * radius]);
  }
  return points;
}

export function outlineBounds(outline) {
  return pointsBounds(flattenOutline(outline));
}

export function pointsBounds(points) {
  if (!Array.isArray(points) || !points.length) return null;
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const point of points) {
    const x = Number(point[0]);
    const y = Number(point[1]);
    if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
    if (x < minX) minX = x;
    if (y < minY) minY = y;
    if (x > maxX) maxX = x;
    if (y > maxY) maxY = y;
  }
  if (!Number.isFinite(minX)) return null;
  return {
    minX, minY, maxX, maxY,
    width: maxX - minX,
    height: maxY - minY,
    cx: (minX + maxX) / 2,
    cy: (minY + maxY) / 2
  };
}

export function sketchBounds(sketch) {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  const add = (point) => {
    const x = Number(point[0]);
    const y = Number(point[1]);
    if (!Number.isFinite(x) || !Number.isFinite(y)) return;
    if (x < minX) minX = x;
    if (y < minY) minY = y;
    if (x > maxX) maxX = x;
    if (y > maxY) maxY = y;
  };

  for (const outline of sketch?.outlines || []) {
    for (const point of flattenOutline(outline)) add(point);
  }
  for (const circle of sketch?.circles || []) {
    const radius = Math.abs(Number(circle.r) || 0);
    add([Number(circle.x) - radius, Number(circle.y) - radius]);
    add([Number(circle.x) + radius, Number(circle.y) + radius]);
  }

  if (!Number.isFinite(minX)) {
    return { minX: -60, minY: -45, maxX: 60, maxY: 45, width: 120, height: 90, cx: 0, cy: 0 };
  }
  return {
    minX, minY, maxX, maxY,
    width: maxX - minX,
    height: maxY - minY,
    cx: (minX + maxX) / 2,
    cy: (minY + maxY) / 2
  };
}

export function nearestPointOnSegment(point, a, b) {
  const ax = Number(a[0]);
  const ay = Number(a[1]);
  const bx = Number(b[0]);
  const by = Number(b[1]);
  const abx = bx - ax;
  const aby = by - ay;
  const lengthSquared = abx * abx + aby * aby;
  const t = lengthSquared > 1e-12
    ? clamp(((Number(point[0]) - ax) * abx + (Number(point[1]) - ay) * aby) / lengthSquared, 0, 1)
    : 0;
  const x = ax + abx * t;
  const y = ay + aby * t;
  return {
    point: [x, y],
    distance: Math.hypot(Number(point[0]) - x, Number(point[1]) - y),
    t
  };
}

export function snapToGrid(value, step) {
  const size = Math.max(1e-6, Number(step) || 1);
  return Math.round(Number(value) / size) * size;
}

export function edgeCountOf(outline) {
  const count = Array.isArray(outline?.vertices) ? outline.vertices.length : 0;
  return outline?.closed ? count : Math.max(0, count - 1);
}

export function normalizeSegments(outline) {
  const edgeCount = edgeCountOf(outline);
  const segments = Array.isArray(outline?.segments) ? outline.segments.slice(0, edgeCount) : [];
  while (segments.length < edgeCount) segments.push({ type: 'line' });
  return segments.map((segment) => (segment?.type === 'arc'
    ? {
      type: 'arc',
      cx: round2(segment.cx),
      cy: round2(segment.cy),
      r: round2(segment.r),
      clockwise: !!segment.clockwise
    }
    : { type: 'line' }));
}

/**
 * 给轮廓上的一个顶点倒圆角。
 * 返回 { vertices, segments } 或 { error }。
 */
export function filletCorner(vertices, segments, index, radius, closed) {
  const count = Array.isArray(vertices) ? vertices.length : 0;
  if (count < 3) return { error: '顶点太少，无法倒圆角' };
  if (!Number.isInteger(index) || index < 0 || index >= count) return { error: '顶点不存在' };
  if (!closed && (index === 0 || index === count - 1)) return { error: '开放轮廓的端点不能倒圆角' };

  const r = Number(radius);
  if (!Number.isFinite(r) || r <= 0) return { error: '圆角半径必须大于 0' };

  const prevIndex = (index - 1 + count) % count;
  const nextIndex = (index + 1) % count;
  const prev = vertices[prevIndex];
  const curr = vertices[index];
  const next = vertices[nextIndex];

  const edgeCount = closed ? count : count - 1;
  const segs = Array.isArray(segments) ? segments.slice(0, edgeCount) : [];
  while (segs.length < edgeCount) segs.push({ type: 'line' });

  const prevSegment = segs[prevIndex] || { type: 'line' };
  const nextSegment = segs[index % edgeCount] || { type: 'line' };
  if (prevSegment.type !== 'line' || nextSegment.type !== 'line') {
    return { error: '圆角两侧必须是直线段' };
  }

  const v1 = [Number(prev[0]) - Number(curr[0]), Number(prev[1]) - Number(curr[1])];
  const v2 = [Number(next[0]) - Number(curr[0]), Number(next[1]) - Number(curr[1])];
  const l1 = Math.hypot(v1[0], v1[1]);
  const l2 = Math.hypot(v2[0], v2[1]);
  if (l1 < 1e-6 || l2 < 1e-6) return { error: '相邻点太近，无法倒圆角' };

  const u1 = [v1[0] / l1, v1[1] / l1];
  const u2 = [v2[0] / l2, v2[1] / l2];
  const dot = clamp(u1[0] * u2[0] + u1[1] * u2[1], -1, 1);
  const theta = Math.acos(dot);
  if (theta < 1e-3) return { error: '两条边几乎重合，无法倒圆角' };
  if (Math.PI - theta < 1e-3) return { error: '这个角接近平角，不需要倒圆角' };

  const tangentDistance = r / Math.tan(theta / 2);
  if (tangentDistance >= l1 - 1e-6 || tangentDistance >= l2 - 1e-6) {
    return { error: '圆角半径过大，超过相邻边长' };
  }

  const t1 = [Number(curr[0]) + u1[0] * tangentDistance, Number(curr[1]) + u1[1] * tangentDistance];
  const t2 = [Number(curr[0]) + u2[0] * tangentDistance, Number(curr[1]) + u2[1] * tangentDistance];

  const bisector = [u1[0] + u2[0], u1[1] + u2[1]];
  const bisectorLength = Math.hypot(bisector[0], bisector[1]);
  if (bisectorLength < 1e-6) return { error: '夹角接近 180°，无法倒圆角' };
  const centerDistance = r / Math.sin(theta / 2);
  const center = [
    Number(curr[0]) + (bisector[0] / bisectorLength) * centerDistance,
    Number(curr[1]) + (bisector[1] / bisectorLength) * centerDistance
  ];

  const c1 = [t1[0] - center[0], t1[1] - center[1]];
  const c2 = [t2[0] - center[0], t2[1] - center[1]];
  const cross = c1[0] * c2[1] - c1[1] * c2[0];
  const clockwise = cross < 0;

  const newVertices = vertices.map((point) => [Number(point[0]), Number(point[1])]);
  newVertices.splice(index, 1, [round2(t1[0]), round2(t1[1])], [round2(t2[0]), round2(t2[1])]);

  const newSegments = segs.map((segment) => (segment.type === 'arc'
    ? { ...segment }
    : { type: 'line' }));
  newSegments[prevIndex] = { type: 'line' };
  newSegments.splice(index, 0, {
    type: 'arc',
    cx: round2(center[0]),
    cy: round2(center[1]),
    r: round2(r),
    clockwise
  });
  newSegments[index + 1] = { type: 'line' };

  return { vertices: newVertices, segments: newSegments };
}

export function isArcEndpoint(outline, vertexIndex) {
  const count = Array.isArray(outline?.vertices) ? outline.vertices.length : 0;
  if (!count || !Number.isInteger(vertexIndex)) return false;
  const closed = !!outline?.closed;
  const segments = Array.isArray(outline.segments) ? outline.segments : [];
  const edgeCount = closed ? count : Math.max(0, count - 1);
  if (edgeCount <= 0) return false;
  const hasOutgoing = closed || vertexIndex < edgeCount;
  const hasIncoming = closed || vertexIndex > 0;
  const outgoing = hasOutgoing ? segments[vertexIndex % edgeCount] : null;
  const incoming = hasIncoming ? segments[(vertexIndex - 1 + edgeCount) % edgeCount] : null;
  return outgoing?.type === 'arc' || incoming?.type === 'arc';
}

export function formatMm(value) {
  return `${round2(value).toFixed(2).replace(/\.?0+$/, '')}`;
}
