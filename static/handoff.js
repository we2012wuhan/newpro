/*
 * 图片接力：把一个 Blob 从「文生图」带到「图片工坊」
 * ==================================================
 * 两个页面同源，但页面跳转会丢掉内存里的 Blob。
 * 用 IndexedDB 暂存一下：文生图写入，图片工坊读出后立刻删除。
 * 图片不会经过服务器，也不需要用户先下载再上传。
 */
const DB_NAME = 'tb-handoff';
const STORE = 'items';
const VERSION = 1;

function openDb() {
  return new Promise((resolve, reject) => {
    if (!window.indexedDB) {
      reject(new Error('浏览器不支持 IndexedDB'));
      return;
    }
    const req = indexedDB.open(DB_NAME, VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) db.createObjectStore(STORE);
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error || new Error('打不开本地暂存'));
  });
}

function transactionDone(tx) {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error || new Error('本地暂存写入失败'));
    tx.onabort = () => reject(tx.error || new Error('本地暂存写入被取消'));
  });
}

/** 写入一张图，返回给图片工坊读取用的 key。 */
export async function putHandoff(blob, name) {
  if (!(blob instanceof Blob)) throw new Error('没有可接力的图片');
  const db = await openDb();
  const key = 'gen-' + Date.now() + '-' + Math.random().toString(36).slice(2, 9);
  const tx = db.transaction(STORE, 'readwrite');
  tx.objectStore(STORE).put({
    blob,
    name: String(name || 'ai-image.png'),
    at: Date.now()
  }, key);
  await transactionDone(tx);
  db.close();
  return key;
}

/** 读取并删除（读不到返回 null）。 */
export async function takeHandoff(key) {
  if (!key) return null;
  const db = await openDb();
  // 注意：不能在 await 一个请求之后再用同一个事务发 delete，
  // 事务会自动提交；所以 delete 放进 get 的 success 回调里同步发出。
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, 'readwrite');
    const store = tx.objectStore(STORE);
    let record = null;
    const req = store.get(key);
    req.onsuccess = () => {
      record = req.result || null;
      if (record) store.delete(key);
    };
    tx.oncomplete = () => {
      db.close();
      resolve(record);
    };
    tx.onerror = () => {
      db.close();
      reject(tx.error || new Error('本地暂存读取失败'));
    };
    tx.onabort = () => {
      db.close();
      reject(tx.error || new Error('本地暂存读取被取消'));
    };
  });
}

export async function deleteHandoff(key) {
  if (!key) return;
  try {
    const db = await openDb();
    const tx = db.transaction(STORE, 'readwrite');
    tx.objectStore(STORE).delete(key);
    await transactionDone(tx);
    db.close();
  } catch (error) {
    // 清理失败不影响主流程
  }
}

/** 去掉地址栏里的 handoff 参数，避免刷新时重复读取。 */
export function stripHandoffParam() {
  try {
    const url = new URL(location.href);
    if (!url.searchParams.has('handoff')) return;
    url.searchParams.delete('handoff');
    history.replaceState(null, '', url.pathname + (url.search || '') + url.hash);
  } catch (error) {
    // 忽略
  }
}
