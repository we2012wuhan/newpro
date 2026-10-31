(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };
  var state = {
    controller: null,
    fullText: '',
    busy: false,
    renderTimer: 0
  };

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function inlineMarkdown(value) {
    return escapeHtml(value)
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/`([^`]+)`/g, '<code>$1</code>');
  }

  function renderMarkdown(markdown) {
    var lines = String(markdown || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n');
    var out = [];
    var listType = '';
    var paragraph = [];

    function closeList() {
      if (listType) {
        out.push('</' + listType + '>');
        listType = '';
      }
    }

    function flushParagraph() {
      if (paragraph.length) {
        out.push('<p>' + inlineMarkdown(paragraph.join(' ')) + '</p>');
        paragraph = [];
      }
    }

    lines.forEach(function (line) {
      var text = line.replace(/\s+$/, '');
      if (!text.trim()) {
        flushParagraph();
        closeList();
        return;
      }
      var heading = /^(#{1,3})\s+(.+)$/.exec(text);
      if (heading) {
        flushParagraph();
        closeList();
        var level = heading[1].length;
        out.push('<h' + level + '>' + inlineMarkdown(heading[2]) + '</h' + level + '>');
        return;
      }
      if (/^---+$/.test(text.trim())) {
        flushParagraph();
        closeList();
        out.push('<hr>');
        return;
      }
      var bullet = /^\s*[-*]\s+(.+)$/.exec(text);
      if (bullet) {
        flushParagraph();
        if (listType !== 'ul') {
          closeList();
          listType = 'ul';
          out.push('<ul>');
        }
        out.push('<li>' + inlineMarkdown(bullet[1]) + '</li>');
        return;
      }
      var numbered = /^\s*\d+\.\s+(.+)$/.exec(text);
      if (numbered) {
        flushParagraph();
        if (listType !== 'ol') {
          closeList();
          listType = 'ol';
          out.push('<ol>');
        }
        out.push('<li>' + inlineMarkdown(numbered[1]) + '</li>');
        return;
      }
      var quote = /^\s*>\s?(.*)$/.exec(text);
      if (quote) {
        flushParagraph();
        closeList();
        out.push('<blockquote>' + inlineMarkdown(quote[1]) + '</blockquote>');
        return;
      }
      closeList();
      paragraph.push(text.trim());
    });
    flushParagraph();
    closeList();
    return out.join('');
  }

  function scheduleRender(force) {
    window.clearTimeout(state.renderTimer);
    if (force) {
      $('markdown').innerHTML = renderMarkdown(state.fullText);
      return;
    }
    state.renderTimer = window.setTimeout(function () {
      $('markdown').innerHTML = renderMarkdown(state.fullText);
    }, 80);
  }

  function toast(text) {
    var el = $('toast');
    el.textContent = text;
    el.classList.add('on');
    clearTimeout(toast.timer);
    toast.timer = setTimeout(function () { el.classList.remove('on'); }, 1800);
  }

  function setStatus(text, kind) {
    var el = $('status');
    el.textContent = text || '';
    el.className = 'status' + (text ? ' show ' + (kind || 'info') : '');
  }

  function payload() {
    return {
      page_type: $('pageType').value,
      level: $('level').value,
      focus: Array.from(document.querySelectorAll('.focus.on')).map(function (button) {
        return button.dataset.focus;
      }),
      concern: $('concern').value.trim()
    };
  }

  function setBusy(busy) {
    state.busy = busy;
    $('btnGenerate').disabled = busy;
    $('btnAgain').disabled = busy;
    $('btnSample').disabled = busy;
    $('btnStop').hidden = !busy;
    $('btnGenerate').textContent = busy ? '正在生成学习报告…' : '诊断并生成学习方向';
  }

  function beginReport() {
    state.fullText = '';
    $('empty').hidden = true;
    $('streaming').classList.add('on');
    $('streamText').textContent = '正在分析你的设计起点…';
    $('markdown').innerHTML = '';
    $('reportTools').classList.remove('on');
    $('reportState').textContent = '生成中';
    $('btnCopy').disabled = true;
    $('btnDownload').disabled = true;
    setStatus('');
  }

  function finishReport() {
    scheduleRender(true);
    $('streamText').textContent = '报告已完成';
    $('reportState').textContent = '已完成';
    $('reportTools').classList.add('on');
    $('btnCopy').disabled = false;
    $('btnDownload').disabled = false;
  }

  async function generate() {
    if (state.busy) return;
    beginReport();
    setBusy(true);
    state.controller = new AbortController();
    try {
      var response = await fetch('/design-sense/api/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload()),
        signal: state.controller.signal
      });
      if (!response.ok) {
        var errorData = await response.json().catch(function () { return {}; });
        throw new Error(errorData.message || ('服务返回 ' + response.status));
      }
      var reader = response.body.getReader();
      var decoder = new TextDecoder('utf-8');
      var buffer = '';
      while (true) {
        var part = await reader.read();
        if (part.done) break;
        buffer += decoder.decode(part.value, { stream: true });
        var boundary;
        while ((boundary = buffer.indexOf('\n\n')) >= 0) {
          var block = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          var dataLine = block.split(/\n/).find(function (line) { return line.indexOf('data:') === 0; });
          if (!dataLine) continue;
          var event = JSON.parse(dataLine.slice(5).trim());
          if (event.error) throw new Error(event.error);
          if (event.t) {
            state.fullText += event.t;
            scheduleRender(false);
          }
          if (event.done) finishReport();
        }
      }
      if (!state.fullText.trim()) throw new Error('模型没有返回内容，请重新生成。');
      finishReport();
    } catch (error) {
      if (error.name === 'AbortError') {
        setStatus('已停止生成。', 'info');
        $('streamText').textContent = '已停止';
        $('reportState').textContent = '已停止';
        if (state.fullText.trim()) {
          finishReport();
          $('reportState').textContent = '已停止';
          $('streamText').textContent = '已停止生成';
          setStatus('生成已停止，可以复制当前已有内容。', 'info');
        }
      } else {
        setStatus(error.message || '生成失败，请稍后再试。', 'err');
        $('streamText').textContent = '生成失败';
        $('reportState').textContent = '失败';
      }
    } finally {
      state.controller = null;
      setBusy(false);
    }
  }

  async function copyReport() {
    if (!state.fullText) return;
    try {
      await navigator.clipboard.writeText(state.fullText);
    } catch (error) {
      var area = document.createElement('textarea');
      area.value = state.fullText;
      document.body.appendChild(area);
      area.select();
      document.execCommand('copy');
      area.remove();
    }
    toast('已复制完整报告');
  }

  function downloadReport() {
    if (!state.fullText) return;
    var blob = new Blob(['\ufeff' + state.fullText], { type: 'text/markdown;charset=utf-8' });
    var link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = '懂点设计-学习报告-' + new Date().toISOString().slice(0, 10) + '.md';
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(function () { URL.revokeObjectURL(link.href); }, 1500);
    toast('Markdown 已开始下载');
  }

  function bind() {
    document.querySelectorAll('.focus').forEach(function (button) {
      button.addEventListener('click', function () {
        button.classList.toggle('on');
      });
    });
    $('btnGenerate').addEventListener('click', generate);
    $('btnAgain').addEventListener('click', generate);
    $('btnStop').addEventListener('click', function () {
      if (state.controller) state.controller.abort();
    });
    $('btnCopy').addEventListener('click', copyReport);
    $('btnDownload').addEventListener('click', downloadReport);
    $('btnSample').addEventListener('click', function () {
      $('pageType').value = '后台管理页面';
      $('level').value = '看得懂，但自己排不好';
      $('concern').value = '我在做一个数据库查询工具，功能都能用，但页面看起来像临时拼出来的，层级不清，颜色也有点乱。希望更像成熟产品，不想直接照抄模板。';
      document.querySelectorAll('.focus').forEach(function (button) {
        button.classList.toggle('on', ['版式', '色彩', '间距'].indexOf(button.dataset.focus) >= 0);
      });
      toast('示例已填入');
    });
    $('concern').addEventListener('keydown', function (event) {
      if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') generate();
    });
  }

  function init() {
    bind();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
