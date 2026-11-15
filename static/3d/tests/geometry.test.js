import test from 'node:test';
import assert from 'node:assert/strict';

import {
  TEMPLATES,
  buildSketchBody,
  triangleCount
} from '../core/geometry.js';

function squareSketch(size = 40) {
  const half = size / 2;
  return {
    outlines: [{
      id: 'outer-1',
      role: 'outer',
      closed: true,
      vertices: [[-half, -half], [half, -half], [half, half], [-half, half]],
      segments: [
        { type: 'line' }, { type: 'line' }, { type: 'line' }, { type: 'line' }
      ]
    }],
    circles: []
  };
}

test('三个模板都能生成有效几何体', () => {
  for (const key of ['tray', 'stand', 'hook']) {
    const built = TEMPLATES[key].build({ ...TEMPLATES[key].defaults });
    assert.ok(built.geometry, key);
    assert.ok(triangleCount(built.geometry) > 0, key);
    const box = built.geometry.boundingBox;
    assert.ok(box, key);
    assert.ok(box.max.x > box.min.x, key);
    assert.ok(box.max.y > box.min.y, key);
    assert.ok(box.max.z > box.min.z, key);
    built.geometry.dispose();
  }
});

test('草图拉伸后平放在地面，Y 轴是厚度', () => {
  const built = buildSketchBody(squareSketch(40), 6);
  const box = built.geometry.boundingBox;
  assert.ok(Math.abs((box.max.x - box.min.x) - 40) < 1e-6);
  assert.ok(Math.abs((box.max.y - box.min.y) - 6) < 1e-6);
  assert.ok(Math.abs((box.max.z - box.min.z) - 40) < 1e-6);
  assert.ok(Math.abs(box.min.y) < 1e-6);
  assert.equal(built.warning, '');
  assert.ok(triangleCount(built.geometry) > 0);
  built.geometry.dispose();
});

test('圆孔和内部孔轮廓会被镂空', () => {
  const sketch = squareSketch(40);
  sketch.circles.push({ id: 'hole-1', x: 0, y: 0, r: 5 });
  sketch.outlines.push({
    id: 'hole-2',
    role: 'hole',
    closed: true,
    vertices: [[-12, -12], [-4, -12], [-4, -4], [-12, -4]],
    segments: [
      { type: 'line' }, { type: 'line' }, { type: 'line' }, { type: 'line' }
    ]
  });
  const built = buildSketchBody(sketch, 4);
  assert.equal(built.warning, '');
  assert.ok(triangleCount(built.geometry) > 0);
  built.geometry.dispose();
});

test('落在外轮廓外的孔会被忽略并给出提示', () => {
  const sketch = squareSketch(40);
  sketch.circles.push({ id: 'hole-1', x: 200, y: 200, r: 5 });
  const built = buildSketchBody(sketch, 4);
  assert.match(built.warning, /已忽略/);
  built.geometry.dispose();
});

test('多个外轮廓会分别拉伸后合并', () => {
  const sketch = squareSketch(20);
  sketch.outlines.push({
    id: 'outer-2',
    role: 'outer',
    closed: true,
    vertices: [[40, 0], [60, 0], [60, 20], [40, 20]],
    segments: [
      { type: 'line' }, { type: 'line' }, { type: 'line' }, { type: 'line' }
    ]
  });
  const built = buildSketchBody(sketch, 3);
  const box = built.geometry.boundingBox;
  assert.ok(Math.abs(box.min.x + 10) < 1e-6);
  assert.ok(Math.abs(box.max.x - 60) < 1e-6);
  assert.ok(triangleCount(built.geometry) > 0);
  built.geometry.dispose();
});

test('没有闭合外轮廓时拒绝生成', () => {
  const sketch = squareSketch(40);
  sketch.outlines[0].closed = false;
  sketch.outlines[0].segments = sketch.outlines[0].segments.slice(0, 3);
  assert.throws(() => buildSketchBody(sketch, 5), /闭合外轮廓/);
});

test('自相交外轮廓时拒绝生成', () => {
  const sketch = {
    outlines: [{
      id: 'outer-1',
      role: 'outer',
      closed: true,
      vertices: [[0, 0], [20, 20], [20, 0], [0, 20]],
      segments: [
        { type: 'line' }, { type: 'line' }, { type: 'line' }, { type: 'line' }
      ]
    }],
    circles: []
  };
  assert.throws(() => buildSketchBody(sketch, 5), /交叉/);
});
