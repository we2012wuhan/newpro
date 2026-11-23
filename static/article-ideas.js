/*
 * AI 文章写作 · 选题页签
 * =======================
 * 从「写作选题」搬进 /article 的「选题」页签，保持原有 localStorage 数据
 * （wr_draft / wr_saved）。接口：
 *   POST /writing-ideas/api/ideas
 *   POST /writing-ideas/api/develop
 * 每次成功出题还会在服务端留一条历史（tool='writing-ideas'），
 * 页面底部的「最近选题」可以回看、恢复当时的输入并删除。
 *
 * 和旧页面的区别：
 *   - 长文体裁的「用这个选题开写」不再跳转页面，直接调用
 *     window.TBArticleAdoptIdea(idea)，在同一页切到「写作」并带入内容；
 *   - toast 复用文章页右下角的状态提示；
 *   - 类名统一加 wi- 前缀，避免和写作区的 .btn/.empty/.actions 等冲突。
 */
(function () {
  'use strict';

  var BASE = '';
  var K_DRAFT = 'wr_draft', K_SAVED = 'wr_saved';

  // 只有适合长文的体裁才给「用这个选题开写」；其他体裁保留复制，避免误导。
  var LONGFORM_GENRE = {
    '公众号长文': 'insight',
    '公众号': 'insight',
    '公众号推文': 'insight',
    '长文': 'insight',
    '技术博客': 'tutorial',
    '博客': 'tutorial',
    '知乎回答': 'insight',
    '知乎': 'insight',
    '复盘': 'story'
  };

  var FORMS = [
    { k: 'wechat', l: '公众号长文', i: '📰' },
    { k: 'xiaohongshu', l: '小红书', i: '📕' },
    { k: 'tech', l: '技术博客', i: '🧩' },
    { k: 'zhihu', l: '知乎回答', i: '💬' },
    { k: 'note', l: '短评 / 朋友圈', i: '💭' },
    { k: 'review', l: '复盘', i: '🔁' },
    { k: 'email', l: '邮件 / 汇报', i: '✉️' },
    { k: 'newsletter', l: '周报 / Newsletter', i: '📮' }
  ];
  var FLAVORS = [
    { k: 'contrarian', l: '反常识' },
    { k: 'story', l: '讲故事' },
    { k: 'howto', l: '干货方法' },
    { k: 'rant', l: '吐槽' },
    { k: 'reflect', l: '反思' },
    { k: 'data', l: '用数据说话' },
    { k: 'explain', l: '讲清一件事' },
    { k: 'warm', l: '温柔陪伴' }
  ];
  var HINTS = [
    { f: 'wiTrigger', t: '一次返工' },
    { f: 'wiTrigger', t: '我以为很简单、结果很麻烦的一个任务' },
    { f: 'wiSeed', t: '一件我每天都在重复做的小事' },
    { f: 'wiSeed', t: '带新人 / 被新人问住的时候' },
    { f: 'wiAudience', t: '跟我在同一个坑里的人' },
    { f: 'wiAudience', t: '一年前的我自己' }
  ];
  var FIELD_LABEL = {
    wiSeed: '我正在做 / 关心的事',
    wiAudience: '想写给谁看',
    wiTrigger: '最近让我有反应的一件事'
  };

  var state = {
    forms: [], flavors: [], n: 8,
    ideas: [], source: '', pack: null, packIdea: null, packSource: '',
    history: [], busy: false
  };
  var historySeq = 0;

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function store(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }
  function load(k, fb) {
    try {
      var v = localStorage.getItem(k);
      return v === null ? fb : JSON.parse(v);
    } catch (e) { return fb; }
  }

  function toast(msg, kind) {
    if (typeof window.TBArticleToast === 'function') {
      window.TBArticleToast(msg, kind || 'info');
      return;
    }
    try { console.log(msg); } catch (e) {}
  }

  function jsonApi(path, options) {
    options = options || {};
    var headers = Object.assign({ Accept: 'application/json' }, options.headers || {});
    return fetch(BASE + path, Object.assign({ credentials: 'same-origin' }, options, {
      headers: headers
    })).then(function (r) {
      var ct = r.headers.get('content-type') || '';
      if (ct.indexOf('json') < 0) throw new Error('登录状态可能已失效，刷新页面重新登录');
      return r.json().then(function (d) {
        if (!r.ok || (d && d.ok === false)) {
          var err = new Error((d && (d.message || d.detail)) || ('请求失败（HTTP ' + r.status + '）'));
          err.status = r.status;
          throw err;
        }
        return d;
      });
    });
  }

  function formatAt(value) {
    var text = String(value || '');
    return text.length >= 16 ? text.slice(5, 16) : text;
  }

  function setNote(html, isErr) {
    var n = $('wiNote');
    if (!n) return;
    n.className = 'wi-note' + (isErr ? ' err' : '');
    n.innerHTML = html || '';
  }
  function stamp() {
    var d = new Date(), p = function (x) { return (x < 10 ? '0' : '') + x; };
    return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' '
      + p(d.getHours()) + ':' + p(d.getMinutes());
  }

  /* ---------- 输入区 ---------- */
  function chipHTML(list, kind) {
    return list.map(function (x) {
      var on = state[kind].indexOf(x.k) >= 0;
      return '<button class="wi-chip' + (on ? ' on' : '') + '" type="button" data-w' + kind
        + '="' + x.k + '">' + (x.i ? '<span>' + x.i + '</span>' : '') + esc(x.l) + '</button>';
    }).join('');
  }
  function renderChips() {
    $('wiForms').innerHTML = chipHTML(FORMS, 'forms');
    $('wiFlavors').innerHTML = chipHTML(FLAVORS, 'flavors');
    $('wiHints').innerHTML = HINTS.map(function (h, i) {
      return '<button class="wi-chip" type="button" data-whint="' + i + '">「' + esc(h.t) + '」</button>';
    }).join('');
    Array.prototype.forEach.call(document.querySelectorAll('[data-wforms]'), function (b) {
      b.onclick = function () {
        var k = b.getAttribute('data-wforms'), i = state.forms.indexOf(k);
        if (i >= 0) state.forms.splice(i, 1); else state.forms.push(k);
        renderChips(); saveDraft();
      };
    });
    Array.prototype.forEach.call(document.querySelectorAll('[data-wflavors]'), function (b) {
      b.onclick = function () {
        var k = b.getAttribute('data-wflavors'), i = state.flavors.indexOf(k);
        if (i >= 0) state.flavors.splice(i, 1); else state.flavors.push(k);
        renderChips(); saveDraft();
      };
    });
    Array.prototype.forEach.call(document.querySelectorAll('[data-whint]'), function (b) {
      b.onclick = function () {
        var h = HINTS[+b.getAttribute('data-whint')];
        var el = $(h.f);
        if (!el) return;
        el.value = el.value.trim() ? (el.value.replace(/\s+$/, '') + '；' + h.t) : h.t;
        el.focus(); saveDraft();
        toast('已填进「' + FIELD_LABEL[h.f] + '」');
      };
    });
  }
  function renderNums() {
    Array.prototype.forEach.call(document.querySelectorAll('#wiNums .wi-chip'), function (b) {
      b.classList.toggle('on', +b.getAttribute('data-n') === state.n);
      b.onclick = function () { state.n = +b.getAttribute('data-n'); renderNums(); saveDraft(); };
    });
  }
  function saveDraft() {
    store(K_DRAFT, {
      seed: $('wiSeed').value, audience: $('wiAudience').value, trigger: $('wiTrigger').value,
      forms: state.forms, flavors: state.flavors, n: state.n
    });
  }
  function loadDraft() {
    var d = load(K_DRAFT, null);
    if (!d) return;
    $('wiSeed').value = d.seed || '';
    $('wiAudience').value = d.audience || '';
    $('wiTrigger').value = d.trigger || '';
    state.forms = d.forms || [];
    state.flavors = d.flavors || [];
    state.n = d.n === 6 ? 6 : 8;
  }
  function collect() {
    return {
      seed: $('wiSeed').value.trim(),
      audience: $('wiAudience').value.trim(),
      trigger: $('wiTrigger').value.trim(),
      forms: state.forms, flavors: state.flavors, n: state.n
    };
  }

  /* ---------- 出题 ---------- */
  function ask(extra) {
    if (state.busy) return;
    state.busy = true;
    $('wiGo').disabled = true;
    setNote('<span class="spin"></span> 正在找选题…');
    var body = collect();
    body.exclude = (extra && extra.exclude) || [];
    fetch(BASE + '/writing-ideas/api/ideas', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    }).then(function (r) {
      var ct = r.headers.get('content-type') || '';
      if (ct.indexOf('json') < 0) throw new Error('登录状态可能已失效，刷新页面重新登录');
      return r.json();
    }).then(function (d) {
      if (!d || !d.ok) throw new Error((d && (d.detail || d.message)) || '没生成出来，再试一次');
      state.ideas = d.ideas || [];
      state.source = d.source || '';
      if (!state.ideas.length) throw new Error('这次没凑出选题，把线索写多一点再试');
      var hintCard = $('wiHintCard');
      if (hintCard) hintCard.hidden = true;
      renderIdeas();
      loadHistory();
      setNote('');
    }).catch(function (e) {
      setNote(esc(e && e.message ? e.message : '生成失败，再试一次'), true);
    }).then(function () {
      state.busy = false;
      $('wiGo').disabled = false;
    });
  }

  function ideaCard(it, idx) {
    var lvl = it.level || '中等';
    var cls = lvl === '好写' ? 'b-ok' : (lvl === '要下功夫' ? 'b-warn' : 'b-n');
    var h = '<div class="wi-idea wi-up" style="animation-delay:' + (idx * 40) + 'ms">'
      + '<div class="wi-idea-t">' + esc(it.title) + '</div>'
      + '<div class="wi-idea-claim">' + esc(it.claim) + '</div>'
      + '<div class="wi-meta">'
      + (it.form ? '<span class="wi-badge">' + esc(it.form) + '</span>' : '')
      + (it.flavor ? '<span class="wi-badge">' + esc(it.flavor) + '</span>' : '')
      + '<span class="wi-badge ' + cls + '">' + esc(lvl) + '</span>'
      + '</div>';
    if (it.angle) h += '<div class="wi-line"><b>怎么切</b>' + esc(it.angle) + '</div>';
    if (it.why_now) h += '<div class="wi-line"><b>为什么值得写</b>' + esc(it.why_now) + '</div>';
    if (it.hook) h += '<div class="wi-line"><b>开头第一句</b><div class="wi-hook">' + esc(it.hook) + '</div></div>';
    var extra = '';
    if (it.outline && it.outline.length) {
      extra += '<div class="wi-line"><b>提纲</b><ol class="wi-outline">'
        + it.outline.map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ol></div>';
    }
    if (it.material) extra += '<div class="wi-line"><b>要补的素材</b>' + esc(it.material) + '</div>';
    if (extra) h += '<details class="wi-fold"><summary>提纲与素材</summary>' + extra + '</details>';
    h += '<div class="wi-actions">'
      + (longformGenre(it)
        ? '<button class="wi-iconbtn primary" type="button" data-wuse="' + idx + '">用这个选题开写</button>'
        : '')
      + '<button class="wi-iconbtn" type="button" data-wdev="' + idx + '">深挖成提纲</button>'
      + '<button class="wi-iconbtn" type="button" data-wsave="' + idx + '">收进我的选题</button>'
      + '<button class="wi-iconbtn" type="button" data-wcopy="' + idx + '">复制选题</button>'
      + '</div></div>';
    return h;
  }

  function renderIdeas() {
    var w = $('wiListWrap');
    if (!state.ideas.length) { w.innerHTML = ''; return; }
    var src = state.source === 'recipe' ? ' <span class="wi-badge">内置配方</span>' : '';
    w.innerHTML = '<div class="wi-sec-head"><b>这 ' + state.ideas.length + ' 个方向' + src + '</b>'
      + '<button class="btn mini" type="button" id="wiReroll">换一批</button></div>'
      + state.ideas.map(ideaCard).join('');
    $('wiReroll').onclick = function () {
      ask({ exclude: state.ideas.map(function (x) { return x.title; }) });
    };
    Array.prototype.forEach.call(w.querySelectorAll('[data-wdev]'), function (b) {
      b.onclick = function () { develop(state.ideas[+b.getAttribute('data-wdev')]); };
    });
    Array.prototype.forEach.call(w.querySelectorAll('[data-wuse]'), function (b) {
      b.onclick = function () { useIdea(state.ideas[+b.getAttribute('data-wuse')]); };
    });
    Array.prototype.forEach.call(w.querySelectorAll('[data-wsave]'), function (b) {
      b.onclick = function () { saveIdea(state.ideas[+b.getAttribute('data-wsave')]); };
    });
    Array.prototype.forEach.call(w.querySelectorAll('[data-wcopy]'), function (b) {
      b.onclick = function () { copyText(ideaMd(state.ideas[+b.getAttribute('data-wcopy')]), '选题已复制'); };
    });
  }

  /* ---------- 深挖 ---------- */
  function develop(it) {
    if (!it || state.busy) return;
    state.busy = true;
    setNote('<span class="spin"></span> 正在拆成提纲…');
    fetch(BASE + '/writing-ideas/api/develop', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        idea: it, seed: $('wiSeed').value.trim(),
        audience: $('wiAudience').value.trim(), trigger: $('wiTrigger').value.trim(),
        forms: state.forms, flavors: state.flavors
      })
    }).then(function (r) {
      var ct = r.headers.get('content-type') || '';
      if (ct.indexOf('json') < 0) throw new Error('登录状态可能已失效，刷新页面重新登录');
      return r.json();
    }).then(function (d) {
      if (!d || !d.ok) throw new Error((d && (d.detail || d.message)) || '没拆出来，再试一次');
      state.pack = d.pack;
      state.packIdea = it;
      state.packSource = d.source || '';
      renderPack();
      setNote('');
      var el = $('wiPackWrap');
      if (el && el.scrollIntoView) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }).catch(function (e) {
      setNote(esc(e && e.message ? e.message : '拆解失败，再试一次'), true);
    }).then(function () {
      state.busy = false;
    });
  }

  function renderPack() {
    var p = state.pack, it = state.packIdea || {};
    var w = $('wiPackWrap');
    if (!p) { w.innerHTML = ''; return; }
    var h = '<div class="wi-card wi-up" id="wiPackCard">'
      + '<div class="wi-sec-head" style="margin:0 0 6px"><b>写作启动包</b>'
      + '<button class="btn mini" type="button" id="wiClosePack">收起</button></div>'
      + '<div class="wi-idea-t">' + esc(it.title || '') + '</div>'
      + (it.claim ? '<div class="wi-idea-claim">' + esc(it.claim) + '</div>' : '');
    if (p.audience) h += '<div class="wi-pk-sec"><div class="wi-pk-h">写给谁</div><div class="wi-pk-ul">' + esc(p.audience) + '</div></div>';
    if (p.titles && p.titles.length) {
      h += '<div class="wi-pk-sec"><div class="wi-pk-h">备选标题 · 点一下复制</div>'
        + p.titles.map(function (t) {
          return '<button class="wi-tbtn" type="button" data-wtt="' + esc(t) + '">' + esc(t) + '</button>';
        }).join('')
        + '</div>';
    }
    if (p.outline && p.outline.length) {
      h += '<div class="wi-pk-sec"><div class="wi-pk-h">结构</div>' + p.outline.map(function (s, i) {
        return '<div style="margin-top:8px"><div style="font-size:13px;font-weight:650;color:var(--ink)">'
          + (i + 1) + '. ' + esc(s.h) + '</div>'
          + '<ul class="wi-pk-ul">'
          + (s.points || []).map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('')
          + '</ul></div>';
      }).join('') + '</div>';
    }
    if (p.evidence && p.evidence.length) {
      h += '<div class="wi-pk-sec"><div class="wi-pk-h">要准备的证据</div><ul class="wi-pk-ul">'
        + p.evidence.map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ul></div>';
    }
    if (p.objections && p.objections.length) {
      h += '<div class="wi-pk-sec"><div class="wi-pk-h">可能被反驳</div><ul class="wi-pk-ul">'
        + p.objections.map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ul></div>';
    }
    if (p.ending) h += '<div class="wi-pk-sec"><div class="wi-pk-h">结尾怎么收</div><div class="wi-pk-ul">' + esc(p.ending) + '</div></div>';
    if (p.series && p.series.length) {
      h += '<div class="wi-pk-sec"><div class="wi-pk-h">还能往下写</div><div class="wi-series">'
        + p.series.map(function (x) {
          return '<div class="wi-series-item"><span>' + esc(x) + '</span>'
            + '<button class="wi-iconbtn" type="button" data-wmore="' + esc(x) + '">再出一批</button></div>';
        }).join('') + '</div></div>';
    }
    h += '<div class="wi-actions">'
      + '<button class="wi-iconbtn" type="button" id="wiCopyPack">复制全文</button>'
      + '<button class="wi-iconbtn" type="button" id="wiDlPack">下载 .md</button>'
      + '<button class="wi-iconbtn" type="button" id="wiSavePack">收进我的选题</button>'
      + '</div></div>';
    w.innerHTML = h;

    $('wiClosePack').onclick = function () { state.pack = null; renderPack(); };
    $('wiCopyPack').onclick = function () { copyText(packMd(p, it), '启动包已复制'); };
    $('wiDlPack').onclick = function () { download('选题-' + (it.title || '未命名') + '.md', packMd(p, it)); };
    $('wiSavePack').onclick = function () { saveIdea(it, p); };
    Array.prototype.forEach.call(w.querySelectorAll('[data-wtt]'), function (b) {
      b.onclick = function () { copyText(b.getAttribute('data-wtt'), '标题已复制'); };
    });
    Array.prototype.forEach.call(w.querySelectorAll('[data-wmore]'), function (b) {
      b.onclick = function () {
        $('wiSeed').value = b.getAttribute('data-wmore');
        saveDraft();
        window.scrollTo({ top: 0, behavior: 'smooth' });
        ask({ exclude: state.ideas.map(function (x) { return x.title; }) });
      };
    });
  }

  /* ---------- 我的选题 ---------- */
  function saveIdea(it, pack) {
    if (!it || !it.title) return;
    var recs = load(K_SAVED, []);
    var has = recs.filter(function (r) { return r.title === it.title; })[0];
    if (has) {
      if (pack) {
        has.pack = pack;
        store(K_SAVED, recs);
        renderMine();
        toast('已把启动包存进这条');
        return;
      }
      toast('这条已经在里面了');
      return;
    }
    recs.unshift({
      id: 'w' + Date.now(), at: stamp(),
      title: it.title, claim: it.claim || '', angle: it.angle || '',
      form: it.form || '', flavor: it.flavor || '', level: it.level || '',
      outline: it.outline || [], hook: it.hook || '', material: it.material || '',
      pack: pack || null
    });
    store(K_SAVED, recs.slice(0, 200));
    renderMine();
    toast('已收进我的选题');
  }

  function renderMine() {
    var recs = load(K_SAVED, []);
    var w = $('wiMineWrap');
    if (!recs.length) { w.innerHTML = ''; return; }
    w.innerHTML = '<div class="wi-sec-head"><b>我的选题（' + recs.length + '）</b>'
      + '<button class="btn mini" type="button" id="wiDlAll">下载全部 .md</button></div>'
      + recs.map(function (r, i) {
        return '<div class="wi-mine-item">'
          + '<div class="wi-mine-t">' + esc(r.title) + '</div>'
          + (r.claim ? '<div class="wi-line" style="margin-top:4px">' + esc(r.claim) + '</div>' : '')
          + '<div class="wi-mine-m">'
          + (r.form ? '<span>' + esc(r.form) + '</span>' : '')
          + (r.flavor ? '<span>' + esc(r.flavor) + '</span>' : '')
          + (r.angle ? '<span>' + esc(r.angle) + '</span>' : '')
          + '<span>' + esc(r.at) + '</span>'
          + (r.pack ? '<span class="wi-badge b-ok">有启动包</span>' : '')
          + '</div>'
          + '<div class="wi-actions">'
          + (longformGenre(r)
            ? '<button class="wi-iconbtn primary" type="button" data-wsuse="' + i + '">用这个选题开写</button>'
            : '')
          + '<button class="wi-iconbtn" type="button" data-wsdev="' + i + '">'
          + (r.pack ? '看启动包' : '深挖成提纲') + '</button>'
          + '<button class="wi-iconbtn" type="button" data-wscopy="' + i + '">复制选题</button>'
          + '<button class="wi-iconbtn danger" type="button" data-wsdel="' + i + '">删除</button>'
          + '</div></div>';
      }).join('')
      + '<div class="wi-btn-row"><button class="btn" type="button" id="wiClearAll">清空我的选题</button></div>';

    $('wiDlAll').onclick = function () {
      download('我的选题-' + stamp().slice(0, 10) + '.md', recs.map(function (r) {
        return ideaMd(r) + (r.pack ? '\n\n' + packMd(r.pack, r) : '');
      }).join('\n\n---\n\n'));
    };
    $('wiClearAll').onclick = function () {
      if (!window.confirm('清空「我的选题」？已经复制和下载的不受影响。')) return;
      store(K_SAVED, []);
      renderMine();
      toast('已清空');
    };
    Array.prototype.forEach.call(w.querySelectorAll('[data-wsuse]'), function (b) {
      b.onclick = function () { useIdea(recs[+b.getAttribute('data-wsuse')]); };
    });
    Array.prototype.forEach.call(w.querySelectorAll('[data-wsdev]'), function (b) {
      b.onclick = function () {
        var r = recs[+b.getAttribute('data-wsdev')];
        if (r.pack) {
          state.pack = r.pack;
          state.packIdea = r;
          renderPack();
          $('wiPackWrap').scrollIntoView({ behavior: 'smooth', block: 'start' });
          return;
        }
        develop(r);
      };
    });
    Array.prototype.forEach.call(w.querySelectorAll('[data-wscopy]'), function (b) {
      b.onclick = function () {
        var r = recs[+b.getAttribute('data-wscopy')];
        copyText(ideaMd(r) + (r.pack ? '\n\n' + packMd(r.pack, r) : ''), '已复制');
      };
    });
    Array.prototype.forEach.call(w.querySelectorAll('[data-wsdel]'), function (b) {
      b.onclick = function () {
        var r = recs[+b.getAttribute('data-wsdel')];
        store(K_SAVED, load(K_SAVED, []).filter(function (x) { return x.id !== r.id; }));
        renderMine();
        toast('已删除');
      };
    });
  }

  /* ---------- 最近选题（服务端历史） ---------- */
  function renderHistory() {
    var recs = state.history || [];
    var w = $('wiHistoryWrap');
    if (!w) return;
    var head = '<div class="wi-sec-head"><b>最近选题（' + recs.length + '）</b>'
      + '<span class="wi-badge">自动记录 · 最近 30 批</span></div>';
    if (!recs.length) {
      w.innerHTML = head + '<div class="wi-empty"><b>还没有生成记录</b>'
        + '生成一批选题后会自动记在这里，换浏览器、换设备也还在。</div>';
      return;
    }
    w.innerHTML = head + recs.map(function (r, i) {
      var meta = [
        (r.count || 0) + ' 个',
        r.source === 'recipe' ? '内置配方' : (r.source ? '模型' : ''),
        formatAt(r.created_at)
      ].filter(Boolean).join(' · ');
      var ctx = r.seed ? ('线索：' + r.seed)
        : (r.trigger ? ('最近：' + r.trigger)
          : (r.audience ? ('写给：' + r.audience) : '没填线索'));
      return '<div class="wi-mine-item">'
        + '<div class="wi-mine-t">' + esc(r.title || '一批选题') + '</div>'
        + '<div class="wi-mine-m"><span>' + esc(meta) + '</span></div>'
        + '<div class="wi-hist-ctx" title="' + esc(ctx) + '">' + esc(ctx) + '</div>'
        + '<div class="wi-actions">'
        + '<button class="wi-iconbtn primary" type="button" data-whopen="' + i + '">打开这一批</button>'
        + '<button class="wi-iconbtn danger" type="button" data-whdel="' + i + '">删除</button>'
        + '</div></div>';
    }).join('');

    Array.prototype.forEach.call(w.querySelectorAll('[data-whopen]'), function (b) {
      b.onclick = function () { openHistory(recs[+b.getAttribute('data-whopen')]); };
    });
    Array.prototype.forEach.call(w.querySelectorAll('[data-whdel]'), function (b) {
      b.onclick = function () { deleteHistory(recs[+b.getAttribute('data-whdel')]); };
    });
  }

  function loadHistory() {
    var w = $('wiHistoryWrap');
    if (!w) return;
    var seq = ++historySeq;
    w.innerHTML = '<div class="wi-sec-head"><b>最近选题</b>'
      + '<span class="wi-badge">自动记录</span></div>'
      + '<div class="wi-empty">正在读记录…</div>';
    return jsonApi('/writing-ideas/api/history').then(function (d) {
      if (seq !== historySeq) return;        // 后发的请求已经覆盖了这一屏
      state.history = d.items || [];
      renderHistory();
    }).catch(function (e) {
      if (seq !== historySeq) return;
      state.history = [];
      w.innerHTML = '<div class="wi-sec-head"><b>最近选题</b>'
        + '<span class="wi-badge">自动记录</span></div>'
        + '<div class="wi-empty">' + esc(e && e.message ? e.message : '记录加载失败') + '</div>';
    });
  }

  function openHistory(rec) {
    if (!rec || state.busy) return;
    state.busy = true;
    setNote('<span class="spin"></span> 正在打开历史记录…');
    jsonApi('/writing-ideas/api/history/' + encodeURIComponent(rec.id)).then(function (d) {
      var p = d.payload || {};
      var ideas = Array.isArray(p.ideas) ? p.ideas : [];
      if (!ideas.length) throw new Error('这条记录里没有可恢复的选题');
      var formKeys = FORMS.map(function (x) { return x.k; });
      var flavorKeys = FLAVORS.map(function (x) { return x.k; });
      state.ideas = ideas;
      state.source = p.source || '';
      state.forms = (p.forms || []).filter(function (k) { return formKeys.indexOf(k) >= 0; });
      state.flavors = (p.flavors || []).filter(function (k) { return flavorKeys.indexOf(k) >= 0; });
      state.n = p.n === 6 ? 6 : 8;
      state.pack = null;
      state.packIdea = null;
      state.packSource = '';
      $('wiSeed').value = p.seed || '';
      $('wiAudience').value = p.audience || '';
      $('wiTrigger').value = p.trigger || '';
      saveDraft();
      renderChips();
      renderNums();
      renderPack();
      var hintCard = $('wiHintCard');
      if (hintCard) hintCard.hidden = true;
      renderIdeas();
      setNote(p.note ? esc(p.note) : '已打开历史记录里的这一批选题。');
      var target = $('wiListWrap');
      if (target && target.scrollIntoView) {
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    }).catch(function (e) {
      setNote(esc(e && e.message ? e.message : '打开失败，再试一次'), true);
    }).then(function () {
      state.busy = false;
    });
  }

  function deleteHistory(rec) {
    if (!rec) return;
    if (!window.confirm('删除这条选题记录？已经复制或收进「我的选题」的内容不受影响。\n\n'
      + (rec.title || '一批选题'))) return;
    jsonApi('/writing-ideas/api/history/' + encodeURIComponent(rec.id), { method: 'DELETE' })
      .then(function () {
        toast('已删除');
        return loadHistory();
      })
      .catch(function (e) {
        toast(e && e.message ? e.message : '删除失败', 'err');
      });
  }

  /* ---------- 文本输出 ---------- */
  function ideaMd(it) {
    var L = ['## ' + (it.title || '')];
    if (it.claim) L.push('', '**核心观点**：' + it.claim);
    if (it.angle) L.push('', '**怎么切**：' + it.angle);
    if (it.form) L.push('', '**体裁**：' + it.form);
    if (it.hook) L.push('', '**开头第一句**：' + it.hook);
    if (it.outline && it.outline.length) {
      L.push('', '**提纲**');
      it.outline.forEach(function (x, i) { L.push((i + 1) + '. ' + x); });
    }
    if (it.material) L.push('', '**要补的素材**：' + it.material);
    return L.join('\n');
  }
  function packMd(p, it) {
    var L = ['# ' + ((it && it.title) || '写作启动包')];
    if (it && it.claim) L.push('', '> ' + it.claim);
    if (p.audience) L.push('', '**写给谁**：' + p.audience);
    if (p.titles && p.titles.length) {
      L.push('', '**备选标题**');
      p.titles.forEach(function (t, i) { L.push((i + 1) + '. ' + t); });
    }
    if (p.outline && p.outline.length) {
      L.push('', '**结构**');
      p.outline.forEach(function (s, i) {
        L.push('', (i + 1) + '. ' + s.h);
        (s.points || []).forEach(function (x) { L.push('   - ' + x); });
      });
    }
    if (p.evidence && p.evidence.length) {
      L.push('', '**要准备的证据**');
      p.evidence.forEach(function (x) { L.push('- ' + x); });
    }
    if (p.objections && p.objections.length) {
      L.push('', '**可能被反驳**');
      p.objections.forEach(function (x) { L.push('- ' + x); });
    }
    if (p.ending) L.push('', '**结尾怎么收**：' + p.ending);
    if (p.series && p.series.length) {
      L.push('', '**还能往下写**');
      p.series.forEach(function (x) { L.push('- ' + x); });
    }
    return L.join('\n');
  }
  function copyText(text, msg) {
    function fallback() {
      var ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed'; ta.style.top = '-1000px';
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand('copy'); toast(msg || '已复制'); }
      catch (e) { toast('复制失败，手动选中再复制', 'err'); }
      ta.remove();
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { toast(msg || '已复制'); }, fallback);
    } else { fallback(); }
  }
  function longformGenre(it) {
    return LONGFORM_GENRE[String((it && it.form) || '').trim()] || '';
  }

  // 同一页切到「写作」：不再写 sessionStorage、不再跳转 /article。
  function useIdea(it) {
    if (!it || !it.title) return;
    var genre = longformGenre(it);
    if (!genre) { toast('这个体裁建议先复制选题', 'err'); return; }
    var payload = {
      version: 1,
      title: String(it.title || '').slice(0, 200),
      claim: String(it.claim || '').slice(0, 200),
      angle: String(it.angle || '').slice(0, 140),
      hook: String(it.hook || '').slice(0, 200),
      material: String(it.material || '').slice(0, 200),
      form: String(it.form || '').slice(0, 20),
      genre: genre,
      outline: (it.outline || []).slice(0, 3).map(function (x) {
        return String(x || '').slice(0, 120);
      })
    };
    if (typeof window.TBArticleAdoptIdea !== 'function') {
      toast('写作区还没准备好，刷新页面再试', 'err');
      return;
    }
    window.TBArticleAdoptIdea(payload);
  }

  function download(name, text) {
    try {
      var blob = new Blob([text], { type: 'text/markdown;charset=utf-8' });
      var a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = name;
      document.body.appendChild(a);
      a.click();
      setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 600);
    } catch (e) { toast('这台浏览器不支持直接下载', 'err'); }
  }

  /* ---------- 绑定 ---------- */
  if (!$('wiGo')) return;
  $('wiGo').onclick = function () { ask(); };
  $('wiClear').onclick = function () {
    $('wiSeed').value = '';
    $('wiAudience').value = '';
    $('wiTrigger').value = '';
    state.forms = [];
    state.flavors = [];
    saveDraft(); renderChips();
    setNote('');
    toast('已清空');
  };
  ['wiSeed', 'wiAudience', 'wiTrigger'].forEach(function (id) {
    $(id).addEventListener('input', saveDraft);
  });
  $('wiSeed').addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) ask();
  });

  loadDraft();
  renderChips();
  renderNums();
  renderMine();
  loadHistory();
})();
