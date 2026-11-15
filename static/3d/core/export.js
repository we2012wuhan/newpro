// STL / 3MF 导出：纯字节生成，不依赖页面状态。

import * as THREE from '../vendor/three.module.js';

function triangleNormal(a, b, c) {
  const ab = new THREE.Vector3().subVectors(b, a);
  const ac = new THREE.Vector3().subVectors(c, a);
  return new THREE.Vector3().crossVectors(ab, ac).normalize();
}

export function makeStlBytes(geometry) {
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

function escapeXml(value) {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');
}

/**
 * 生成 3MF。先按 0.0001 mm 合并重复顶点，避免每个三角形重复写 3 个顶点。
 */
export function make3mfBytes(geometry, title = '3D 作品') {
  const source = geometry.index ? geometry.toNonIndexed() : geometry;
  const positions = source.attributes.position;
  const count = positions.count;
  const vertexMap = new Map();
  const vertices = [];
  const triangleIndices = [];

  for (let i = 0; i < count; i += 1) {
    const x = positions.getX(i);
    const y = positions.getY(i);
    const z = positions.getZ(i);
    const key = `${x.toFixed(4)},${y.toFixed(4)},${z.toFixed(4)}`;
    let index = vertexMap.get(key);
    if (index === undefined) {
      index = vertices.length;
      vertexMap.set(key, index);
      vertices.push([x, y, z]);
    }
    triangleIndices.push(index);
  }
  if (source !== geometry) source.dispose();

  const vertexXml = vertices.map(([x, y, z]) =>
    `<vertex x="${x.toFixed(4)}" y="${y.toFixed(4)}" z="${z.toFixed(4)}"/>`).join('');
  const triangleXml = [];
  for (let i = 0; i < triangleIndices.length; i += 3) {
    triangleXml.push(
      `<triangle v1="${triangleIndices[i]}" v2="${triangleIndices[i + 1]}" v3="${triangleIndices[i + 2]}"/>`
    );
  }

  const model = `<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter" xml:lang="zh-CN" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
  <metadata name="Title">${escapeXml(title)}</metadata>
  <resources>
    <object id="1" type="model">
      <mesh>
        <vertices>${vertexXml}</vertices>
        <triangles>${triangleXml.join('')}</triangles>
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

export function exportBytes(geometry, format, title) {
  if (!geometry) throw new Error('模型还没有准备好');
  return format === '3mf' ? make3mfBytes(geometry, title) : makeStlBytes(geometry);
}

export function downloadBytes(bytes, filename, mime) {
  const blob = new Blob([bytes], { type: mime });
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(link.href), 2000);
}
