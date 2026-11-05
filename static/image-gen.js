/*
 * AI 文生图 · 页面交互
 * =====================
 * 流程很短：
 *   1. 读 /image-gen/api/status，知道 Key 配没配、今天还能生成几张；
 *   2. 提交任务，拿到 job_id；
 *   3. 每 1.6 秒问一次 /image-gen/api/jobs/{id}，直到 succeeded / failed；
 *   4. 成功图可以直接下载，也可以经 IndexedDB 接力到图片工坊继续处理。
 *
 * 图片地址都带登录 Cookie，不能把后端返回的 object URL 当成公开链接。
 */
import { putHandoff } from './handoff.js';

const $ = (id) => document.getElementById(id);

const promptEl = $('prompt');
const promptCount = $('promptCount');
const modeEl = $('mode');
const styleEl = $('style');
const styleHint = $('styleHint');
const notionTip = $('notionTip');
const modelEl = $('model');
const modelHint = $('modelHint');
const ratioEl = $('ratio');
const resolutionEl = $('resolution');
const countEl = $('count');
const negativeEl = $('negative');
const generateBtn = $('generate');
const quotaEl = $('quota');
const keyBanner = $('keyBanner');
const resultBody = $('resultBody');
const jobMeta = $('jobMeta');
const progress = $('progress');
const progressBar = $('progressBar');
const progressText = $('progressText');
const jobError = $('jobError');
const historyList = $('historyList');
const historyCount = $('historyCount');
const refreshBtn = $('refreshBtn');
const statusEl = $('status');

const STATUS_TEXT = {
  queued: '排队中',
  running: '生成中',
  succeeded: '已完成',
  failed: '失败'
};

const FALLBACK_STYLES = [
  { key: 'none', label: '不指定' },
  { key: 'photo', label: '摄影' },
  { key: 'illustration', label: '插画' },
  { key: '3d', label: '3D 渲染' },
  { key: 'watercolor', label: '水彩' },
  { key: 'minimal', label: '极简' },
  { key: 'poster', label: '海报' },
  { key: 'notion', label: 'Notion 信息图' }
];

const DEFAULT_PLACEHOLDER =
  '例如：清晨的江南小镇，石桥下有小船，薄雾，水彩画风格，画面干净';
const NOTION_PLACEHOLDER =
  '例如：一个人站在分岔路口，左边路牌写「传统开发」，右边写「AI 编程」；底部标注「72% 开发者已用 AI」';

const state = {
  ready: false,
  model: '',
  models: [],
  styles: [],
  history: [],
  dailyLimit: 0,
  usedToday: 0,
  ttlDays: 7,
  mode: 'free',
  style: 'none',
  freeSnapshot: null,
  current: null,
  polling: 0,
  busy: false,
  statusTimer: 0
};

function toast(message, kind = 'info') {
  if (!message) {
    statusEl.classList.remove('show');
    return;
  }
  statusEl.textContent = message;
  statusEl.className = 'status show ' + (kind === 'err' ? 'err' : 'info');
  clearTimeout(state.statusTimer);
  state.statusTimer = setTimeout(() => statusEl.classList.remove('show'),
    kind === 'err' ? 4600 : 2600);
}

function showBanner(message, kind = 'err') {
  if (!message) {
    keyBanner.hidden = true;
    return;
  }
  keyBanner.textContent = message;
  keyBanner.className = 'banner' + (kind === 'info' ? ' info' : '');
  keyBanner.hidden = false;
}

function setBusy(busy) {
  state.busy = busy;
  generateBtn.disabled = busy || !state.ready;
  generateBtn.textContent = busy ? '生成中…' : '生成图片';
}

function updateQuota() {
  if (!state.ready) {
    quotaEl.textContent = '服务端配置好 Key 之后，这里会显示今日额度。';
    return;
  }
  const limit = state.dailyLimit || 0;
  const used = state.usedToday || 0;
  quotaEl.textContent = limit
    ? `今日已用 ${used} / ${limit} 张；每次按你选的数量向火山方舟计费。`
    : '每次按你选择的数量向火山方舟计费。';
}

function api(path, options = {}) {
  const headers = Object.assign({ Accept: 'application/json' }, options.headers || {});
  return fetch(path, Object.assign({}, options, {
    credentials: 'same-origin',
    headers
  })).then(async (res) => {
    let data = {};
    try {
      data = await res.json();
    } catch (error) {
      data = {};
    }
    if (!res.ok || data.ok === false) {
      const err = new Error(data.message || data.error || ('请求失败（HTTP ' + res.status + '）'));
      err.status = res.status;
      throw err;
    }
    return data;
  });
}

function formatTime(value) {
  const text = String(value || '');
  return text.length >= 16 ? text.slice(5, 16) : text;
}

function statusText(job) {
  if (job.status === 'running' && job.count > 1) {
    return `生成中 ${job.done || 0}/${job.count}`;
  }
  return STATUS_TEXT[job.status] || job.status || '未知';
}

function fileName(job, image) {
  const ext = String((image && image.ext) || 'png').toLowerCase();
  return `AI生成-${String(job.id || '').slice(0, 8)}-${image.index}.${ext}`;
}

function storedModel() {
  try {
    return localStorage.getItem('tb_image_model') || '';
  } catch (error) {
    return '';
  }
}

function rememberModel(modelId) {
  try {
    if (modelId) localStorage.setItem('tb_image_model', modelId);
  } catch (error) { /* 隐私模式下写不进去，不影响正常使用 */ }
}

function modelInfo(modelId) {
  return (state.models || []).find((model) => model.id === modelId) || null;
}

function modelLabel(modelId) {
  const info = modelInfo(modelId);
  return info ? info.label : (modelId || '默认模型');
}

function updateModelHint() {
  const info = modelInfo(modelEl.value);
  modelHint.textContent = info && info.note ? info.note : '切换模型会按对应的价格计费';
}

function renderModels(models) {
  const previous = modelEl.value || storedModel() || state.model;
  modelEl.innerHTML = '';
  (models || []).forEach((model) => {
    const option = document.createElement('option');
    option.value = model.id;
    option.textContent = model.note ? `${model.label} · ${model.note}` : model.label;
    modelEl.appendChild(option);
  });
  const ids = (models || []).map((model) => model.id);
  if (ids.includes(previous)) {
    modelEl.value = previous;
  } else if (ids.includes(state.model)) {
    modelEl.value = state.model;
  }
  updateModelHint();
}

function styleLabel(style) {
  const info = (state.styles.length ? state.styles : FALLBACK_STYLES)
    .find((item) => item.key === style);
  if (info) return info.label;
  return style === 'notion' ? 'Notion 信息图' : style;
}

function setStyle(style) {
  const list = state.styles.length ? state.styles : FALLBACK_STYLES;
  const next = list.some((item) => item.key === style) ? style : 'none';
  state.style = next;
  styleEl.value = next;
}

function renderStyles(styles) {
  const list = Array.isArray(styles) && styles.length ? styles : FALLBACK_STYLES;
  const previous = styleEl.value || state.style;
  state.styles = list;
  styleEl.innerHTML = '';
  list.forEach((item) => {
    const option = document.createElement('option');
    option.value = item.key;
    option.textContent = item.label;
    styleEl.appendChild(option);
  });
  setStyle(previous);
}

function applyMode(mode) {
  const next = mode === 'notion' ? 'notion' : 'free';
  if (next === 'notion') {
    if (state.mode !== 'notion') {
      state.freeSnapshot = {
        style: state.style,
        ratio: ratioEl.value,
        resolution: resolutionEl.value,
        count: countEl.value
      };
    }
    state.mode = 'notion';
    modeEl.value = 'notion';
    setStyle('notion');
    ratioEl.value = '16:9';
    ratioEl.disabled = true;
    resolutionEl.value = '2K';
    countEl.value = '1';
    styleEl.disabled = true;
    styleHint.textContent = 'Notion 信息图使用固定风格';
    notionTip.hidden = false;
    promptEl.placeholder = NOTION_PLACEHOLDER;
    return;
  }

  const snapshot = state.freeSnapshot;
  state.mode = 'free';
  state.freeSnapshot = null;
  modeEl.value = 'free';
  styleEl.disabled = false;
  ratioEl.disabled = false;
  if (snapshot) {
    setStyle(snapshot.style);
    ratioEl.value = snapshot.ratio;
    resolutionEl.value = snapshot.resolution;
    countEl.value = snapshot.count;
  } else if (state.style === 'notion') {
    setStyle('none');
    if (ratioEl.value === '16:9') ratioEl.value = '1:1';
  }
  styleHint.textContent = '只是追加描述，不会改你的原话';
  notionTip.hidden = true;
  promptEl.placeholder = DEFAULT_PLACEHOLDER;
}

function updatePromptCount() {
  promptCount.textContent = promptEl.value.length + ' / 2000';
}

function emptyBlock(title, text, icon) {
  const wrap = document.createElement('div');
  wrap.className = 'empty';
  if (icon) {
    const ico = document.createElement('span');
    ico.className = 'ico';
    ico.setAttribute('aria-hidden', 'true');
    ico.textContent = icon;
    wrap.appendChild(ico);
  }
  const head = document.createElement('b');
  head.textContent = title;
  wrap.appendChild(head);
  if (text) {
    const line = document.createElement('span');
    line.textContent = text;
    wrap.appendChild(line);
  }
  return wrap;
}

function renderJob(job) {
  state.current = job;
  const images = job.images || [];
  const modelText = job.model ? modelLabel(job.model) + ' · ' : '';
  const styleText = job.style && job.style !== 'none' ? styleLabel(job.style) + ' · ' : '';
  jobMeta.textContent = `${statusText(job)} · ${styleText}${modelText}${job.ratio || '1:1'} · ${job.count || 1} 张 · ${formatTime(job.createdAt)}`;

  resultBody.innerHTML = '';
  if (images.length) {
    const grid = document.createElement('div');
    grid.className = 'result-grid';
    images.forEach((image) => grid.appendChild(imageCard(job, image)));
    resultBody.appendChild(grid);
  } else if (job.status === 'failed') {
    resultBody.appendChild(emptyBlock('这次没有生成成功', job.error || '可以换个描述再试一次', '😕'));
  } else {
    resultBody.appendChild(emptyBlock(
      job.status === 'queued' ? '任务排队中' : '正在生成',
      `后台按顺序处理，已生成 ${job.done || 0}/${job.count || 1} 张。`,
      '🎨'
    ));
  }

  const active = job.status === 'queued' || job.status === 'running';
  progress.hidden = !active;
  if (active) {
    const done = Math.max(0, Number(job.done) || 0);
    const total = Math.max(1, Number(job.count) || 1);
    const ratio = done ? Math.min(100, Math.round(done / total * 100)) : 8;
    progressBar.style.width = ratio + '%';
    progressText.textContent = job.status === 'queued'
      ? '排队中…'
      : `已生成 ${done}/${total}`;
  }

  jobError.hidden = !job.error;
  jobError.textContent = job.error ? (images.length ? '部分结果：' + job.error : job.error) : '';
}

function imageCard(job, image) {
  const figure = document.createElement('figure');
  figure.className = 'shot';

  const img = document.createElement('img');
  img.src = image.url;
  img.alt = `生成图 ${image.index}`;
  img.loading = 'lazy';
  figure.appendChild(img);

  const bar = document.createElement('div');
  bar.className = 'shot-bar';
  const label = document.createElement('span');
  label.textContent = '第 ' + image.index + ' 张';

  const links = document.createElement('div');
  links.className = 'links';

  const download = document.createElement('a');
  download.href = image.url;
  download.download = fileName(job, image);
  download.textContent = '下载';

  const studio = document.createElement('button');
  studio.type = 'button';
  studio.textContent = '继续编辑';
  studio.addEventListener('click', () => openInStudio(job, image, studio));

  links.appendChild(download);
  links.appendChild(studio);
  bar.appendChild(label);
  bar.appendChild(links);
  figure.appendChild(bar);
  return figure;
}

async function openInStudio(job, image, button) {
  const oldText = button.textContent;
  button.disabled = true;
  button.textContent = '接力中…';
  try {
    const res = await fetch(image.url, { credentials: 'same-origin' });
    if (!res.ok) throw new Error('图片取不回来');
    const blob = await res.blob();
    const key = await putHandoff(blob, fileName(job, image));
    location.href = '/image-studio?handoff=' + encodeURIComponent(key);
  } catch (error) {
    toast('接力失败，改为下载；可以手动拖进图片工坊', 'err');
    const a = document.createElement('a');
    a.href = image.url;
    a.download = fileName(job, image);
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => { location.href = '/image-studio'; }, 600);
  } finally {
    button.disabled = false;
    button.textContent = oldText;
  }
}

function stopPoll() {
  clearTimeout(state.polling);
  state.polling = 0;
}

function poll(jobId) {
  stopPoll();
  const tick = async () => {
    try {
      const data = await api('/image-gen/api/jobs/' + encodeURIComponent(jobId));
      const job = data.job;
      renderJob(job);
      if (job.status === 'queued' || job.status === 'running') {
        state.polling = setTimeout(tick, 1600);
        return;
      }
      setBusy(false);
      loadStatus();
      loadHistory({ autoSelect: false });
      if (job.status === 'failed') toast(job.error || '生成失败', 'err');
      else toast('生成完成', 'info');
    } catch (error) {
      setBusy(false);
      toast(error.message || '查询任务失败', 'err');
      if (error.status !== 404) state.polling = setTimeout(tick, 2600);
    }
  };
  state.polling = setTimeout(tick, 300);
}

function selectJob(job, autoPoll = true) {
  renderJob(job);
  const active = job.status === 'queued' || job.status === 'running';
  // 切到一条已结束的记录时，要把「生成中…」解锁；否则原来那条
  // 正在轮询的任务被 stopPoll 停掉后，按钮会一直灰着。
  setBusy(active);
  if (active && autoPoll) poll(job.id);
  else stopPoll();
}

function historyItem(job) {
  const wrap = document.createElement('div');
  wrap.className = 'history-item';

  const first = (job.images || [])[0];
  if (first) {
    const img = document.createElement('img');
    img.className = 'history-thumb';
    img.src = first.url;
    img.alt = job.prompt || '生成图';
    img.loading = 'lazy';
    wrap.appendChild(img);
  } else {
    const ph = document.createElement('div');
    ph.className = 'history-thumb ph';
    ph.textContent = job.status === 'failed' ? '×' : '…';
    wrap.appendChild(ph);
  }

  const main = document.createElement('button');
  main.type = 'button';
  main.className = 'history-main';
  const title = document.createElement('b');
  title.textContent = job.prompt || '未命名任务';
  const meta = document.createElement('span');
  const modelText = job.model ? modelLabel(job.model) + ' · ' : '';
  const styleText = job.style && job.style !== 'none' ? styleLabel(job.style) + ' · ' : '';
  meta.textContent = `${statusText(job)} · ${styleText}${modelText}${formatTime(job.createdAt)} · ${job.ratio || '1:1'} · ${job.count || 1} 张`;
  main.appendChild(title);
  main.appendChild(meta);
  main.addEventListener('click', () => selectJob(job));

  const del = document.createElement('button');
  del.type = 'button';
  del.className = 'history-del';
  del.textContent = '×';
  del.setAttribute('aria-label', '删除生成记录');
  if (job.status === 'queued' || job.status === 'running') {
    del.disabled = true;
    del.title = '任务进行中，暂时不能删除';
  } else {
    del.addEventListener('click', () => deleteJob(job));
  }

  wrap.appendChild(main);
  wrap.appendChild(del);
  return wrap;
}

function renderHistory(items) {
  state.history = items;
  historyList.innerHTML = '';
  historyCount.textContent = items.length ? items.length + ' 条' : '';
  if (!items.length) {
    const empty = document.createElement('div');
    empty.className = 'history-empty';
    empty.textContent = '还没有生成记录';
    historyList.appendChild(empty);
    return;
  }
  items.forEach((job) => historyList.appendChild(historyItem(job)));
}

async function loadHistory({ autoSelect = true } = {}) {
  try {
    const data = await api('/image-gen/api/jobs');
    const items = data.items || [];
    renderHistory(items);
    if (autoSelect && !state.current && items.length) {
      selectJob(items[0], true);
    }
  } catch (error) {
    historyList.innerHTML = '';
    const empty = document.createElement('div');
    empty.className = 'history-empty';
    empty.textContent = error.message || '记录加载失败';
    historyList.appendChild(empty);
  }
}

async function deleteJob(job) {
  if (!window.confirm(`删除这条生成记录？图片文件也会一起删掉。\n\n${job.prompt || ''}`)) return;
  try {
    await api('/image-gen/api/jobs/' + encodeURIComponent(job.id), { method: 'DELETE' });
    if (state.current && state.current.id === job.id) {
      stopPoll();
      setBusy(false);
      state.current = null;
      resultBody.innerHTML = '';
      resultBody.appendChild(emptyBlock('已删除', '再写一句描述生成新的图片吧', '🎨'));
      jobMeta.textContent = '还没有任务';
      progress.hidden = true;
      jobError.hidden = true;
    }
    await loadHistory({ autoSelect: false });
    toast('已删除', 'info');
  } catch (error) {
    toast(error.message || '删除失败', 'err');
  }
}

async function loadStatus() {
  try {
    const data = await api('/image-gen/api/status');
    state.ready = !!data.ready;
    state.model = data.model || '';
    state.models = Array.isArray(data.models) ? data.models : [];
    state.dailyLimit = Number(data.dailyLimit) || 0;
    state.usedToday = Number(data.usedToday) || 0;
    state.ttlDays = Number(data.ttlDays) || 7;
    if (!state.ready) {
      showBanner('服务端还没有配置火山方舟 Key（ARK_API_KEY）。把 Key 加到 .env 或环境变量后重启服务，这个页面就能用了。');
    } else if (data.storageError) {
      showBanner('数据库暂时不可用：' + data.storageError);
    } else {
      showBanner('');
    }
    renderModels(state.models);
    renderStyles(data.styles);
    // 历史可能比 status 先渲染完，模型显示名要等清单到了再刷一遍。
    if (state.history.length) renderHistory(state.history);
    if (state.current) renderJob(state.current);
    updateQuota();
    setBusy(state.busy);
  } catch (error) {
    state.ready = false;
    showBanner(error.message || '服务状态读取失败');
    setBusy(false);
    updateQuota();
  }
}

async function generate() {
  if (state.busy) return;
  const prompt = promptEl.value.trim();
  if (!prompt) {
    toast('先写一句画面描述', 'err');
    promptEl.focus();
    return;
  }
  if (!state.ready) {
    toast('服务端还没有配置 ARK_API_KEY', 'err');
    return;
  }

  const body = {
    prompt,
    negative: negativeEl.value.trim(),
    style: state.style,
    ratio: ratioEl.value,
    resolution: resolutionEl.value,
    count: Number(countEl.value) || 1,
    model: modelEl.value || state.model
  };

  setBusy(true);
  stopPoll();
  state.current = null;
  resultBody.innerHTML = '';
  resultBody.appendChild(emptyBlock('任务已提交', '正在等火山方舟返回第一张图…', '⏳'));
  jobMeta.textContent = '排队中';
  jobError.hidden = true;
  progress.hidden = false;
  progressBar.style.width = '8%';
  progressText.textContent = '提交任务…';

  try {
    const data = await api('/image-gen/api/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    renderJob(data.job);
    loadStatus();
    loadHistory({ autoSelect: false });
    poll(data.job.id);
  } catch (error) {
    setBusy(false);
    progress.hidden = true;
    resultBody.innerHTML = '';
    resultBody.appendChild(emptyBlock('没有提交成功', error.message || '请稍后再试', '😕'));
    jobMeta.textContent = '提交失败';
    toast(error.message || '提交失败', 'err');
    if (error.status === 503) loadStatus();
  }
}

// ---------------------------------------------------------------
// 事件绑定
// ---------------------------------------------------------------
modelEl.addEventListener('change', () => {
  rememberModel(modelEl.value);
  updateModelHint();
});

modeEl.addEventListener('change', () => applyMode(modeEl.value));
styleEl.addEventListener('change', () => {
  if (styleEl.value === 'notion') {
    // 从风格下拉直接选信息图，也切到对应模式，保证比例是 16:9。
    applyMode('notion');
    return;
  }
  setStyle(styleEl.value);
});

promptEl.addEventListener('input', updatePromptCount);
promptEl.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
    event.preventDefault();
    generate();
  }
});
generateBtn.addEventListener('click', generate);
refreshBtn.addEventListener('click', () => {
  loadStatus();
  loadHistory({ autoSelect: !state.current });
});

setStyle('none');
applyMode('free');
updatePromptCount();
loadStatus();
loadHistory();
