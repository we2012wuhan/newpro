/* 联网搜索：IQS 检索来源，DeepSeek 流式总结，SQLite 保存研究记录。 */
(function () {
  'use strict';

  function $(id) { return document.getElementById(id); }
  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (ch) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
    });
  }

  function apiError(data, fallback) {
    return (data && (data.message || data.msg || data.error)) || fallback || '请求失败';
  }

  function getJSON(url) {
    return fetch(url, { headers: { Accept: 'application/json' } }).then(function (resp) {
      return resp.json().catch(function () { return {}; }).then(function (data) {
        if (!resp.ok || !data || data.ok === false) {
          throw new Error(apiError(data, '请求失败（HTTP ' + resp.status + '）'));
        }
        return data;
      });
    });
  }

  function shortTime(value) {
    var text = String(value || '');
    if (!text) { return ''; }
    return text.length > 16 ? text.slice(0, 16).replace('T', ' ') : text;
  }

  function modeLabel(mode) {
    return mode === 'deep' ? '深度' : '快速';
  }

  var S = {
    mode: 'quick', timeRange: 'NoLimit', busy: false, ctrl: null,
    iqs: false, ai: false, recordId: null, answer: '',
    sources: [], selected: {}, followups: [], pendingQuestion: ''
  };

  var queryEl = $('query');
  var startBtn = $('startBtn');
  var stopBtn = $('stopBtn');
  var statusEl = $('status');
  var answerEl = $('answer');
  var resultCard = $('resultCard');
  var sourcesCard = $('sourcesCard');
  var followCard = $('followCard');
  var queryCard = $('queryCard');
  var serviceSignal = $('serviceSignal');
  var serviceSignalText = $('serviceSignalText');
  var configTip = $('configTip');
  var copyBtn = $('copyBtn');
  var speakBtn = $('speakBtn');
  var exportBtn = $('exportBtn');
  var followInput = $('followInput');
  var followBtn = $('followBtn');

  function syncStick() {
    var bar = document.querySelector('.topbar');
    document.documentElement.style.setProperty('--stick', (bar ? bar.offsetHeight : 0) + 'px');
  }

  function setStatus(text) {
    statusEl.textContent = text || '';
  }

  function notice(text, isError) {
    configTip.textContent = text || '';
    configTip.classList.toggle('err', !!isError);
    configTip.hidden = !text;
  }

  function setSearching(on) {
    queryCard.classList.toggle('searching', !!on);
  }

  function setServiceState(state, text, title) {
    serviceSignal.setAttribute('data-state', state);
    serviceSignalText.textContent = text;
    serviceSignal.title = title || text;
  }

  function setBusy(on) {
    S.busy = !!on;
    startBtn.disabled = !!on;
    stopBtn.hidden = !on;
    followBtn.disabled = !!on;
    Array.prototype.forEach.call(document.querySelectorAll('#modeSeg button'), function (b) {
      b.disabled = !!on;
    });
    document.getElementById('timeRange').disabled = !!on;
    syncActions();
  }

  function syncActions() {
    copyBtn.disabled = !S.answer || S.busy;
    speakBtn.disabled = !S.answer || S.busy;
    exportBtn.disabled = !S.answer || S.busy;
  }

  function setMode(mode) {
    S.mode = mode === 'deep' ? 'deep' : 'quick';
    Array.prototype.forEach.call(document.querySelectorAll('#modeSeg button'), function (b) {
      b.classList.toggle('on', b.getAttribute('data-mode') === S.mode);
    });
  }

  function resetResult(query) {
    S.recordId = null;
    S.answer = '';
    S.sources = [];
    S.selected = {};
    S.followups = [];
    S.pendingQuestion = '';
    answerEl.textContent = '';
    $('sources').innerHTML = '';
    $('thread').innerHTML = '';
    $('resultTitle').textContent = query || '研究结果';
    resultCard.hidden = false;
    sourcesCard.hidden = true;
    followCard.hidden = true;
    syncActions();
  }

  function renderSources() {
    var box = $('sources');
    if (!S.sources.length) {
      sourcesCard.hidden = true;
      box.innerHTML = '';
      followCard.hidden = true;
      return;
    }
    sourcesCard.hidden = false;
    followCard.hidden = false;
    box.innerHTML = S.sources.map(function (item, i) {
      var rank = Number(item.rank || (i + 1));
      var checked = S.selected[rank] !== false;
      var meta = [item.host, item.published_time].filter(Boolean).map(esc).join(' · ');
      var text = item.summary || item.snippet || item.main_text || '';
      return '<article class="source' + (checked ? ' on' : '') + '">' +
        '<div class="source-top">' +
          '<input class="source-check" type="checkbox" data-rank="' + rank + '"' +
            (checked ? ' checked' : '') + ' aria-label="使用来源 ' + rank + '">' +
          '<div><div class="source-rank">[' + rank + ']</div>' +
            '<div class="source-title">' + esc(item.title || '未命名来源') + '</div>' +
            '<div class="source-meta">' + (meta || '来源') + '</div></div>' +
        '</div>' +
        '<div class="source-text">' + esc(text) + '</div>' +
        '<a class="source-link" href="' + esc(item.url || '#') + '" target="_blank" rel="noopener noreferrer">打开原文</a>' +
      '</article>';
    }).join('');
    Array.prototype.forEach.call(box.querySelectorAll('.source-check'), function (boxEl) {
      boxEl.onchange = function () {
        var rank = Number(boxEl.getAttribute('data-rank'));
        S.selected[rank] = boxEl.checked;
        var card = boxEl.closest('.source');
        if (card) { card.classList.toggle('on', boxEl.checked); }
      };
    });
  }

  function selectedRanks() {
    return S.sources.map(function (item, i) {
      return Number(item.rank || (i + 1));
    }).filter(function (rank) {
      return S.selected[rank] !== false;
    });
  }

  function renderThread() {
    var box = $('thread');
    box.innerHTML = S.followups.map(function (turn) {
      return '<div class="turn"><div class="turn-q">你：' + esc(turn.question || '') + '</div>' +
        '<div class="turn-a">' + esc(turn.answer || '') + '</div></div>';
    }).join('');
  }

  function parseSSE(text) {
    var events = [];
    text.split('\n\n').forEach(function (block) {
      block.split('\n').forEach(function (line) {
        if (line.indexOf('data:') !== 0) { return; }
        try { events.push(JSON.parse(line.slice(5).trim())); } catch (e) {}
      });
    });
    return events;
  }

  function streamPost(url, body, onEvent) {
    if (S.ctrl) { try { S.ctrl.abort(); } catch (e) {} }
    S.ctrl = ('AbortController' in window) ? new AbortController() : null;
    var init = {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    };
    if (S.ctrl) { init.signal = S.ctrl.signal; }
    return fetch(url, init).then(function (resp) {
      var type = resp.headers.get('content-type') || '';
      if (type.indexOf('application/json') >= 0) {
        return resp.json().catch(function () { return {}; }).then(function (data) {
          throw new Error(apiError(data, '请求失败（HTTP ' + resp.status + '）'));
        });
      }
      if (!resp.ok || !resp.body) {
        throw new Error('请求失败（HTTP ' + resp.status + '）');
      }
      var reader = resp.body.getReader();
      var decoder = new TextDecoder();
      var buffer = '';
      function pump() {
        return reader.read().then(function (packet) {
          if (packet.done) {
            if (buffer.trim()) {
              parseSSE(buffer).forEach(onEvent);
            }
            return;
          }
          buffer += decoder.decode(packet.value, { stream: true });
          var parts = buffer.split('\n\n');
          buffer = parts.pop();
          parts.forEach(function (part) {
            parseSSE(part + '\n\n').forEach(onEvent);
          });
          return pump();
        });
      }
      return pump();
    });
  }

  function handleSearchEvent(event) {
    if (event.phase) {
      setStatus(event.msg || '正在研究…');
    }
    if (event.sources) {
      S.sources = event.sources;
      S.selected = {};
      S.sources.forEach(function (item, i) {
        S.selected[Number(item.rank || (i + 1))] = true;
      });
      renderSources();
      setStatus('找到 ' + S.sources.length + ' 个来源，正在整理答案…');
    }
    if (event.t) {
      S.answer += event.t;
      answerEl.textContent = S.answer;
      syncActions();
    }
    if (event.notice) {
      setStatus(event.notice);
    }
    if (event.err) {
      setStatus('');
      notice(event.err, true);
      setSearching(false);
      setBusy(false);
    }
    if (event.done) {
      if (event.record_id) { S.recordId = event.record_id; }
      setSearching(false);
      setBusy(false);
      if (S.answer) {
        setStatus('完成 · ' + S.sources.length + ' 个来源');
        notice('', false);
      } else if (S.sources.length) {
        setStatus('已找到来源，但还没有生成总结。');
      }
      if (window.TBHistory) { window.TBHistory.reload({ reset: true }); }
    }
  }

  function runSearch() {
    var query = queryEl.value.trim();
    if (query.length < 2) {
      notice('先写下想研究的问题。', true);
      queryEl.focus();
      return;
    }
    if (!S.iqs) {
      notice('服务器还没有配置 ALIYUN_IQS_API_KEY。', true);
      return;
    }
    resetResult(query);
    notice('', false);
    setStatus('正在联网检索资料…');
    setSearching(true);
    setBusy(true);
    streamPost('/web-search/api/search', {
      query: query,
      mode: S.mode,
      time_range: $('timeRange').value
    }, handleSearchEvent).catch(function (err) {
      setSearching(false);
      if (err && err.name === 'AbortError') { setStatus('已停止'); return; }
      notice(err.message || '联网搜索失败', true);
      setStatus('');
      setBusy(false);
    });
  }

  function handleFollowEvent(event) {
    if (event.t) {
      S.answer = S.answer; /* 首轮回答保持不变 */
      if (!S._followAnswer) { S._followAnswer = ''; }
      S._followAnswer += event.t;
      renderThread();
      $('thread').insertAdjacentHTML('beforeend',
        '<div class="turn" id="liveFollow"><div class="turn-q">你：' + esc(S.pendingQuestion) +
        '</div><div class="turn-a">' + esc(S._followAnswer) + '</div></div>');
    }
    if (event.err) {
      notice(event.err, true);
      setBusy(false);
    }
    if (event.done) {
      if (S._followAnswer) {
        S.followups.push({ question: S.pendingQuestion, answer: S._followAnswer, selected: selectedRanks() });
      }
      S._followAnswer = '';
      S.pendingQuestion = '';
      setBusy(false);
      renderThread();
      followInput.value = '';
      if (window.TBHistory) { window.TBHistory.reload(); }
    }
  }

  function askFollowup() {
    var question = followInput.value.trim();
    if (question.length < 2) {
      followInput.focus();
      return;
    }
    if (!S.recordId) {
      notice('这次研究还没有保存成功，暂时不能连续追问。', true);
      return;
    }
    notice('', false);
    S.pendingQuestion = question;
    S._followAnswer = '';
    renderThread();
    $('thread').insertAdjacentHTML('beforeend',
      '<div class="turn" id="liveFollow"><div class="turn-q">你：' + esc(question) +
      '</div><div class="turn-a">正在回答…</div></div>');
    setBusy(true);
    streamPost('/web-search/api/followup', {
      id: S.recordId,
      question: question,
      selected: selectedRanks()
    }, handleFollowEvent).catch(function (err) {
      if (err && err.name === 'AbortError') { return; }
      notice(err.message || '追问失败', true);
      setBusy(false);
    });
  }

  function markdown() {
    var lines = ['# ' + (queryEl.value.trim() || '联网研究'), ''];
    if (S.answer) {
      lines.push('## 研究回答', '', S.answer, '');
    }
    if (S.sources.length) {
      lines.push('## 来源', '');
      S.sources.forEach(function (item, i) {
        var rank = Number(item.rank || (i + 1));
        lines.push(rank + '. [' + (item.title || '来源') + '](' + (item.url || '') + ')');
        if (item.host) { lines.push('   - 站点：' + item.host); }
        if (item.published_time) { lines.push('   - 发布时间：' + item.published_time); }
      });
      lines.push('');
    }
    if (S.followups.length) {
      lines.push('## 继续追问', '');
      S.followups.forEach(function (turn) {
        lines.push('### ' + (turn.question || ''), '', turn.answer || '', '');
      });
    }
    return lines.join('\n').trim() + '\n';
  }

  function copyAnswer() {
    var text = S.answer || '';
    if (!text) { return; }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () {
        copyBtn.textContent = '已复制';
        setTimeout(function () { copyBtn.textContent = '复制回答'; }, 1200);
      });
      return;
    }
    var area = document.createElement('textarea');
    area.value = text;
    area.style.position = 'fixed';
    area.style.opacity = '0';
    document.body.appendChild(area);
    area.select();
    try { document.execCommand('copy'); } catch (e) {}
    document.body.removeChild(area);
  }

  function exportMarkdown() {
    var blob = new Blob([markdown()], { type: 'text/markdown;charset=utf-8' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = '联网研究-' + new Date().toISOString().slice(0, 10) + '.md';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
  }

  function openRecord(id) {
    getJSON('/web-search/api/records/' + encodeURIComponent(id)).then(function (data) {
      var item = data.item || {};
      queryEl.value = item.query || item.title || '';
      setMode(item.mode);
      $('timeRange').value = item.time_range || 'NoLimit';
      resetResult(item.query || item.title || '研究结果');
      S.recordId = item.id;
      S.answer = item.answer || '';
      answerEl.textContent = S.answer;
      S.sources = item.sources || [];
      S.selected = {};
      S.sources.forEach(function (source, i) {
        S.selected[Number(source.rank || (i + 1))] = true;
      });
      S.followups = item.followups || [];
      renderSources();
      renderThread();
      syncActions();
      setStatus('已打开历史研究 · ' + shortTime(item.created_at));
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }).catch(function (err) {
      notice(err.message || '打开历史研究失败', true);
    });
  }

  function deleteRecord(id) {
    return fetch('/web-search/api/records/' + encodeURIComponent(id), { method: 'DELETE' })
      .then(function (resp) {
        return resp.json().catch(function () { return {}; }).then(function (data) {
          if (!resp.ok || !data.ok) { throw new Error(apiError(data, '删除失败')); }
          if (String(S.recordId) === String(id)) {
            S.recordId = null;
          }
          return data;
        });
      });
  }

  function bind() {
    Array.prototype.forEach.call(document.querySelectorAll('#modeSeg button'), function (button) {
      button.onclick = function () { setMode(button.getAttribute('data-mode')); };
    });
    startBtn.onclick = runSearch;
    stopBtn.onclick = function () {
      if (S.ctrl) { try { S.ctrl.abort(); } catch (e) {} }
      setSearching(false);
      setBusy(false);
      setStatus('已停止');
    };
    queryEl.onkeydown = function (event) {
      if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
        event.preventDefault();
        runSearch();
      }
    };
    followBtn.onclick = askFollowup;
    followInput.onkeydown = function (event) {
      if (event.key === 'Enter') {
        event.preventDefault();
        askFollowup();
      }
    };
    copyBtn.onclick = copyAnswer;
    exportBtn.onclick = exportMarkdown;
    if (window.TBVoice && window.TBVoice.attach) {
      window.TBVoice.attach(speakBtn, function () { return S.answer || ''; });
    } else {
      speakBtn.hidden = true;
    }
  }

  function initHistory() {
    if (!window.TBHistory) { return; }
    window.TBHistory.init({
      url: '/web-search/api/records',
      confirmDel: '删掉这条研究记录？',
      isActive: function (id) { return String(id) === String(S.recordId); },
      title: function (item) { return item.title || item.query || '未命名研究'; },
      meta: function (item) {
        return [
          modeLabel(item.mode),
          item.source_count ? item.source_count + ' 个来源' : '',
          item.followup_count ? item.followup_count + ' 次追问' : '',
          shortTime(item.created_at)
        ].filter(Boolean).join(' · ');
      },
      snippet: function (item, q) {
        return window.TBHistory.snippetFrom([item.query || '', item.answer_preview || ''], q);
      },
      empty: {
        none: '还没有研究记录。上面发起一次搜索，结果会保存在服务器。',
        noHit: '没有包含「{q}」的研究记录。'
      },
      open: openRecord,
      del: deleteRecord
    });
  }

  function init() {
    bind();
    syncStick();
    window.addEventListener('resize', syncStick);
    initHistory();
    var prefill = '';
    var recordId = 0;
    try {
      var params = new URLSearchParams(location.search);
      prefill = params.get('q') || '';
      recordId = Number(params.get('r') || 0);
    } catch (e) {}
    if (recordId > 0) {
      openRecord(recordId);
    } else if (prefill) {
      queryEl.value = prefill.slice(0, 500);
      queryEl.focus();
    }
    getJSON('/web-search/api/status').then(function (data) {
      S.iqs = !!data.iqs;
      S.ai = !!data.ai;
      if (!S.iqs) {
        setServiceState('missing', 'IQS SETUP', '联网搜索 Key 未配置');
        notice('联网搜索还没有配置 ALIYUN_IQS_API_KEY。填好配置并重启服务后即可使用。', true);
      } else if (!S.ai) {
        setServiceState('ready', 'IQS READY', '联网搜索已连接；总结模型未配置');
        notice('可以搜索来源，但还没有配置 DEEPSEEK_API_KEY，暂时不会生成总结。', false);
      } else {
        setServiceState('ready', 'IQS READY', '联网搜索与总结模型均已配置');
      }
    }).catch(function (err) {
      setServiceState('missing', 'IQS OFFLINE', '无法读取联网搜索服务状态');
      notice(err.message || '读取服务状态失败', true);
    });
  }

  init();
})();
