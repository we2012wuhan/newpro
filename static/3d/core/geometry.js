// Three.js 几何层：现成模板 + 自由草图拉伸。
// 这里只负责把参数/草图变成 BufferGeometry，不碰 DOM 和界面状态。

import * as THREE from '../vendor/three.module.js';
import {
  ARC_TOLERANCE,
  MIN_AREA,
  flattenOutline,
  flattenCircle,
  pointInPolygon,
  polygonArea,
  outlineSelfIntersects,
  clamp,
  round2
} from './geomath.js';

function boxGeometry(width, height, depth, x, y, z, rotateX = 0) {
  const geometry = new THREE.BoxGeometry(
    Math.max(0.1, width),
    Math.max(0.1, height),
    Math.max(0.1, depth)
  );
  if (rotateX) geometry.rotateX(rotateX);
  geometry.translate(x, y, z);
  return geometry;
}

/**
 * 合并若干几何体，只保留 position 属性并重新计算法线。
 * 输入几何体在复制后会被释放，调用方不要再使用它们。
 */
function mergeGeometries(geometries) {
  if (!Array.isArray(geometries) || !geometries.length) {
    throw new Error('没有可合并的几何体');
  }

  const prepared = geometries.map((geometry) => {
    if (!geometry) throw new Error('几何体为空');
    if (!geometry.index) return { geometry, temporary: false };
    return { geometry: geometry.toNonIndexed(), temporary: true };
  });

  let total = 0;
  for (const item of prepared) {
    const position = item.geometry.getAttribute('position');
    if (!position) throw new Error('几何体缺少顶点数据');
    total += position.array.length;
  }

  const positions = new Float32Array(total);
  let offset = 0;
  for (const item of prepared) {
    const position = item.geometry.getAttribute('position');
    positions.set(position.array, offset);
    offset += position.array.length;
  }

  const merged = new THREE.BufferGeometry();
  merged.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  merged.computeVertexNormals();
  merged.computeBoundingBox();

  // 源几何体和 toNonIndexed 产生的临时副本都已经复制进 merged，释放掉避免显存泄漏。
  geometries.forEach((geometry, index) => {
    if (geometry) geometry.dispose();
    const temporary = prepared[index].geometry;
    if (temporary && temporary !== geometry) temporary.dispose();
  });

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
    geometry: mergeGeometries(parts),
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
    geometry: mergeGeometries(parts),
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
  const plate = new THREE.ExtrudeGeometry(shape, {
    depth: plateDepth,
    bevelEnabled: false,
    steps: 1
  });

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
    geometry: mergeGeometries([plate, arm, tip]),
    warning: screwRadius * 2 > width * 0.22 ? '螺丝孔相对底板偏大，请确认边缘强度。' : ''
  };
}

export const TEMPLATES = {
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
    desc: '在毫米网格上画外轮廓和孔，再设置拉伸厚度。',
    slug: 'custom-model',
    defaults: {
      extrusion: 6
    },
    fields: [
      { key: 'extrusion', label: '拉伸厚度', min: 1, max: 40, step: 0.5, unit: 'mm' }
    ]
  }
};

export function cloneDefaults(key) {
  return JSON.parse(JSON.stringify(TEMPLATES[key].defaults));
}

function containsPoint(polygon, point) {
  return pointInPolygon(point, polygon);
}

/**
 * 自由草图 -> 3D。
 * sketch: { outlines: [{ id, role:'outer'|'hole', closed, vertices, segments }], circles: [{x,y,r}] }
 *
 * 多个 outer 会分别拉伸后合并；hole 轮廓和圆孔按“落在哪个 outer 内”分配给对应外形。
 * 结果会旋转成 Y 轴向上，平放在地面（y=0），和现成模板一致。
 */
export function buildSketchBody(sketch, extrusion) {
  const depth = clamp(extrusion, 1, 40);
  const outlines = Array.isArray(sketch?.outlines) ? sketch.outlines : [];
  const circles = Array.isArray(sketch?.circles) ? sketch.circles : [];

  const outerOutlines = outlines.filter((outline) => outline.closed && outline.role === 'outer');
  const holeOutlines = outlines.filter((outline) => outline.closed && outline.role === 'hole');
  if (!outerOutlines.length) {
    throw new Error('请先画完并闭合外轮廓');
  }

  const holes = [];
  let ignoredHoles = 0;

  for (const hole of holeOutlines) {
    const points = flattenOutline(hole, ARC_TOLERANCE);
    if (points.length < 3) {
      ignoredHoles += 1;
      continue;
    }
    if (outlineSelfIntersects(points, true)) {
      ignoredHoles += 1;
      continue;
    }
    holes.push({ kind: 'polygon', points, anchor: points[0] });
  }
  for (const circle of circles) {
    const radius = Number(circle.r);
    if (!Number.isFinite(radius) || radius < 0.5) {
      ignoredHoles += 1;
      continue;
    }
    holes.push({
      kind: 'circle',
      points: flattenCircle(circle, ARC_TOLERANCE),
      anchor: [Number(circle.x), Number(circle.y)]
    });
  }

  const geometries = [];
  const holeAssigned = new Array(holes.length).fill(false);

  for (const outer of outerOutlines) {
    const points = flattenOutline(outer, ARC_TOLERANCE);
    if (points.length < 3) continue;
    if (outlineSelfIntersects(points, true)) {
      throw new Error('外轮廓有交叉，请撤回交叉的点');
    }
    if (polygonArea(points) < MIN_AREA) {
      throw new Error('轮廓面积太小，请至少画出 1 mm² 的形状');
    }

    const shape = new THREE.Shape(points.map((point) => new THREE.Vector2(point[0], point[1])));
    holes.forEach((hole, holeIndex) => {
      if (!containsPoint(points, hole.anchor)) return;
      shape.holes.push(new THREE.Path(
        hole.points.map((point) => new THREE.Vector2(point[0], point[1]))
      ));
      holeAssigned[holeIndex] = true;
    });

    const geometry = new THREE.ExtrudeGeometry(shape, {
      depth,
      bevelEnabled: false,
      curveSegments: 1,
      steps: 1
    });
    geometries.push(geometry);
  }

  if (!geometries.length) {
    throw new Error('没有可生成的外轮廓');
  }

  const merged = mergeGeometries(geometries);
  // ExtrudeGeometry 默认沿 +Z 拉伸；转成 Y 轴向上、平放在地面。
  merged.rotateX(-Math.PI / 2);
  merged.computeVertexNormals();
  merged.computeBoundingBox();

  const assignedHoles = holeAssigned.filter(Boolean).length;
  const ignored = Math.max(0, ignoredHoles + holes.length - assignedHoles);
  return {
    geometry: merged,
    warning: ignored
      ? `有 ${ignored} 个孔不在外轮廓内或形状无效，已忽略。`
      : ''
  };
}

export function triangleCount(geometry) {
  const position = geometry?.getAttribute?.('position');
  return position ? Math.floor(position.count / 3) : 0;
}

export function geometrySize(geometry) {
  if (!geometry?.boundingBox) return null;
  const box = geometry.boundingBox;
  return {
    width: round2(box.max.x - box.min.x),
    height: round2(box.max.y - box.min.y),
    depth: round2(box.max.z - box.min.z)
  };
}
