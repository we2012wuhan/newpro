/* 划词问 AI（全站共用）
 * ------------------------------------------------------------
 * 选中任意工具页的文字 -> 浮出「✦ 问 AI」-> 打开抽屉 -> 四个动作或自由追问。
 * 全部样式封在 Shadow DOM 里，不和 27 个页面各自的 CSS 打架。
 *
 * 页面想关掉：<body data-quick-ai="off"> 或 window.TB_NO_QUICK_AI = true
 * 页面想主动喂一段文字：window.TBQuickAI.open('...')
 */
(function () {
  'use strict';
  if (window.TBQuickAI) { return; }

  var CSS = [
    ':host{all:initial}',
    '*{box-sizing:border-box}',
    '#pill{position:fixed;pointer-events:auto;display:none;align-items:center;gap:6px;',
    '  min-height:38px;padding:0 14px;border-radius:999px;border:1px solid rgba(109,124,255,.55);',
    '  background:rgba(23,26,43,.96);color:#eef1fa;font:13px/1 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;',
    '  box-shadow:0 8px 24px -8px rgba(0,0,0,.6);cursor:pointer;user-select:none;white-space:nowrap;z-index:2}',
    '#pill:hover{border-color:#6d7cff}',
    '#pill.show{display:inline-flex}',
    '#panel{position:fixed;pointer-events:auto;display:none;flex-direction:column;gap:10px;',
    '  width:min(390px,calc(100vw - 24px));max-height:min(72vh,640px);overflow:hidden;',
    '  padding:14px;border-radius:16px;border:1px solid var(--tb-line,#282d45);',
    '  background:var(--tb-card,#171a2b);color:var(--tb-ink,#eef1fa);',
    '  box-shadow:0 24px 60px -20px rgba(0,0,0,.7);',
    '  font:13px/1.6 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;z-index:3}',
    '#panel.show{display:flex}',
    '.hd{display:flex;align-items:center;gap:8px;flex:0 0 auto}',
    '.body{flex:1 1 auto;min-height:0;overflow-y:auto;display:flex;flex-direction:column;gap:10px;padding-right:2px}',
    '.ttl{flex:1 1 auto;font-size:14px;font-weight:600}',
    '.x{min-height:32px;padding:0 12px;border-radius:999px;border:1px solid var(--tb-line,#282d45);',
    '  background:transparent;color:var(--tb-muted,#8b93ab);font:inherit;cursor:pointer}',
    '.quote{padding:8px 10px;border-radius:10px;background:rgba(127,127,127,.10);',
    '  color:var(--tb-text,#c6cbdd);font-size:12.5px;line-height:1.55;max-height:4.6em;overflow:hidden;',
    '  white-space:pre-wrap;word-break:break-word}',
    '.acts{display:flex;flex-wrap:wrap;gap:6px}',
    '.act{min-height:34px;padding:0 12px;border-radius:999px;border:1px solid var(--tb-line,#282d45);',
    '  background:transparent;color:var(--tb-ink,#eef1fa);font:inherit;cursor:pointer}',
    '.act:hover{border-color:var(--tb-acc1,#6d7cff)}',
    '.act.on{background:rgba(109,124,255,.18);border-color:#6d7cff}',
    '.qrow{display:flex;gap:8px}',
    '.qrow input{flex:1 1 auto;min-width:0;min-height:38px;padding:0 10px;border-radius:10px;',
    '  border:1px solid var(--tb-line,#282d45);background:var(--tb-field,rgba(255,255,255,.04));',
    '  color:var(--tb-ink,#eef1fa);font:inherit}',
    '.qrow input:focus{outline:none;border-color:#6d7cff}',
    '.send{flex:0 0 auto;min-height:38px;padding:0 14px;border-radius:10px;border:1px solid rgba(45,212,191,.55);',
    '  background:rgba(45,212,191,.16);color:#b8f2e6;font:inherit;cursor:pointer}',
    '.status{font-size:11.5px;color:var(--tb-muted,#8b93ab);min-height:1em}',
    '.ans{white-space:pre-wrap;word-break:break-word;line-height:1.7;font-size:13.5px;',
    '  color:var(--tb-ink,#eef1fa);min-height:2em}',
    '.ans.err{color:#ff9a9a}',
    '.foot{flex:0 0 auto;display:flex;gap:8px;flex-wrap:wrap;border-top:1px solid var(--tb-line,#282d45);padding-top:10px}',
    '.foot button{min-height:34px;padding:0 12px;border-radius:999px;border:1px solid var(--tb-line,#282d45);',
    '  background:transparent;color:var(--tb-text,#c6cbdd);font:inherit;cursor:pointer}',
    '.foot button.on{border-color:rgba(45,212,191,.6);background:rgba(45,212,191,.16);color:#b8f2e6}',
    '.foot button:disabled{opacity:.45;cursor:default}',
    '@media (pointer:coarse){',
    '  #panel{left:0;right:0;bottom:0;width:100%;max-width:none;max-height:76vh;',
    '    border-radius:18px 18px 0 0;border-left:0;border-right:0;border-bottom:0;',
    '    padding-bottom:calc(14px + env(safe-area-inset-bottom))}',
    '  #pill{bottom:calc(84px + env(safe-area-inset-bottom));right:14px}',
    '}',
  ].join('\n');

  var HTML = [
    '<button id="pill" type="button" aria-label="问 AI">✦ <span>问 AI</span></button>',
    '<div id="panel" role="dialog" aria-label="划词问 AI">',
    '  <div class="hd"><div class="ttl">✦ 问 AI</div><button class="x" id="close" type="button">关闭</button></div>',
    '  <div class="body" id="body">',
    '    <div class="quote" id="quote"></div>',
    '  <div class="acts" id="acts"></div>',
    '  <div class="qrow"><input id="q" type="text" maxlength="500" placeholder="想追问什么？（可选）">',
    '    <button class="send" id="send" type="button">问</button></div>',
    '  <div class="status" id="status"></div>',
    '    <div class="ans" id="ans"></div>',
    '  </div>',
    '  <div class="foot">',
    '    <button id="speak" type="button" aria-pressed="false">🔊 朗读</button>',
    '    <button id="copy" type="button">复制回答</button>',
    '    <button id="stop" type="button" hidden>停止</button>',
    '  </div>',
    '</div>',
  ].join('');

  var host = null, root = null, pill = null, panel = null;
  var quoteEl = null, actsEl = null, qEl = null, ansEl = null, statusEl = null, bodyEl = null;
  var copyBtn = null, stopBtn = null, speakBtn = null;
  var voicePromise = null, voiceBound = false;
  var S = { text: '', answer: '', busy: false, ctrl: null, hideTimer: 0 };
  var ACTIONS = [['explain', '解释'], ['translate', '翻译'], ['fact', '查一下'], ['summary', '总结']];

  function build() {
    host = document.createElement('div');
    host.id = 'tbqa-host';
    host.style.cssText = 'position:fixed;inset:0;z-index:2147483000;pointer-events:none';
    document.body.appendChild(host);
    root = host.attachShadow({ mode: 'open' });
    root.innerHTML = '<style>' + CSS + '</style>' + HTML;
    pill = root.getElementById('pill');
    panel = root.getElementById('panel');
    quoteEl = root.getElementById('quote');
    actsEl = root.getElementById('acts');
    qEl = root.getElementById('q');
    ansEl = root.getElementById('ans');
    bodyEl = root.getElementById('body');
    statusEl = root.getElementById('status');
    copyBtn = root.getElementById('copy');
    speakBtn = root.getElementById('speak');
    stopBtn = root.getElementById('stop');
    actsEl.innerHTML = ACTIONS.map(function (a) {
      return '<button class="act" type="button" data-a="' + a[0] + '">' + a[1] + '</button>';
    }).join('');
    pill.onclick = function (e) { e.stopPropagation(); openPanel(S.text); };
    root.getElementById('close').onclick = function () { closePanel(); };
    copyBtn.onclick = copyAnswer;
    speakBtn.onclick = speakAnswer;
    document.addEventListener('tbvoice:error', function (e) {
      if (e && e.detail && e.detail.message && panel.classList.contains('show')) {
        statusEl.textContent = e.detail.message;
      }
    });
    stopBtn.onclick = function () { if (S.ctrl) { S.ctrl.abort(); } };
    root.getElementById('send').onclick = function () {
      var q = String(qEl.value || '').trim();
      ask(S.action || 'explain', q);
    };
    qEl.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); ask(S.action || 'explain', String(qEl.value || '').trim()); }
    });
    Array.prototype.forEach.call(actsEl.querySelectorAll('[data-a]'), function (b) {
      b.onclick = function () { ask(b.getAttribute('data-a'), ''); };
    });
    setBusy(false);
  }

  function isCoarse() {
    try { return window.matchMedia('(pointer: coarse)').matches; } catch (e) { return false; }
  }

  function pageLabel() {
    var t = (document.title || '').replace(/\s*·\s*我的工具箱$/, '').slice(0, 80);
    return (t ? t + ' ' : '') + location.pathname;
  }

  function selectionInfo() {
    // 1) 输入框 / 文本域：window.getSelection() 拿不到，用 selectionStart/End
    var ae = document.activeElement;
    if (ae && (ae.tagName === 'INPUT' || ae.tagName === 'TEXTAREA')) {
      var st = ae.selectionStart, en = ae.selectionEnd;
      if (typeof st === 'number' && typeof en === 'number' && en > st) {
        var picked = String(ae.value || '').slice(st, en).replace(/\u00a0/g, ' ').trim();
        if (picked.length >= 2) {
          return { text: picked.slice(0, 4000), rect: ae.getBoundingClientRect() };
        }
      }
      // 只是光标停在输入框里、没选字：继续往下看页面里的普通选区
    }
    // 2) 普通文本
    var sel = window.getSelection();
    if (!sel || sel.rangeCount === 0 || sel.isCollapsed) { return null; }
    var text = String(sel.toString() || '').replace(/\u00a0/g, ' ').trim();
    if (text.length < 2) { return null; }
    var node = sel.anchorNode;
    var el = node && (node.nodeType === 1 ? node : node.parentElement);
    if (el && el.closest && el.closest('[data-quick-ai="off"]')) { return null; }
    if (host && el && (el === host || host.contains(el))) { return null; }
    var rect = null;
    try { rect = sel.getRangeAt(0).getBoundingClientRect(); } catch (e) {}
    return { text: text.slice(0, 4000), rect: rect };
  }

  function showPill(info) {
    if (!info) { hidePill(); return; }
    S.text = info.text;
    pill.classList.add('show');
    if (!isCoarse() && info.rect && info.rect.width) {
      var w = pill.offsetWidth || 92, h = pill.offsetHeight || 38;
      var x = Math.min(window.innerWidth - w - 10, Math.max(10, info.rect.right - 10));
      var y = info.rect.bottom + 8;
      if (y + h > window.innerHeight - 10) { y = info.rect.top - h - 8; }
      pill.style.left = x + 'px';
      pill.style.top = Math.max(10, y) + 'px';
      pill.style.right = 'auto';
      pill.style.bottom = 'auto';
    } else {
      pill.style.left = 'auto';
      pill.style.top = 'auto';
      pill.style.right = '14px';
      pill.style.bottom = 'calc(84px + env(safe-area-inset-bottom))';
    }
  }

  function hidePill() { if (pill) { pill.classList.remove('show'); } }

  function openPanel(text) {
    if (!text || text.length < 2) { return; }
    stopVoice();
    S.text = text.slice(0, 4000);
    S.answer = '';
    quoteEl.textContent = S.text.length > 260 ? S.text.slice(0, 260) + '…' : S.text;
    ansEl.textContent = '';
    ansEl.classList.remove('err');
    statusEl.textContent = '选一个动作，或者直接追问';
    setBusy(false);
    qEl.value = '';
    if (bodyEl) { bodyEl.scrollTop = 0; }
    hidePill();
    panel.classList.add('show');
    markAction('');
    if (!isCoarse() || !window.matchMedia) { /* 桌面把面板贴右边 */ }
    if (!isCoarse()) {
      panel.style.right = '16px';
      panel.style.top = '16px';
      panel.style.left = 'auto';
      panel.style.bottom = 'auto';
    } else {
      panel.style.right = '';
      panel.style.top = '';
      panel.style.left = '';
      panel.style.bottom = '';
    }
  }

  function closePanel() {
    stopVoice();
    if (S.ctrl) { try { S.ctrl.abort(); } catch (e) {} }
    S.ctrl = null;
    S.busy = false;
    panel.classList.remove('show');
    stopBtn.hidden = true;
  }

  function markAction(a) {
    S.action = a;
    Array.prototype.forEach.call(actsEl.querySelectorAll('[data-a]'), function (b) {
      b.classList.toggle('on', b.getAttribute('data-a') === a);
    });
  }

  function setBusy(on) {
    S.busy = on;
    stopBtn.hidden = !on;
    copyBtn.disabled = !S.answer;
    if (speakBtn) { speakBtn.disabled = !S.answer; }
    Array.prototype.forEach.call(actsEl.querySelectorAll('[data-a]'), function (b) { b.disabled = on; });
  }

  function fail(msg) {
    ansEl.classList.add('err');
    ansEl.textContent = msg;
    statusEl.textContent = '';
  }

  function handleSSE(chunk) {
    chunk.split('\n').forEach(function (line) {
      if (line.indexOf('data:') !== 0) { return; }
      var obj;
      try { obj = JSON.parse(line.slice(5).trim()); } catch (e) { return; }
      if (obj.t) {
        S.answer += obj.t;
        ansEl.textContent = S.answer;
        if (bodyEl) { bodyEl.scrollTop = bodyEl.scrollHeight; }
      }
      if (obj.err) { fail(obj.err); setBusy(false); }
      if (obj.done) { statusEl.textContent = obj.cached ? '完成（缓存）' : '完成'; setBusy(false); }
    });
  }

  function ask(action, question) {
    if (!S.text || S.busy) { return; }
    stopVoice();
    markAction(action);
    S.answer = '';
    ansEl.textContent = '';
    ansEl.classList.remove('err');
    statusEl.textContent = '正在想…';
    setBusy(true);
    S.ctrl = ('AbortController' in window) ? new AbortController() : null;
    var init = {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: action, text: S.text, question: question || '', page: pageLabel() }),
    };
    if (S.ctrl) { init.signal = S.ctrl.signal; }
    fetch('/api/quick-ask', init).then(function (resp) {
      var ct = resp.headers.get('content-type') || '';
      if (ct.indexOf('application/json') >= 0) {
        return resp.json().then(function (d) { throw new Error((d && d.msg) || '请求被拒绝'); });
      }
      if (!resp.ok || !resp.body) { throw new Error('请求失败（HTTP ' + resp.status + '）'); }
      var reader = resp.body.getReader();
      var dec = new TextDecoder();
      var buf = '';
      function pump() {
        return reader.read().then(function (r) {
          if (r.done) {
            if (buf.trim()) { handleSSE(buf); }
            return;
          }
          buf += dec.decode(r.value, { stream: true });
          var parts = buf.split('\n\n');
          buf = parts.pop();
          parts.forEach(handleSSE);
          return pump();
        });
      }
      return pump();
    }).catch(function (err) {
      if (err && err.name === 'AbortError') { statusEl.textContent = '已停止'; setBusy(false); return; }
      fail((err && err.message) || '网络不通');
      setBusy(false);
    });
  }

  function answerText() { return S.answer || ''; }

  /* voice.js 只有 3 个页面引了，其余页面第一次点朗读时再懒加载 */
  function ensureVoice() {
    if (window.TBVoice) { return Promise.resolve(window.TBVoice); }
    if (voicePromise) { return voicePromise; }
    voicePromise = new Promise(function (resolve) {
      var tag = document.createElement('script');
      tag.src = '/static/voice.js';
      tag.onload = function () { resolve(window.TBVoice || null); };
      tag.onerror = function () { resolve(null); };
      document.head.appendChild(tag);
    });
    return voicePromise;
  }

  function stopVoice() {
    if (window.TBVoice) { try { window.TBVoice.stop(); } catch (e) {} }
  }

  function speakAnswer() {
    var text = answerText();
    if (!text) { return; }
    ensureVoice().then(function (V) {
      if (!V || !V.available()) { statusEl.textContent = '朗读组件没加载出来'; return; }
      if (!voiceBound) {
        voiceBound = true;
        V.on(function (st) {
          var on = !!(st && st.speaking && st.text === answerText());
          speakBtn.classList.toggle('on', on);
          speakBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
          speakBtn.textContent = on ? '⏹ 停止' : '🔊 朗读';
        });
      }
      V.toggle(text, { key: 'quick-ai' });
    });
  }

  function copyAnswer() {
    var text = S.answer || '';
    if (!text) { return; }
    function done(ok) {
      statusEl.textContent = ok ? '已复制' : '复制失败，手动选中吧';
      setTimeout(function () { if (statusEl.textContent === (ok ? '已复制' : '复制失败，手动选中吧')) { statusEl.textContent = ''; } }, 1600);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { done(true); }, function () { fallback(); });
    } else { fallback(); }
    function fallback() {
      try {
        var ta = document.createElement('textarea');
        ta.value = text;
        ta.style.cssText = 'position:fixed;left:-9999px;top:0';
        document.body.appendChild(ta);
        ta.select();
        var ok = document.execCommand('copy');
        document.body.removeChild(ta);
        done(ok);
      } catch (e) { done(false); }
    }
  }

  function inHost(e) {
    try { return (e.composedPath ? e.composedPath() : []).indexOf(host) >= 0; } catch (err) { return false; }
  }

  function bind() {
    document.addEventListener('mouseup', function (e) {
      if (inHost(e)) { return; }
      clearTimeout(S.hideTimer);
      S.hideTimer = setTimeout(function () { showPill(selectionInfo()); }, 30);
    });
    document.addEventListener('touchend', function (e) {
      if (inHost(e)) { return; }
      clearTimeout(S.hideTimer);
      S.hideTimer = setTimeout(function () { showPill(selectionInfo()); }, 260);
    }, { passive: true });
    document.addEventListener('mousedown', function (e) {
      if (inHost(e)) { return; }
      if (panel.classList.contains('show') || pill.classList.contains('show')) { /* 保留，等 mouseup 重新判断 */ }
    }, true);
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && panel.classList.contains('show')) { closePanel(); }
    });
    window.addEventListener('scroll', function () { hidePill(); }, true);
    document.addEventListener('select', function (e) {
      if (inHost(e)) { return; }
      clearTimeout(S.hideTimer);
      S.hideTimer = setTimeout(function () { showPill(selectionInfo()); }, 60);
    }, true);
    document.addEventListener('selectionchange', function () {
      var sel = window.getSelection();
      if (!sel || sel.isCollapsed) { hidePill(); }
    });
  }

  function ready(fn) {
    if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', fn); }
    else { fn(); }
  }

  ready(function () {
    if (window.TB_NO_QUICK_AI) { return; }
    if (document.body && document.body.getAttribute('data-quick-ai') === 'off') { return; }
    build();
    bind();
    window.TBQuickAI = {
      open: function (text) { openPanel(String(text || '')); },
      close: closePanel,
      hide: hidePill,
    };
  });
})();
