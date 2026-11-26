/* 出题小工具前端：设置 → 试卷 → 判分，答案只提交到服务端判。 */
(function () {
  'use strict';

  var API = '/quiz/api';
  var TYPE_TIP = {
    single: '每题只有一个正确项',
    multiple: '每题至少两个正确项',
    judge: '判断正误',
    blank: '答案短且唯一'
  };
  var DEFAULTS = {
    types: ['single', 'multiple', 'judge'],
    count: '5',
    level: 'basic'
  };

  var state = {
    meta: null,
    quiz: null,
    answers: {},
    result: null,
    favorites: {},
    tab: 'generate',
    view: 'setup',
    busy: false
  };
  var TABS = {
    generate: { btn: 'tabBtnGenerate', panel: 'tabGenerate' },
    wrong: { btn: 'tabBtnWrong', panel: 'tabWrong' },
    fav: { btn: 'tabBtnFav', panel: 'tabFav' }
  };

  var $ = function (id) { return document.getElementById(id); };
  var setup = $('setup');
  var paper = $('paper');
  var result = $('result');
  var questions = $('questions');
  var reviewList = $('reviewList');
  var answerSheet = $('answerSheet');
  var historyList = $('historyList');
  var genLayout = $('genLayout');
  var historyRail = $('historyRail');
  var foldHistory = $('foldHistory');
  var FOLD_KEY = 'quiz_hist_fold';
  var historyFolded = false;
  var wrongList = $('wrongList');
  var favList = $('favList');
  var generateBtn = $('generateBtn');
  var submitBtn = $('submitBtn');
  var toastTimer = 0;

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) { node.className = cls; }
    if (text !== undefined && text !== null) { node.textContent = String(text); }
    return node;
  }

  function setText(id, value) {
    var node = $(id);
    if (node) { node.textContent = value === undefined || value === null ? '' : String(value); }
  }

  function toast(message) {
    var node = $('toast');
    node.textContent = message;
    node.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { node.hidden = true; }, 2800);
  }

  async function api(path, options) {
    var opts = Object.assign({}, options || {});
    opts.headers = Object.assign({}, opts.headers || {});
    if (opts.body && !opts.headers['Content-Type']) {
      opts.headers['Content-Type'] = 'application/json';
    }
    var res;
    try {
      res = await fetch(path, opts);
    } catch (err) {
      var net = new Error('网络异常，请稍后重试。');
      net.status = 0;
      throw net;
    }
    var data = {};
    try { data = await res.json(); } catch (err) { data = {}; }
    if (!res.ok || data.ok === false) {
      var error = new Error(data.msg || ('请求失败（' + res.status + '）'));
      error.status = res.status;
      throw error;
    }
    return data;
  }

  function smoothScroll(node) {
    var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    node.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' });
  }

  function scrollTop() {
    var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    window.scrollTo({ top: 0, behavior: reduce ? 'auto' : 'smooth' });
  }

  function showView(name) {
    state.view = name;
    setup.hidden = name !== 'setup';
    paper.hidden = name !== 'paper';
    result.hidden = name !== 'result';
  }

  function setTab(name) {
    if (!TABS[name]) { return; }
    Object.keys(TABS).forEach(function (key) {
      var conf = TABS[key];
      var on = key === name;
      var btn = $(conf.btn);
      btn.classList.toggle('is-on', on);
      btn.setAttribute('aria-selected', on ? 'true' : 'false');
      $(conf.panel).hidden = !on;
    });
    state.tab = name;
    if (name === 'wrong') { loadWrong(); }
    if (name === 'fav') { loadFav(); }
    window.scrollTo({ top: 0, behavior: 'auto' });
  }

  /* ---------- 收藏 ---------- */
  function favSet() {
    if (!(state.favorites instanceof Set)) {
      state.favorites = new Set((state.quiz && state.quiz.favorites) || []);
    }
    return state.favorites;
  }

  function syncFavorites(quiz) {
    state.favorites = new Set((quiz && quiz.favorites) || []);
  }

  function paintStar(btn, on) {
    btn.textContent = on ? '★' : '☆';
    btn.classList.toggle('is-on', on);
    btn.title = on ? '取消收藏' : '收藏';
    btn.setAttribute('aria-label', btn.title);
  }

  function makeStar(index) {
    var btn = el('button', 'icon-btn star');
    btn.type = 'button';
    paintStar(btn, favSet().has(index));
    btn.addEventListener('click', function (event) {
      event.preventDefault();
      event.stopPropagation();
      toggleFav(index, btn);
    });
    return btn;
  }

  async function toggleFav(index, btn) {
    if (!state.quiz || btn.disabled) { return; }
    btn.disabled = true;
    try {
      var data = await api(API + '/fav/toggle', {
        method: 'POST',
        body: JSON.stringify({ quizId: state.quiz.id, index: index })
      });
      var set = favSet();
      if (data.fav) { set.add(index); } else { set.delete(index); }
      state.quiz.favorites = Array.from(set);
      paintStar(btn, !!data.fav);
    } catch (err) {
      if (err.status !== 401) { toast(err.message || '操作失败。'); }
    } finally {
      btn.disabled = false;
    }
  }

  function isEmpty(value) {
    if (Array.isArray(value)) { return value.length === 0; }
    return !value || !String(value).trim();
  }

  function fmtDate(value) {
    var text = String(value || '');
    return text ? text.slice(0, 16) : '';
  }

  /* ---------- 本地暂存：刷新页面不丢作答 ---------- */
  function savedKey(id) { return 'quiz.answers.' + id; }

  function loadSaved(id) {
    try {
      var raw = localStorage.getItem(savedKey(id));
      var data = JSON.parse(raw || '{}');
      return data && typeof data === 'object' && !Array.isArray(data) ? data : {};
    } catch (err) {
      return {};
    }
  }

  function saveAnswers() {
    if (!state.quiz) { return; }
    try {
      localStorage.setItem(savedKey(state.quiz.id), JSON.stringify(state.answers));
    } catch (err) { /* 隐私模式下不可用就算了 */ }
  }

  function clearSaved(id) {
    try { localStorage.removeItem(savedKey(id)); } catch (err) { /* ignore */ }
  }

  /* ---------- 设置区 ---------- */
  function buildChoices(container, items, name, kind, selected) {
    container.textContent = '';
    items.forEach(function (item) {
      var label = el('label', 'pill');
      var input = document.createElement('input');
      input.type = kind;
      input.name = name;
      input.value = String(item.key);
      input.checked = selected.indexOf(String(item.key)) >= 0;
      if (item.hint) { label.title = item.hint; }
      label.append(input, el('span', 'tick', '✓'), el('span', '', item.label));
      container.append(label);
    });
  }

  function readTypes() {
    return Array.prototype.map.call(
      document.querySelectorAll('#typeChoices input:checked'),
      function (input) { return input.value; }
    );
  }

  function readChoice(name) {
    var input = document.querySelector('input[name="' + name + '"]:checked');
    return input ? input.value : '';
  }

  async function loadStatus() {
    try {
      var data = await api(API + '/status');
      state.meta = data;
      var badge = $('modelState');
      badge.textContent = data.ai ? '模型可用' : '模型未配置';
      badge.classList.toggle('is-off', !data.ai);

      var typeItems = (data.types || []).map(function (item) {
        return { key: item.key, label: item.label, hint: item.hint || TYPE_TIP[item.key] };
      });
      var typeKeys = typeItems.map(function (item) { return String(item.key); });
      var selected = DEFAULTS.types.filter(function (key) { return typeKeys.indexOf(key) >= 0; });
      buildChoices($('typeChoices'), typeItems, 'qtype', 'checkbox', selected);

      var counts = (data.counts || [3, 5, 10]).map(function (n) {
        return { key: String(n), label: n + ' 题' };
      });
      var countKeys = counts.map(function (item) { return item.key; });
      buildChoices($('countChoices'), counts, 'qcount', 'radio',
        [countKeys.indexOf(DEFAULTS.count) >= 0 ? DEFAULTS.count : countKeys[0]]);

      buildChoices($('levelChoices'),
        (data.levels || [{ key: 'basic', label: '基础' }, { key: 'advanced', label: '进阶' }]),
        'qlevel', 'radio', [DEFAULTS.level]);

      if (data.materialMax) { $('material').maxLength = data.materialMax; }
      if (data.topicMax) { $('topic').maxLength = data.topicMax; }

      if (!data.ai) {
        generateBtn.disabled = true;
        setText('setupMsg', data.hint || '服务端没有配置模型 Key，暂时不能出题。');
      } else {
        generateBtn.disabled = false;
      }
    } catch (err) {
      if (err.status === 401) { return; }
      $('modelState').textContent = '模型状态未知';
      $('modelState').classList.add('is-off');
      setText('setupMsg', err.message);
    }
  }

  async function generate() {
    if (state.busy) { return; }
    var topic = $('topic').value.trim();
    var material = $('material').value.trim();
    var types = readTypes();
    var count = readChoice('qcount') || DEFAULTS.count;
    var level = readChoice('qlevel') || DEFAULTS.level;
    var msg = $('setupMsg');

    if (topic.length < 2) {
      msg.textContent = '先写一个主题，至少 2 个字。';
      $('topic').focus();
      return;
    }
    if (!types.length) {
      msg.textContent = '至少选择一种题型。';
      return;
    }
    if (!state.meta || !state.meta.ai) {
      msg.textContent = '模型还没就绪，暂时不能出题。';
      return;
    }
    state.busy = true;
    msg.textContent = '';
    generateBtn.disabled = true;
    generateBtn.textContent = '正在出题…';
    try {
      var data = await api(API + '/generate', {
        method: 'POST',
        body: JSON.stringify({
          topic: topic, material: material, types: types,
          count: parseInt(count, 10), level: level
        })
      });
      state.quiz = data.quiz;
      state.answers = {};
      state.result = null;
      renderPaper();
      showView('paper');
      loadHistory();
      scrollTop();
    } catch (err) {
      if (err.status === 401) { return; }
      msg.textContent = err.message || '出题失败，请重试。';
    } finally {
      state.busy = false;
      generateBtn.textContent = '生成题目';
      generateBtn.disabled = !(state.meta && state.meta.ai);
    }
  }

  /* ---------- 试卷 ---------- */
  function renderPaper() {
    var quiz = state.quiz;
    if (!quiz) { return; }
    setText('paperTitle', quiz.title || quiz.topic);
    var types = (quiz.typeCounts || []).map(function (item) {
      return item.label + ' ' + item.n;
    }).join(' · ');
    setText('paperMeta', [quiz.topic, quiz.levelLabel, quiz.count + ' 题', types]
      .filter(Boolean).join(' · '));

    state.answers = loadSaved(quiz.id);
    syncFavorites(quiz);

    answerSheet.textContent = '';
    quiz.questions.forEach(function (question, index) {
      var dot = el('button', 'sheet-dot', String(index + 1));
      dot.type = 'button';
      dot.dataset.target = String(index);
      dot.setAttribute('aria-label', '跳到第 ' + (index + 1) + ' 题');
      dot.addEventListener('click', function () {
        var card = document.getElementById('q-' + index);
        if (card) { smoothScroll(card); }
      });
      answerSheet.append(dot);
    });

    questions.textContent = '';
    quiz.questions.forEach(function (question, index) {
      questions.append(buildQuestion(question, index));
    });

    setText('submitMsg', '');
    submitBtn.disabled = false;
    submitBtn.textContent = '提交判分';
    updateProgress();
  }

  function buildQuestion(question, index) {
    var card = el('article', 'q');
    card.id = 'q-' + index;
    card.dataset.index = String(index);

    var head = el('div', 'q-head');
    head.append(
      el('span', 'q-no', String(index + 1).padStart(2, '0')),
      el('span', 'q-type', question.typeLabel)
    );
    if (question.type === 'multiple') {
      head.append(el('span', 'q-tip', '可多选'));
    }
    head.append(makeStar(index));
    card.append(head, el('div', 'q-stem', question.stem));

    if (question.type === 'blank') {
      var wrap = el('div', 'blank-wrap');
      var input = document.createElement('input');
      input.type = 'text';
      input.className = 'blank-input';
      input.autocomplete = 'off';
      input.maxLength = 60;
      input.placeholder = '在这里填写答案';
      input.setAttribute('aria-label', '第 ' + (index + 1) + ' 题答案');
      var saved = state.answers[index];
      if (typeof saved === 'string') { input.value = saved; }
      wrap.append(input);
      card.append(wrap);
    } else {
      var options = el('div', 'options');
      var given = state.answers[index];
      var picked = Array.isArray(given) ? given : (given ? [String(given)] : []);
      question.options.forEach(function (option) {
        var label = el('label', 'opt');
        var input = document.createElement('input');
        input.type = question.type === 'multiple' ? 'checkbox' : 'radio';
        input.name = 'q-' + state.quiz.id + '-' + index;
        input.value = option.key;
        input.checked = picked.indexOf(option.key) >= 0;
        label.append(input, el('span', 'opt-key', option.key), el('span', 'opt-text', option.text));
        options.append(label);
      });
      card.append(options);
    }
    return card;
  }

  function readAnswer(index) {
    var card = document.getElementById('q-' + index);
    var question = state.quiz.questions[index];
    if (!card || !question) { return ''; }
    if (question.type === 'blank') {
      var input = card.querySelector('.blank-input');
      return input ? input.value.trim() : '';
    }
    var checked = Array.prototype.map.call(
      card.querySelectorAll('input:checked'),
      function (node) { return node.value; }
    );
    if (question.type === 'multiple') { return checked; }
    return checked.length ? checked[0] : '';
  }

  function onAnswerChange(event) {
    var target = event.target;
    if (!target || target.tagName !== 'INPUT' || !state.quiz) { return; }
    var card = target.closest('.q');
    if (!card || !card.dataset.index) { return; }
    var index = parseInt(card.dataset.index, 10);
    var value = readAnswer(index);
    if (isEmpty(value)) {
      delete state.answers[index];
    } else {
      state.answers[index] = value;
    }
    saveAnswers();
    updateProgress();
  }

  function updateProgress() {
    if (!state.quiz) { return; }
    var total = state.quiz.questions.length;
    var answered = 0;
    state.quiz.questions.forEach(function (question, index) {
      if (!isEmpty(state.answers[index])) { answered += 1; }
    });
    setText('progressText', '已答 ' + answered + ' / ' + total);
    var dots = answerSheet.children;
    for (var i = 0; i < dots.length; i += 1) {
      dots[i].classList.toggle('is-answered', !isEmpty(state.answers[i]));
    }
  }

  async function submitQuiz() {
    if (!state.quiz || state.busy) { return; }
    var answers = {};
    var answered = 0;
    state.quiz.questions.forEach(function (question, index) {
      var value = state.answers[index];
      if (!isEmpty(value)) {
        answers[index] = value;
        answered += 1;
      }
    });
    if (!answered) {
      $('submitMsg').textContent = '至少答一题再提交。';
      return;
    }
    state.busy = true;
    submitBtn.disabled = true;
    submitBtn.textContent = '判分中…';
    $('submitMsg').textContent = '';
    try {
      var data = await api(API + '/quizzes/' + state.quiz.id + '/submit', {
        method: 'POST',
        body: JSON.stringify({ answers: answers })
      });
      clearSaved(state.quiz.id);
      state.result = data.result;
      renderResult(state.quiz, data.result, data.result.attemptNo);
      showView('result');
      loadHistory();
      scrollTop();
    } catch (err) {
      if (err.status === 401) { return; }
      $('submitMsg').textContent = err.message || '提交失败，请重试。';
    } finally {
      state.busy = false;
      submitBtn.disabled = false;
      submitBtn.textContent = '提交判分';
    }
  }

  /* ---------- 判分结果 ---------- */
  function noteFor(score, unanswered) {
    var note;
    if (score.percent === 100) {
      note = '全对。把解析扫一遍就可以收工。';
    } else if (score.percent >= 80) {
      note = '基本掌握，重点看标红那几题的解析。';
    } else if (score.percent >= 60) {
      note = '过半了，把错题的考点补一遍再做。';
    } else {
      note = '错的比对的多的，先看解析和考点，再做一遍。';
    }
    if (unanswered) {
      note += ' 有 ' + unanswered + ' 题没答，按错计分。';
    }
    return note;
  }

  function renderResult(quiz, result, attemptNo) {
    var score = result.score || { correct: 0, total: 0, percent: 0 };
    setText('scoreNum', score.correct + ' / ' + score.total);
    setText('scorePercent', score.percent + '%');
    var types = (quiz.typeCounts || []).map(function (item) {
      return item.label + ' ' + item.n;
    }).join(' · ');
    var meta = [quiz.topic, quiz.levelLabel, types];
    if (attemptNo) { meta.push('第 ' + attemptNo + ' 次作答'); }
    if (result.at) { meta.push(fmtDate(result.at)); }
    setText('scoreMeta', meta.filter(Boolean).join(' · '));

    var unanswered = (result.items || []).filter(function (item) {
      return isEmpty(item.given);
    }).length;
    setText('scoreNote', noteFor(score, unanswered));

    reviewList.textContent = '';
    quiz.questions.forEach(function (question, index) {
      var item = (result.items || []).find(function (row) { return row.index === index; });
      if (item) { reviewList.append(buildReview(question, item, index)); }
    });
  }

  function formatGiven(question, item) {
    if (question.type === 'blank') {
      return item.given || '未作答';
    }
    var keys = Array.isArray(item.given) ? item.given : [];
    if (!keys.length) { return '未作答'; }
    var lookup = {};
    (question.options || []).forEach(function (option) { lookup[option.key] = option.text; });
    return keys.map(function (key) {
      return lookup[key] ? key + '. ' + lookup[key] : key;
    }).join('；');
  }

  function buildReview(question, item, index) {
    var card = el('article', 'review q');
    card.id = 'r-' + index;
    var head = el('div', 'q-head');
    head.append(
      el('span', 'q-no', String(index + 1).padStart(2, '0')),
      el('span', 'q-type', item.typeLabel || question.typeLabel),
      el('span', 'verdict ' + (item.correct ? 'ok' : 'bad'),
        item.correct ? '正确' : (isEmpty(item.given) ? '未作答' : '错误'))
    );
    head.append(makeStar(index));
    card.append(head, el('div', 'q-stem', question.stem));

    var given = Array.isArray(item.given) ? item.given : (item.given ? [item.given] : []);
    if (question.type !== 'blank') {
      var options = el('div', 'result-options');
      (question.options || []).forEach(function (option) {
        var isAnswer = (item.answer || []).indexOf(option.key) >= 0;
        var isPicked = given.indexOf(option.key) >= 0;
        var row = el('div', 'result-opt');
        if (isAnswer) { row.classList.add('is-answer'); }
        if (isPicked) { row.classList.add('is-picked'); }
        row.append(el('span', 'opt-key', option.key), el('span', 'opt-text', option.text));
        var tags = [];
        if (isAnswer) { tags.push('正确答案'); }
        if (isPicked) { tags.push('你的选择'); }
        if (tags.length) { row.append(el('span', 'tag', tags.join(' · '))); }
        options.append(row);
      });
      card.append(options);
    }

    var feedback = el('div', 'feedback');
    feedback.append(fbRow('你的答案', formatGiven(question, item),
      isEmpty(item.given) ? 'is-missing' : ''));
    feedback.append(fbRow('正确答案', item.answerText || '', 'is-right'));
    if (item.explanation) {
      feedback.append(el('p', 'fb-explain', item.explanation));
    }

    var whys = item.optionWhy && typeof item.optionWhy === 'object' ? item.optionWhy : {};
    var wrongKeys = Object.keys(whys).filter(function (key) {
      return (item.answer || []).indexOf(key) < 0;
    });
    if (wrongKeys.length) {
      feedback.append(el('p', 'why-title', '其他选项为什么错'));
      var list = el('ul', 'why-list');
      wrongKeys.forEach(function (key) {
        var li = el('li');
        li.append(el('b', '', key), document.createTextNode('  ' + whys[key]));
        list.append(li);
      });
      feedback.append(list);
    }
    if (item.point) {
      feedback.append(el('p', 'point-line', '考点：' + item.point));
    }
    card.append(feedback);
    return card;
  }

  function fbRow(label, value, cls) {
    var row = el('div', 'fb-row');
    row.append(el('span', 'fb-label', label));
    row.append(el('span', 'fb-value' + (cls ? ' ' + cls : ''), value));
    return row;
  }

  function retry() {
    if (!state.quiz) { return; }
    clearSaved(state.quiz.id);
    state.answers = {};
    state.result = null;
    renderPaper();
    showView('paper');
    scrollTop();
  }

  function resetToSetup() {
    state.quiz = null;
    state.answers = {};
    state.result = null;
    showView('setup');
    setText('setupMsg', '');
    setText('submitMsg', '');
    scrollTop();
  }

  /* ---------- 错题库 / 收藏 ---------- */
  function emptyBox(text) {
    return el('div', 'empty-collect', text);
  }

  async function loadWrong() {
    wrongList.textContent = '';
    wrongList.append(emptyBox('加载中…'));
    try {
      var data = await api(API + '/wrong');
      renderCollect(wrongList, data.items || [], 'wrong');
    } catch (err) {
      if (err.status === 401) { return; }
      wrongList.textContent = '';
      wrongList.append(emptyBox(err.message || '加载失败。'));
    }
  }

  async function loadFav() {
    favList.textContent = '';
    favList.append(emptyBox('加载中…'));
    try {
      var data = await api(API + '/fav');
      renderCollect(favList, data.items || [], 'fav');
    } catch (err) {
      if (err.status === 401) { return; }
      favList.textContent = '';
      favList.append(emptyBox(err.message || '加载失败。'));
    }
  }

  function renderCollect(box, items, kind) {
    box.textContent = '';
    if (!items.length) {
      box.append(emptyBox(kind === 'wrong' ? '还没有错题。' : '还没有收藏。'));
      return;
    }
    items.forEach(function (item) { box.append(buildCollectItem(item, kind)); });
  }

  function collectLine(label, value) {
    var line = el('p', 'collect-line');
    line.append(el('b', '', label + '：'), document.createTextNode(value));
    return line;
  }

  function buildCollectItem(item, kind) {
    var card = el('article', 'collect-item');
    var head = el('div', 'collect-head');
    head.append(el('span', 'collect-source',
      [item.typeLabel, item.title].filter(Boolean).join(' · ')));

    var actions = el('div', 'collect-actions');
    var star = el('button', 'icon-btn star');
    star.type = 'button';
    paintStar(star, kind === 'fav' || !!item.fav);
    star.addEventListener('click', function () {
      if (kind === 'fav') { removeFav(item.id); } else { toggleCollectFav(item, star); }
    });
    actions.append(star);
    if (kind === 'wrong') {
      actions.append(makeWrongRemove(item.id));
    }
    head.append(actions);

    card.append(head, el('div', 'collect-stem', item.stem));

    if (item.type !== 'blank' && (item.options || []).length) {
      var opts = el('div', 'collect-opts');
      var picked = Array.isArray(item.given) ? item.given : [];
      item.options.forEach(function (option) {
        var row = el('div', 'collect-opt', option.key + '. ' + option.text);
        if (kind === 'wrong' && picked.indexOf(option.key) >= 0) {
          row.classList.add('is-picked');
        }
        if ((item.answer || []).indexOf(option.key) >= 0) {
          row.classList.add('is-answer');
        }
        opts.append(row);
      });
      card.append(opts);
    }

    if (kind === 'wrong') {
      card.append(collectLine('你的答案', item.givenText || '未作答'));
    }
    card.append(collectLine('正确答案', item.answerText || ''));
    if (item.explanation) {
      card.append(el('p', 'collect-explain', item.explanation));
    }
    return card;
  }

  async function toggleCollectFav(item, star) {
    if (star.disabled) { return; }
    star.disabled = true;
    try {
      var data = await api(API + '/fav/toggle', {
        method: 'POST',
        body: JSON.stringify({ quizId: item.quizId, index: item.index })
      });
      item.fav = !!data.fav;
      paintStar(star, item.fav);
    } catch (err) {
      if (err.status !== 401) { toast(err.message || '操作失败。'); }
    } finally {
      star.disabled = false;
    }
  }

  async function removeFav(id) {
    try {
      await api(API + '/fav/' + id, { method: 'DELETE' });
      loadFav();
    } catch (err) {
      if (err.status !== 401) { toast(err.message || '操作失败。'); }
    }
  }

  function makeWrongRemove(id) {
    var btn = el('button', 'ghost small', '移除');
    btn.type = 'button';
    var armed = false;
    var timer = 0;
    btn.addEventListener('click', async function () {
      if (!armed) {
        armed = true;
        btn.textContent = '确认';
        btn.classList.add('danger');
        timer = setTimeout(function () {
          armed = false;
          btn.textContent = '移除';
          btn.classList.remove('danger');
        }, 3200);
        return;
      }
      clearTimeout(timer);
      btn.disabled = true;
      try {
        await api(API + '/wrong/' + id, { method: 'DELETE' });
        loadWrong();
      } catch (err) {
        if (err.status === 401) { return; }
        btn.disabled = false;
        btn.textContent = '移除';
        btn.classList.remove('danger');
        armed = false;
        toast(err.message || '删除失败。');
      }
    });
    return btn;
  }

  /* ---------- 历史 ---------- */
  async function loadHistory() {
    try {
      var data = await api(API + '/quizzes');
      renderHistory(data.items || []);
    } catch (err) {
      if (err.status === 401) { return; }
      historyList.textContent = '';
      historyList.append(el('div', 'empty-history', err.message || '历史加载失败。'));
    }
  }

  function renderHistory(items) {
    historyList.textContent = '';
    if (!items.length) {
      historyList.append(el('div', 'empty-history', '还没有出过题。上面出一套，提交后就会留在这里。'));
      return;
    }
    items.forEach(function (item) {
      var row = el('div', 'hist-row');
      row.dataset.id = String(item.id);

      var open = el('button', 'hist-open');
      open.type = 'button';
      open.append(el('span', 'hist-title', item.title || item.topic || '未命名'));
      var types = (item.typeCounts || []).map(function (part) { return part.label + ' ' + part.n; }).join(' · ');
      var meta = [item.topic, item.count + ' 题', item.levelLabel, types, fmtDate(item.lastAt || item.createdAt)]
        .filter(Boolean).join(' · ');
      open.append(el('span', 'hist-meta', meta));
      open.addEventListener('click', function () { openQuiz(item.id); });

      var score = el('span', 'hist-score');
      if (item.lastScore && item.lastScore.total) {
        score.textContent = item.lastScore.correct + '/' + item.lastScore.total;
      } else {
        score.textContent = '未作答';
        score.classList.add('is-empty');
      }

      var del = el('button', 'ghost small', '删除');
      del.type = 'button';
      var armed = false;
      var timer = 0;
      del.addEventListener('click', async function () {
        if (!armed) {
          armed = true;
          del.textContent = '确认删除';
          del.classList.add('danger');
          timer = setTimeout(function () {
            armed = false;
            del.textContent = '删除';
            del.classList.remove('danger');
          }, 3200);
          return;
        }
        clearTimeout(timer);
        del.disabled = true;
        try {
          await api(API + '/quizzes/' + item.id, { method: 'DELETE' });
          if (state.quiz && state.quiz.id === item.id) { resetToSetup(); }
          toast('已删除');
          loadHistory();
        } catch (err) {
          if (err.status === 401) { return; }
          del.disabled = false;
          del.textContent = '删除';
          del.classList.remove('danger');
          armed = false;
          toast(err.message || '删除失败。');
        }
      });

      row.append(open, score, del);
      historyList.append(row);
    });
  }

  function applyHistoryFold(folded, persist) {
    historyFolded = !!folded;
    genLayout.classList.toggle('is-folded', historyFolded);
    historyRail.hidden = !historyFolded;
    historyRail.setAttribute('aria-expanded', historyFolded ? 'false' : 'true');
    foldHistory.textContent = historyFolded ? '‹' : '›';
    foldHistory.setAttribute('aria-expanded', historyFolded ? 'false' : 'true');
    foldHistory.setAttribute('aria-label', historyFolded ? '展开记录' : '收起记录');
    foldHistory.title = historyFolded ? '展开记录' : '收起记录';
    if (persist) {
      try { localStorage.setItem(FOLD_KEY, historyFolded ? '1' : '0'); } catch (e) { /* 隐私模式 */ }
    }
  }

  async function openQuiz(id) {
    try {
      var data = await api(API + '/quizzes/' + id);
      state.quiz = data.quiz;
      state.result = data.lastResult || null;
      state.answers = {};
      syncFavorites(data.quiz);
      if (state.result) {
        renderResult(data.quiz, state.result, (data.attempts || []).length);
        showView('result');
      } else {
        renderPaper();
        showView('paper');
      }
      scrollTop();
    } catch (err) {
      if (err.status === 401) { return; }
      toast(err.message || '这套题打不开。');
    }
  }

  /* ---------- 事件绑定 ---------- */
  function bind() {
    $('tabBtnGenerate').addEventListener('click', function () { setTab('generate'); });
    $('tabBtnWrong').addEventListener('click', function () { setTab('wrong'); });
    $('tabBtnFav').addEventListener('click', function () { setTab('fav'); });
    $('setupForm').addEventListener('submit', function (event) {
      event.preventDefault();
      generate();
    });
    $('material').addEventListener('input', function () {
      setText('materialCount', $('material').value.length);
    });
    questions.addEventListener('input', onAnswerChange);
    submitBtn.addEventListener('click', submitQuiz);
    $('retryBtn').addEventListener('click', retry);
    $('newBtn').addEventListener('click', resetToSetup);
    $('resultNewBtn').addEventListener('click', resetToSetup);
    $('refreshHistory').addEventListener('click', loadHistory);
    foldHistory.addEventListener('click', function () { applyHistoryFold(!historyFolded, true); });
    historyRail.addEventListener('click', function () { applyHistoryFold(false, true); });
    var folded = false;
    try { folded = localStorage.getItem(FOLD_KEY) === '1'; } catch (e) { folded = false; }
    applyHistoryFold(folded, false);
  }

  function boot() {
    bind();
    generateBtn.disabled = true;
    loadStatus();
    loadHistory();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
