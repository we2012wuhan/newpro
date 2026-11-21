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
const imageStyleHint = $('imageStyleHint');
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
const ideaBanner = $('ideaBanner');
const ideaBannerText = $('ideaBannerText');
const ideaBannerClose = $('ideaBannerClose');
const writeView = $('writeView');
const ideasView = $('ideasView');
const modeTabs = Array.from(document.querySelectorAll('.mode-tab'));
const histFold = $('histFold');
const histReopen = $('histReopen');
const refreshBtn = $('refreshBtn');
const statusEl = $('status');
const refineTools = $('refineTools');
const refineMask = $('refineMask');
const refineTitle = $('refineTitle');
const refineMeta = $('refineMeta');
const refineOriginal = $('refineOriginal');
const refineResultLabel = $('refineResultLabel');
const refineResult = $('refineResult');
const refineNote = $('refineNote');
const refineClose = $('refineClose');
const refineCancel = $('refineCancel');
const refineApply = $('refineApply');

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
  markdownText: '',
  editView: null,
  editSelection: null,
  refineBusy: false,
  refineData: null,
  refineToken: 0,
  selectionTimer: 0,
  sourceIdea: '',
  mode: 'write'
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

const HIST_FOLD_KEY = 'tb_article_hist_fold';

function setHistFold(folded, persist = true) {
  if (folded) document.documentElement.setAttribute('data-hist-fold', 'on');
  else document.documentElement.removeAttribute('data-hist-fold');
  histFold.setAttribute('aria-expanded', folded ? 'false' : 'true');
  histFold.title = folded ? '展开最近文章' : '收起最近文章';
  histFold.setAttribute('aria-label', histFold.title);
  if (persist) {
    try {
      localStorage.setItem(HIST_FOLD_KEY, folded ? '1' : '0');
    } catch (error) {
      /* 隐私模式下 localStorage 可能不可写，不影响本次折叠 */
    }
  }
}

function toggleHistFold() {
  setHistFold(document.documentElement.getAttribute('data-hist-fold') !== 'on');
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

const HANDOFF_KEY = 'tb_article_idea';

function handoffMaterial(idea) {
  const lines = [];
  if (idea.claim) lines.push('【核心观点】' + idea.claim);
  if (idea.angle) lines.push('【切入角度】' + idea.angle);
  if (idea.hook) lines.push('【开头灵感】' + idea.hook);
  const outline = (Array.isArray(idea.outline) ? idea.outline : [])
    .filter((item) => String(item || '').trim());
  if (outline.length) {
    lines.push('【选题提纲】');
    outline.forEach((item, index) => lines.push(`${index + 1}. ${item}`));
  }
  if (idea.material) lines.push('【要补的素材】' + idea.material);
  return lines.join('\n');
}

function applyIdeaData(idea) {
  if (!idea || !idea.title) return false;
  topicEl.value = String(idea.title).slice(0, 200);
  const genre = String(idea.genre || '');
  if (Array.from(genreEl.options).some((option) => option.value === genre)) {
    genreEl.value = genre;
  }
  const block = handoffMaterial(idea);
  const current = materialEl.value.trim();
  if (block && !current.includes(block)) {
    materialEl.value = (current ? current + '\n\n' : '') + block;
  }
  if (materialEl.value.length > 8000) {
    materialEl.value = materialEl.value.slice(0, 8000);
  }
  state.sourceIdea = String(idea.title).slice(0, 120);
  ideaBannerText.textContent =
    `已从选题「${String(idea.title).slice(0, 40)}」带入，确认篇幅和配图后生成大纲。`;
  ideaBanner.hidden = false;
  updateCounts();
  updateStyleHint();
  return true;
}

function setMode(mode, { updateUrl = true, scroll = true } = {}) {
  const next = mode === 'ideas' ? 'ideas' : 'write';
  state.mode = next;
  if (writeView) writeView.hidden = next !== 'write';
  if (ideasView) ideasView.hidden = next !== 'ideas';
  // 「最近文章」的贴边标签只属于写作区，切到选题时先藏起来。
  if (histReopen) histReopen.hidden = next !== 'write';
  modeTabs.forEach((button) => {
    const on = button.dataset.mode === next;
    button.classList.toggle('on', on);
    button.setAttribute('aria-selected', on ? 'true' : 'false');
    button.tabIndex = on ? 0 : -1;
  });
  document.title = next === 'ideas' ? '选题 · AI 文章写作' : 'AI 文章写作';
  if (updateUrl) {
    const url = new URL(window.location.href);
    if (next === 'ideas') url.searchParams.set('tab', 'ideas');
    else url.searchParams.delete('tab');
    window.history.replaceState({}, '', url.pathname + url.search + url.hash);
  }
  if (scroll) window.scrollTo({ top: 0, behavior: 'smooth' });
}

function adoptIdea(idea) {
  if (!applyIdeaData(idea)) {
    toast('选题数据不完整，请重新选择', 'err');
    return false;
  }
  setMode('write');
  toast('已带入选题，确认篇幅和配图后生成大纲', 'info');
  return true;
}

function applyIdeaHandoff() {
  const params = new URLSearchParams(window.location.search);
  if (params.get('from') !== 'idea') return;
  let idea = null;
  try {
    idea = JSON.parse(window.sessionStorage.getItem(HANDOFF_KEY) || 'null');
    window.sessionStorage.removeItem(HANDOFF_KEY);
  } catch (error) {
    idea = null;
  }
  if (!idea || !idea.title) {
    toast('没有找到选题数据，请到「选题」页签重新选择', 'err');
    return;
  }
  adoptIdea(idea);
  const url = new URL(window.location.href);
  url.searchParams.delete('from');
  window.history.replaceState({}, '', url.pathname + url.search + url.hash);
}

// 给「选题」页签的脚本调用：同一个页面里直接切到写作，不再跳转。
window.TBArticleAdoptIdea = adoptIdea;
window.TBArticleToast = (message, kind) => toast(message, kind);
window.TBArticleShowIdeas = () => setMode('ideas');

function updateStyleHint() {
  const genre = state.genres.find((item) => item.key === genreEl.value);
  const tone = state.tones.find((item) => item.key === toneEl.value);
  const parts = [];
  if (genre && genre.hint) parts.push(genre.label + '：' + genre.hint);
  if (tone && tone.hint) parts.push(tone.label + '：' + tone.hint);
  styleHint.textContent = parts.join('　');
}

function updateImageStyleHint() {
  const style = imageStyleEl.value;
  if (style === 'notion') {
    imageStyleHint.textContent = 'Notion 信息图固定 16:9，全部 2K 出图；图和正文都带简短中文标注。';
  } else if (style === 'comic') {
    imageStyleHint.textContent = '漫画信息图固定 3:4，全部 2K 出图；中文手写标题、标签和对话框。';
  } else {
    imageStyleHint.textContent = '封面 16:9 / 2K，内文图 4:3 / 1K；文字由模型写，配图本身不含文字。';
  }
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
  if (state.sourceIdea) body.sourceIdea = state.sourceIdea;

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
    state.sourceIdea = '';
    ideaBanner.hidden = true;
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
    ['edit', '编辑润色'],
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

function hideRefineTools(clear = false) {
  refineTools.hidden = true;
  if (clear) state.editSelection = null;
}

function paragraphFromNode(node) {
  if (!node) return null;
  const element = node.nodeType === 3 ? node.parentElement : node;
  if (!element || !element.closest) return null;
  return element.closest('.edit-p');
}

function offsetWithin(root, node, offset) {
  const range = document.createRange();
  range.selectNodeContents(root);
  try {
    range.setEnd(node, offset);
  } catch (error) {
    return -1;
  }
  return range.toString().length;
}

function showRefineTools(range) {
  const rect = range.getBoundingClientRect();
  refineTools.hidden = false;
  const width = refineTools.offsetWidth || 150;
  const height = refineTools.offsetHeight || 38;
  const anchor = rect.width ? rect.left + rect.width / 2 : window.innerWidth / 2;
  let left = anchor - width / 2;
  left = Math.max(8, Math.min(window.innerWidth - width - 8, left));
  let top = rect.bottom + 9;
  if (top + height > window.innerHeight - 8) top = rect.top - height - 9;
  top = Math.max(8, Math.min(window.innerHeight - height - 8, top));
  refineTools.style.left = left + 'px';
  refineTools.style.top = top + 'px';
}

function captureSelection() {
  if (!state.editView || !refineMask.hidden) return;
  const pane = state.editView.pane;
  const selection = window.getSelection();
  if (!selection || !selection.rangeCount || selection.isCollapsed) {
    hideRefineTools();
    return;
  }
  const range = selection.getRangeAt(0);
  const startParagraph = paragraphFromNode(range.startContainer);
  const endParagraph = paragraphFromNode(range.endContainer);
  if (!startParagraph || startParagraph !== endParagraph
      || !pane.contains(startParagraph)) {
    hideRefineTools();
    return;
  }
  const start = offsetWithin(startParagraph, range.startContainer, range.startOffset);
  const end = offsetWithin(startParagraph, range.endContainer, range.endOffset);
  const text = startParagraph.textContent || '';
  if (start < 0 || end <= start || end > text.length) {
    hideRefineTools();
    return;
  }
  const selected = text.slice(start, end);
  if (!selected.trim()) {
    hideRefineTools();
    return;
  }
  state.editSelection = {
    jobId: state.current ? state.current.id : '',
    sectionIndex: Number(startParagraph.dataset.section),
    paragraphIndex: Number(startParagraph.dataset.paragraph),
    start,
    end,
    text: selected,
    paragraph: text
  };
  showRefineTools(range);
}

function renderEditor(job, pane, tabs) {
  state.editView = { job, pane, tabs };
  pane.classList.add('edit-pane');
  const doc = el('div', 'edit-doc');
  // 编辑区有自己的「润色 / 扩展」浮标，关掉全站划词问 AI，避免两个浮层叠在一起。
  doc.setAttribute('data-quick-ai', 'off');
  doc.appendChild(el('p', 'edit-tip',
    '选中正文中的任意一段文字，会出现「润色 / 扩展」。确认结果后会直接写回正文，'
    + '网页版、公众号版、Markdown 和 ZIP 导出都会同步更新。'));

  if (job.title) doc.appendChild(el('h2', 'edit-title', job.title));
  if (job.summary) doc.appendChild(el('p', 'edit-summary', job.summary));

  let paragraphs = 0;
  (job.sections || []).forEach((section, sectionIndex) => {
    if (!section || typeof section !== 'object') return;
    const wrap = el('section', 'edit-section');
    if (section.heading) wrap.appendChild(el('h3', 'edit-heading', section.heading));
    (section.paragraphs || []).forEach((text, paragraphIndex) => {
      if (!text) return;
      const p = el('p', 'edit-p', text);
      p.dataset.section = String(sectionIndex);
      p.dataset.paragraph = String(paragraphIndex);
      p.tabIndex = 0;
      wrap.appendChild(p);
      paragraphs += 1;
    });
    doc.appendChild(wrap);
  });
  if (!paragraphs) {
    doc.appendChild(el('div', 'edit-empty', '正文还没有内容。等生成完成后再来润色。'));
  }
  pane.appendChild(doc);

  const schedule = () => setTimeout(captureSelection, 0);
  doc.addEventListener('mouseup', schedule);
  doc.addEventListener('touchend', () => setTimeout(captureSelection, 260));
  doc.addEventListener('keyup', schedule);
}

function openRefineDialog(action) {
  const selection = state.editSelection;
  const job = state.current;
  if (!selection || !job || selection.jobId !== job.id) {
    toast('先在正文里选中一段文字', 'err');
    return;
  }
  hideRefineTools();
  const token = ++state.refineToken;
  state.refineBusy = true;
  state.refineData = null;
  const label = action === 'expand' ? '扩展' : '润色';
  refineTitle.textContent = label + '选中文字';
  refineMeta.textContent = `${selection.text.length} 字 · 第 ${selection.sectionIndex + 1} 节`
    + `第 ${selection.paragraphIndex + 1} 段`;
  refineOriginal.textContent = selection.text;
  refineResultLabel.textContent = 'AI 结果';
  refineResult.textContent = '正在' + label + '…';
  refineNote.classList.remove('err');
  refineNote.textContent = '会调用一次文本模型，通常几秒；确认前不会改动正文。';
  refineApply.disabled = true;
  refineApply.textContent = '应用到正文';
  refineMask.hidden = false;
  document.body.style.overflow = 'hidden';

  api(`/article/api/jobs/${encodeURIComponent(job.id)}/refine`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      sectionIndex: selection.sectionIndex,
      paragraphIndex: selection.paragraphIndex,
      start: selection.start,
      end: selection.end,
      action
    })
  }).then((data) => {
    if (token !== state.refineToken) return;
    state.refineBusy = false;
    state.refineData = data;
    refineResult.textContent = data.text || '';
    refineNote.textContent = `${data.actionLabel || label}完成。确认后会替换选中的文字，`
      + '导出也会同步更新。';
    refineApply.disabled = !data.replacement;
  }).catch((error) => {
    if (token !== state.refineToken) return;
    state.refineBusy = false;
    refineResult.textContent = '没有处理成功';
    refineNote.classList.add('err');
    refineNote.textContent = error.message || '润色失败，请稍后再试';
  });
}

function closeRefine() {
  if (refineMask.hidden) return;
  state.refineToken += 1;
  state.refineBusy = false;
  state.refineData = null;
  refineMask.hidden = true;
  document.body.style.overflow = '';
  refineApply.disabled = true;
  refineApply.textContent = '应用到正文';
  refineNote.classList.remove('err');
  hideRefineTools();
}

function refreshPills(job) {
  const old = resultBody.querySelector('.pills');
  if (old) old.replaceWith(pills(job));
}

async function applyRefine() {
  const data = state.refineData;
  const job = state.current;
  if (!data || !job || state.refineBusy || refineApply.disabled) return;
  const editView = state.editView;
  refineApply.disabled = true;
  refineApply.textContent = '保存中…';
  refineNote.classList.remove('err');
  refineNote.textContent = '正在写回正文…';
  try {
    const res = await api(`/article/api/jobs/${encodeURIComponent(job.id)}/paragraph`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        sectionIndex: data.sectionIndex,
        paragraphIndex: data.paragraphIndex,
        text: data.replacement
      })
    });
    const updated = res.job;
    Object.assign(job, updated);
    state.markdownJob = '';
    state.markdownText = '';
    state.editSelection = null;
    closeRefine();
    refreshPills(job);
    if (editView && editView.job === job && editView.pane.isConnected) {
      showPreview(job, 'edit', editView.tabs, editView.pane);
    } else {
      renderJob(job);
    }
    loadHistory({ autoSelect: false });
    toast('已应用到正文，导出会同步更新', 'info');
  } catch (error) {
    refineApply.disabled = false;
    refineApply.textContent = '应用到正文';
    refineNote.classList.add('err');
    refineNote.textContent = error.message || '保存失败，请稍后再试';
  }
}

async function showPreview(job, tab, tabs, pane) {
  state.tab = tab;
  hideRefineTools(true);
  state.editView = null;
  tabs.querySelectorAll('.preview-tab').forEach((button) => {
    button.classList.toggle('on', button.dataset.tab === tab);
  });
  pane.innerHTML = '';
  pane.className = 'preview-pane';

  if (tab === 'edit') {
    renderEditor(job, pane, tabs);
    return;
  }

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
  if (!refineMask.hidden) closeRefine();
  hideRefineTools(true);
  state.editView = null;
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
    + `${(job.images || []).length} 图 · ${formatTime(job.createdAt)}`
    + (job.sourceIdea ? ' · 来自选题' : ''));
  if (job.sourceIdea) main.title = '来自选题：' + job.sourceIdea;
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
      if (!refineMask.hidden) closeRefine();
      hideRefineTools(true);
      state.editView = null;
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
imageStyleEl.addEventListener('change', updateImageStyleHint);
ideaBannerClose.addEventListener('click', () => { ideaBanner.hidden = true; });
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

modeTabs.forEach((button) => {
  button.addEventListener('click', () => setMode(button.dataset.mode));
  button.addEventListener('keydown', (event) => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    event.preventDefault();
    const next = button.dataset.mode === 'ideas' ? 'write' : 'ideas';
    const target = modeTabs.find((item) => item.dataset.mode === next);
    if (target) {
      target.focus();
      setMode(next);
    }
  });
});

histFold.addEventListener('click', toggleHistFold);
histReopen.addEventListener('click', () => setHistFold(false));

refineTools.addEventListener('mousedown', (event) => event.preventDefault());
refineTools.querySelectorAll('[data-refine]').forEach((button) => {
  button.addEventListener('click', () => openRefineDialog(button.dataset.refine));
});
refineApply.addEventListener('click', applyRefine);
refineClose.addEventListener('click', closeRefine);
refineCancel.addEventListener('click', closeRefine);
refineMask.addEventListener('click', (event) => {
  if (event.target === refineMask) closeRefine();
});
document.addEventListener('mousedown', (event) => {
  if (refineTools.hidden) return;
  if (refineTools.contains(event.target)) return;
  hideRefineTools();
});
document.addEventListener('selectionchange', () => {
  if (!state.editView || !refineMask.hidden) return;
  clearTimeout(state.selectionTimer);
  state.selectionTimer = setTimeout(() => {
    const selection = window.getSelection();
    if (selection && !selection.isCollapsed && refineTools.hidden) captureSelection();
  }, 260);
});
window.addEventListener('scroll', () => {
  if (!refineTools.hidden) hideRefineTools();
}, true);
window.addEventListener('resize', () => {
  if (!refineTools.hidden) hideRefineTools();
});
document.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape') return;
  if (!refineMask.hidden) {
    closeRefine();
    return;
  }
  hideRefineTools(true);
});

setHistFold(document.documentElement.getAttribute('data-hist-fold') === 'on', false);
setMode(new URLSearchParams(window.location.search).get('tab') === 'ideas'
  ? 'ideas' : 'write', { updateUrl: false, scroll: false });
applyIdeaHandoff();
updateCounts();
updateStyleHint();
updateImageStyleHint();
updateQuota();
loadStatus().then(() => loadHistory());
