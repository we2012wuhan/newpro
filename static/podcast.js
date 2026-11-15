/*
 * AI 双人播客 · 页面交互
 * =======================
 * 流程：
 *   1. 读 /podcast/api/status，确认文本模型 / 语音合成 / ffmpeg 都就绪；
 *   2. 提交主题，后端先写逐字稿，再逐段合成，最后拼成一条 MP3；
 *   3. 每 1.6 秒轮询 /podcast/api/jobs/{id}，直到 succeeded / failed；
 *   4. 成功后直接播放、下载 MP3 或文字稿。
 *
 * 音频地址需要登录 Cookie，不能当成公开链接分享。
 */

const $ = (id) => document.getElementById(id);

const topicEl = $('topic');
const topicCount = $('topicCount');
const materialEl = $('material');
const materialCount = $('materialCount');
const toneEl = $('tone');
const toneTip = $('toneTip');
const durationsEl = $('durations');
const generateBtn = $('generate');
const quotaEl = $('quota');
const keyBanner = $('keyBanner');
const resultBody = $('resultBody');
const jobMeta = $('jobMeta');
const progress = $('progress');
const progressBar = $('progressBar');
const progressText = $('progressText');
const stageTip = $('stageTip');
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

const FALLBACK_TONES = [
  { key: 'casual', label: '轻松闲聊', hint: '像两个朋友边喝咖啡边聊，语气自然，可以开玩笑，但别油腻。' },
  { key: 'deep', label: '深度对谈', hint: '认真拆解问题，有观点、有追问，也允许承认不确定。' },
  { key: 'news', label: '新闻解读', hint: '先交代背景，再解释影响，最后给普通人一个观察角度。' },
  { key: 'story', label: '故事叙事', hint: '用具体场景和转折推进，少讲道理，多讲发生了什么。' },
  { key: 'science', label: '科普拆解', hint: '把复杂概念讲清楚，多用生活类比，专业词第一次出现要解释。' }
];

const WAVE = [30, 55, 80, 45, 95, 60, 38, 72, 50, 88, 42, 66];

const state = {
  ready: false,
  missing: '',
  tones: FALLBACK_TONES,
  dailyLimit: 0,
  usedToday: 0,
  ttlDays: 7,
  minutes: 5,
  jobs: [],
  current: null,
  busy: false,
  polling: 0,
  statusTimer: 0
};

function esc(value) {
  return String(value == null ? '' : value).replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[ch]));
}

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

function formatDuration(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  if (!total) return '';
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return `${minutes}:${String(rest).padStart(2, '0')}`;
}

function toneInfo(key) {
  return state.tones.find((item) => item.key === key)
    || FALLBACK_TONES.find((item) => item.key === key)
    || FALLBACK_TONES[0];
}

function statusText(job) {
  if (job.status === 'running' && job.stage === 'synthesizing') {
    return `合成语音 ${job.doneTurns || 0}/${job.totalTurns || 0}`;
  }
  if (job.status === 'running' && job.stage === 'script') return '写逐字稿';
  return STATUS_TEXT[job.status] || job.status || '未知';
}

function stageText(job) {
  if (job.status === 'queued') return '排队中，前面还有任务在处理…';
  if (job.stage === 'script') return '正在写完整对谈稿，通常十几秒到半分钟。';
  if (job.stage === 'synthesizing') {
    return `正在逐段合成语音：${job.doneTurns || 0} / ${job.totalTurns || 0} 段。`
      + '两位主持人使用不同音色，全部合成完后会拼接成一条 MP3。';
  }
  if (job.stage === 'done') return '已完成';
  return '正在处理…';
}

function setBusy(busy) {
  state.busy = busy;
  generateBtn.disabled = busy || !state.ready;
  generateBtn.textContent = busy ? '正在制作…' : '生成这期播客';
}

function updateQuota() {
  if (!state.ready) {
    quotaEl.textContent = '服务端配置好模型 Key、语音 Key 和 ffmpeg 之后，这里会显示今日额度。';
    return;
  }
  const limit = state.dailyLimit || 0;
  const used = state.usedToday || 0;
  quotaEl.textContent = limit
    ? `今日已用 ${used} / ${limit} 分钟；每期会同时调用文本模型和语音合成。`
    : '每期会同时调用文本模型和语音合成。';
}

function updateToneTip() {
  const info = toneInfo(toneEl.value);
  toneTip.textContent = info.hint || '';
}

function updateCounts() {
  topicCount.textContent = topicEl.value.length + ' / 200';
  materialCount.textContent = materialEl.value.length + ' / 8000';
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

function coverHtml(job) {
  const name = job.title || job.topic || '新一期播客';
  const bars = WAVE.map((height) => `<i style="height:${height}%"></i>`).join('');
  return `<div class="cover" aria-hidden="true">
    <div class="kicker">AI PODCAST</div>
    <div class="wave">${bars}</div>
    <div class="name">${esc(name)}</div>
  </div>`;
}

function pillsHtml(job) {
  const pills = [
    `<span class="pill">${esc(job.toneLabel || toneInfo(job.tone).label)}</span>`,
    `<span class="pill">${job.minutes || 5} 分钟</span>`,
    `<span class="pill host-a">小唯</span>`,
    `<span class="pill host-b">刘飞</span>`
  ];
  if (job.durationSeconds) {
    pills.push(`<span class="pill">实际 ${formatDuration(job.durationSeconds)}</span>`);
  }
  if (job.ttsWords) {
    pills.push(`<span class="pill">${job.ttsWords} 字</span>`);
  }
  return `<div class="pills">${pills.join('')}</div>`;
}

function transcriptBlock(job) {
  const turns = Array.isArray(job.transcript) ? job.transcript : [];
  if (!turns.length) return null;
  const block = document.createElement('div');
  block.className = 'transcript';
  const body = turns.map((turn) => {
    const side = String(turn.speaker || 'A').toUpperCase() === 'B' ? 'b' : 'a';
    const name = turn.name || (side === 'b' ? '刘飞' : '小唯');
    return `<div class="turn ${side}">
      <div class="turn-ava">${esc(name.slice(0, 1))}</div>
      <div class="turn-body"><b>${esc(name)}</b><p>${esc(turn.text)}</p></div>
    </div>`;
  }).join('');
  block.innerHTML = `<div class="transcript-head">
      <span>逐字稿 · ${turns.length} 段</span>
      <button type="button" id="toggleTranscript">收起</button>
    </div>
    <div class="transcript-list" id="transcriptList">${body}</div>`;
  const button = block.querySelector('#toggleTranscript');
  const list = block.querySelector('#transcriptList');
  button.addEventListener('click', () => {
    const hidden = list.hidden;
    list.hidden = !hidden;
    button.textContent = hidden ? '收起' : '展开';
  });
  return block;
}

function renderJob(job) {
  state.current = job;
  const active = job.status === 'queued' || job.status === 'running';
  jobMeta.textContent = `${statusText(job)} · ${job.toneLabel || toneInfo(job.tone).label} · `
    + `${job.minutes || 5} 分钟 · ${formatTime(job.createdAt)}`;

  resultBody.innerHTML = '';
  if (job.status === 'failed') {
    resultBody.appendChild(emptyBlock('这期没有做出来',
      job.error || '可以换个主题重新生成，或稍后再试。', '😕'));
  } else if (job.status === 'succeeded') {
    resultBody.appendChild(episodeView(job));
  } else {
    resultBody.appendChild(activeView(job));
  }

  progress.hidden = !active;
  stageTip.hidden = !active;
  if (active) {
    const width = Math.max(4, Math.min(100, Number(job.progress) || 0));
    progressBar.style.width = width + '%';
    progressText.textContent = job.status === 'queued'
      ? '排队中…'
      : (job.stage === 'script'
        ? '正在写对谈稿…'
        : `合成语音 ${job.doneTurns || 0}/${job.totalTurns || 0}`);
    stageTip.textContent = stageText(job);
  }
  jobError.hidden = !job.error || job.status === 'failed';
  jobError.textContent = job.error && job.status !== 'failed' ? job.error : '';
}

function activeView(job) {
  const wrap = document.createElement('div');
  const title = job.title || job.topic || '正在准备新一期';
  const summary = job.summary || (job.stage === 'script'
    ? '模型正在把主题写成两位主持人的自然对谈…'
    : '对谈稿已经写好，正在逐段合成语音。');
  wrap.innerHTML = `<div class="episode">
    ${coverHtml({ title, topic: job.topic })}
    <div class="ep-main">
      <h2>${esc(title)}</h2>
      <p class="ep-summary">${esc(summary)}</p>
      ${pillsHtml(job)}
    </div>
  </div>`;
  return wrap;
}

function episodeView(job) {
  const wrap = document.createElement('div');
  const title = job.title || job.topic || '未命名播客';
  const summary = job.summary || '这期节目已经生成完成。';
  const fileName = String(title).replace(/[\\/:*?"<>|\s]+/g, '-').slice(0, 80) + '.mp3';
  const actions = [];
  if (job.audioUrl) {
    actions.push(`<a class="btn mini" href="${esc(job.audioUrl)}" download="${esc(fileName)}">下载 MP3</a>`);
  }
  if (job.transcriptUrl) {
    actions.push(`<a class="btn mini" href="${esc(job.transcriptUrl)}">下载文字稿</a>`);
  }

  wrap.innerHTML = `<div class="episode">
    ${coverHtml(job)}
    <div class="ep-main">
      <h2>${esc(title)}</h2>
      <p class="ep-summary">${esc(summary)}</p>
      ${pillsHtml(job)}
      ${job.audioUrl ? `<div class="audio-wrap"><audio controls preload="metadata" src="${esc(job.audioUrl)}"></audio></div>` : ''}
      <div class="ep-actions">${actions.join('')}</div>
    </div>
  </div>`;

  const transcript = transcriptBlock(job);
  if (transcript) wrap.appendChild(transcript);
  return wrap;
}

function stopPoll() {
  clearTimeout(state.polling);
  state.polling = 0;
}

function poll(jobId) {
  stopPoll();
  const tick = async () => {
    try {
      const data = await api('/podcast/api/jobs/' + encodeURIComponent(jobId));
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
      else toast('这期播客做好了', 'info');
    } catch (error) {
      setBusy(false);
      toast(error.message || '查询任务失败', 'err');
      if (error.status !== 404) state.polling = setTimeout(tick, 2600);
    }
  };
  state.polling = setTimeout(tick, 300);
}

async function openJob(jobId) {
  try {
    const data = await api('/podcast/api/jobs/' + encodeURIComponent(jobId));
    selectJob(data.job);
  } catch (error) {
    toast(error.message || '读取节目失败', 'err');
    loadHistory({ autoSelect: false });
  }
}

function selectJob(job, autoPoll = true) {
  renderJob(job);
  const active = job.status === 'queued' || job.status === 'running';
  setBusy(active);
  if (active && autoPoll) poll(job.id);
  else stopPoll();
}

function historyItem(job) {
  const wrap = document.createElement('div');
  wrap.className = 'history-item';

  const thumb = document.createElement('div');
  thumb.className = 'history-thumb' + (job.status === 'succeeded' ? '' : ' ph');
  thumb.textContent = job.status === 'succeeded' ? '🎧' : (job.status === 'failed' ? '×' : '…');
  wrap.appendChild(thumb);

  const main = document.createElement('button');
  main.type = 'button';
  main.className = 'history-main';
  const title = document.createElement('b');
  title.textContent = job.title || job.topic || '未命名节目';
  const meta = document.createElement('span');
  const length = job.durationSeconds
    ? formatDuration(job.durationSeconds)
    : `${job.minutes || 5}:00 目标`;
  meta.textContent = `${statusText(job)} · ${job.toneLabel || toneInfo(job.tone).label} · `
    + `${length} · ${formatTime(job.createdAt)}`;
  main.appendChild(title);
  main.appendChild(meta);
  main.addEventListener('click', () => openJob(job.id));

  const del = document.createElement('button');
  del.type = 'button';
  del.className = 'history-del';
  del.textContent = '×';
  del.setAttribute('aria-label', '删除这期节目');
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
  state.jobs = items;
  historyList.innerHTML = '';
  historyCount.textContent = items.length ? items.length + ' 期' : '';
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
    const data = await api('/podcast/api/jobs');
    const items = data.items || [];
    renderHistory(items);
    if (autoSelect && !state.current && items.length) {
      openJob(items[0].id);
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
  const title = job.title || job.topic || '';
  if (!window.confirm(`删除这期节目？MP3 和文字稿也会一起删掉。\n\n${title}`)) return;
  try {
    await api('/podcast/api/jobs/' + encodeURIComponent(job.id), { method: 'DELETE' });
    if (state.current && state.current.id === job.id) {
      stopPoll();
      setBusy(false);
      state.current = null;
      resultBody.innerHTML = '';
      resultBody.appendChild(emptyBlock('已删除', '再写一个主题生成新的节目吧', '🎙️'));
      jobMeta.textContent = '还没有任务';
      progress.hidden = true;
      stageTip.hidden = true;
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
    const data = await api('/podcast/api/status');
    state.ready = !!data.ready;
    state.missing = data.missing || '';
    state.dailyLimit = Number(data.dailyLimit) || 0;
    state.usedToday = Number(data.usedToday) || 0;
    state.ttlDays = Number(data.ttlDays) || 7;
    if (Array.isArray(data.tones) && data.tones.length) state.tones = data.tones;

    if (!state.ready) {
      showBanner(state.missing || '服务端还没有配置好模型 Key、语音 Key 或 ffmpeg。');
    } else if (data.storageError) {
      showBanner('数据库暂时不可用：' + data.storageError);
    } else {
      showBanner('');
    }
    updateToneTip();
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
  const topic = topicEl.value.trim();
  if (!topic) {
    toast('先写一个播客主题', 'err');
    topicEl.focus();
    return;
  }
  if (!state.ready) {
    toast(state.missing || '服务端还没有配置好', 'err');
    return;
  }

  const body = {
    topic,
    material: materialEl.value.trim(),
    tone: toneEl.value,
    minutes: state.minutes
  };

  setBusy(true);
  stopPoll();
  state.current = null;
  resultBody.innerHTML = '';
  resultBody.appendChild(emptyBlock('任务已提交', '正在写对谈稿，随后会逐段合成语音…', '⏳'));
  jobMeta.textContent = '排队中';
  jobError.hidden = true;
  stageTip.hidden = false;
  stageTip.textContent = '任务已提交，等待后台开始处理。';
  progress.hidden = false;
  progressBar.style.width = '4%';
  progressText.textContent = '提交任务…';

  try {
    const data = await api('/podcast/api/jobs', {
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
    stageTip.hidden = true;
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
topicEl.addEventListener('input', updateCounts);
materialEl.addEventListener('input', updateCounts);
topicEl.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') {
    event.preventDefault();
    generate();
  }
});
materialEl.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
    event.preventDefault();
    generate();
  }
});
toneEl.addEventListener('change', updateToneTip);
durationsEl.addEventListener('click', (event) => {
  const button = event.target.closest('.dur-btn');
  if (!button) return;
  state.minutes = Number(button.dataset.minutes) || 5;
  durationsEl.querySelectorAll('.dur-btn').forEach((item) => {
    item.classList.toggle('on', item === button);
  });
});
generateBtn.addEventListener('click', generate);
refreshBtn.addEventListener('click', () => {
  loadStatus();
  loadHistory({ autoSelect: !state.current });
});

updateCounts();
updateToneTip();
loadStatus();
loadHistory();
