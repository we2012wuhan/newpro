/*
 * AI 文章写作 · 页面交互
 * =======================
 * 流程：
 *   1. 填主题 -> POST /article/api/outline，只拿大纲，不产生图片费用；
 *   2. 在页面上改标题、导语、每节标题 / 要点 / 画面想法；
 *   3. POST /article/api/jobs，后台逐节写正文并出图，页面每 1.5 秒轮询；
 *   4. 完成后预览网页版、公众号版、Markdown，复制正文或下载 ZIP。
 *
 * 模型输出一律通过 textContent / input.value 放进页面，不拼 innerHTML。
 */

const $ = (id) => document.getElementById(id);

const topicEl = $('topic');
const topicCount = $('topicCount');
const materialEl = $('material');
const materialCount = $('materialCount');
const genreEl = $('genre');
const toneEl = $('tone');
const styleHint = $('styleHint');
const lengthsEl = $('lengths');
const imageCountEl = $('imageCount');
const imageCountHint = $('imageCountHint');
const imageStyleEl = $('imageStyle');
const imageModelEl = $('imageModel');
const outlineBtn = $('outlineBtn');
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

const state = {
  ready: false,
  missing: '',
  genres: [],
  tones: [],
  imageModels: [],
  defaultImageModel: '',
  dailyLimit: 0,
  usedToday: 0,
  ttlDays: 7,
  length: 'medium',
  jobs: [],
  current: null,
  outline: null,
  outlineEstimate: null,
  busy: false,
  polling: 0,
  statusTimer: 0,
  tab: 'web',
  markdownJob: '',
  markdownText: ''
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

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = String(text);
  return node;
}

function emptyBlock(title, text, icon) {
  const wrap = el('div', 'empty');
  if (icon) wrap.appendChild(el('span', 'ico', icon));
  wrap.appendChild(el('b', null, title));
  if (text) wrap.appendChild(el('span', null, text));
  return wrap;
}

function optionsBody() {
  return {
    topic: topicEl.value.trim(),
    material: materialEl.value.trim(),
    genre: genreEl.value,
    tone: toneEl.value,
    length: state.length
  };
}

function statusText(job) {
  if (job.status === 'running' && job.stage === 'writing') {
    return `写正文 ${job.doneSections || 0}/${job.totalSections || 0}`;
  }
  if (job.status === 'running' && job.stage === 'images') {
    return `配图 ${job.doneImages || 0}/${job.totalImages || 0}`;
  }
  return STATUS_TEXT[job.status] || job.status || '未知';
}

function stageText(job) {
  if (job.status === 'queued') return '排队中，前面还有任务在处理…';
  if (job.stage === 'writing') {
    return `正在按大纲逐节写正文：${job.doneSections || 0} / ${job.totalSections || 0} 节。`
      + '每节都会重试一次格式不合格的输出。';
  }
  if (job.stage === 'images') {
    return `正文已经写好，正在生成配图：${job.doneImages || 0} / ${job.totalImages || 0} 张。`
      + '单张失败不会丢掉正文。';
  }
  if (job.stage === 'done') return '文章和配图都完成了。';
  return '正在处理…';
}

function setBusy(busy) {
  state.busy = busy;
  outlineBtn.disabled = busy || !state.ready;
  outlineBtn.textContent = busy ? '正在处理…' : '先生成大纲';
}

function updateCounts() {
  topicCount.textContent = topicEl.value.length + ' / 200';
  materialCount.textContent = materialEl.value.length + ' / 8000';
}

function updateStyleHint() {
  const genre = state.genres.find((item) => item.key === genreEl.value);
  const tone = state.tones.find((item) => item.key === toneEl.value);
  const parts = [];
  if (genre && genre.hint) parts.push(genre.label + '：' + genre.hint);
  if (tone && tone.hint) parts.push(tone.label + '：' + tone.hint);
  styleHint.textContent = parts.join('　');
}

function setImageLimit(sections) {
  const max = sections == null ? 6 : Math.min(6, Math.max(1, Number(sections) + 1));
  Array.from(imageCountEl.options).forEach((option) => {
    option.disabled = Number(option.value) > max;
  });
  if (Number(imageCountEl.value) > max) imageCountEl.value = String(max);
  imageCountHint.textContent = sections == null
    ? '含封面 · 最多 6 张'
    : `含封面 · 当前大纲最多 ${max} 张`;
  updateQuota();
}

function updateQuota() {
  if (!state.ready) {
    quotaEl.textContent = '服务端配置好文本模型和火山方舟 Key 之后，这里会显示今日配图额度。';
    return;
  }
  const limit = state.dailyLimit || 0;
  const used = state.usedToday || 0;
  const estimate = Number(imageCountEl.value || 0);
  quotaEl.textContent = `今日已用 ${used} / ${limit} 张配图；本次选择 ${estimate} 张（含封面）。`
    + `图片默认保留 ${state.ttlDays} 天。`;
}

function fillSelect(select, items, valueKey, labelKey, noteKey) {
  if (!Array.isArray(items) || !items.length) return;
  const old = select.value;
  select.innerHTML = '';
  items.forEach((item) => {
    const option = document.createElement('option');
    option.value = String(item[valueKey]);
    option.textContent = String(item[labelKey] || item[valueKey])
      + (noteKey && item[noteKey] ? ' · ' + item[noteKey] : '');
    select.appendChild(option);
  });
  if (Array.from(select.options).some((option) => option.value === old)) {
    select.value = old;
  }
}

function syncSelects() {
  fillSelect(genreEl, state.genres, 'key', 'label');
  fillSelect(toneEl, state.tones, 'key', 'label');
  if (Array.isArray(state.imageModels) && state.imageModels.length) {
    fillSelect(imageModelEl, state.imageModels, 'id', 'label', 'note');
    const wanted = state.defaultImageModel || state.imageModels[0].id;
    const exists = Array.from(imageModelEl.options)
      .some((option) => option.value === imageModelEl.value);
    if (!exists && Array.from(imageModelEl.options)
      .some((option) => option.value === wanted)) {
      imageModelEl.value = wanted;
    }
  }
  updateStyleHint();
  updateQuota();
}

async function loadStatus() {
  try {
    const data = await api('/article/api/status');
    state.ready = !!data.ready;
    state.missing = data.missing || '';
    state.genres = Array.isArray(data.genres) ? data.genres : [];
    state.tones = Array.isArray(data.tones) ? data.tones : [];
    state.imageModels = Array.isArray(data.imageModels) ? data.imageModels : [];
    state.defaultImageModel = data.defaultImageModel || data.imageModel || '';
    state.dailyLimit = Number(data.dailyLimit) || 0;
    state.usedToday = Number(data.usedToday) || 0;
    state.ttlDays = Number(data.ttlDays) || 7;

    if (!state.ready) {
      showBanner(state.missing || '服务端还没有配置好文本模型或图片模型。');
    } else if (data.storageError) {
      showBanner('数据库暂时不可用：' + data.storageError);
    } else {
      showBanner('');
    }
    syncSelects();
    setBusy(state.busy);
  } catch (error) {
    state.ready = false;
    showBanner(error.message || '服务状态读取失败');
    setBusy(false);
    updateQuota();
  }
}

/* ---------------------------------------------------------------
 * 大纲编辑
 * --------------------------------------------------------------- */
function renderOutlineLoading(text) {
  resultBody.innerHTML = '';
  const empty = emptyBlock('正在搭大纲', text, '⏳');
  const spinner = el('span', 'spin');
  empty.appendChild(spinner);
  resultBody.appendChild(empty);
}

function makeSectionCard(section, index) {
  const card = el('div', 'sec-card');
  const top = el('div', 'sec-top');
  top.appendChild(el('span', 'sec-no', String(index + 1)));

  const heading = document.createElement('input');
  heading.type = 'text';
  heading.maxLength = 50;
  heading.value = section.heading || '';
  heading.placeholder = '这一节的小标题';
  heading.dataset.field = 'heading';
  top.appendChild(heading);

  const del = el('button', 'sec-del', '×');
  del.type = 'button';
  del.title = '删除这一节';
  del.setAttribute('aria-label', '删除这一节');
  del.addEventListener('click', () => {
    const cards = resultBody.querySelectorAll('.sec-card');
    if (cards.length <= 3) {
      toast('至少保留 3 节', 'err');
      return;
    }
    card.remove();
    renumberSections();
  });
  top.appendChild(del);
  card.appendChild(top);

  card.appendChild(el('label', null, '要点 · 每行一条'));
  const points = document.createElement('textarea');
  points.rows = 3;
  points.maxLength = 520;
  points.dataset.field = 'points';
  points.value = (section.points || []).join('\n');
  points.placeholder = '每行写一个要点，2～4 条';
  card.appendChild(points);

  card.appendChild(el('label', null, '画面想法 · 给这一节配什么图'));
  const idea = document.createElement('input');
  idea.type = 'text';
  idea.maxLength = 200;
  idea.dataset.field = 'imageIdea';
  idea.value = section.imageIdea || '';
  idea.placeholder = '例如：清晨的公交站，一个人抱着电脑等车';
  card.appendChild(idea);

  return card;
}

function renumberSections() {
  const cards = resultBody.querySelectorAll('.sec-card');
  cards.forEach((card, index) => {
    const no = card.querySelector('.sec-no');
    if (no) no.textContent = String(index + 1);
  });
  setImageLimit(cards.length);
}

function renderOutline(outline, estimate) {
  state.outline = outline;
  state.outlineEstimate = estimate || null;
  resultBody.innerHTML = '';

  const head = el('div', 'outline-head');
  const headText = el('div');
  headText.appendChild(el('b', null, '确认大纲'));
  headText.appendChild(el('span', null, '可以直接修改，确认后才开始写正文和出图'));
  head.appendChild(headText);

  const headActions = el('div', 'outline-actions');
  const regen = el('button', 'btn mini', '重新生成大纲');
  regen.type = 'button';
  regen.addEventListener('click', generateOutline);
  headActions.appendChild(regen);
  head.appendChild(headActions);
  resultBody.appendChild(head);

  const titleWrap = el('div', 'outline-title');
  titleWrap.appendChild(el('label', 'field-label', '文章标题'));
  const titleInput = document.createElement('input');
  titleInput.type = 'text';
  titleInput.maxLength = 120;
  titleInput.dataset.outlineField = 'title';
  titleInput.value = outline.title || '';
  titleWrap.appendChild(titleInput);
  resultBody.appendChild(titleWrap);

  const summaryWrap = el('div', 'outline-summary');
  summaryWrap.appendChild(el('label', 'field-label', '导语'));
  const summaryInput = document.createElement('textarea');
  summaryInput.rows = 3;
  summaryInput.maxLength = 300;
  summaryInput.dataset.outlineField = 'summary';
  summaryInput.value = outline.summary || '';
  summaryWrap.appendChild(summaryInput);
  resultBody.appendChild(summaryWrap);

  const list = el('div', 'sec-list');
  (outline.sections || []).forEach((section, index) => {
    list.appendChild(makeSectionCard(section, index));
  });
  resultBody.appendChild(list);

  const foot = el('div', 'outline-foot');
  const add = el('button', 'btn mini', '＋ 添加一节');
  add.type = 'button';
  add.addEventListener('click', () => {
    if (resultBody.querySelectorAll('.sec-card').length >= 6) {
      toast('最多 6 节', 'err');
      return;
    }
    const card = makeSectionCard({ heading: '', points: [''], imageIdea: '' },
      resultBody.querySelectorAll('.sec-card').length);
    list.appendChild(card);
    renumberSections();
    card.querySelector('input').focus();
  });
  foot.appendChild(add);

  const go = el('button', 'btn gold', '生成文章和配图');
  go.type = 'button';
  go.addEventListener('click', generateJob);
  foot.appendChild(go);
  resultBody.appendChild(foot);

  const note = el('p', 'cost');
  note.textContent = estimate && estimate.note
    ? estimate.note
    : '确认后开始写正文并出图，按实际图片张数计费。';
  resultBody.appendChild(note);

  setImageLimit((outline.sections || []).length);
  jobMeta.textContent = `大纲待确认 · ${(outline.sections || []).length} 节`;
  progress.hidden = true;
  stageTip.hidden = true;
  jobError.hidden = true;
  state.current = null;
  setBusy(false);
}

function collectOutline() {
  const title = (resultBody.querySelector('[data-outline-field="title"]') || {}).value || '';
  const summary = (resultBody.querySelector('[data-outline-field="summary"]') || {}).value || '';
  const sections = [];
  resultBody.querySelectorAll('.sec-card').forEach((card) => {
    const heading = (card.querySelector('[data-field="heading"]') || {}).value || '';
    const pointsText = (card.querySelector('[data-field="points"]') || {}).value || '';
    const imageIdea = (card.querySelector('[data-field="imageIdea"]') || {}).value || '';
    const points = pointsText.split(/\n|[；;]/).map((text) => text.trim()).filter(Boolean);
    if (heading.trim()) {
      sections.push({
        heading: heading.trim(),
        points: points.slice(0, 4),
        imageIdea: imageIdea.trim() || heading.trim()
      });
    }
  });
  if (!title.trim()) throw new Error('给文章起一个标题');
  if (sections.length < 3) throw new Error('至少保留 3 节');
  const missing = sections.find((section) => !section.points.length);
  if (missing) throw new Error(`「${missing.heading}」还没有写要点`);
  return { title: title.trim(), summary: summary.trim(), sections };
}

async function generateOutline() {
  if (state.busy) return;
  const body = optionsBody();
  if (!body.topic) {
    toast('先写一个文章主题', 'err');
    topicEl.focus();
    return;
  }
  if (!state.ready) {
    toast(state.missing || '服务端还没有配置好', 'err');
    return;
  }

  setBusy(true);
  stopPoll();
  state.current = null;
  state.outline = null;
  resultBody.innerHTML = '';
  renderOutlineLoading('正在读主题、定结构，通常十几秒。大纲阶段不会生成图片。');
  jobMeta.textContent = '正在生成大纲';
  progress.hidden = true;
  stageTip.hidden = true;
  jobError.hidden = true;

  try {
    const data = await api('/article/api/outline', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    setBusy(false);
    renderOutline(data.outline || {}, data.estimated || {});
    toast('大纲好了，改完再生成正文', 'info');
  } catch (error) {
    setBusy(false);
    resultBody.innerHTML = '';
    resultBody.appendChild(emptyBlock('大纲没有生成出来', error.message || '稍后再试', '😕'));
    jobMeta.textContent = '大纲生成失败';
    toast(error.message || '大纲生成失败', 'err');
    if (error.status === 503) loadStatus();
  }
}

async function generateJob() {
  if (state.busy) return;
  let outline;
  try {
    outline = collectOutline();
  } catch (error) {
    toast(error.message, 'err');
    return;
  }
  if (!state.ready) {
    toast(state.missing || '服务端还没有配置好', 'err');
    return;
  }

  const body = optionsBody();
  body.imageCount = Number(imageCountEl.value) || 3;
  body.imageStyle = imageStyleEl.value;
  body.imageModel = imageModelEl.value;
  body.outline = outline;

  setBusy(true);
  stopPoll();
  renderOutlineLoading('正在提交任务…');
  jobMeta.textContent = '提交中';
  progress.hidden = false;
  progressBar.style.width = '3%';
  progressText.textContent = '提交任务…';
  stageTip.hidden = false;
  stageTip.textContent = '任务提交后会先写正文，再逐张生成配图。';

  try {
    const data = await api('/article/api/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    state.outline = null;
    state.outlineEstimate = null;
    renderJob(data.job);
    loadStatus();
    loadHistory({ autoSelect: false });
    poll(data.job.id);
  } catch (error) {
    setBusy(false);
    progress.hidden = true;
    stageTip.hidden = true;
    if (state.outline) {
      renderOutline(state.outline, state.outlineEstimate);
    } else {
      resultBody.innerHTML = '';
      resultBody.appendChild(emptyBlock('没有提交成功', error.message || '请稍后再试', '😕'));
    }
    jobMeta.textContent = '提交失败';
    toast(error.message || '提交失败', 'err');
    if (error.status === 429 || error.status === 503) loadStatus();
  }
}

/* ---------------------------------------------------------------
 * 任务视图
 * --------------------------------------------------------------- */
function pills(job) {
  const wrap = el('div', 'pills');
  [
    ['pill acc', job.genreLabel || '文章'],
    ['pill', job.toneLabel || ''],
    ['pill', job.lengthLabel ? job.lengthLabel + '篇' : ''],
    ['pill', job.bodyChars ? job.bodyChars + ' 字' : ''],
    ['pill gold', `${(job.images || []).length} 张图`]
  ].forEach(([className, text]) => {
    if (text) wrap.appendChild(el('span', className, text));
  });
  return wrap;
}

function activeView(job) {
  const wrap = el('div');
  const card = el('div', 'active-card');
  const art = el('div', 'active-art');
  art.appendChild(el('span', null, 'AI ARTICLE'));
  art.appendChild(el('b', null, job.title || job.topic || '正在准备'));
  card.appendChild(art);
  const info = el('div', 'active-info');
  info.appendChild(el('b', null, job.title || job.topic || '正在准备新文章'));
  info.appendChild(el('p', null, stageText(job)));
  card.appendChild(info);
  wrap.appendChild(card);
  wrap.appendChild(pills(job));
  return wrap;
}

function actionLink(className, text, href, download) {
  const link = el('a', className, text);
  link.href = href;
  if (download) link.setAttribute('download', '');
  return link;
}

function previewShell(job) {
  const shell = el('div', 'preview-shell');
  const tabs = el('div', 'preview-tabs');
  const pane = el('div', 'preview-pane');

  const buttons = [
    ['web', '网页版'],
    ['wechat', '公众号版'],
    ['markdown', 'Markdown']
  ].map(([key, label]) => {
    const button = el('button', 'preview-tab', label);
    button.type = 'button';
    button.dataset.tab = key;
    button.addEventListener('click', () => showPreview(job, key, tabs, pane));
    tabs.appendChild(button);
    return button;
  });
  shell.appendChild(tabs);
  shell.appendChild(pane);
  state.tab = 'web';
  setTimeout(() => showPreview(job, 'web', tabs, pane), 0);
  return shell;
}

async function showPreview(job, tab, tabs, pane) {
  state.tab = tab;
  tabs.querySelectorAll('.preview-tab').forEach((button) => {
    button.classList.toggle('on', button.dataset.tab === tab);
  });
  pane.innerHTML = '';
  pane.className = 'preview-pane';

  if (tab === 'markdown') {
    pane.classList.add('md-pane');
    const pre = el('pre');
    pre.textContent = '正在读取 Markdown…';
    pane.appendChild(pre);
    try {
      if (state.markdownJob !== job.id) {
        const res = await fetch(job.export.markdown, { credentials: 'same-origin' });
        if (!res.ok) throw new Error('Markdown 读取失败');
        state.markdownText = await res.text();
        state.markdownJob = job.id;
      }
      pre.textContent = state.markdownText;
    } catch (error) {
      pre.textContent = error.message || 'Markdown 读取失败';
    }
    return;
  }

  if (tab === 'wechat') pane.classList.add('wechat-frame');
  const frame = document.createElement('iframe');
  frame.loading = 'lazy';
  frame.title = tab === 'wechat' ? '公众号预览' : '网页版预览';
  frame.src = tab === 'wechat'
    ? (job.export.wechat + '?images=url')
    : (job.export.html + '?images=url');
  pane.appendChild(frame);
}

function resultView(job) {
  const wrap = el('div');
  const title = el('h2', 'job-title', job.title || job.topic || '未命名文章');
  wrap.appendChild(title);
  if (job.summary) wrap.appendChild(el('p', 'job-summary', job.summary));
  wrap.appendChild(pills(job));

  if (job.warning) {
    const warn = el('p', 'warn', job.warning);
    wrap.appendChild(warn);
  }

  const actions = el('div', 'job-actions');
  const copy = el('button', 'btn gold', '复制公众号正文');
  copy.type = 'button';
  copy.addEventListener('click', () => copyWechat(job));
  actions.appendChild(copy);
  actions.appendChild(actionLink('btn', '下载公众号包（ZIP）', job.export.zip, true));
  actions.appendChild(actionLink('btn', '下载网页版 HTML',
    job.export.html + '?images=relative&download=1', true));
  actions.appendChild(actionLink('btn', '下载 Markdown', job.export.markdown, true));

  const del = el('button', 'btn', '删除');
  del.type = 'button';
  del.addEventListener('click', () => deleteJob(job));
  actions.appendChild(del);
  wrap.appendChild(actions);

  wrap.appendChild(el('p', 'cost',
    '复制的是公众号兼容正文，图片不会自动上传；ZIP 里有 wechat.html、images/ 和手动上传说明。'));
  wrap.appendChild(previewShell(job));
  return wrap;
}

function renderJob(job) {
  state.current = job;
  const active = job.status === 'queued' || job.status === 'running';
  jobMeta.textContent = `${statusText(job)} · ${job.genreLabel || '文章'} · `
    + `${formatTime(job.createdAt)}`;
  resultBody.innerHTML = '';

  if (job.status === 'failed') {
    const written = Array.isArray(job.sections) ? job.sections.length : 0;
    if (written) {
      const view = resultView(job);
      const note = el('p', 'warn',
        `正文写到第 ${written} 节后中断：${job.error || '模型调用失败'}。`
        + '已经写好的部分仍可预览、复制和下载。');
      view.insertBefore(note, view.firstChild);
      resultBody.appendChild(view);
    } else {
      resultBody.appendChild(emptyBlock('这篇没有写完',
        job.error || '可以换个角度重新生成。', '😕'));
    }
  } else if (job.status === 'succeeded') {
    resultBody.appendChild(resultView(job));
  } else {
    resultBody.appendChild(activeView(job));
  }

  progress.hidden = !active;
  stageTip.hidden = !active;
  if (active) {
    const width = Math.max(4, Math.min(100, Number(job.progress) || 0));
    progressBar.style.width = width + '%';
    progressText.textContent = statusText(job);
    stageTip.textContent = stageText(job);
  }
  jobError.hidden = !job.error || job.status === 'failed';
  jobError.textContent = job.error && job.status !== 'failed' ? job.error : '';
}

function stopPoll() {
  clearTimeout(state.polling);
  state.polling = 0;
}

function poll(jobId) {
  stopPoll();
  const tick = async () => {
    try {
      const data = await api('/article/api/jobs/' + encodeURIComponent(jobId));
      const job = data.job;
      renderJob(job);
      if (job.status === 'queued' || job.status === 'running') {
        state.polling = setTimeout(tick, 1500);
        return;
      }
      setBusy(false);
      loadStatus();
      loadHistory({ autoSelect: false });
      if (job.status === 'failed') toast(job.error || '生成失败', 'err');
      else toast('文章和配图做好了', 'info');
    } catch (error) {
      setBusy(false);
      toast(error.message || '查询任务失败', 'err');
      if (error.status !== 404) state.polling = setTimeout(tick, 2600);
    }
  };
  state.polling = setTimeout(tick, 300);
}

async function openJob(jobId) {
  stopPoll();
  try {
    const data = await api('/article/api/jobs/' + encodeURIComponent(jobId));
    selectJob(data.job);
  } catch (error) {
    toast(error.message || '读取文章失败', 'err');
    loadHistory({ autoSelect: false });
  }
}

function selectJob(job, autoPoll = true) {
  state.outline = null;
  state.markdownJob = '';
  state.markdownText = '';
  renderJob(job);
  const active = job.status === 'queued' || job.status === 'running';
  setBusy(active);
  if (active && autoPoll) poll(job.id);
  else stopPoll();
}

/* ---------------------------------------------------------------
 * 复制 / 删除 / 历史
 * --------------------------------------------------------------- */
function copyWithSelection(htmlText) {
  if (!document.execCommand) throw new Error('浏览器不支持富文本复制');
  const holder = document.createElement('div');
  holder.contentEditable = 'true';
  holder.style.position = 'fixed';
  holder.style.left = '-9999px';
  holder.style.top = '0';
  holder.innerHTML = htmlText;
  document.body.appendChild(holder);
  const range = document.createRange();
  range.selectNodeContents(holder);
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
  const ok = document.execCommand('copy');
  selection.removeAllRanges();
  holder.remove();
  if (!ok) throw new Error('浏览器拒绝了复制');
}

async function copyWechat(job) {
  try {
    const res = await fetch(job.export.wechat + '?images=placeholder&fragment=1',
      { credentials: 'same-origin' });
    if (!res.ok) throw new Error('公众号正文读取失败');
    const htmlText = await res.text();
    const doc = new DOMParser().parseFromString(htmlText, 'text/html');
    const plain = doc.body.innerText || doc.body.textContent || '';

    let copied = false;
    if (navigator.clipboard && navigator.clipboard.write && window.ClipboardItem) {
      try {
        await navigator.clipboard.write([new ClipboardItem({
          'text/html': new Blob([htmlText], { type: 'text/html' }),
          'text/plain': new Blob([plain], { type: 'text/plain' })
        })]);
        copied = true;
      } catch (error) {
        copied = false;              // 权限或安全上下文不允许时，再试旧接口
      }
    }
    if (!copied) copyWithSelection(htmlText);
    toast('公众号正文已复制；图片请从 ZIP 里手动上传', 'info');
  } catch (error) {
    toast((error.message || '复制失败') + '，可以改用下载 ZIP', 'err');
  }
}

function historyItem(job) {
  const wrap = el('div', 'history-item');
  const thumb = el('div', 'history-thumb' + (job.status === 'succeeded' ? '' : ' ph'),
    job.status === 'succeeded' ? '📄' : (job.status === 'failed' ? '×' : '…'));
  wrap.appendChild(thumb);

  const main = el('button', 'history-main');
  main.type = 'button';
  main.appendChild(el('b', null, job.title || job.topic || '未命名文章'));
  const meta = el('span', null,
    `${statusText(job)} · ${job.genreLabel || '文章'} · ${job.bodyChars || 0} 字 · `
    + `${(job.images || []).length} 图 · ${formatTime(job.createdAt)}`);
  main.appendChild(meta);
  main.addEventListener('click', () => openJob(job.id));
  wrap.appendChild(main);

  const del = el('button', 'history-del', '×');
  del.type = 'button';
  del.setAttribute('aria-label', '删除这篇文章');
  if (job.status === 'queued' || job.status === 'running') {
    del.disabled = true;
    del.title = '任务进行中，暂时不能删除';
  } else {
    del.addEventListener('click', () => deleteJob(job));
  }
  wrap.appendChild(del);
  return wrap;
}

function renderHistory(items) {
  state.jobs = items;
  historyList.innerHTML = '';
  historyCount.textContent = items.length ? items.length + ' 篇' : '';
  if (!items.length) {
    historyList.appendChild(el('div', 'history-empty', '还没有生成记录'));
    return;
  }
  items.forEach((job) => historyList.appendChild(historyItem(job)));
}

async function loadHistory({ autoSelect = true } = {}) {
  try {
    const data = await api('/article/api/jobs');
    const items = data.items || [];
    renderHistory(items);
    if (autoSelect && !state.current && items.length) openJob(items[0].id);
  } catch (error) {
    historyList.innerHTML = '';
    historyList.appendChild(el('div', 'history-empty', error.message || '记录加载失败'));
  }
}

async function deleteJob(job) {
  const title = job.title || job.topic || '';
  if (!window.confirm(`删除这篇文章？正文、配图和 ZIP 都会一起删掉。\n\n${title}`)) return;
  try {
    await api('/article/api/jobs/' + encodeURIComponent(job.id), { method: 'DELETE' });
    if (state.current && state.current.id === job.id) {
      stopPoll();
      setBusy(false);
      state.current = null;
      state.outline = null;
      resultBody.innerHTML = '';
      resultBody.appendChild(emptyBlock('已删除', '再写一个主题，生成新的文章吧', '📝'));
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

async function refresh() {
  await loadStatus();
  if (state.current) {
    await openJob(state.current.id);
  } else {
    await loadHistory();
  }
}

/* ---------------------------------------------------------------
 * 事件绑定
 * --------------------------------------------------------------- */
topicEl.addEventListener('input', updateCounts);
materialEl.addEventListener('input', updateCounts);
topicEl.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.isComposing) {
    event.preventDefault();
    generateOutline();
  }
});
materialEl.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.isComposing && (event.ctrlKey || event.metaKey)) {
    event.preventDefault();
    generateOutline();
  }
});
genreEl.addEventListener('change', updateStyleHint);
toneEl.addEventListener('change', updateStyleHint);
imageCountEl.addEventListener('change', updateQuota);
lengthsEl.querySelectorAll('[data-length]').forEach((button) => {
  button.addEventListener('click', () => {
    state.length = button.dataset.length || 'medium';
    lengthsEl.querySelectorAll('[data-length]').forEach((item) => {
      item.classList.toggle('on', item === button);
    });
  });
});
outlineBtn.addEventListener('click', generateOutline);
refreshBtn.addEventListener('click', refresh);

updateCounts();
updateQuota();
loadStatus().then(() => loadHistory());
