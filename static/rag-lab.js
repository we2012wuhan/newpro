/* RAG 实验台页面逻辑：调 /rag-lab/api/*，把检索、生成和评估结果画出来。 */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };

  var els = {
    question: $('question'),
    qCount: $('qCount'),
    samples: $('samples'),
    askBtn: $('askBtn'),
    generate: $('generate'),
    askState: $('askState'),
    emptyCard: $('emptyCard'),
    answerCard: $('answerCard'),
    answerMeta: $('answerMeta'),
    answerText: $('answerText'),
    answerCites: $('answerCites'),
    answerWarn: $('answerWarn'),
    hitsCard: $('hitsCard'),
    hitsMeta: $('hitsMeta'),
    hitsPill: $('hitsPill'),
    hits: $('hits'),
    evalBtn: $('evalBtn'),
    compareBtn: $('compareBtn'),
    evalBody: $('evalBody'),
    compareBody: $('compareBody'),
    aiBadge: $('aiBadge'),
    goldenCount: $('goldenCount'),
    statDocs: $('statDocs'),
    statChunks: $('statChunks'),
    statAvg: $('statAvg'),
    statAi: $('statAi'),
    fileList: $('fileList'),
    strategySeg: $('strategySeg'),
    retrieverSeg: $('retrieverSeg'),
    k: $('k'),
    maxChars: $('maxChars'),
    overlap: $('overlap')
  };

  if (!els.question) { return; }

  var selected = { strategy: 'heading', retriever: 'bm25' };
  var locked = false;

  /* ---------- 基础工具 ---------- */

  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (ch) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
    });
  }

  function pad(value) {
    return String(value).padStart(2, '0');
  }

  function nowText() {
    var d = new Date();
    return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
  }

  function setLocked(on) {
    locked = on;
    [els.askBtn, els.evalBtn, els.compareBtn].forEach(function (btn) {
      if (btn) { btn.disabled = on; }
    });
  }

  function busyButton(btn, on, label) {
    if (!btn) { return; }
    if (on) {
      btn.dataset.label = btn.textContent;
      btn.innerHTML = '<span class="spinner"></span>' + esc(label);
    } else {
      btn.textContent = btn.dataset.label || btn.textContent;
      delete btn.dataset.label;
    }
  }

  function setState(html, kind) {
    if (!els.askState) { return; }
    els.askState.className = 'state' + (kind ? ' ' + kind : '');
    els.askState.innerHTML = html || '';
  }

  function request(url, options, timeoutMs) {
    var ctrl = new AbortController();
    var timer = setTimeout(function () { ctrl.abort(); }, timeoutMs || 20000);
    var opts = options || {};
    opts.signal = ctrl.signal;
    return fetch(url, opts).then(function (resp) {
      return resp.text().then(function (raw) {
        var data;
        try {
          data = raw ? JSON.parse(raw) : {};
        } catch (err) {
          throw new Error('服务器返回的不是 JSON，请刷新后再试。');
        }
        if (!resp.ok) {
          throw new Error(data.detail || data.message || ('请求失败（HTTP ' + resp.status + '）'));
        }
        return data;
      });
    }).finally(function () {
      clearTimeout(timer);
    });
  }

  function post(url, body, timeoutMs) {
    return request(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    }, timeoutMs);
  }

  function errorText(err) {
    if (err && err.name === 'AbortError') {
      return '请求超时了，请稍后再试。';
    }
    return (err && err.message) || '请求失败，请稍后再试。';
  }

  function numberValue(input, fallback, low, high) {
    var value = parseInt(input && input.value, 10);
    if (!isFinite(value)) { value = fallback; }
    return Math.max(low, Math.min(high, value));
  }

  function settings() {
    return {
      strategy: selected.strategy,
      retriever: selected.retriever,
      k: numberValue(els.k, 5, 1, 10),
      max_chars: numberValue(els.maxChars, 700, 200, 2000),
      overlap: numberValue(els.overlap, 120, 0, 500)
    };
  }

  function retrieverName(name) {
    return name === 'bigram' ? '中文二元组' : 'BM25';
  }

  function strategyName(name) {
    return name === 'fixed' ? '固定窗口' : '按标题切';
  }

  function updateCount() {
    if (els.qCount) {
      els.qCount.textContent = (els.question.value || '').length + ' / 500';
    }
  }

  /* ---------- 分段选择 ---------- */

  function bindSeg(root, key) {
    if (!root) { return; }
    root.addEventListener('click', function (event) {
      var button = event.target.closest('.seg-item');
      if (!button || !root.contains(button)) { return; }
      selected[key] = button.getAttribute('data-value');
      Array.prototype.forEach.call(root.querySelectorAll('.seg-item'), function (item) {
        item.classList.toggle('is-on', item === button);
      });
    });
  }

  bindSeg(els.strategySeg, 'strategy');
  bindSeg(els.retrieverSeg, 'retriever');

  /* ---------- 状态加载 ---------- */

  function loadStatus() {
    request('/rag-lab/api/status', {}, 15000).then(function (data) {
      els.statDocs.textContent = data.files.length + ' 个';
      els.statChunks.textContent = data.chunks + ' 块';
      els.statAvg.textContent = Math.round(data.avg_chars) + ' 字';
      els.statAi.textContent = data.ai ? '已配置' : '未配置';
      els.goldenCount.textContent = data.answerable + data.negative;

      if (data.ai) {
        els.aiBadge.className = 'badge ok';
        els.aiBadge.innerHTML = '<i></i>模型已配置';
      } else {
        els.aiBadge.className = 'badge warn';
        els.aiBadge.innerHTML = '<i></i>仅检索模式';
        els.generate.checked = false;
        els.generate.title = '没有配置 DEEPSEEK_API_KEY，暂时只能做检索';
      }

      renderFiles(data.files);
      renderSamples(data.samples || []);
    }).catch(function (err) {
      els.aiBadge.className = 'badge warn';
      els.aiBadge.innerHTML = '<i></i>状态读取失败';
      setState(esc(errorText(err)), 'err');
    });
  }

  function renderFiles(files) {
    if (!els.fileList) { return; }
    var details = document.createElement('details');
    var summary = document.createElement('summary');
    summary.textContent = '查看语料文件';
    details.appendChild(summary);
    var list = document.createElement('ul');
    files.forEach(function (file) {
      var item = document.createElement('li');
      item.textContent = file;
      item.title = file;
      list.appendChild(item);
    });
    details.appendChild(list);
    els.fileList.appendChild(details);
  }

  function renderSamples(samples) {
    if (!els.samples) { return; }
    samples.forEach(function (sample) {
      var button = document.createElement('button');
      button.type = 'button';
      button.className = 'sample';
      button.title = sample.question;
      var text = document.createElement('b');
      text.textContent = sample.question;
      button.appendChild(text);
      if (!sample.answerable) {
        var tag = document.createElement('span');
        tag.className = 'tag';
        tag.textContent = '文档里没有';
        button.appendChild(tag);
      }
      button.addEventListener('click', function () {
        els.question.value = sample.question;
        updateCount();
        els.question.focus();
      });
      els.samples.appendChild(button);
    });
  }

  /* ---------- 检索结果 ---------- */

  function snippet(text, limit) {
    var flat = String(text || '').replace(/\s+/g, ' ').trim();
    return flat.length > limit ? flat.slice(0, limit) + '…' : flat;
  }

  function renderAsk(data) {
    els.emptyCard.classList.add('hide');
    renderHits(data);
    if (data.answer || data.answer_error) {
      renderAnswer(data);
    } else {
      els.answerCard.classList.add('hide');
    }
  }

  function renderHits(data) {
    els.hitsCard.classList.remove('hide');
    var s = data.settings;
    els.hitsMeta.textContent = retrieverName(s.retriever) + ' · ' + strategyName(s.strategy) +
      ' · 语料 ' + data.chunks + ' 块 · 检索耗时 ' + data.elapsed_ms + ' ms';
    els.hitsPill.textContent = data.hits.length + ' 段';
    els.hits.innerHTML = '';

    if (!data.hits.length) {
      els.hits.innerHTML = '<div class="note">没有命中任何块。换个说法，或者把「每块最多」调大一点再试。</div>';
      return;
    }

    var top = data.hits[0].score || 1;
    data.hits.forEach(function (hit) {
      var width = Math.max(4, Math.round((hit.score / top) * 100));
      var node = document.createElement('article');
      node.className = 'hit';
      node.innerHTML =
        '<div class="hit-top">' +
          '<span class="rank">' + hit.rank + '</span>' +
          '<span class="hit-file">' + esc(hit.file) + '<span>:' + hit.line + '</span></span>' +
          '<span class="score">' + Number(hit.score).toFixed(4) + '</span>' +
        '</div>' +
        '<div class="meter"><i style="width:' + width + '%"></i></div>' +
        (hit.heading ? '<div class="hit-heading">' + esc(hit.heading) + '</div>' : '') +
        '<p class="hit-text">' + esc(snippet(hit.body, 240)) + '</p>' +
        '<details><summary>展开全文（' + hit.chars + ' 字）</summary><pre>' + esc(hit.body) + '</pre></details>';
      els.hits.appendChild(node);
    });
  }

  function formatAnswer(text, maxCitation) {
    var parts = String(text || '').split(/(\[\d+\])/g);
    var out = '';
    parts.forEach(function (part) {
      var match = part.match(/^\[(\d+)\]$/);
      if (match) {
        var number = parseInt(match[1], 10);
        var bad = number < 1 || number > maxCitation;
        out += '<span class="cite' + (bad ? ' bad' : '') + '">[' + number + ']</span>';
        return;
      }
      var safe = esc(part);
      safe = safe.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
      out += safe.replace(/\n{2,}/g, '</p><p>').replace(/\n/g, '<br>');
    });
    return '<p>' + out + '</p>';
  }

  function renderAnswer(data) {
    els.answerCard.classList.remove('hide');
    els.answerCites.innerHTML = '';
    els.answerWarn.className = 'note warn hide';
    els.answerWarn.textContent = '';

    var meta = [];
    if (data.model) { meta.push(data.model); }
    if (typeof data.answer_elapsed_ms === 'number') {
      meta.push('生成 ' + (data.answer_elapsed_ms / 1000).toFixed(1) + ' 秒');
    }
    els.answerMeta.textContent = meta.join(' · ') || '基于检索到的资料';

    if (!data.answer) {
      els.answerText.innerHTML = '';
      els.answerText.classList.add('hide');
      els.answerWarn.className = 'note err';
      els.answerWarn.textContent = data.answer_error || '模型没有返回内容。';
      return;
    }

    els.answerText.classList.remove('hide');
    els.answerText.innerHTML = formatAnswer(data.answer, data.max_citation);

    var cited = data.cited || [];
    var invalid = data.invalid_citations || [];
    if (cited.length) {
      var html = '<span class="lbl">引用</span>';
      cited.forEach(function (number) {
        var bad = invalid.indexOf(number) >= 0;
        html += '<span class="cite' + (bad ? ' bad' : '') + '">[' + number + ']</span>';
      });
      els.answerCites.innerHTML = html;
    } else {
      els.answerCites.innerHTML = '<span class="lbl">没有标注引用</span>';
    }

    if (invalid.length) {
      els.answerWarn.className = 'note warn';
      els.answerWarn.textContent = '模型引用了不存在的资料编号 ' + invalid.join('、') +
        '（本次只有 1–' + data.max_citation + '）。这条回答需要人工核对。';
    }
  }

  /* ---------- 评估 ---------- */

  function evalLoading() {
    els.evalBody.innerHTML = '<div class="eval-empty"><span class="spinner"></span>正在跑完全部标注问题…</div>';
  }

  function metricTile(label, value, note) {
    return '<div class="metric"><div class="m-label">' + esc(label) + '</div>' +
      '<div class="m-value">' + esc(value) + '</div>' +
      '<div class="m-note">' + esc(note) + '</div></div>';
  }

  function failItem(row) {
    var kind = row.failure === '召回失败' ? 'recall'
      : row.failure === 'gold 缺失' ? 'missing' : '';
    var rank = row.rank ? ('gold 排在第 ' + row.rank + ' 位') : 'gold 没进前 20';
    var hint = row.failure === '排序失败'
      ? '在候选池里，只是没排进前 k。下一步：重排或查询改写。'
      : row.failure === '召回失败'
        ? '前 20 里都没有。下一步：换切法 / 调块大小 / 改写问题。'
        : '标注指向的原文没找到，先检查 golden.jsonl 里的 contains。';
    var top = row.top1 || {};
    return '<div class="fail-item ' + kind + '">' +
      '<div class="fail-q">' + esc(row.question) + '</div>' +
      '<div class="fail-meta">' + esc(rank) +
        '｜top1：<code>' + esc(top.file) + ':' + (top.line || 0) + '</code>' +
        '（' + Number(top.score || 0).toFixed(2) + ' 分）</div>' +
      '<div class="fail-meta">' + esc(hint) + '</div>' +
      '</div>';
  }

  function renderEval(data) {
    var summary = data.summary;
    var s = data.settings;
    var k = summary.k;
    var failure = summary.failure;
    var html = '';

    html += '<div class="metrics">' +
      metricTile('Recall@' + k, summary['recall@k'].toFixed(4), '前 ' + k + ' 段里至少命中 1 段') +
      metricTile('MRR', summary.mrr.toFixed(4), '第一段正确资料的排名倒数') +
      metricTile('Context@' + k, summary['context@k'].toFixed(4), '前 ' + k + ' 段里正确资料占比') +
      metricTile('Recall@20', summary['recall@20'].toFixed(4), '正确资料有没有进候选池') +
      '</div>';

    var rankingClass = failure['排序失败'] ? ' warn' : '';
    var recallClass = failure['召回失败'] ? ' err' : '';
    html += '<div class="failure-bar">' +
      '<span class="fchip ok">成功 <b>' + failure['成功'] + '</b></span>' +
      '<span class="fchip' + rankingClass + '">排序失败 <b>' + failure['排序失败'] + '</b></span>' +
      '<span class="fchip' + recallClass + '">召回失败 <b>' + failure['召回失败'] + '</b></span>' +
      '<span class="fchip">gold 缺失 <b>' + failure['gold 缺失'] + '</b></span>' +
      '</div>';

    html += '<div class="eval-note">' +
      esc(strategyName(s.strategy) + ' · ' + retrieverName(s.retriever) +
        ' · 每块 ' + s.max_chars + ' 字 · 重叠 ' + s.overlap + ' 字 · ' +
        data.answerableText) +
      '</div>';

    if (summary.negative) {
      html += '<div class="eval-note">不可答样本 ' + summary.negative + ' 条，最高分均值 ' +
        summary.negative_top_score_avg.toFixed(2) +
        '。它和可答题的分数有重叠，所以只靠一个分数阈值没法干净拒答。</div>';
    }

    if (data.misses.length) {
      html += '<div class="sub-h">没成功的 ' + data.misses.length + ' 条</div><div class="fails">';
      data.misses.forEach(function (row) { html += failItem(row); });
      html += '</div>';
    } else {
      html += '<div class="note" style="margin-top:12px">全部命中：前 ' + k + ' 段里都找到了标注资料。</div>';
    }

    els.evalBody.innerHTML = html;
  }

  /* ---------- 对比 ---------- */

  function compareLoading() {
    els.compareBody.innerHTML = '<div class="sub-h">4 种组合</div>' +
      '<div class="eval-empty" style="margin-top:8px"><span class="spinner"></span>正在分别构建并评估 4 种组合…</div>';
  }

  function renderCompare(data) {
    var rows = data.rows || [];
    var bestRecall = Math.max.apply(null, rows.map(function (row) { return row.summary['recall@k']; }));
    var bestMrr = Math.max.apply(null, rows.map(function (row) { return row.summary.mrr; }));
    var html = '<div class="sub-h">4 种组合（k=' + data.k + '，每组跑 ' + data.golden + ' 题）</div>';
    html += '<div class="tbl-wrap"><table class="cmp"><thead><tr>' +
      '<th>切块</th><th>检索</th><th>Recall@' + data.k + '</th><th>MRR</th>' +
      '<th>Context@' + data.k + '</th><th>Recall@20</th><th>排序失败</th><th>召回失败</th>' +
      '</tr></thead><tbody>';

    rows.forEach(function (row) {
      var summary = row.summary;
      var recall = summary['recall@k'];
      var mrr = summary.mrr;
      html += '<tr>' +
        '<td class="alg">' + esc(strategyName(row.strategy)) + '</td>' +
        '<td class="alg">' + esc(retrieverName(row.retriever)) + '</td>' +
        '<td class="' + (recall === bestRecall ? 'best' : '') + '">' + recall.toFixed(4) + '</td>' +
        '<td class="' + (mrr === bestMrr ? 'best' : '') + '">' + mrr.toFixed(4) + '</td>' +
        '<td>' + summary['context@k'].toFixed(4) + '</td>' +
        '<td>' + summary['recall@20'].toFixed(4) + '</td>' +
        '<td>' + summary.failure['排序失败'] + '</td>' +
        '<td>' + summary.failure['召回失败'] + '</td>' +
        '</tr>';
    });
    html += '</tbody></table></div>';
    html += '<div class="eval-note">绿色是当前这组里最好的。Recall 看找没找到，MRR 看排得靠不靠前；' +
      '两个指标一起看，才不会把「找到了但排得烂」误判成「检索很强」。</div>';
    els.compareBody.innerHTML = html;
  }

  /* ---------- 动作 ---------- */

  function ask() {
    if (locked) { return; }
    var question = String(els.question.value || '').trim();
    if (!question) {
      setState('先写一个问题，或者点上面的示例。', 'err');
      els.question.focus();
      return;
    }

    var body = settings();
    body.question = question;
    body.generate = !!els.generate.checked;

    setLocked(true);
    busyButton(els.askBtn, true, body.generate ? '检索并回答中…' : '检索中…');
    setState('<span class="spinner"></span>' + (body.generate
      ? '正在检索资料，并让模型只依据资料回答…'
      : '正在检索资料…'), 'busy');
    els.emptyCard.classList.add('hide');

    post('/rag-lab/api/ask', body, body.generate ? 120000 : 30000).then(function (data) {
      if (!data.ok) { throw new Error(data.message || '检索失败'); }
      renderAsk(data);
      var state = '完成 · 检索 ' + data.elapsed_ms + ' ms';
      if (body.generate) {
        state += (data.answer
          ? ' · 回答 ' + ((data.answer_elapsed_ms || 0) / 1000).toFixed(1) + ' 秒'
          : ' · 回答失败');
      }
      setState(esc(state), '');
    }).catch(function (err) {
      setState(esc(errorText(err)), 'err');
      if (!els.hitsCard.classList.contains('hide')) { return; }
      els.emptyCard.classList.remove('hide');
    }).finally(function () {
      busyButton(els.askBtn, false);
      setLocked(false);
    });
  }

  function evaluate() {
    if (locked) { return; }
    var body = settings();
    setLocked(true);
    busyButton(els.evalBtn, true, '评估中…');
    evalLoading();
    post('/rag-lab/api/eval', body, 60000).then(function (data) {
      if (!data.ok) { throw new Error(data.message || '评估失败'); }
      var answerable = data.summary.answerable;
      var negative = data.summary.negative;
      data.answerableText = '可答 ' + answerable + ' 题 · 不可答 ' + negative + ' 题 · 用时 ' +
        data.elapsed_ms + ' ms';
      renderEval(data);
    }).catch(function (err) {
      els.evalBody.innerHTML = '<div class="note err">' + esc(errorText(err)) + '</div>';
    }).finally(function () {
      busyButton(els.evalBtn, false);
      setLocked(false);
    });
  }

  function compare() {
    if (locked) { return; }
    var body = settings();
    setLocked(true);
    busyButton(els.compareBtn, true, '对比中…');
    compareLoading();
    post('/rag-lab/api/compare', body, 90000).then(function (data) {
      if (!data.ok) { throw new Error(data.message || '对比失败'); }
      renderCompare(data);
    }).catch(function (err) {
      els.compareBody.innerHTML = '<div class="sub-h">4 种组合</div>' +
        '<div class="note err" style="margin-top:8px">' + esc(errorText(err)) + '</div>';
    }).finally(function () {
      busyButton(els.compareBtn, false);
      setLocked(false);
    });
  }

  /* ---------- 事件 ---------- */

  els.askBtn.addEventListener('click', ask);
  els.evalBtn.addEventListener('click', evaluate);
  els.compareBtn.addEventListener('click', compare);

  els.question.addEventListener('input', updateCount);
  els.question.addEventListener('keydown', function (event) {
    if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
      event.preventDefault();
      ask();
    }
  });

  updateCount();
  loadStatus();
})();
