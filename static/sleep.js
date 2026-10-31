(function () {
  'use strict';

  var LABELS = {
    base: { white: '白噪音', pink: '粉红噪音', brown: '棕噪音' },
    ambience: {
      none: '无环境声', stream: '溪流', rain: '细雨', fan: '风扇', waves: '海浪',
      library: '我的音频'
    }
  };
  var state = {
    baseType: 'pink',
    baseLevel: 0,
    ambienceType: 'stream',
    ambienceLevel: 58,
    duration: 30,
    fade: 10,
    volume: 65,
    playing: false,
    paused: false,
    sessionEnd: 0,
    pauseStarted: 0,
    timer: 0,
    context: null,
    fadeGain: null,
    sources: [],
    totalPaused: 0,
    bubbleTimer: 0,
    bubbleBuffer: null,
    selectedAudioId: 0,
    audioItems: [],
    mediaElement: null
  };
  var presets = [];
  var controlsTimer = 0;
  var endHideTimer = 0;

  var $ = function (id) { return document.getElementById(id); };
  var statusEl = $('status');
  var startBtn = $('startBtn');
  var sleepScreen = $('sleepScreen');
  var sleepControls = $('sleepControls');
  var sleepClock = $('sleepClock');
  var sleepRemaining = $('sleepRemaining');
  var toggleAudioBtn = $('toggleAudio');
  var presetListEl = $('presetList');
  var presetNameEl = $('presetName');
  var savePresetBtn = $('savePreset');
  var audioListEl = $('audioList');
  var uploadAudioBtn = $('uploadAudioBtn');

  function setStatus(text, kind) {
    statusEl.textContent = text || '';
    statusEl.className = 'status' + (text ? ' show ' + (kind || 'info') : '');
  }

  function clamp(value, minimum, maximum) {
    return Math.max(minimum, Math.min(maximum, Number(value)));
  }

  function config() {
    return {
      base_type: state.baseType,
      base_level: state.baseLevel,
      ambience_type: state.ambienceType,
      ambience_level: state.ambienceLevel,
      duration: state.duration,
      fade: state.fade,
      volume: state.volume,
      audio_id: state.selectedAudioId || 0
    };
  }

  function describe(data) {
    var base = LABELS.base[data.base_type] || '底噪';
    var ambience = LABELS.ambience[data.ambience_type] || '无环境声';
    if (data.ambience_type === 'library') {
      var selected = state.audioItems.find(function (item) {
        return item.id === Number(data.audio_id);
      });
      ambience = selected ? selected.title : '我的音频';
    }
    var sound = data.ambience_type === 'none'
      ? base
      : base + ' + ' + ambience;
    return sound + ' · ' + data.duration + ' 分钟';
  }

  function setChoice(selector, chosen) {
    document.querySelectorAll(selector).forEach(function (button) {
      var isChosen = button.dataset.base === chosen ||
        button.dataset.ambience === chosen ||
        Number(button.dataset.duration) === chosen ||
        Number(button.dataset.fade) === chosen;
      if (button.dataset.duration !== undefined) isChosen = Number(button.dataset.duration) === chosen;
      if (button.dataset.fade !== undefined) isChosen = Number(button.dataset.fade) === chosen;
      button.classList.toggle('on', isChosen);
    });
  }

  function syncControls() {
    $('baseLevel').value = String(state.baseLevel);
    $('ambienceLevel').value = String(state.ambienceLevel);
    $('volumeLevel').value = String(state.volume);
    $('baseValue').textContent = state.baseLevel + '%';
    $('ambienceValue').textContent = state.ambienceLevel + '%';
    $('volumeValue').textContent = state.volume + '%';
    $('durationText').textContent = state.duration + ' 分钟';
    $('fadeText').textContent = state.fade
      ? '最后 ' + state.fade + ' 分钟'
      : '结束时直接关闭';
    setChoice('[data-base]', state.baseType);
    setChoice('[data-ambience]', state.ambienceType);
    setChoice('[data-duration]', state.duration);
    setChoice('[data-fade]', state.fade);
  }

  function applyPreset(item) {
    var data = item && item.payload ? item.payload : item;
    if (!data) return;
    if (LABELS.base[data.base_type]) state.baseType = data.base_type;
    if (LABELS.ambience[data.ambience_type]) state.ambienceType = data.ambience_type;
    state.selectedAudioId = Number(data.audio_id || 0);
    state.baseLevel = clamp(data.base_level == null ? 0 : data.base_level, 0, 100);
    state.ambienceLevel = clamp(data.ambience_level == null ? 0 : data.ambience_level, 0, 100);
    state.volume = clamp(data.volume == null ? 65 : data.volume, 0, 100);
    if ([20, 30, 45, 60].indexOf(Number(data.duration)) >= 0) state.duration = Number(data.duration);
    if ([0, 5, 10, 15].indexOf(Number(data.fade)) >= 0) state.fade = Number(data.fade);
    syncControls();
    renderAudioLibrary();
    setStatus('已恢复「' + (item.title || '声音预设') + '」。', 'info');
  }

  function renderPresets() {
    presetListEl.innerHTML = '';
    if (!presets.length) {
      var empty = document.createElement('div');
      empty.className = 'empty';
      empty.textContent = '还没有保存预设。调好声音和时长后，可以在上面保存。';
      presetListEl.appendChild(empty);
      return;
    }
    presets.forEach(function (item) {
      var card = document.createElement('button');
      card.type = 'button';
      card.className = 'preset';
      var title = document.createElement('b');
      title.textContent = item.title || '声音预设';
      var detail = document.createElement('span');
      detail.textContent = describe(item.payload || {});
      card.appendChild(title);
      card.appendChild(detail);
      card.addEventListener('click', function () { applyPreset(item); });

      var remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'preset-del';
      remove.textContent = '×';
      remove.title = '删除这个预设';
      remove.setAttribute('aria-label', '删除 ' + (item.title || '声音预设'));
      remove.addEventListener('click', function (event) {
        event.stopPropagation();
        deletePreset(item);
      });
      card.appendChild(remove);
      presetListEl.appendChild(card);
    });
  }

  async function loadPresets() {
    try {
      var response = await fetch('/sleep/api/presets');
      var data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.message || '读取失败');
      presets = data.items || [];
      renderPresets();
    } catch (error) {
      presets = [];
      renderPresets();
      setStatus('读取声音预设失败：' + (error.message || error), 'err');
    }
  }

  async function savePreset() {
    if (state.ambienceType === 'library' && !state.selectedAudioId) {
      setStatus('请先在“我的永久音频”里选择一个音频。', 'err');
      return;
    }
    savePresetBtn.disabled = true;
    var name = presetNameEl.value.trim() || describe(config());
    try {
      var response = await fetch('/sleep/api/presets', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(Object.assign({ name: name }, config()))
      });
      var data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.message || '保存失败');
      presetNameEl.value = '';
      await loadPresets();
      setStatus('声音预设已保存。', 'info');
    } catch (error) {
      setStatus('保存失败：' + (error.message || error), 'err');
    } finally {
      savePresetBtn.disabled = false;
    }
  }

  async function deletePreset(item) {
    if (!window.confirm('删除「' + (item.title || '声音预设') + '」？')) return;
    try {
      var response = await fetch('/sleep/api/presets/' + item.id, { method: 'DELETE' });
      var data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.message || '删除失败');
      presets = presets.filter(function (preset) { return preset.id !== item.id; });
      renderPresets();
      setStatus('声音预设已删除。', 'info');
    } catch (error) {
      setStatus('删除失败：' + (error.message || error), 'err');
    }
  }

  function formatBytes(size) {
    size = Number(size || 0);
    if (size >= 1024 * 1024) return (size / 1024 / 1024).toFixed(1) + ' MB';
    return Math.max(1, Math.round(size / 1024)) + ' KB';
  }

  function selectAudio(item) {
    state.selectedAudioId = item.id;
    state.ambienceType = 'library';
    syncControls();
    renderAudioLibrary();
    setStatus('已选择永久音频「' + item.title + '」。', 'info');
  }

  function renderAudioLibrary() {
    audioListEl.innerHTML = '';
    if (!state.audioItems.length) {
      var empty = document.createElement('div');
      empty.className = 'empty';
      empty.textContent = '还没有永久音频。上传一次后，手机和电脑都能使用。';
      audioListEl.appendChild(empty);
      return;
    }
    state.audioItems.forEach(function (item) {
      var card = document.createElement('div');
      card.className = 'audio-item' + (state.selectedAudioId === item.id ? ' on' : '');

      var choose = document.createElement('button');
      choose.type = 'button';
      choose.className = 'audio-select';
      var title = document.createElement('b');
      title.textContent = item.title;
      var meta = document.createElement('span');
      meta.textContent = formatBytes(item.size) + ' · ' + String(item.created_at || '').slice(0, 10);
      choose.appendChild(title);
      choose.appendChild(meta);
      choose.addEventListener('click', function () { selectAudio(item); });

      var remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'audio-del';
      remove.textContent = '×';
      remove.title = '删除这个永久音频';
      remove.setAttribute('aria-label', '删除 ' + item.title);
      remove.addEventListener('click', function () { deleteAudio(item); });
      card.appendChild(choose);
      card.appendChild(remove);
      audioListEl.appendChild(card);
    });
  }

  async function loadAudioLibrary() {
    try {
      var response = await fetch('/sleep/api/audio');
      var data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.message || '读取失败');
      state.audioItems = data.items || [];
      if (state.selectedAudioId &&
          !state.audioItems.some(function (item) { return item.id === state.selectedAudioId; })) {
        state.selectedAudioId = 0;
        if (state.ambienceType === 'library') state.ambienceType = 'stream';
      }
      renderAudioLibrary();
      syncControls();
    } catch (error) {
      state.audioItems = [];
      renderAudioLibrary();
      setStatus('读取永久音频失败：' + (error.message || error), 'err');
    }
  }

  async function uploadAudio(file) {
    if (!file) return;
    if (file.size > 100 * 1024 * 1024) {
      setStatus('音频文件请控制在 100MB 以内。', 'err');
      return;
    }
    uploadAudioBtn.disabled = true;
    uploadAudioBtn.textContent = '上传中…';
    try {
      var form = new FormData();
      form.append('file', file, file.name);
      form.append('title', file.name.replace(/\.[^.]+$/, ''));
      var response = await fetch('/sleep/api/audio', { method: 'POST', body: form });
      var data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.message || '上传失败');
      await loadAudioLibrary();
      state.selectedAudioId = data.item.id;
      state.ambienceType = 'library';
      syncControls();
      renderAudioLibrary();
      setStatus('音频已经永久保存，可以在手机和电脑上使用。', 'info');
    } catch (error) {
      setStatus('上传失败：' + (error.message || error), 'err');
    } finally {
      uploadAudioBtn.disabled = false;
      uploadAudioBtn.textContent = '上传音频';
    }
  }

  async function deleteAudio(item) {
    if (!window.confirm('删除永久音频「' + item.title + '」？')) return;
    try {
      var response = await fetch('/sleep/api/audio/' + item.id, { method: 'DELETE' });
      var data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.message || '删除失败');
      if (state.selectedAudioId === item.id) {
        state.selectedAudioId = 0;
        state.ambienceType = 'stream';
      }
      await loadAudioLibrary();
      setStatus('永久音频已删除。', 'info');
    } catch (error) {
      setStatus('删除失败：' + (error.message || error), 'err');
    }
  }

  function noiseBuffer(context, type, seconds) {
    var length = Math.max(1, Math.floor(context.sampleRate * seconds));
    var buffer = context.createBuffer(1, length, context.sampleRate);
    var data = buffer.getChannelData(0);
    var b0 = 0;
    var b1 = 0;
    var b2 = 0;
    var b3 = 0;
    var b4 = 0;
    var b5 = 0;
    var b6 = 0;
    var last = 0;
    for (var i = 0; i < length; i += 1) {
      var white = Math.random() * 2 - 1;
      if (type === 'white') {
        data[i] = white * 0.52;
      } else if (type === 'pink') {
        b0 = 0.99886 * b0 + white * 0.0555179;
        b1 = 0.99332 * b1 + white * 0.0750759;
        b2 = 0.96900 * b2 + white * 0.1538520;
        b3 = 0.86650 * b3 + white * 0.3104856;
        b4 = 0.55000 * b4 + white * 0.5329522;
        b5 = -0.7616 * b5 - white * 0.0168980;
        var pink = b0 + b1 + b2 + b3 + b4 + b5 + b6 + white * 0.5362;
        b6 = white * 0.115926;
        data[i] = pink * 0.11;
      } else {
        last = (last + 0.02 * white) / 1.02;
        data[i] = last * 3.5;
      }
    }
    return buffer;
  }

  function createSource(context, type) {
    var source = context.createBufferSource();
    source.buffer = noiseBuffer(context, type, 6);
    source.loop = true;
    state.sources.push(source);
    return source;
  }

  function makePanner(context, pan) {
    if (!context.createStereoPanner) return context.createGain();
    var panner = context.createStereoPanner();
    panner.pan.value = pan;
    return panner;
  }

  function makeStreamSpace(context) {
    var seconds = 1.8;
    var length = Math.floor(context.sampleRate * seconds);
    var impulse = context.createBuffer(2, length, context.sampleRate);
    for (var channel = 0; channel < 2; channel += 1) {
      var data = impulse.getChannelData(channel);
      for (var i = 0; i < length; i += 1) {
        data[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / length, 2.8);
      }
    }
    var convolver = context.createConvolver();
    convolver.buffer = impulse;
    return convolver;
  }

  function makeBubbleBuffer(context) {
    var length = Math.floor(context.sampleRate * 0.13);
    var buffer = context.createBuffer(1, length, context.sampleRate);
    var data = buffer.getChannelData(0);
    for (var i = 0; i < length; i += 1) {
      data[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / length, 3.8);
    }
    return buffer;
  }

  async function buildLibraryAudio(context, fadeGain) {
    var item = state.audioItems.find(function (audio) {
      return audio.id === state.selectedAudioId;
    });
    if (!item) throw new Error('请先上传并选择“我的音频”');
    var element = new Audio(item.url);
    element.loop = true;
    element.preload = 'auto';
    var source = context.createMediaElementSource(element);
    var gain = context.createGain();
    gain.gain.value = (state.ambienceLevel / 100) * 0.78;
    source.connect(gain);
    gain.connect(fadeGain);
    state.mediaElement = element;
    state.sources.push(source);
    try {
      await element.play();
    } catch (error) {
      element.pause();
      element.removeAttribute('src');
      element.load();
      state.mediaElement = null;
      throw new Error('永久音频无法播放，请换一个 MP3 或 M4A 文件');
    }
  }

  function scheduleBubbles(context, output) {
    function nextBubble() {
      if (!state.playing || state.ambienceType !== 'stream') return;
      var delay = 95 + Math.random() * 260;
      state.bubbleTimer = window.setTimeout(function () {
        if (!state.playing || state.ambienceType !== 'stream') return;
        var source = context.createBufferSource();
        var filter = context.createBiquadFilter();
        var gain = context.createGain();
        var panner = makePanner(context, Math.random() * 1.2 - 0.6);
        var now = context.currentTime;
        source.buffer = state.bubbleBuffer;
        filter.type = 'bandpass';
        filter.frequency.value = 1150 + Math.random() * 2400;
        filter.Q.value = 1.1 + Math.random() * 1.8;
        source.playbackRate.value = 0.75 + Math.random() * 0.8;
        gain.gain.setValueAtTime(0.0001, now);
        gain.gain.exponentialRampToValueAtTime(
          0.012 + Math.random() * 0.018,
          now + 0.008
        );
        gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.13);
        source.connect(filter);
        filter.connect(gain);
        gain.connect(panner);
        panner.connect(output);
        source.start(now);
        source.stop(now + 0.15);
        source.onended = function () {
          try { source.disconnect(); filter.disconnect(); gain.disconnect(); panner.disconnect(); } catch (error) {}
        };
        nextBubble();
      }, delay);
    }
    nextBubble();
  }

  function buildStream(context, fadeGain) {
    var streamLevel = (state.ambienceLevel / 100) * 0.72;
    var bus = context.createGain();
    bus.gain.value = streamLevel;
    bus.connect(fadeGain);

    var convolver = makeStreamSpace(context);
    var wet = context.createGain();
    wet.gain.value = 0.13;
    bus.connect(convolver);
    convolver.connect(wet);
    wet.connect(fadeGain);

    function layer(type, filterType, frequency, q, level, pan, lfoRate, lfoDepth) {
      var source = createSource(context, type);
      var filter = context.createBiquadFilter();
      var gain = context.createGain();
      var panner = makePanner(context, pan);
      filter.type = filterType;
      filter.frequency.value = frequency;
      filter.Q.value = q;
      gain.gain.value = level;
      var lfo = context.createOscillator();
      var depth = context.createGain();
      lfo.frequency.value = lfoRate;
      depth.gain.value = lfoDepth;
      lfo.connect(depth);
      depth.connect(gain.gain);
      source.connect(filter);
      filter.connect(gain);
      gain.connect(panner);
      panner.connect(bus);
      source.start();
      lfo.start();
      state.sources.push(source, lfo);
    }

    layer('pink', 'highpass', 240, 0.35, 0.38, -0.2, 0.085, 0.055);
    layer('pink', 'bandpass', 1750, 0.72, 0.17, 0.24, 0.13, 0.022);
    layer('brown', 'lowpass', 310, 0.3, 0.16, 0.04, 0.055, 0.018);
    layer('white', 'bandpass', 3150, 0.9, 0.028, 0.12, 0.19, 0.005);

    state.bubbleBuffer = makeBubbleBuffer(context);
    scheduleBubbles(context, bus);
  }

  async function buildAudio(context) {
    var fadeGain = context.createGain();
    var master = context.createGain();
    var compressor = context.createDynamicsCompressor();
    var volume = Math.pow(state.volume / 100, 1.45);
    fadeGain.gain.value = 1;
    master.gain.value = volume;
    compressor.threshold.value = -18;
    compressor.knee.value = 18;
    compressor.ratio.value = 4;
    fadeGain.connect(master);
    master.connect(compressor);
    compressor.connect(context.destination);
    state.fadeGain = fadeGain;

    if (state.baseLevel > 0) {
      var base = createSource(context, state.baseType);
      var baseGain = context.createGain();
      baseGain.gain.value = (state.baseLevel / 100) * 0.48;
      base.connect(baseGain);
      baseGain.connect(fadeGain);
      base.start();
    }

    if (state.ambienceType === 'none' || state.ambienceLevel <= 0) return;
    if (state.ambienceType === 'stream') {
      buildStream(context, fadeGain);
      return;
    }
    if (state.ambienceType === 'library') {
      await buildLibraryAudio(context, fadeGain);
      return;
    }
    var ambience = createSource(
      context,
      state.ambienceType === 'fan' || state.ambienceType === 'waves' ? 'brown' : 'white'
    );
    var ambienceGain = context.createGain();
    var ambienceLevel = (state.ambienceLevel / 100) * 0.38;
    ambienceGain.gain.value = ambienceLevel;
    var filter = context.createBiquadFilter();

    if (state.ambienceType === 'rain') {
      filter.type = 'bandpass';
      filter.frequency.value = 2600;
      filter.Q.value = 0.55;
      ambience.connect(filter);
      filter.connect(ambienceGain);
    } else if (state.ambienceType === 'fan') {
      filter.type = 'lowpass';
      filter.frequency.value = 920;
      filter.Q.value = 0.35;
      var fanMod = context.createGain();
      fanMod.gain.value = 0.82;
      var fanLfo = context.createOscillator();
      var fanDepth = context.createGain();
      fanLfo.frequency.value = 0.22;
      fanDepth.gain.value = 0.16;
      fanLfo.connect(fanDepth);
      fanDepth.connect(fanMod.gain);
      ambience.connect(filter);
      filter.connect(fanMod);
      fanMod.connect(ambienceGain);
      state.sources.push(fanLfo);
      fanLfo.start();
    } else {
      filter.type = 'lowpass';
      filter.frequency.value = 1500;
      filter.Q.value = 0.25;
      var waveMod = context.createGain();
      waveMod.gain.value = 0.72;
      var waveLfo = context.createOscillator();
      var waveDepth = context.createGain();
      waveLfo.frequency.value = 0.085;
      waveDepth.gain.value = 0.28;
      waveLfo.connect(waveDepth);
      waveDepth.connect(waveMod.gain);
      ambience.connect(filter);
      filter.connect(waveMod);
      waveMod.connect(ambienceGain);
      state.sources.push(waveLfo);
      waveLfo.start();
    }
    ambienceGain.connect(fadeGain);
    ambience.start();
  }

  function fadeFactor(remainingMs, fadeMinutes) {
    if (remainingMs <= 0) return 0;
    if (!fadeMinutes) return 1;
    var ratio = clamp(remainingMs / (fadeMinutes * 60000), 0, 1);
    return ratio * ratio * (3 - 2 * ratio);
  }

  function updateClock(remainingMs) {
    var seconds = Math.max(0, Math.ceil(remainingMs / 1000));
    var minutes = Math.floor(seconds / 60);
    var secs = String(seconds % 60).padStart(2, '0');
    sleepRemaining.textContent = seconds
      ? (minutes + ':' + secs + ' 后结束')
      : '今晚已打烊';
    var now = new Date();
    sleepClock.textContent = String(now.getHours()).padStart(2, '0') + ':' +
      String(now.getMinutes()).padStart(2, '0');
  }

  function tick() {
    if (!state.playing) return;
    var now = performance.now();
    var remaining = state.sessionEnd - now;
    updateClock(remaining);
    if (state.fadeGain && state.context) {
      var level = fadeFactor(remaining, state.fade);
      state.fadeGain.gain.setTargetAtTime(level, state.context.currentTime, 0.16);
    }
    if (remaining <= 0) finishSession();
  }

  function scheduleTick() {
    window.clearInterval(state.timer);
    state.timer = window.setInterval(tick, 250);
    tick();
  }

  async function startSession(secondsOverride) {
    if (state.playing) return;
    if (state.ambienceType === 'library' && !state.selectedAudioId) {
      setStatus('请先在“我的永久音频”里上传并选择一个音频。', 'err');
      return;
    }
    stopSources(0.05);
    var AudioCtor = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtor) {
      setStatus('当前浏览器不支持 Web Audio，无法生成白噪音。', 'err');
      return;
    }
    startBtn.disabled = true;
    try {
      var context = new AudioCtor();
      await context.resume();
      state.context = context;
      state.sources = [];
      state.totalPaused = 0;
      state.paused = false;
      state.playing = true;
      await buildAudio(context);
      var durationMs = secondsOverride
        ? Number(secondsOverride) * 1000
        : state.duration * 60000;
      var now = performance.now();
      state.sessionEnd = now + durationMs;
      sleepScreen.hidden = false;
      document.body.style.overflow = 'hidden';
      updateClock(durationMs);
      toggleAudioBtn.textContent = '暂停';
      showSleepControls();
      scheduleTick();
      updateMediaSession();
      try {
        if (document.documentElement.requestFullscreen) {
          await document.documentElement.requestFullscreen();
        }
      } catch (error) {
        // 某些手机浏览器不允许全屏，黑屏层本身仍然可用。
      }
    } catch (error) {
      state.playing = false;
      state.context = null;
      setStatus('启动声音失败：' + (error.message || error), 'err');
    } finally {
      startBtn.disabled = false;
    }
  }

  function stopSources(delay) {
    window.clearInterval(state.timer);
    state.timer = 0;
    window.clearTimeout(state.bubbleTimer);
    state.bubbleTimer = 0;
    state.bubbleBuffer = null;
    if (state.mediaElement) {
      try { state.mediaElement.pause(); } catch (error) {}
      try {
        state.mediaElement.removeAttribute('src');
        state.mediaElement.load();
      } catch (error) {}
      state.mediaElement = null;
    }
    state.sources.forEach(function (source) {
      try { source.stop((state.context ? state.context.currentTime : 0) + (delay || 0)); } catch (error) {}
    });
    state.sources = [];
    var context = state.context;
    state.context = null;
    state.fadeGain = null;
    if (context) {
      window.setTimeout(function () {
        context.close().catch(function () {});
      }, Math.max(50, (delay || 0) * 1000 + 20));
    }
  }

  function hideSleepScreen() {
    window.clearTimeout(endHideTimer);
    window.clearTimeout(controlsTimer);
    sleepScreen.hidden = true;
    sleepScreen.classList.remove('controls-visible');
    sleepControls.classList.remove('show');
    document.body.style.overflow = '';
    if (document.fullscreenElement && document.exitFullscreen) {
      document.exitFullscreen().catch(function () {});
    }
  }

  function stopSession(silent) {
    if (!state.playing && sleepScreen.hidden) return;
    state.playing = false;
    state.paused = false;
    if (state.fadeGain && state.context) {
      state.fadeGain.gain.cancelScheduledValues(state.context.currentTime);
      state.fadeGain.gain.setTargetAtTime(0.0001, state.context.currentTime, 0.08);
    }
    stopSources(0.25);
    hideSleepScreen();
    if (!silent) setStatus('声音已停止。', 'info');
    if ('mediaSession' in navigator) navigator.mediaSession.playbackState = 'none';
  }

  function finishSession() {
    if (!state.playing) return;
    state.playing = false;
    if (state.fadeGain && state.context) {
      state.fadeGain.gain.setTargetAtTime(0.0001, state.context.currentTime, 0.2);
    }
    stopSources(1.2);
    sleepRemaining.textContent = '今晚已打烊';
    if ('mediaSession' in navigator) navigator.mediaSession.playbackState = 'none';
    endHideTimer = window.setTimeout(hideSleepScreen, 4500);
  }

  async function togglePlayback() {
    if (!state.playing || !state.context) return;
    if (state.paused) {
      var pauseDuration = performance.now() - state.pauseStarted;
      state.totalPaused += pauseDuration;
      state.sessionEnd += pauseDuration;
      state.pauseStarted = 0;
      state.paused = false;
      await state.context.resume();
      toggleAudioBtn.textContent = '暂停';
      scheduleTick();
      if ('mediaSession' in navigator) navigator.mediaSession.playbackState = 'playing';
    } else {
      state.paused = true;
      state.pauseStarted = performance.now();
      await state.context.suspend();
      window.clearInterval(state.timer);
      state.timer = 0;
      toggleAudioBtn.textContent = '继续';
      if ('mediaSession' in navigator) navigator.mediaSession.playbackState = 'paused';
      showSleepControls();
    }
  }

  function showSleepControls() {
    window.clearTimeout(controlsTimer);
    sleepControls.classList.add('show');
    sleepScreen.classList.add('controls-visible');
    controlsTimer = window.setTimeout(function () {
      sleepControls.classList.remove('show');
      sleepScreen.classList.remove('controls-visible');
    }, state.paused ? 9000 : 4200);
  }

  function updateMediaSession() {
    if (!('mediaSession' in navigator)) return;
    if ('MediaMetadata' in window) {
      navigator.mediaSession.metadata = new MediaMetadata({
        title: '睡前打烊',
        artist: describe(config()),
        album: '我的工具箱'
      });
    }
    navigator.mediaSession.playbackState = 'playing';
    try {
      navigator.mediaSession.setActionHandler('pause', function () {
        if (!state.paused) togglePlayback();
      });
      navigator.mediaSession.setActionHandler('play', function () {
        if (state.paused) togglePlayback();
      });
      navigator.mediaSession.setActionHandler('stop', function () { stopSession(false); });
    } catch (error) {}
  }

  function bindChoices() {
    document.querySelectorAll('[data-base]').forEach(function (button) {
      button.addEventListener('click', function () {
        state.baseType = button.dataset.base;
        syncControls();
      });
    });
    document.querySelectorAll('[data-ambience]').forEach(function (button) {
      button.addEventListener('click', function () {
        state.ambienceType = button.dataset.ambience;
        syncControls();
      });
    });
    document.querySelector('[data-ambience="library"]').addEventListener('click', function () {
      if (!state.selectedAudioId) {
        setStatus('先在下面上传一个音频，上传后会永久保存。', 'info');
        document.querySelector('.audio-head').scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    });
    uploadAudioBtn.addEventListener('click', function () { $('audioFile').click(); });
    $('audioFile').addEventListener('change', function (event) {
      var file = event.target.files && event.target.files[0];
      uploadAudio(file);
      event.target.value = '';
    });
    document.querySelectorAll('[data-duration]').forEach(function (button) {
      button.addEventListener('click', function () {
        state.duration = Number(button.dataset.duration);
        syncControls();
      });
    });
    document.querySelectorAll('[data-fade]').forEach(function (button) {
      button.addEventListener('click', function () {
        state.fade = Number(button.dataset.fade);
        syncControls();
      });
    });
    $('baseLevel').addEventListener('input', function (event) {
      state.baseLevel = Number(event.target.value);
      syncControls();
    });
    $('ambienceLevel').addEventListener('input', function (event) {
      state.ambienceLevel = Number(event.target.value);
      syncControls();
    });
    $('volumeLevel').addEventListener('input', function (event) {
      state.volume = Number(event.target.value);
      syncControls();
    });
  }

  function bindSleepScreen() {
    sleepScreen.addEventListener('click', function (event) {
      if (event.target.closest('button')) return;
      if (sleepControls.classList.contains('show')) {
        sleepControls.classList.remove('show');
        sleepScreen.classList.remove('controls-visible');
        window.clearTimeout(controlsTimer);
      } else {
        showSleepControls();
      }
    });
    $('exitSleep').addEventListener('click', function () { stopSession(true); });
    $('stopAudio').addEventListener('click', function () { stopSession(false); });
    toggleAudioBtn.addEventListener('click', togglePlayback);
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden && state.playing && !state.paused &&
          state.context && state.context.state === 'suspended') {
        state.context.resume().catch(function () {});
      }
    });
  }

  async function init() {
    bindChoices();
    bindSleepScreen();
    startBtn.addEventListener('click', function () { startSession(); });
    savePresetBtn.addEventListener('click', savePreset);
    syncControls();
    await Promise.all([loadAudioLibrary(), loadPresets()]);
    window.TBSleep = {
      state: state,
      config: config,
      applyPreset: applyPreset,
      start: startSession,
      stop: stopSession,
      fadeFactor: fadeFactor,
      uploadAudio: uploadAudio,
      loadAudioLibrary: loadAudioLibrary
    };
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
