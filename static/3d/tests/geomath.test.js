import test from 'node:test';
import assert from 'node:assert/strict';

import {
  arcPoints,
  flattenOutline,
  isArcEndpoint,
  normalizeSegments,
  outlineSelfIntersects,
  pointInPolygon,
  polygonArea,
  sketchBounds,
  snapToGrid,
  filletCorner
} from '../core/geomath.js';

test('闭合矩形倒圆角：切点、圆弧和线段数量正确', () => {
  const vertices = [[0, 0], [100, 0], [100, 60], [0, 60]];
  const segments = [
    { type: 'line' }, { type: 'line' }, { type: 'line' }, { type: 'line' }
  ];
  const result = filletCorner(vertices, segments, 1, 10, true);
  assert.ok(!result.error, result.error);
  assert.deepEqual(result.vertices[1], [90, 0]);
  assert.deepEqual(result.vertices[2], [100, 10]);
  assert.equal(result.vertices.length, 5);
  assert.equal(result.segments.length, 5);
  assert.equal(result.segments.filter((item) => item.type === 'arc').length, 1);

  const arc = result.segments.find((item) => item.type === 'arc');
  assert.deepEqual([arc.cx, arc.cy, arc.r], [90, 10, 10]);

  const points = flattenOutline({
    vertices: result.vertices,
    segments: result.segments,
    closed: true
  });
  assert.ok(points.length > 5);
  assert.equal(outlineSelfIntersects(points, true), false);
  assert.ok(polygonArea(points) > 0);
  assert.ok(polygonArea(points) < 6000);
});

test('开放折线倒圆角，端点不允许倒圆角', () => {
  const vertices = [[0, 0], [50, 0], [50, 50]];
  const segments = [{ type: 'line' }, { type: 'line' }];
  const result = filletCorner(vertices, segments, 1, 5, false);
  assert.ok(!result.error, result.error);
  assert.equal(result.vertices.length, 4);
  assert.equal(result.segments.length, 3);
  assert.equal(result.segments.filter((item) => item.type === 'arc').length, 1);

  assert.match(filletCorner(vertices, segments, 0, 5, false).error, /端点/);
  assert.match(filletCorner(vertices, segments, 2, 5, false).error, /端点/);
});

test('圆角半径过大时给出错误', () => {
  const vertices = [[0, 0], [10, 0], [10, 10]];
  const segments = [{ type: 'line' }, { type: 'line' }];
  const result = filletCorner(vertices, segments, 1, 20, false);
  assert.match(result.error, /半径过大/);
});

test('圆弧展开方向正确', () => {
  const upper = arcPoints(
    [1, 0], [-1, 0],
    { type: 'arc', cx: 0, cy: 0, r: 1, clockwise: false },
    0.01
  );
  assert.deepEqual(upper[upper.length - 1], [-1, 0]);
  assert.ok(upper.some((point) => point[1] > 0.9));

  const lower = arcPoints(
    [1, 0], [-1, 0],
    { type: 'arc', cx: 0, cy: 0, r: 1, clockwise: true },
    0.01
  );
  assert.ok(lower.some((point) => point[1] < -0.9));
});

test('自相交检测和点在多边形内', () => {
  const bowtie = [[0, 0], [10, 10], [10, 0], [0, 10]];
  assert.equal(outlineSelfIntersects(bowtie, true), true);
  const square = [[0, 0], [10, 0], [10, 10], [0, 10]];
  assert.equal(outlineSelfIntersects(square, true), false);
  assert.equal(pointInPolygon([5, 5], square), true);
  assert.equal(pointInPolygon([15, 5], square), false);
});

test('网格吸附', () => {
  assert.equal(snapToGrid(7.4, 5), 5);
  assert.equal(snapToGrid(7.6, 5), 10);
  assert.equal(snapToGrid(-2.4, 1), -2);
});

test('开放轮廓最后一个顶点不会被误判为圆弧端点', () => {
  const outline = {
    closed: false,
    vertices: [[0, 0], [1, 0], [2, 1]],
    segments: [
      { type: 'arc', cx: 0.5, cy: 0, r: 0.5, clockwise: false },
      { type: 'line' }
    ]
  };
  assert.equal(isArcEndpoint(outline, 0), true);
  assert.equal(isArcEndpoint(outline, 1), true);
  assert.equal(isArcEndpoint(outline, 2), false);
});

test('normalizeSegments 按闭合状态补齐线段', () => {
  const closed = normalizeSegments({
    closed: true,
    vertices: [[0, 0], [1, 0], [1, 1], [0, 1]],
    segments: [{ type: 'line' }]
  });
  assert.equal(closed.length, 4);
  assert.ok(closed.every((item) => item.type === 'line'));

  const open = normalizeSegments({
    closed: false,
    vertices: [[0, 0], [1, 0], [1, 1]],
    segments: []
  });
  assert.equal(open.length, 2);
});

test('草图边界包含圆孔', () => {
  const bounds = sketchBounds({
    outlines: [{
      closed: true,
      vertices: [[-10, -10], [10, -10], [10, 10], [-10, 10]],
      segments: []
    }],
    circles: [{ x: 20, y: 0, r: 5 }]
  });
  assert.equal(bounds.minX, -10);
  assert.equal(bounds.maxX, 25);
  assert.equal(bounds.minY, -10);
  assert.equal(bounds.maxY, 10);
  assert.equal(bounds.width, 35);
  assert.equal(bounds.height, 20);
});
