/* ============================================================
   voice.js —— 全站通用的「朗读」能力
   ------------------------------------------------------------
   只有一种声音：本站服务端 /api/tts，由火山引擎（豆包）合成 mp3 传回来播。
   浏览器自带的 speechSynthesis 已经彻底不用了 —— 那个声音太出戏，
   宁可念不出来，也不要换来换去两种嗓子。

   哪个工具想要朗读，多挂一行就行（不是四件套必备件）：
       给页面加一个 script 标签，src 指向 /static/voice.js

   挂上之后有一个全局对象 window.TBVoice：

     TBVoice.available()             这个页面能不能读（云端由服务端保证，恒为 true）
     TBVoice.hasZhVoice()            读中文正不正常（豆包是中文音色，恒为 true）
     TBVoice.voices()                兼容老页面留着，现在恒返回空数组
     TBVoice.speak(text, opts)       读一段；再读会自动顶掉上一段
     TBVoice.toggle(text, opts)      正在读同一段就停，否则开始读
     TBVoice.stop()                  立刻停
     TBVoice.isSpeaking()            现在在读吗
     TBVoice.currentText()           正在读的是哪段文字（用来把按钮标亮）
     TBVoice.attach(btn, getText)    把一个按钮变成「读 / 停」开关，状态自动同步
     TBVoice.on(fn)                  状态变了通知你，fn({speaking, text, hasZhVoice})
     TBVoice.autoOn() / setAuto(v)   「自动朗读」这个开关（存 localStorage）
     TBVoice.lastError()             上一次没念出来的原因（没配 Key / 断网 / 被浏览器拦）

   opts: { key: '任意标识' } 只是给调用方自己认的，内部不解释。

   念不出来的时候不会假装没事：会往 document 上发一个 `tbvoice:error` 事件
   （detail.message 是一句人话），页面自己接去提示用户。用法：
       document.addEventListener('tbvoice:error', function(e){ toast(e.detail.message); });

   传进来的正文是 Markdown，直接念会把 ** 、# 、- 都念出来，所以先洗一遍
   （见 stripMd）：代码块整个跳过，链接只念文字，符号全部去掉。

   为什么按标点切成小段：云端一次只合成一句，首字出声快
   （整段几百字丢过去要等一分钟）。段与段之间会「预取下一段」——
   正在播第 1 段时，第 2 段已经在合成的路上了，所以听感上基本是连着的。
   中间任何一段失败，这一次朗读就整体停掉并报错，不会念一半换个嗓子接着念。

   注意：这里只管「读」。语音输入（录音转文字）要 HTTPS + 云端识别，
   是另一件事，没做。
   ============================================================ */
(function (global) {
  'use strict';

  var LS_AUTO = 'tb_voice_auto';   // 唯一放 localStorage 的东西：一个开关偏好
  var MAX_LEN = 58;                // 一段最多多少字
  var MIN_LEN = 26;                // 攒够这么长、又刚好碰到句末，就断一段
  var CUTS = '，、,';              // 硬切时优先挑这些地方断
  var END = '。！？；!?;…';        // 。！？；!?;…

  var TTS_URL = '/api/tts';        // 服务端合成接口（要登录，同源）
  var CACHE_MAX = 40;              // 浏览器这边的音频缓存条数（服务端还有一层）

  var listeners = [];
  var parts = [];
  var token = 0;                   // 每次新朗读 +1；回调里对不上号就丢掉
  var curText = '';
  var lastError = '';

  var audio = null;                // 正在播的 <audio>
  var playing = false;
  var audioCache = {};             // 文本 -> blob URL
  var cacheOrder = [];
  var pending = {};                // 文本 -> Promise（正在取音频，避免重复请求）

  function available() { return true; }
  function isSpeaking() { return !!playing; }
  function currentText() { return curText; }
  function voices() { return []; }
  function hasZhVoice() { return true; }
  function lastErrorText() { return lastError; }

  /* 把 Markdown 洗成「适合念」的纯文字：不是给人看的，是给嘴用的 */
  function stripMd(text) {
    var s = String(text == null ? '' : text);
    s = s.replace(/```[\s\S]*?```/g, ' ');              // 代码块：整块不念
    s = s.replace(/`([^`]*)`/g, '$1');                  // 行内代码：去掉反引号
    s = s.replace(/!\[[^\]]*\]\([^)]*\)/g, ' ');        // 图片：跳过
    s = s.replace(/\[([^\]]*)\]\([^)]*\)/g, '$1');      // 链接：只念文字
    s = s.replace(/^\s{0,3}#{1,6}\s*/gm, '');           // 标题的 #
    s = s.replace(/\*\*|__|~~|\*/g, '');                // 粗体 / 斜体 / 删除线
    s = s.replace(/^\s{0,3}>\s?/gm, '');                // 引用的 >
    s = s.replace(/^\s{0,3}[-*+]\s+/gm, '');            // 无序列表的点
    s = s.replace(/^\s{0,3}\d+[.)]\s+/gm, '');          // 有序列表的序号
    s = s.replace(/^\s{0,3}(-{3,}|\*{3,}|_{3,})\s*$/gm, '');  // 分割线
    /* 表情符号念不出来（会读成「笑脸」之类），直接去掉 */
    s = s.replace(/[\u2190-\u21FF\u2300-\u27BF\u2B00-\u2BFF\uFE0F\u200D]/g, ' ')
         .replace(/[\uD83C-\uDBFF][\uDC00-\uDFFF]/g, ' ');
    s = s.replace(/[\u200b\ufeff\u00a0]/g, ' ');
    s = s.replace(/[ \t]{2,}/g, ' ');
    return s.replace(/\n{3,}/g, '\n\n').trim();
  }

  /* 按标点把长文切成小段 */
  function chunk(text) {
    var flat = String(text == null ? '' : text).replace(/\r/g, '');
    var out = [], buf = '', i, k;
    for (i = 0; i < flat.length; i++) {
      var ch = flat[i];
      if (ch === '\n') {
        if (buf.trim()) out.push(buf.trim());
        buf = '';
        continue;
      }
      buf += ch;
      if (END.indexOf(ch) >= 0 && buf.trim().length >= MIN_LEN) {
        out.push(buf.trim());
        buf = '';
      } else if (buf.length >= MAX_LEN) {
        k = -1;
        for (var j = buf.length - 1; j >= 12; j--) {
          if (CUTS.indexOf(buf[j]) >= 0) { k = j + 1; break; }
        }
        if (k < 0) k = buf.length;
        out.push(buf.slice(0, k).trim());
        buf = buf.slice(k);
      }
    }
    if (buf.trim()) out.push(buf.trim());
    /* 只剩标点 / 空白的段直接丢掉，别浪费一次合成 */
    return out.filter(function (s) { return /[\u4e00-\u9fa5A-Za-z0-9]/.test(s); });
  }

  function fire(ev) {
    for (var i = 0; i < listeners.length; i++) {
      try { listeners[i](ev); } catch (e) { /* 一个监听器坏了不能拖累别人 */ }
    }
  }

  function state(extra) {
    var ev = { speaking: isSpeaking(), text: curText, hasZhVoice: hasZhVoice() };
    if (extra) { for (var k in extra) { if (extra.hasOwnProperty(k)) ev[k] = extra[k]; } }
    fire(ev);
  }

  /* 念不出来时唯一要说的话：往 document 上发个事件，别自己吞掉 */
  function report(msg) {
    lastError = String(msg || '朗读失败了');
    try {
      global.document.dispatchEvent(new CustomEvent('tbvoice:error', { detail: { message: lastError } }));
    } catch (e) { /* 很老的浏览器没有 CustomEvent，那就只剩 console 了 */ }
    if (global.console && global.console.warn) {
      try { global.console.warn('[TBVoice] ' + lastError); } catch (e) {}
    }
  }

  /* ---------------- 云端音频 ---------------- */
  function cacheGet(text) {
    var url = audioCache[text];
    if (!url) return null;
    var i = cacheOrder.indexOf(text);
    if (i >= 0) { cacheOrder.splice(i, 1); cacheOrder.push(text); }
    return url;
  }

  function cachePut(text, url) {
    audioCache[text] = url;
    cacheOrder.push(text);
    while (cacheOrder.length > CACHE_MAX) {
      var old = cacheOrder.shift();
      if (old === text) continue;
      var dead = audioCache[old];
      delete audioCache[old];
      if (dead) { try { URL.revokeObjectURL(dead); } catch (e) {} }
    }
  }

  /* 取一段音频。成功给 blob URL；失败把服务端那句人话抛出来 */
  function fetchAudio(text) {
    return global.fetch(TTS_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ text: text })
    }).then(function (r) {
      var ct = (r.headers && r.headers.get && r.headers.get('content-type')) || '';
      if (!r.ok || ct.indexOf('audio') < 0) {
        return r.text().then(function (raw) {
          var msg = '';
          try { msg = (JSON.parse(raw) || {}).message || ''; } catch (e) { msg = ''; }
          throw new Error(msg || ('朗读接口返回了 ' + r.status));
        }, function () {
          throw new Error('朗读接口返回了 ' + r.status);
        });
      }
      return r.blob();
    }).then(function (blob) {
      if (!blob || !blob.size) throw new Error('朗读接口没有返回声音');
      return URL.createObjectURL(blob);
    });
  }

  function cloudAudio(text) {
    var hit = cacheGet(text);
    if (hit) return Promise.resolve(hit);
    if (pending[text]) return pending[text];
    var p = fetchAudio(text).then(function (url) {
      cachePut(text, url);
      return url;
    });
    pending[text] = p;
    var clear = function () { delete pending[text]; };
    p.then(clear, clear);
    return p;
  }

  /* 播一段。成功 resolve，失败 reject（带一句人话） */
  function playCloud(text, my) {
    return cloudAudio(text).then(function (url) {
      if (my !== token) return false;
      return new Promise(function (resolve, reject) {
        var a = new Audio(url);
        var done = false;
        function finish(ok, err) {
          if (done) return;
          done = true;
          if (audio === a) { audio = null; playing = false; }
          if (ok) resolve(true);
          else reject(err || new Error('这段声音没能播出来'));
        }
        a.onended = function () { finish(true); };
        a.onerror = function () { finish(false, new Error('这段音频解不开')); };
        audio = a;
        playing = true;
        var pr = a.play();
        if (pr && pr['catch']) {
          /* 浏览器拦住自动播放时会走这里：再点一次通常就成了 */
          pr['catch'](function () { finish(false, new Error('浏览器拦住了播放，再点一下小喇叭试试')); });
        }
      });
    });
  }

  /* 一段接一段地放；正在放第 i 段时就把第 i+1 段的音频先取上。
     中间任何一段出事：整次朗读停掉 + 报错，绝不换嗓子接着念。 */
  function run(my) {
    var i = 0;
    function step() {
      if (my !== token) return;
      if (i >= parts.length) { stop(false); return; }
      var text = parts[i++];
      if (i < parts.length) {
        cloudAudio(parts[i])['catch'](function () { /* 预取失败不吭声，真播到它再说 */ });
      }
      playCloud(text, my).then(function () {
        if (my !== token) return;
        step();
      }, function (err) {
        if (my !== token) return;
        stop(true);
        state({ done: true, error: true });
        report(err && err.message ? err.message : '朗读失败了');
      });
    }
    step();
  }

  function stop(quiet) {
    token++;
    curText = '';
    parts = [];
    if (audio) {
      try { audio.pause(); } catch (e) { /* 有些浏览器 pause 会抛 */ }
      audio = null;
    }
    playing = false;
    if (!quiet) state({ done: true });
  }

  function speak(text, opts) {
    opts = opts || {};
    if (!available()) return false;
    var list = chunk(stripMd(text));
    if (!list.length) return false;

    stop(true);
    var my = ++token;
    parts = list;
    curText = String(text == null ? '' : text);
    state({ speaking: true, started: true });
    run(my);
    return true;
  }

  function toggle(text, opts) {
    if (isSpeaking() && curText === String(text == null ? '' : text)) stop();
    else speak(text, opts);
  }

  /* 把一个按钮变成「朗读 / 停」开关：点一下读，再点一下停，状态自动跟。
     getText 可以给字符串，也可以给函数（每次点的时候现取，适合内容会变的场景）。 */
  function attach(btn, getText, opts) {
    if (!btn || !available()) return null;
    function read() {
      var t = typeof getText === 'function' ? getText() : getText;
      return t == null ? '' : String(t);
    }
    btn.onclick = function () {
      var t = read();
      if (!t) return;
      if (isSpeaking() && curText === t) stop();
      else speak(t, opts);
    };
    on(function (st) {
      var on = !!(st && st.speaking && st.text === read());
      btn.classList.toggle('on', on);
      btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
    return btn;
  }

  function on(fn) {
    if (typeof fn !== 'function') return;
    listeners.push(fn);
    fn({ speaking: isSpeaking(), text: curText, hasZhVoice: hasZhVoice() });   // 先给一次当前状态
  }

  function autoOn() {
    try { return global.localStorage.getItem(LS_AUTO) === '1'; } catch (e) { return false; }
  }

  function setAuto(v) {
    try {
      if (v) global.localStorage.setItem(LS_AUTO, '1');
      else global.localStorage.removeItem(LS_AUTO);
    } catch (e) { /* 隐私模式下写不了，就当没记住 */ }
    state({ auto: !!v });
  }

  global.TBVoice = {
    available: available,
    hasZhVoice: hasZhVoice,
    voices: voices,
    speak: speak,
    toggle: toggle,
    stop: stop,
    isSpeaking: isSpeaking,
    currentText: currentText,
    lastError: lastErrorText,
    attach: attach,
    on: on,
    autoOn: autoOn,
    setAuto: setAuto
  };
})(window);