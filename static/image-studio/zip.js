/*
 * 极简 ZIP 写入器（store 模式，不压缩）
 * =====================================
 * 为什么不用 JSZip？
 *   - 只打包几张 PNG/JPEG，图片本身已经是压缩格式，再 deflate 几乎不省体积；
 *   - store 模式（compression method = 0）只需要写文件头和 CRC32，代码两百行以内，
 *     少一个外部依赖，也少一次版本升级的心智负担。
 *
 * ZIP 的结构（从前往后）：
 *
 *   [本地文件头 + 文件名 + 原始数据] × N
 *   [中央目录头 + 文件名] × N
 *   [EOCD 结束记录]
 *
 * 读取方（资源管理器、unzip）先找 EOCD，再按中央目录里的偏移去取每个文件。
 * 所有多字节整数都是小端序（little-endian），所以用 DataView + true。
 *
 * 限制：不做 ZIP64。条目 ≤ 65535、总大小 < 4GB，对切图场景够用；
 * 超了就明确报错，而不是写出一个坏包。
 */

const LOCAL_SIG = 0x04034b50;
const CENTRAL_SIG = 0x02014b50;
const EOCD_SIG = 0x06054b50;
const UTF8_FLAG = 0x0800;
const MAX_UINT32 = 0xFFFFFFFF;

// CRC32 查表：多项式 0xEDB88320，和 ZIP/PNG 用的是同一个
const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n += 1) {
    let c = n;
    for (let k = 0; k < 8; k += 1) {
      c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
    }
    table[n] = c >>> 0;
  }
  return table;
})();

function crc32(data) {
  let c = 0xFFFFFFFF;
  for (let i = 0; i < data.length; i += 1) {
    c = CRC_TABLE[(c ^ data[i]) & 0xFF] ^ (c >>> 8);
  }
  return (c ^ 0xFFFFFFFF) >>> 0;
}

function toBytes(data) {
  if (data instanceof Uint8Array) return data;
  if (data instanceof ArrayBuffer) return new Uint8Array(data);
  if (ArrayBuffer.isView(data)) {
    return new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
  }
  if (Array.isArray(data)) return Uint8Array.from(data);
  throw new Error('ZIP 条目数据必须是字节数组');
}

function dosDateTime(now) {
  const year = Math.min(2107, Math.max(1980, now.getFullYear()));
  const time = ((now.getHours() & 0x1F) << 11) |
    ((now.getMinutes() & 0x3F) << 5) |
    (Math.floor(now.getSeconds() / 2) & 0x1F);
  const date = (((year - 1980) & 0x7F) << 9) |
    (((now.getMonth() + 1) & 0x0F) << 5) |
    (now.getDate() & 0x1F);
  return { time, date };
}

function uniqueName(name, taken) {
  if (!taken.has(name)) {
    taken.add(name);
    return name;
  }
  const dot = name.lastIndexOf('.');
  const stem = dot > 0 ? name.slice(0, dot) : name;
  const ext = dot > 0 ? name.slice(dot) : '';
  let index = 2;
  while (taken.has(`${stem}-${index}${ext}`)) index += 1;
  const next = `${stem}-${index}${ext}`;
  taken.add(next);
  return next;
}

/**
 * @param {Array<{name: string, data: Uint8Array|ArrayBuffer}>} entries
 * @returns {Blob} type=application/zip
 */
export function createStoreZip(entries) {
  if (!Array.isArray(entries)) throw new Error('ZIP 条目格式不正确');
  if (entries.length > 0xFFFF) throw new Error('文件数量超过 65535，无法打包');

  const encoder = new TextEncoder();
  const { time, date } = dosDateTime(new Date());
  const taken = new Set();
  const locals = [];
  const centrals = [];
  let offset = 0;

  for (const entry of entries) {
    if (!entry || !entry.name) throw new Error('ZIP 条目缺少文件名');
    const data = toBytes(entry.data);
    const name = uniqueName(String(entry.name), taken);
    const nameBytes = encoder.encode(name);
    const size = data.length;
    const crc = crc32(data);

    if (size > MAX_UINT32 || offset > MAX_UINT32) {
      throw new Error('文件超过 4GB，当前 ZIP 写入器不支持');
    }

    // ---- 本地文件头 ----
    const local = new Uint8Array(30 + nameBytes.length + size);
    const lv = new DataView(local.buffer);
    lv.setUint32(0, LOCAL_SIG, true);
    lv.setUint16(4, 20, true);          // 解压所需版本 2.0
    lv.setUint16(6, UTF8_FLAG, true);   // 文件名是 UTF-8
    lv.setUint16(8, 0, true);           // 0 = store，不压缩
    lv.setUint16(10, time, true);
    lv.setUint16(12, date, true);
    lv.setUint32(14, crc, true);
    lv.setUint32(18, size, true);       // 压缩后大小 = 原始大小
    lv.setUint32(22, size, true);       // 原始大小
    lv.setUint16(26, nameBytes.length, true);
    lv.setUint16(28, 0, true);          // extra 长度
    local.set(nameBytes, 30);
    local.set(data, 30 + nameBytes.length);
    locals.push(local);

    // ---- 中央目录头（记录本地头的位置，供读取方跳转） ----
    const central = new Uint8Array(46 + nameBytes.length);
    const cv = new DataView(central.buffer);
    cv.setUint32(0, CENTRAL_SIG, true);
    cv.setUint16(4, 20, true);          // 创建版本 2.0
    cv.setUint16(6, 20, true);          // 解压所需版本 2.0
    cv.setUint16(8, UTF8_FLAG, true);
    cv.setUint16(10, 0, true);
    cv.setUint16(12, time, true);
    cv.setUint16(14, date, true);
    cv.setUint32(16, crc, true);
    cv.setUint32(20, size, true);
    cv.setUint32(24, size, true);
    cv.setUint16(28, nameBytes.length, true);
    cv.setUint16(30, 0, true);          // extra
    cv.setUint16(32, 0, true);          // comment
    cv.setUint16(34, 0, true);          // 起始磁盘号
    cv.setUint16(36, 0, true);          // 内部属性
    cv.setUint32(38, 0, true);          // 外部属性
    cv.setUint32(42, offset, true);     // 本地头偏移
    central.set(nameBytes, 46);
    centrals.push(central);

    offset += local.length;
  }

  const centralSize = centrals.reduce((sum, part) => sum + part.length, 0);
  if (offset + centralSize > MAX_UINT32) {
    throw new Error('ZIP 总大小超过 4GB，当前写入器不支持');
  }

  // ---- EOCD：告诉读取方中央目录在哪、有多少条 ----
  const eocd = new Uint8Array(22);
  const ev = new DataView(eocd.buffer);
  ev.setUint32(0, EOCD_SIG, true);
  ev.setUint16(4, 0, true);                  // 当前磁盘号
  ev.setUint16(6, 0, true);                  // 中央目录起始磁盘号
  ev.setUint16(8, entries.length, true);     // 本磁盘条目数
  ev.setUint16(10, entries.length, true);    // 总条目数
  ev.setUint32(12, centralSize, true);
  ev.setUint32(16, offset, true);            // 中央目录起始偏移
  ev.setUint16(20, 0, true);                 // 注释长度

  return new Blob([...locals, ...centrals, eocd], { type: 'application/zip' });
}
