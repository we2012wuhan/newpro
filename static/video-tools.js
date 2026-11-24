/*
 * 视频小工具 · 页面交互
 * =====================
 * 流程：
 *   1. POST /video-tools/api/uploads：源视频只传一次，ffmpeg 探测时长/分辨率；
 *   2. 对同一素材提交 clip / gif / audio / frames / contact / compress 任务；
 *   3. 任务在服务端串行处理，页面轮询状态，完成后直接预览或下载；
 *   4. 素材和任务都按账号隔离，2 天后由服务端清理。
 *
 * 所有来自服务端的文字都用 textContent / createElement 放进页面，不拼 innerHTML。
 */

const $ = (id) => document.getElementById(id);

const banner = $('banner');
const drop = $('drop');
const dropHint = $('dropHint');
const fileInput = $('fileInput');
const upProgress = $('upProgress');
const upBar = $('upBar');
const upText = $('upText');
const uploadList = $('uploadList');
const uploadCount = $('uploadCount');
const viewerTitle = $('viewerTitle');
const viewerSub = $('viewerSub');
const assetInfo = $('assetInfo');
const infoDuration = $('infoDuration');
const infoSize = $('infoSize');
const infoRes = $('infoRes');
const infoFps = $('infoFps');
const infoCodec = $('infoCodec');
const infoAudio = $('infoAudio');
const kindTabs = $('kindTabs');
const kindHint = $('kindHint');
const forms = $('forms');
const runBtn = $('runBtn');
const progress = $('progress');
const progressBar = $('progressBar');
const progressText = $('progressText');
const jobError = $('jobError');
const result = $('result');
const jobList = $('jobList');
const historyCount = $('historyCount');
const statusEl = $('status');

const KINDS_FALLBACK = [
  { key: 'clip', label: '截取片段', hint: '按起止时间导出 MP4，可快速复制或精确重编码' },
  { key: 'gif', label: '转 GIF', hint: '1–10 秒，自动生成调色板，动图更干净' },
  { key: 'audio', label: '提取音频', hint: 'm4a / mp3，或转写用的 16k 单声道 WAV' },
  { key: 'frames', label: '抽帧', hint: '按时间点抓 1–9 张 JPG，多张自动打包 ZIP' },
  { key: 'contact', label: '九宫格', hint: '均匀取帧拼成一张总览图' },
  { key: 'compress', label: '压缩瘦身', hint: '三档画质，最高 1080p' }
];

const KIND_ICON = {
  clip: '✂️', gif: '🧩', audio: '🎧', frames: '🖼️', contact: '🗂️', compress: '📦'
};

const KIND_LABEL = {
  clip: '截取片段', gif: '转 GIF', audio: '提取音频', frames: '抽帧',
  contact: '九宫格', compress: '压缩瘦身'
};

const STATUS_TEXT = {
  queued: '排队中', running: '处理中', succeeded: '已完成', failed: '失败'
};

const state = {
  ready: false,
  status: null,
  kinds: KINDS_FALLBACK,
  uploads: [],
  jobs: [],
  selectedId: null,
  kind: 'clip',
  current: null,
  timer: 0,
  pollCount: 0,
  uploading: false,
  statusTimer: 0,
  lastAppliedId: null
};

const STATUS_URL = '/video-tools/api/status';

// ---------------------------------------------------------------- 基础工具
function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function fmtSize(bytes) {
  const n = Number(bytes) || 0;
  if (n <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = n;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  const fixed = i === 0 ? Math.round(value) : (value >= 100 ? value.toFixed(0) : value.toFixed(1));
  return fixed + ' ' + units[i];
}

function fmtDuration(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h) return h + ':' + String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
  return m + ':' + String(s).padStart(2, '0');
}

function shortTime(text) {
  const raw = String(text || '');
  return raw.length >= 16 ? raw.slice(5, 16) : raw;
}

function toast(message, kind) {
  clearTimeout(state.statusTimer);
  if (!message) {
    statusEl.classList.remove('show');
    return;
  }
  statusEl.textContent = message;
  statusEl.className = 'status show' + (kind === 'err' ? ' err' : '');
  state.statusTimer = setTimeout(() => statusEl.classList.remove('show'),
    kind === 'err' ? 4200 : 2600);
}

async function api(url, options) {
  const opts = Object.assign({ credentials: 'same-origin' }, options || {});
  const res = await fetch(url, opts);
  let data = null;
  try {
    data = await res.json();
  } catch (error) {
    data = null;
  }
  if (!res.ok || !data || data.ok === false) {
    const message = (data && data.message) || ('请求失败（' + res.status + '）');
    const failure = new Error(message);
    failure.status = res.status;
    throw failure;
  }
  return data;
}

function readSeg(name) {
  const box = document.querySelector('[data-seg="' + name + '"]');
  const on = box && box.querySelector('.seg-btn.on');
  return on ? on.dataset.val : '';
}

function numVal(id) {
  const node = $(id);
  const value = node ? Number(node.value) : NaN;
  return Number.isFinite(value) ? value : NaN;
}

// ---------------------------------------------------------------- 状态与素材
function selectedUpload() {
  for (const item of state.uploads) {
    if (item.id === state.selectedId) return item;
  }
  return null;
}

function setBanner(text, info) {
  if (!text) {
    banner.hidden = true;
    banner.textContent = '';
    return;
  }
  banner.hidden = false;
  banner.textContent = text;
  banner.className = 'banner' + (info ? ' info' : '');
}

async function loadStatus() {
  try {
    const data = await api(STATUS_URL);
    state.status = data;
    state.ready = !!data.ready;
    if (Array.isArray(data.kinds) && data.kinds.length) state.kinds = data.kinds;
    if (!state.ready) {
      setBanner(data.missing || '服务器暂时不能处理视频。');
      state.ready = false;
    } else {
      setBanner('', false);
    }
    if (data.maxMb) dropHint.textContent = 'MP4 / MOV / MKV，单个最大 ' + data.maxMb + ' MB';
    if (data.maxUploads) {
      state.maxUploads = data.maxUploads;
    }
    renderTabs();
    updateQuotaText();
    updateRunState();
    if (data.storageError) {
      setBanner('存储暂时不可用：' + data.storageError);
    }
  } catch (error) {
    state.ready = false;
    setBanner(error.message || '服务状态读取失败');
    updateRunState();
  }
}

function updateQuotaText() {
  if (state.selectedId) return;
  const status = state.status;
  if (!status) {
    viewerSub.textContent = '上传一次，多种处理复用';
    return;
  }
  viewerSub.textContent = '今日任务 ' + (status.usedToday || 0) + ' / ' +
    (status.dailyLimit || 0) + ' · 素材保留 ' + (status.ttlDays || 2) + ' 天';
}

function renderTabs() {
  kindTabs.textContent = '';
  state.kinds.forEach((kind) => {
    const btn = el('button', 'kind-tab' + (kind.key === state.kind ? ' on' : ''), kind.label);
    btn.type = 'button';
    btn.dataset.kind = kind.key;
    btn.setAttribute('role', 'tab');
    btn.setAttribute('aria-selected', kind.key === state.kind ? 'true' : 'false');
    btn.addEventListener('click', () => setKind(kind.key));
    kindTabs.appendChild(btn);
  });
  kindTabs.hidden = !state.selectedId;
}

function setKind(key) {
  if (!KIND_LABEL[key]) key = 'clip';
  state.kind = key;
  Array.from(kindTabs.querySelectorAll('.kind-tab')).forEach((btn) => {
    const on = btn.dataset.kind === key;
    btn.classList.toggle('on', on);
    btn.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  Array.from(forms.querySelectorAll('.kind-form')).forEach((box) => {
    box.hidden = box.dataset.kind !== key;
  });
  const info = state.kinds.find((item) => item.key === key);
  kindHint.hidden = !state.selectedId;
  kindHint.textContent = info ? info.hint : '';
  updateRunState();
}

function renderUploads() {
  uploadList.textContent = '';
  if (!state.uploads.length) {
    uploadList.appendChild(el('div', 'empty-soft', '还没有素材，先传一个视频。'));
  } else {
    state.uploads.forEach((item) => {
      const row = el('div', 'upload-item' + (item.id === state.selectedId ? ' on' : ''));
      row.appendChild(el('div', 'upload-thumb', '🎞'));

      const main = el('button', 'upload-main');
      main.type = 'button';
      main.appendChild(el('b', '', item.name));
      const meta = fmtDuration(item.duration) + ' · ' + fmtSize(item.size);
      main.appendChild(el('span', '', meta));
      main.addEventListener('click', () => selectUpload(item, false));
      row.appendChild(main);

      const del = el('button', 'upload-del', '×');
      del.type = 'button';
      del.title = '删除素材';
      del.setAttribute('aria-label', '删除素材 ' + item.name);
      del.addEventListener('click', () => deleteUpload(item));
      row.appendChild(del);
      uploadList.appendChild(row);
    });
  }
  const max = state.status && state.status.maxUploads ? state.status.maxUploads : 3;
  uploadCount.textContent = state.uploads.length + ' / ' + max;
}

function applyUpload(upload, keepResult) {
  state.selectedId = upload.id;
  viewerTitle.textContent = upload.name;
  viewerSub.textContent = '创建于 ' + String(upload.createdAt || '').slice(0, 16);

  infoDuration.textContent = fmtDuration(upload.duration);
  infoSize.textContent = fmtSize(upload.size);
  infoRes.textContent = upload.width && upload.height
    ? upload.width + ' × ' + upload.height : '分辨率未知';
  infoFps.textContent = upload.fps ? (Math.round(upload.fps * 10) / 10) + ' fps' : '帧率未知';
  infoCodec.textContent = (upload.videoCodec || '未知编码').toUpperCase();
  infoAudio.textContent = upload.hasAudio
    ? ('音轨 ' + (upload.audioCodec || '').toUpperCase()) : '无音轨';

  const duration = Number(upload.duration) || 0;
  const fresh = state.lastAppliedId !== upload.id;
  state.lastAppliedId = upload.id;
  const clipEnd = $('clipEnd');
  const gifDuration = $('gifDuration');
  const framesTimes = $('framesTimes');
  if (fresh && clipEnd) {
    clipEnd.value = String(Math.max(0.1, Math.min(duration || 5, 5)).toFixed(1));
  }
  if (fresh && gifDuration) {
    gifDuration.value = String(Math.max(1, Math.min(duration || 3, 3)).toFixed(1));
  }
  if (fresh && framesTimes) {
    framesTimes.value = duration >= 6 ? '1, 5' : '0.5';
  }

  assetInfo.hidden = false;
  kindTabs.hidden = false;
  kindHint.hidden = false;
  forms.hidden = false;
  renderTabs();
  setKind(state.kind);
  renderUploads();
  if (!keepResult) resetResult();
}

function selectUpload(upload, keepResult) {
  if (state.current) stopPoll();
  state.current = null;
  applyUpload(upload, keepResult);
  progress.hidden = true;
  jobError.hidden = true;
}

function clearSelection() {
  state.selectedId = null;
  state.current = null;
  stopPoll();
  assetInfo.hidden = true;
  kindTabs.hidden = true;
  kindHint.hidden = true;
  forms.hidden = true;
  viewerTitle.textContent = '先选一个素材';
  progress.hidden = true;
  jobError.hidden = true;
  resetResult();
  updateQuotaText();
  renderUploads();
  updateRunState();
}

async function loadUploads(selectFirst) {
  try {
    const data = await api('/video-tools/api/uploads');
    state.uploads = Array.isArray(data.items) ? data.items : [];
    const exists = state.uploads.some((item) => item.id === state.selectedId);
    if (!exists && state.selectedId !== null) {
      state.selectedId = null;
      assetInfo.hidden = true;
      kindTabs.hidden = true;
      kindHint.hidden = true;
      forms.hidden = true;
    }
    renderUploads();
    if (selectFirst && !state.selectedId && state.uploads.length) {
      selectUpload(state.uploads[0], false);
    }
    updateQuotaText();
    updateRunState();
  } catch (error) {
    toast(error.message || '素材列表读取失败', 'err');
  }
}

function updateRunState() {
  if (!runBtn) return;
  const upload = selectedUpload();
  const blocked = !state.ready || !upload;
  runBtn.disabled = blocked || state.uploading;
  if (!upload) return;
  if (state.kind === 'audio' && !upload.hasAudio) {
    runBtn.disabled = true;
    kindHint.textContent = '这个视频没有音轨，提取不了音频。';
  }
}

// ---------------------------------------------------------------- 上传
const ALLOWED_EXT = ['mp4', 'm4v', 'mov', 'webm', 'mkv', 'avi', 'flv', 'wmv',
  'mpeg', 'mpg', 'ts', 'mts', '3gp'];

function setUploadProgress(percent, text) {
  upProgress.hidden = false;
  upBar.style.width = Math.max(0, Math.min(100, percent)) + '%';
  upText.textContent = text;
}

function uploadFile(file) {
  if (!file || state.uploading) return;
  if (!state.ready) {
    toast('服务器暂时不能处理视频', 'err');
    return;
  }
  const name = String(file.name || 'video');
  const ext = (name.split('.').pop() || '').toLowerCase();
  if (ALLOWED_EXT.indexOf(ext) < 0) {
    toast('支持 MP4 / MOV / MKV 等视频格式', 'err');
    return;
  }
  const maxMb = state.status && state.status.maxMb ? state.status.maxMb : 300;
  if (file.size > maxMb * 1024 * 1024) {
    toast('视频超过 ' + maxMb + ' MB 上限', 'err');
    return;
  }

  state.uploading = true;
  updateRunState();
  setUploadProgress(0, '准备上传…');

  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/video-tools/api/uploads');
  xhr.withCredentials = true;
  xhr.responseType = 'json';

  xhr.upload.onprogress = (event) => {
    if (!event.lengthComputable) return;
    const percent = event.total ? (event.loaded / event.total) * 100 : 0;
    setUploadProgress(percent, '上传中 ' + Math.round(percent) + '% · ' + fmtSize(event.loaded));
  };

  const finish = () => {
    state.uploading = false;
    updateRunState();
  };

  xhr.onload = async () => {
    finish();
    upProgress.hidden = true;
    const data = xhr.response;
    if (xhr.status === 201 && data && data.ok && data.upload) {
      toast('素材上传好了', 'info');
      await loadUploads(false);
      selectUpload(data.upload, false);
      await loadJobs();
      return;
    }
    const message = (data && data.message) ||
      (xhr.status === 413 ? '视频太大' : '上传失败（' + xhr.status + '）');
    toast(message, 'err');
  };
  xhr.onerror = () => {
    finish();
    upProgress.hidden = true;
    toast('上传中断，请检查网络后重试', 'err');
  };
  xhr.onabort = () => {
    finish();
    upProgress.hidden = true;
    toast('上传已取消', 'err');
  };

  const form = new FormData();
  form.append('file', file, name);
  setUploadProgress(0, '上传中…');
  xhr.send(form);
}

function onDrop(event) {
  event.preventDefault();
  drop.classList.remove('over');
  const files = event.dataTransfer && event.dataTransfer.files;
  if (files && files.length) uploadFile(files[0]);
}

// ---------------------------------------------------------------- 任务
function collectParams() {
  const kind = state.kind;
  if (kind === 'clip') {
    const start = numVal('clipStart');
    const end = numVal('clipEnd');
    if (!Number.isFinite(start) || start < 0) throw new Error('开始时间要填 0 或更大的数字');
    if (!Number.isFinite(end)) throw new Error('结束时间要填数字');
    if (end <= start) throw new Error('结束时间要晚于开始时间');
    if (end - start > 600) throw new Error('截取时长最多 600 秒');
    return { start: start, end: end, mode: readSeg('clipMode') || 'fast' };
  }
  if (kind === 'gif') {
    const start = numVal('gifStart');
    const duration = numVal('gifDuration');
    if (!Number.isFinite(start) || start < 0) throw new Error('开始时间要填 0 或更大的数字');
    if (!Number.isFinite(duration) || duration < 1 || duration > 10) {
      throw new Error('GIF 时长要在 1–10 秒之间');
    }
    return {
      start: start, duration: duration,
      width: Number(readSeg('gifWidth') || 480),
      fps: Number(readSeg('gifFps') || 15)
    };
  }
  if (kind === 'audio') {
    return { format: readSeg('audioFormat') || 'm4a' };
  }
  if (kind === 'frames') {
    const raw = ($('framesTimes') && $('framesTimes').value || '').trim();
    if (!raw) throw new Error('至少填一个时间点');
    return { times: raw };
  }
  if (kind === 'contact') {
    return { grid: readSeg('contactGrid') || '3x3' };
  }
  if (kind === 'compress') {
    return {
      quality: readSeg('compressQuality') || 'balanced',
      cap: Number(readSeg('compressCap') || 720)
    };
  }
  throw new Error('不支持的操作类型');
}

async function submitJob() {
  const upload = selectedUpload();
  if (!upload) {
    toast('先选一个视频素材', 'err');
    return;
  }
  let params;
  try {
    params = collectParams();
  } catch (error) {
    toast(error.message, 'err');
    return;
  }
  runBtn.disabled = true;
  runBtn.textContent = '提交中…';
  try {
    const data = await api('/video-tools/api/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ uploadId: upload.id, kind: state.kind, params: params })
    });
    state.current = data.job;
    state.pollCount = 0;
    progress.hidden = false;
    progressBar.style.width = '4%';
    progressText.textContent = '已提交，排队中…';
    jobError.hidden = true;
    renderPending(data.job);
    await loadJobs();
    schedulePoll(300);
  } catch (error) {
    toast(error.message || '提交失败', 'err');
  } finally {
    runBtn.textContent = '开始处理';
    updateRunState();
  }
}

function stopPoll() {
  clearTimeout(state.timer);
  state.timer = 0;
}

function schedulePoll(delay) {
  clearTimeout(state.timer);
  state.timer = setTimeout(poll, delay);
}

async function poll() {
  const current = state.current;
  if (!current) return;
  try {
    const data = await api('/video-tools/api/jobs/' + encodeURIComponent(current.id));
    const job = data.job;
    state.current = job;
    renderJobState(job);
    if (job.status === 'succeeded') {
      renderResult(job);
      toast('处理好了', 'info');
      await loadJobs();
      await loadUploads(false);
      if (!state.selectedId) {
        const upload = state.uploads.find((item) => item.id === job.uploadId);
        if (upload) applyUpload(upload, true);
      }
      return;
    }
    if (job.status === 'failed') {
      jobError.hidden = false;
      jobError.textContent = job.error || '处理失败，请换个参数再试。';
      renderFailed(job);
      toast(job.error || '处理失败', 'err');
      await loadJobs();
      return;
    }
  } catch (error) {
    jobError.hidden = false;
    jobError.textContent = error.message || '查询任务失败';
    if (error.status === 404) return;
  }
  state.pollCount += 1;
  schedulePoll(1500);
}

function renderJobState(job) {
  progress.hidden = false;
  const percent = job.status === 'succeeded' ? 100 : Math.max(4, Number(job.progress) || 0);
  progressBar.style.width = percent + '%';
  const label = STATUS_TEXT[job.status] || '处理中';
  progressText.textContent = job.status === 'queued'
    ? '排队中…' : label + ' · ' + percent + '%';
  if (job.status !== 'failed') jobError.hidden = true;
}

function renderPending(job) {
  result.textContent = '';
  const box = el('div', 'result-empty');
  box.appendChild(el('div', 'ico', KIND_ICON[job.kind] || '🎬'));
  box.appendChild(el('b', '', (KIND_LABEL[job.kind] || '任务') + '已提交'));
  box.appendChild(el('span', '', '处理完成会自动出现在这里。'));
  result.appendChild(box);
}

function resetResult() {
  result.textContent = '';
  const box = el('div', 'result-empty');
  box.id = 'resultEmpty';
  box.appendChild(el('div', 'ico', '🎬'));
  box.appendChild(el('b', '', '结果会出现在这里'));
  box.appendChild(el('span', '', '选一个素材，再挑一种处理方式。'));
  result.appendChild(box);
}

function renderFailed(job) {
  result.textContent = '';
  const box = el('div', 'result-empty');
  box.appendChild(el('div', 'ico', '⚠️'));
  box.appendChild(el('b', '', '这次没有成功'));
  box.appendChild(el('span', '', job.error || '换个参数或换个时间点再试一次。'));
  result.appendChild(box);
}

function pickPreview(job) {
  const outputs = job.outputs || [];
  const media = outputs.filter((item) => /^(video|image|audio)\//.test(item.mime || ''));
  const primary = outputs.find((item) => item.index === job.primaryIndex);
  if (primary && media.indexOf(primary) >= 0) return primary;
  return media[0] || null;
}

function renderResult(job) {
  result.textContent = '';
  const head = el('div', 'result-head');
  const title = el('b', '', (KIND_LABEL[job.kind] || '结果') +
    (job.uploadName ? ' · ' + job.uploadName : ''));
  head.appendChild(title);
  head.appendChild(el('span', '', fmtSize(job.outputSize) + ' · ' + shortTime(job.finishedAt)));
  result.appendChild(head);

  if (job.note) result.appendChild(el('p', 'note-box', job.note));

  if (!job.outputs || !job.outputs.length) {
    result.appendChild(el('div', 'empty-soft', '这个任务没有生成文件。'));
    return;
  }

  const preview = pickPreview(job);
  if (preview) {
    const media = el('div', 'result-media');
    const mime = preview.mime || '';
    if (mime.indexOf('video/') === 0) {
      const video = document.createElement('video');
      video.controls = true;
      video.preload = 'metadata';
      video.src = preview.url;
      media.appendChild(video);
    } else if (mime.indexOf('image/') === 0) {
      const img = document.createElement('img');
      img.loading = 'lazy';
      img.alt = preview.label || preview.name;
      img.src = preview.url;
      media.appendChild(img);
    } else if (mime.indexOf('audio/') === 0) {
      const audio = document.createElement('audio');
      audio.controls = true;
      audio.preload = 'metadata';
      audio.src = preview.url;
      media.appendChild(audio);
    }
    if (media.childNodes.length) result.appendChild(media);
  }

  const files = el('div', 'result-files');
  job.outputs.forEach((item) => {
    const link = el('a', 'file-chip');
    link.href = item.url;
    link.download = item.name || '';
    link.appendChild(el('b', '', item.label || item.name || '下载'));
    link.appendChild(el('small', '', fmtSize(item.size)));
    files.appendChild(link);
  });
  result.appendChild(files);

  jobError.hidden = true;
  jobError.textContent = '';
}

async function openJob(id) {
  stopPoll();
  try {
    const data = await api('/video-tools/api/jobs/' + encodeURIComponent(id));
    const job = data.job;
    state.current = job;
    const upload = state.uploads.find((item) => item.id === job.uploadId);
    if (upload) {
      applyUpload(upload, true);
    } else {
      viewerTitle.textContent = job.uploadName || '视频';
      viewerSub.textContent = (KIND_LABEL[job.kind] || '任务') + ' · ' + shortTime(job.createdAt);
    }
    if (job.status === 'succeeded') {
      progress.hidden = true;
      renderResult(job);
    } else if (job.status === 'failed') {
      progress.hidden = true;
      jobError.hidden = false;
      jobError.textContent = job.error || '处理失败';
      renderFailed(job);
    } else {
      jobError.hidden = true;
      renderJobState(job);
      renderPending(job);
      state.pollCount = 0;
      schedulePoll(300);
    }
  } catch (error) {
    toast(error.message || '任务读取失败', 'err');
  }
}

function renderJobs() {
  jobList.textContent = '';
  historyCount.textContent = state.jobs.length ? (state.jobs.length + ' 条') : '';
  if (!state.jobs.length) {
    jobList.appendChild(el('div', 'history-empty', '还没有处理记录'));
    return;
  }
  state.jobs.forEach((job) => {
    const row = el('div', 'history-item');
    const thumb = el('div', 'history-thumb' + (job.status === 'failed' ? ' bad' : ''),
      KIND_ICON[job.kind] || '🎬');
    row.appendChild(thumb);

    const main = el('button', 'history-main');
    main.type = 'button';
    main.appendChild(el('b', '', (job.uploadName || '视频') + ' · ' +
      (KIND_LABEL[job.kind] || job.kind)));
    const meta = el('span', '', STATUS_TEXT[job.status] || job.status);
    if (job.status === 'queued' || job.status === 'running') {
      meta.appendChild(el('i', 'stage-pill run', '进行中'));
    } else if (job.status === 'succeeded') {
      meta.appendChild(el('i', 'stage-pill ok', fmtSize(job.outputSize)));
    } else {
      meta.appendChild(el('i', 'stage-pill bad', '失败'));
    }
    meta.appendChild(document.createTextNode(' · ' + shortTime(job.createdAt)));
    main.appendChild(meta);
    main.addEventListener('click', () => openJob(job.id));
    row.appendChild(main);

    const del = el('button', 'job-del', '×');
    del.type = 'button';
    del.title = '删除任务记录和产物';
    del.setAttribute('aria-label', '删除任务');
    if (job.status === 'queued' || job.status === 'running') {
      del.disabled = true;
    } else {
      del.addEventListener('click', () => deleteJob(job));
    }
    row.appendChild(del);
    jobList.appendChild(row);
  });
}

async function loadJobs() {
  try {
    const data = await api('/video-tools/api/jobs');
    state.jobs = Array.isArray(data.items) ? data.items : [];
    renderJobs();
  } catch (error) {
    jobList.textContent = '';
    jobList.appendChild(el('div', 'history-empty', error.message || '记录加载失败'));
    historyCount.textContent = '';
  }
}

async function deleteJob(job) {
  const title = (job.uploadName || '视频') + ' · ' + (KIND_LABEL[job.kind] || '');
  if (!window.confirm('删除这个任务的产物和记录？\n\n' + title)) return;
  try {
    await api('/video-tools/api/jobs/' + encodeURIComponent(job.id), { method: 'DELETE' });
    if (state.current && state.current.id === job.id) {
      state.current = null;
      stopPoll();
      progress.hidden = true;
      jobError.hidden = true;
      resetResult();
    }
    await loadJobs();
    toast('已删除', 'info');
  } catch (error) {
    toast(error.message || '删除失败', 'err');
  }
}

async function deleteUpload(upload) {
  if (!window.confirm('删除素材「' + upload.name + '」？它的处理记录和产物也会一起删掉。')) {
    return;
  }
  try {
    await api('/video-tools/api/uploads/' + encodeURIComponent(upload.id),
      { method: 'DELETE' });
    if (state.selectedId === upload.id) clearSelection();
    await loadUploads(true);
    await loadJobs();
    toast('素材已删除', 'info');
  } catch (error) {
    toast(error.message || '删除失败', 'err');
  }
}

// ---------------------------------------------------------------- 事件绑定
function bindSegs() {
  Array.from(document.querySelectorAll('[data-seg]')).forEach((box) => {
    box.addEventListener('click', (event) => {
      const btn = event.target.closest('.seg-btn');
      if (!btn || !box.contains(btn)) return;
      Array.from(box.querySelectorAll('.seg-btn')).forEach((item) => {
        item.classList.toggle('on', item === btn);
      });
    });
  });
}

function bindEvents() {
  drop.addEventListener('click', () => fileInput.click());
  drop.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      fileInput.click();
    }
  });
  drop.addEventListener('dragover', (event) => {
    event.preventDefault();
    drop.classList.add('over');
  });
  drop.addEventListener('dragleave', () => drop.classList.remove('over'));
  drop.addEventListener('drop', onDrop);

  fileInput.addEventListener('change', () => {
    const file = fileInput.files && fileInput.files[0];
    fileInput.value = '';
    if (file) uploadFile(file);
  });

  runBtn.addEventListener('click', submitJob);

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && state.current &&
        (state.current.status === 'queued' || state.current.status === 'running')) {
      state.pollCount = 0;
      schedulePoll(300);
    }
  });
}

async function boot() {
  bindSegs();
  bindEvents();
  await loadStatus();
  await loadUploads(true);
  await loadJobs();
  const active = state.jobs.find((job) =>
    job.status === 'queued' || job.status === 'running');
  if (active) {
    await openJob(active.id);
  } else if (!state.selectedId) {
    updateQuotaText();
  }
}

boot();
