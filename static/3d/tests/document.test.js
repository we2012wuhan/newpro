import test from 'node:test';
import assert from 'node:assert/strict';

import {
  DOC_VERSION,
  createDefaultSketch,
  documentLabel,
  normalizeSketch,
  payloadToDocument,
  serializeDocument,
  sketchHasClosedOuter
} from '../core/document.js';

const TRAY_PARAMS = {
  width: 180,
  depth: 130,
  height: 70,
  wall: 2.4,
  cols: 2,
  rows: 1
};

test('v1 模板记录迁移到 v2', () => {
  const doc = payloadToDocument({
    model: 'tray',
    params: TRAY_PARAMS,
    color: '#112233'
  });
  assert.equal(doc.version, DOC_VERSION);
  assert.equal(doc.bodies.length, 1);
  assert.equal(doc.bodies[0].kind, 'template');
  assert.equal(doc.bodies[0].template, 'tray');
  assert.equal(doc.bodies[0].color, '#112233');
  assert.equal(doc.bodies[0].params.width, 180);
});

test('v1 自由绘制记录迁移到草图 body', () => {
  const doc = payloadToDocument({
    model: 'custom',
    params: {
      extrusion: 5,
      closed: true,
      outline: [[0, 0], [10, 0], [10, 10], [0, 10]],
      holes: [{ x: 5, y: 5, r: 1 }]
    },
    color: '#abcdef'
  });
  const body = doc.bodies[0];
  assert.equal(body.kind, 'sketch');
  assert.equal(body.extrusion, 5);
  assert.equal(body.sketch.outlines.length, 1);
  assert.equal(body.sketch.outlines[0].vertices.length, 4);
  assert.equal(body.sketch.outlines[0].segments.length, 4);
  assert.equal(body.sketch.circles.length, 1);
  assert.equal(body.color, '#abcdef');
});

test('无法识别的记录返回 null', () => {
  assert.equal(payloadToDocument(null), null);
  assert.equal(payloadToDocument({ model: 'unknown', params: {} }), null);
  assert.equal(payloadToDocument({ version: 2, bodies: [] }), null);
  assert.equal(payloadToDocument({
    version: 2,
    bodies: [{ kind: 'template', template: 'unknown', params: {} }]
  }), null);
});

test('serializeDocument 生成 v2 模板文档', () => {
  const doc = serializeDocument({
    key: 'stand',
    values: { stand: { ...TRAY_PARAMS } },
    sketch: createDefaultSketch(),
    color: '#00aa88'
  });
  assert.equal(doc.version, 2);
  assert.equal(doc.bodies[0].kind, 'template');
  assert.equal(doc.bodies[0].template, 'stand');
  assert.equal(doc.bodies[0].color, '#00aa88');
});

test('serializeDocument 生成 v2 草图文档', () => {
  const sketch = createDefaultSketch();
  const doc = serializeDocument({
    key: 'custom',
    values: { custom: { extrusion: 8 } },
    sketch,
    color: '#102030'
  });
  const body = doc.bodies[0];
  assert.equal(body.kind, 'sketch');
  assert.equal(body.extrusion, 8);
  assert.equal(body.sketch.outlines[0].closed, true);
  assert.equal(body.sketch.circles.length, 2);
  assert.equal(sketchHasClosedOuter(body.sketch), true);
});

test('normalizeSketch 丢弃非法圆孔、修正闭合状态、补齐线段', () => {
  const sketch = normalizeSketch({
    outlines: [
      {
        id: 'a',
        role: 'outer',
        closed: true,
        vertices: [[0, 0], [10, 0]],
        segments: [{ type: 'arc', cx: 'bad', cy: 0, r: 1 }]
      },
      {
        id: 'b',
        role: 'hole',
        closed: true,
        vertices: [[1, 1], [2, 1], [2, 2]],
        segments: []
      }
    ],
    circles: [
      { id: 'c1', x: 0, y: 0, r: 0.1 },
      { id: 'c2', x: 0, y: 0, r: 5 }
    ]
  });
  assert.equal(sketch.outlines.length, 2);
  assert.equal(sketch.outlines[0].closed, false);
  assert.equal(sketch.outlines[0].segments.length, 1);
  assert.equal(sketch.outlines[0].segments[0].type, 'line');
  assert.equal(sketch.outlines[1].closed, true);
  assert.equal(sketch.outlines[1].segments.length, 3);
  assert.equal(sketch.circles.length, 1);
  assert.equal(sketch.circles[0].r, 5);
  assert.equal(sketchHasClosedOuter(sketch), false);
});

test('documentLabel 兼容 v1 和 v2', () => {
  const names = { tray: '收纳盒', custom: '自由绘制' };
  assert.equal(documentLabel({ model: 'tray' }, names), '收纳盒');
  assert.equal(documentLabel({ model: 'custom' }, names), '自由绘制');
  assert.equal(documentLabel({
    version: 2,
    bodies: [{ kind: 'template', template: 'tray' }]
  }, names), '收纳盒');
  assert.equal(documentLabel({
    version: 2,
    bodies: [{ kind: 'sketch' }]
  }, names), '自由绘制');
  assert.equal(documentLabel(null, names), '3D 作品');
});
