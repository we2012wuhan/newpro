/* ============================================================
   asr.js —— 全站通用的「语音输入」
   ------------------------------------------------------------
   点一下麦克风开始录，再点一下结束；录到的声音转成文字，填进你正在打字的
   那个输入框里（不自动发送 —— 识别错一个字，改比删重打便宜）。

   声音在两处加工，都不花钱：
     1. 浏览器录的是 webm/opus（体积小），用 WebAudio 解回原始波形，
        重采样成 16k 单声道，再自己编成 wav —— 语音识别就吃这个格式；
     2. 编好的 wav POST 给本站 /api/asr，由服务端去调火山引擎，Key 不进前端。

   ★ 前置条件：必须是 https 页面（或 127.0.0.1）。http 页面浏览器直接不给麦克风
     （navigator.mediaDevices 是 undefined），这时按钮照样在，点了会告诉你原因。

   用法（哪个工具想要语音输入就挂这个公共件 + 一行 bind）：

       var mic = TBASR.bind(document.getElementById('micBtn'), {
         input: document.getElementById('say'),   // 文字填这儿，也可以不给
         onError: function (msg) { toast(msg); },  // 出错时的唯一出口
         maxSeconds: 60                            // 保险丝：录太久自动停
       });

   对外 API：

     TBASR.available()   这个环境能不能录（https + 支持 MediaRecorder）
     TBASR.reason()      不能录的原因（人话，可以直接弹给用户）
     TBASR.recording()   正在录吗
     TBASR.busy()        正在转文字吗
     TBASR.bind(btn, o)  把一个按钮变成录音开关，返回同一个小控制器
     TBASR.stop()        停掉并开始转文字（等同再点一下）
     TBASR.cancel()      丢弃这次录音，什么都不发

   按钮上的状态靠 class 表示，页面自己配样式即可：
     .rec  = 正在录（建议红色 + 呼吸动画）    .busy = 正在转文字
   ============================================================ */
(function (global) {
  'use strict';

  var POST_URL = '/api/asr';
  var TARGET_RATE = 16000;          // 语音识别要 16k 单声道
  var MAX_SECONDS = 60;             // 默认保险丝
  var MIN_BYTES = 800;              // 比这还短的录音，基本是没说话
  var MIMES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4'];

  var micBtn = null;
  var stream = null;
  var recorder = null;
  var chunks = [];
  var opts = {};
  var state = 'idle';               // idle | rec | busy
  var timer = 0;
  var startedAt = 0;
  var cancelled = false;            // 这次是被用户取消的，别弹「没录到声音」

  function $el(x) { return typeof x === 'function' ? x() : x; }

  function available() {
    var md = global.navigator && global.navigator.mediaDevices && global.navigator.mediaDevices.getUserMedia;
    var Ctx = global.AudioContext || global.webkitAudioContext;
    return !!(md && global.MediaRecorder && Ctx);
  }

  function reason() {
    if (!global.isSecureContext) {
      return '这个页面不是 https，浏览器不给用麦克风（换成 https 地址，或者本地用 127.0.0.1）';
    }
    if (!global.navigator || !global.navigator.mediaDevices) {
      return '这台设备 / 这个浏览器不支持录音';
    }
    if (!global.MediaRecorder) { return '这个浏览器不支持录音（MediaRecorder）'; }
    if (!(global.AudioContext || global.webkitAudioContext)) { return '这个浏览器不支持音频解码'; }
    return '';
  }

  function pickMime() {
    if (!global.MediaRecorder || !global.MediaRecorder.isTypeSupported) { return ''; }
    for (var i = 0; i < MIMES.length; i++) {
      try { if (global.MediaRecorder.isTypeSupported(MIMES[i])) { return MIMES[i]; } } catch (e) {}
    }
    return '';
  }

  function fire(msg) {
    if (typeof opts.onError === 'function') { opts.onError(msg); }
    else if (global.console && global.console.warn) { try { global.console.warn('[TBASR] ' + msg); } catch (e) {} }
  }

  function paint() {
    if (!micBtn) { return; }
    micBtn.classList.toggle('rec', state === 'rec');
    micBtn.classList.toggle('busy', state === 'busy');
    micBtn.setAttribute('aria-pressed', state === 'rec' ? 'true' : 'false');
    micBtn.title = state === 'rec' ? '正在录…点一下结束'
      : (state === 'busy' ? '正在转成文字…' : '语音输入（点一下开始，再点一下结束）');
    if (typeof opts.onState === 'function') {
      try { opts.onState(state); } catch (e) {}
    }
  }

  function setState(s) { state = s; paint(); }

  /* ---------- 录音 ---------- */
  function start() {
    var why = reason();
    if (why) { fire(why); return; }
    if (state !== 'idle') { return; }
    global.navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 }
    }).then(function (s) {
      stream = s;
      chunks = [];
      var mime = pickMime();
      try {
        recorder = mime ? new global.MediaRecorder(s, { mimeType: mime }) : new global.MediaRecorder(s);
      } catch (e) {
        recorder = new global.MediaRecorder(s);
      }
      recorder.ondataavailable = function (e) { if (e.data && e.data.size) { chunks.push(e.data); } };
      recorder.onstop = function () { finish(); };
      recorder.start();
      startedAt = Date.now();
      setState('rec');
      var limit = opts.maxSeconds || MAX_SECONDS;
      timer = global.setInterval(function () {
        if ((Date.now() - startedAt) / 1000 >= limit) { fire('录满 ' + limit + ' 秒了，自动停'); stop(); }
      }, 500);
    })['catch'](function (err) {
      var name = (err && err.name) || '';
      if (name === 'NotAllowedError' || name === 'SecurityError') {
        fire('麦克风权限被拒绝了：点浏览器地址栏左边那个图标，把麦克风改成「允许」再试');
      } else if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
        fire('没找到麦克风设备，插一个或者检查一下系统设置');
      } else if (name === 'NotReadableError') {
        fire('麦克风被别的程序占着，关掉（会议 / 录音软件）再试');
      } else {
        fire('打不开麦克风：' + (err && err.message ? err.message : name));
      }
      cleanup();
    });
  }

  function cleanup() {
    if (timer) { global.clearInterval(timer); timer = 0; }
    if (stream) {
      try { stream.getTracks().forEach(function (t) { t.stop(); }); } catch (e) {}
      stream = null;
    }
    recorder = null;
    setState('idle');
  }

  /* 停止录音并开始转文字 */
  function stop() {
    if (state !== 'rec' || !recorder) { return; }
    if (timer) { global.clearInterval(timer); timer = 0; }
    try { recorder.stop(); } catch (e) { cleanup(); }
  }

  /* 丢弃这次录音：不算错误，什么提示都不弹 */
  function cancel() {
    if (state !== 'rec') { return; }
    cancelled = true;
    try { recorder.stop(); } catch (e) { cleanup(); }
  }

  function finish() {
    if (cancelled) {                  // 用户取消了，静悄悄收场
      cancelled = false;
      chunks = [];
      cleanup();
      return;
    }
    var blob = new Blob(chunks, { type: (chunks[0] && chunks[0].type) || 'audio/webm' });
    chunks = [];
    if (stream) { try { stream.getTracks().forEach(function (t) { t.stop(); }); } catch (e) {} stream = null; }
    recorder = null;
    if (timer) { global.clearInterval(timer); timer = 0; }

    if (!blob.size || blob.size < MIN_BYTES) {
      setState('idle');
      fire('没录到声音，凑近一点再说一遍');
      return;
    }
    setState('busy');
    toWav(blob).then(function (wav) {
      return send(wav);
    }).then(function (info) {
      setState('idle');
      var text = (info && info.text ? String(info.text) : '').trim();
      if (!text) { fire('这段没听出字来，再说一遍试试'); return; }
      if (typeof opts.onText === 'function') { opts.onText(text, info); return; }
      insert(text);
    })['catch'](function (err) {
      setState('idle');
      fire((err && err.message) ? err.message : '语音输入失败了');
    });
  }

  /* ---------- webm/opus -> 16k 单声道 wav ---------- */
  function toWav(blob) {
    var Ctx = global.AudioContext || global.webkitAudioContext;
    return blob.arrayBuffer().then(function (buf) {
      var ctx = new Ctx();
      return new Promise(function (resolve, reject) {
        ctx.decodeAudioData(buf, function (decoded) { resolve(decoded); },
                            function () { reject(new Error('这段录音解不开，再说一遍试试')); });
      }).then(function (decoded) {
        var frames = Math.max(1, Math.ceil(decoded.duration * TARGET_RATE));
        var Off = global.OfflineAudioContext || global.webkitOfflineAudioContext;
        if (!Off) { return encodeWav(mixdown(decoded), decoded.sampleRate); }
        var off = new Off(1, frames, TARGET_RATE);
        var src = off.createBufferSource();
        src.buffer = decoded;
        src.connect(off.destination);
        src.start(0);
        return new Promise(function (resolve, reject) {
          off.oncomplete = function (e) { resolve(e.renderedBuffer); };
          try { off.startRendering(); } catch (err) { reject(err); }
        }).then(function (rendered) {
          return encodeWav(rendered.getChannelData(0), TARGET_RATE);
        });
      }).then(function (wav) {
        try { ctx.close(); } catch (e) {}
        return wav;
      });
    });
  }

  function mixdown(audio) {
    var n = audio.length;
    var out = new Float32Array(n);
    for (var c = 0; c < audio.numberOfChannels; c++) {
      var data = audio.getChannelData(c);
      for (var i = 0; i < n; i++) { out[i] += data[i] / audio.numberOfChannels; }
    }
    return out;
  }

  function encodeWav(samples, rate) {
    var bytes = samples.length * 2;
    var buf = new ArrayBuffer(44 + bytes);
    var v = new DataView(buf);
    function str(off, s) { for (var i = 0; i < s.length; i++) { v.setUint8(off + i, s.charCodeAt(i)); } }
    str(0, 'RIFF'); v.setUint32(4, 36 + bytes, true); str(8, 'WAVE');
    str(12, 'fmt '); v.setUint32(16, 16, true);
    v.setUint16(20, 1, true);                 // PCM
    v.setUint16(22, 1, true);                 // 单声道
    v.setUint32(24, rate, true);
    v.setUint32(28, rate * 2, true);          // 每秒字节数
    v.setUint16(32, 2, true);                 // 每个采样 2 字节
    v.setUint16(34, 16, true);                // 位深
    str(36, 'data'); v.setUint32(40, bytes, true);
    var o = 44;
    for (var i = 0; i < samples.length; i++) {
      var s = Math.max(-1, Math.min(1, samples[i]));
      v.setInt16(o, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
      o += 2;
    }
    return new Blob([v], { type: 'audio/wav' });
  }

  /* ---------- 发给服务端 ---------- */
  function send(wav) {
    return global.fetch(POST_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'audio/wav' },
      credentials: 'same-origin',
      body: wav
    }).then(function (r) {
      return r.json()['catch'](function () { return {}; }).then(function (d) {
        if (!r.ok) { throw new Error((d && d.message) || ('语音识别接口返回 ' + r.status)); }
        if (!d || d.ok === false) { throw new Error((d && d.message) || '语音识别失败了'); }
        return d;
      });
    }, function () {
      throw new Error('传不上去，检查一下网络');
    });
  }

  /* ---------- 把文字填进输入框 ---------- */
  function insert(text) {
    var el = $el(opts.input);
    if (!el) { return; }
    var cur = el.value || '';
    var sep = '';
    if (cur && !/[\s，。！？、,.!?；;：:]$/.test(cur)) { sep = '，'; }
    el.value = cur + sep + text;
    try { el.dispatchEvent(new global.Event('input', { bubbles: true })); } catch (e) {}
    try { el.focus(); } catch (e) {}
    try { el.selectionStart = el.selectionEnd = el.value.length; } catch (e) {}
  }

  /* ---------- 对外 ---------- */
  function bind(btn, o) {
    if (!btn) { return null; }
    micBtn = btn;
    opts = o || {};
    btn.onclick = function () {
      if (state === 'rec') { stop(); return; }
      if (state === 'idle') { start(); }
    };
    btn.onkeydown = function (e) {
      if (e.key === 'Escape' && state === 'rec') { e.preventDefault(); cancel(); }
    };
    paint();
    return { stop: stop, cancel: cancel, recording: recording, busy: busy };
  }

  function recording() { return state === 'rec'; }
  function busy() { return state === 'busy'; }

  global.TBASR = {
    available: available,
    reason: reason,
    recording: recording,
    busy: busy,
    bind: bind,
    stop: stop,
    cancel: cancel
  };
})(window);