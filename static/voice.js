/* ============================================================
   voice.js —— 全站通用的「朗读」能力
   ------------------------------------------------------------
   用的是浏览器自带语音（window.speechSynthesis）：走操作系统本地音色，
   不联网、不花钱、不需要后端、不用配任何 Key。

   这不是四件套里的必备件，是按需引用的可选件。哪个工具想要朗读，多挂一行：
       给页面加一个 script 标签，src 指向 /static/voice.js

   挂上之后有一个全局对象 window.TBVoice：

     TBVoice.available()             这台设备 / 这个浏览器能不能读
     TBVoice.hasZhVoice()            有没有中文音色（没有的话读中文会很怪）
     TBVoice.speak(text, opts)       读一段；再读会自动顶掉上一段
     TBVoice.toggle(text, opts)      正在读同一段就停，否则开始读
     TBVoice.stop()                  立刻停
     TBVoice.isSpeaking()            现在在读吗
     TBVoice.currentText()           正在读的是哪段文字（用来把按钮标亮）
     TBVoice.attach(btn, getText)    把一个按钮变成「读 / 停」开关，状态自动同步
     TBVoice.on(fn)                  状态变了通知你，fn({speaking, text, hasZhVoice})
     TBVoice.autoOn() / setAuto(v)   「自动朗读」这个开关（存 localStorage）

   opts: { key: '任意标识' } 只是给调用方自己认的，内部不解释。

   两个坑，代码里都绕开了：
   1. 一长段文字整个丢给朗读引擎，Safari 和部分安卓会读一半就停
      —— 所以按标点切成小段排队读。
   2. Chrome 连续读十几秒会自己卡住 —— 所以每段都短（60 字以内），
      另挂 keep-alive：真卡住了把它 resume 回来。

   注意：这里只管「读」。语音输入（录音转文字）要 HTTPS + 云端识别，
   是另一件事，没做。
   ============================================================ */
(function (global) {
  'use strict';

  var LS_AUTO = 'tb_voice_auto';   // 唯一放 localStorage 的东西：一个开关偏好
  var MAX_LEN = 58;                // 一段最多多少字
  var MIN_LEN = 26;                // 攒够这么长、又刚好碰到句末，就断一段
  var CUTS = '，、,';              // 硬切时优先挑这些地方断

  var synth = global.speechSynthesis || null;
  var Utt = global.SpeechSynthesisUtterance || null;

  var listeners = [];
  var parts = [];
  var at = 0;
  var token = 0;                   // 每次新朗读 +1；回调里对不上号就丢掉
  var curText = '';
  var timer = 0;

  function available() { return !!(synth && Utt); }
  function isSpeaking() { return !!(synth && (synth.speaking || synth.pending)); }
  function currentText() { return curText; }

  function voices() {
    if (!synth || typeof synth.getVoices !== 'function') return [];
    try { return synth.getVoices() || []; } catch (e) { return []; }
  }

  function hasZhVoice() {
    var vs = voices();
    for (var i = 0; i < vs.length; i++) {
      var lang = String(vs[i].lang || '').toLowerCase().replace('_', '-');
      if (lang.indexOf('zh') === 0 || lang.indexOf('cmn') === 0) return true;
    }
    return false;
  }

  function pickVoice() {
    var vs = voices(), zh = [], i, lang;
    for (i = 0; i < vs.length; i++) {
      lang = String(vs[i].lang || '').toLowerCase().replace('_', '-');
      if (lang.indexOf('zh') === 0 || lang.indexOf('cmn') === 0) zh.push(vs[i]);
    }
    for (i = 0; i < zh.length; i++) {
      lang = String(zh[i].lang || '').toLowerCase().replace('_', '-');
      if (lang.indexOf('zh-cn') === 0) return zh[i];
    }
    return zh[0] || null;
  }

  /* 按标点把长文切成小段 */
  function chunk(text) {
    var flat = String(text == null ? '' : text).replace(/\r/g, '');
    var END = '\u3002\uff01\uff1f\uff1b!?;…';   // 。！？；!?;…
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
    return out.filter(function (s) { return !!s; });
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

  function stop(quiet) {
    if (!available()) return;
    token++;
    curText = '';
    parts = [];
    at = 0;
    if (timer) { clearInterval(timer); timer = 0; }
    try { synth.cancel(); } catch (e) { /* 有些浏览器 cancel 会抛 */ }
    if (!quiet) state({ done: true });
  }

  function speak(text, opts) {
    opts = opts || {};
    if (!available()) return false;
    var list = chunk(text);
    if (!list.length) return false;

    stop(true);
    var my = ++token;
    parts = list;
    at = 0;
    curText = String(text == null ? '' : text);
    var voice = pickVoice();

    function next() {
      if (my !== token) return;                 // 已经被新的一次朗读顶掉了
      if (at >= parts.length) { stop(false); return; }
      var u = new Utt(parts[at++]);
      if (voice) { u.voice = voice; u.lang = voice.lang || 'zh-CN'; }
      else { u.lang = 'zh-CN'; }                // 没有中文音色也先按中文请求，让引擎自己挑
      u.rate = 1;
      u.pitch = 1;
      u.onend = next;
      u.onerror = function () { if (my === token) stop(false); };
      try { synth.speak(u); } catch (e) { stop(false); }
    }

    state({ speaking: true, started: true });
    next();

    timer = setInterval(function () {
      if (my !== token || !synth.speaking) { clearInterval(timer); timer = 0; return; }
      if (synth.paused) { try { synth.resume(); } catch (e) {} }
    }, 4000);
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
    attach: attach,
    on: on,
    autoOn: autoOn,
    setAuto: setAuto
  };

  /* 音色列表在 Chrome 上是异步填充的，先把监听挂上，等它好了通知一声 */
  if (available()) {
    voices();
    var wake = function () { state({ voicesReady: true }); };
    if (typeof synth.addEventListener === 'function') synth.addEventListener('voiceschanged', wake);
    else synth.onvoiceschanged = wake;
  }
})(window);