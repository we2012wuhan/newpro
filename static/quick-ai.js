/* 全站 AI 助手
 * ------------------------------------------------------------
 * 右下角常驻入口 + 划词浮标，共用一个 Shadow DOM 面板：
 *   - 任意页面随时多轮对话，当前工具和页面标题会自动带上；
 *   - 划词后可以「解释 / 翻译 / 查一下 / 总结」，也可以直接追问；
 *   - 页面文字默认不读，用户打开「读取本页」才发送；
 *   - 对话存在 localStorage，换页、刷新后还能接着看。
 *
 * 页面想关掉：<body data-quick-ai="off"> 或 window.TB_NO_QUICK_AI = true
 * 页面想主动喂一段文字：window.TBQuickAI.open('...')
 * 页面想补充工具状态：window.TBAssistantContext = function () { return '...'; }
 *   （也可以赋一个带 get() 的对象）
 */
(function () {
  'use strict';
  if (window.TBQuickAI) { return; }

  var CSS = [
    ':host{all:initial}',
    '*{box-sizing:border-box}',
    '[hidden]{display:none!important}',
    'button,textarea{font:inherit}',
    'button:focus-visible,textarea:focus-visible{outline:2px solid rgba(109,124,255,.65);outline-offset:2px}',
    /* 常驻入口 */
    '#orb{position:fixed;pointer-events:auto;display:none;width:52px;height:52px;padding:0;',
    '  border-radius:50%;border:1px solid rgba(109,124,255,.55);',
    '  background:linear-gradient(135deg,#6d7cff 0%,#2dd4bf 100%);color:#fff;',
    '  font:600 21px/1 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;',
    '  cursor:pointer;align-items:center;justify-content:center;z-index:4;',
    '  box-shadow:0 12px 30px -10px rgba(60,80,200,.65);transition:transform .16s,box-shadow .16s}',
    '#orb.show{display:flex}',
    '#orb:hover{transform:translateY(-2px) scale(1.03);box-shadow:0 16px 34px -12px rgba(60,80,200,.78)}',
    '#orb:active{transform:scale(.97)}',
    '#pill{position:fixed;pointer-events:auto;display:none;align-items:center;gap:6px;',
    '  min-height:38px;padding:0 14px;border-radius:999px;border:1px solid rgba(109,124,255,.55);',
    '  background:rgba(23,26,43,.96);color:#eef1fa;',
    '  font:13px/1 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;',
    '  box-shadow:0 8px 24px -8px rgba(0,0,0,.6);cursor:pointer;user-select:none;white-space:nowrap;z-index:7}',
    '#pill:hover{border-color:#6d7cff}',
    '#pill.show{display:inline-flex}',
    /* 面板 */
    '#panel{position:fixed;pointer-events:auto;display:none;flex-direction:column;',
    '  right:16px;bottom:88px;width:min(420px,calc(100vw - 24px));height:min(660px,calc(100vh - 112px));',
    '  max-height:calc(100vh - 32px);overflow:hidden;border-radius:18px;',
    '  border:1px solid var(--tb-line,#282d45);background:var(--tb-menu-bg,#171a2b);',
    '  color:var(--tb-ink,#eef1fa);box-shadow:0 28px 70px -24px rgba(0,0,0,.72);',
    '  font:13px/1.6 -apple-system,"PingFang SC","Microsoft YaHei",system-ui,sans-serif;z-index:6}',
    '#panel.show{display:flex}',
    '#panel.show ~ #orb,#panel.show ~ #pill{opacity:0;pointer-events:none}',
    '.hd{display:flex;align-items:center;gap:6px;flex:0 0 auto;padding:10px 12px;',
    '  border-bottom:1px solid var(--tb-line,#282d45)}',
    '.brand{display:flex;align-items:center;gap:7px;flex:1 1 auto;min-width:0}',
    '.logo{flex:0 0 auto;width:24px;height:24px;border-radius:8px;display:flex;align-items:center;',
    '  justify-content:center;background:linear-gradient(135deg,#6d7cff,#2dd4bf);color:#fff;font-size:14px}',
    '.ttl{font-size:14px;font-weight:650;white-space:nowrap}',
    '.page{font-size:11px;color:var(--tb-muted,#8b93ab);white-space:nowrap;overflow:hidden;',
    '  text-overflow:ellipsis;max-width:150px;font-weight:400}',
    '.icon{flex:0 0 auto;min-height:30px;padding:0 9px;border-radius:9px;',
    '  border:1px solid transparent;background:transparent;color:var(--tb-muted,#8b93ab);',
    '  font-size:12px;cursor:pointer}',
    '.icon:hover{border-color:var(--tb-line,#282d45);color:var(--tb-ink,#eef1fa)}',
    /* 页面上下文 */
    '.ctx{display:flex;align-items:center;gap:8px;flex:0 0 auto;padding:8px 12px 4px}',
    '.ctxbtn{flex:0 0 auto;min-height:32px;padding:0 11px;border-radius:999px;',
    '  border:1px solid var(--tb-line,#282d45);background:transparent;color:var(--tb-text,#c6cbdd);',
    '  font-size:12px;cursor:pointer;white-space:nowrap}',
    '.ctxbtn:hover{border-color:var(--tb-acc1,#6d7cff)}',
    '.ctxbtn.on{border-color:rgba(45,212,191,.6);background:rgba(45,212,191,.16);',
    '  color:var(--tb-ink,#152033);font-weight:600}',
    '.ctxmeta{flex:1 1 auto;min-width:0;font-size:11px;color:var(--tb-muted,#8b93ab);',
    '  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    /* 场景 */
    '.scenes{display:flex;flex-wrap:wrap;align-items:center;gap:6px;flex:0 0 auto;padding:2px 12px 4px}',
    '.scene{min-height:28px;padding:0 10px;border-radius:999px;border:1px solid var(--tb-line,#282d45);',
    '  background:transparent;color:var(--tb-text,#c6cbdd);font-size:11.5px;cursor:pointer}',
    '.scene:hover{border-color:var(--tb-acc1,#6d7cff);color:var(--tb-ink,#eef1fa)}',
    '.scene.on{border-color:rgba(45,212,191,.6);background:rgba(45,212,191,.16);',
    '  color:var(--tb-ink,#152033);font-weight:600}',
    '.scene:disabled{opacity:.45;cursor:default}',
    '.range{min-height:28px;padding:0 8px;border-radius:999px;border:1px solid var(--tb-line,#282d45);',
    '  background:transparent;color:var(--tb-text,#c6cbdd);font-size:11.5px}',
    /* 选中的文字 */
    '.sel{display:flex;align-items:flex-start;gap:8px;margin:2px 12px 4px;padding:7px 9px;',
    '  border-left:3px solid #6d7cff;background:rgba(109,124,255,.09);border-radius:8px;font-size:12px;',
    '  color:var(--tb-text,#c6cbdd)}',
    '.seltxt{flex:1 1 auto;min-width:0;max-height:4.8em;overflow:auto;white-space:pre-wrap;',
    '  word-break:break-word;line-height:1.5}',
    '.selx{flex:0 0 auto;width:24px;height:24px;padding:0;border:0;border-radius:50%;',
    '  background:transparent;color:var(--tb-muted,#8b93ab);font-size:16px;cursor:pointer}',
    '.selx:hover{background:rgba(127,127,127,.16);color:var(--tb-ink,#eef1fa)}',
    '.selacts{display:flex;flex-wrap:wrap;gap:6px;margin:0 12px 6px}',
    '.selacts button{min-height:30px;padding:0 10px;border-radius:999px;',
    '  border:1px solid rgba(109,124,255,.4);background:rgba(109,124,255,.10);',
    '  color:var(--tb-ink,#eef1fa);font-size:12px;cursor:pointer}',
    '.selacts button:hover{border-color:#6d7cff}',
    '.selacts button:disabled{opacity:.45;cursor:default}',
    /* 对话 */
    '.thread{flex:1 1 auto;min-height:0;overflow-y:auto;overscroll-behavior:contain;',
    '  padding:8px 12px 4px;display:flex;flex-direction:column;gap:10px}',
    '.msg{display:flex;gap:8px;max-width:100%}',
    '.msg.user{justify-content:flex-end}',
    '.av{flex:0 0 auto;width:24px;height:24px;margin-top:1px;border-radius:8px;display:flex;',
    '  align-items:center;justify-content:center;background:linear-gradient(135deg,#6d7cff,#2dd4bf);',
    '  color:#fff;font-size:12px}',
    '.bub{max-width:86%;padding:9px 11px;border-radius:12px;white-space:pre-wrap;',
    '  word-break:break-word;line-height:1.65;font-size:13px;color:var(--tb-ink,#eef1fa)}',
    '.msg.ai .bub{background:rgba(127,127,127,.08);border:1px solid var(--tb-line,#282d45);',
    '  border-bottom-left-radius:4px}',
    '.msg.user .bub{background:rgba(109,124,255,.16);border:1px solid rgba(109,124,255,.28);',
    '  border-bottom-right-radius:4px}',
    '.msg.err .bub{color:#ff9a9a;border-color:rgba(255,120,120,.35);background:rgba(255,120,120,.08)}',
    '.dots{display:inline-flex;gap:4px;padding:3px 1px}',
    '.dots i{width:5px;height:5px;border-radius:50%;background:var(--tb-muted,#8b93ab);',
    '  animation:tbdot 1s infinite ease-in-out}',
    '.dots i:nth-child(2){animation-delay:.16s}',
    '.dots i:nth-child(3){animation-delay:.32s}',
    '@keyframes tbdot{0%,80%,100%{opacity:.28;transform:translateY(0)}40%{opacity:1;transform:translateY(-3px)}}',
    '.welcome{padding:16px 4px 8px;color:var(--tb-text,#c6cbdd)}',
    '.welcome h3{margin:0 0 6px;font-size:15px;color:var(--tb-ink,#eef1fa)}',
    '.welcome p{margin:0 0 10px;font-size:12px;line-height:1.7;color:var(--tb-muted,#8b93ab)}',
    '.chips{display:flex;flex-wrap:wrap;gap:6px}',
    '.chip{min-height:31px;padding:0 10px;border-radius:999px;border:1px solid var(--tb-line,#282d45);',
    '  background:transparent;color:var(--tb-text,#c6cbdd);font-size:12px;cursor:pointer;text-align:left}',
    '.chip:hover{border-color:var(--tb-acc1,#6d7cff);color:var(--tb-ink,#eef1fa)}',
    /* 输入 */
    '.compose{display:flex;align-items:flex-end;gap:8px;flex:0 0 auto;padding:8px 12px 4px;',
    '  border-top:1px solid var(--tb-line,#282d45)}',
    '.compose textarea{flex:1 1 auto;min-width:0;min-height:38px;max-height:120px;resize:none;',
    '  padding:9px 10px;border-radius:11px;border:1px solid var(--tb-line,#282d45);',
    '  background:var(--tb-field,rgba(255,255,255,.04));color:var(--tb-ink,#eef1fa);',
    '  font-size:13px;line-height:1.45;overflow-y:auto}',
    '.compose textarea::placeholder{color:var(--tb-muted,#8b93ab)}',
    '.send{flex:0 0 auto;min-height:38px;padding:0 14px;border-radius:11px;',
    '  border:1px solid rgba(45,212,191,.55);background:rgba(45,212,191,.18);',
    '  color:var(--tb-ink,#152033);font-weight:600;font-size:13px;cursor:pointer}',
    '.send:disabled{opacity:.45;cursor:default}',
    '.foot{display:flex;align-items:center;justify-content:space-between;gap:8px;',
    '  flex:0 0 auto;padding:2px 12px 10px}',
    '.status{flex:1 1 auto;min-width:0;font-size:11px;color:var(--tb-muted,#8b93ab);',
    '  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    /* 对话下方的操作行：记住 / 朗读 / 复制，统一靠左 */
    '.acts{display:flex;align-items:center;justify-content:flex-start;flex-wrap:wrap;gap:6px;',
    '  flex:0 0 auto;padding:2px 12px 4px}',
    '.acts button{min-height:30px;padding:0 10px;border-radius:999px;',
    '  border:1px solid var(--tb-line,#282d45);background:transparent;',
    '  color:var(--tb-text,#c6cbdd);font-size:12px;cursor:pointer}',
    '.acts button:hover{border-color:var(--tb-acc1,#6d7cff)}',
    '.acts button.on{border-color:rgba(45,212,191,.6);background:rgba(45,212,191,.16);',
    '  color:var(--tb-ink,#152033)}',
    '.acts button:disabled{opacity:.4;cursor:default}',
    /* 历史 */
    '.hist{position:absolute;left:0;right:0;top:52px;z-index:3;max-height:56%;overflow-y:auto;',
    '  padding:8px 10px 10px;background:var(--tb-menu-bg,#171a2b);',
    '  border-bottom:1px solid var(--tb-line,#282d45);box-shadow:0 14px 30px -18px rgba(0,0,0,.7)}',
    '.histhd{display:flex;align-items:center;justify-content:space-between;padding:2px 4px 7px;',
    '  font-size:11.5px;color:var(--tb-muted,#8b93ab)}',
    '.histhd button{min-height:26px;padding:0 8px;border-radius:8px;border:1px solid var(--tb-line,#282d45);',
    '  background:transparent;color:var(--tb-muted,#8b93ab);font-size:11px;cursor:pointer}',
    '.histrow{display:flex;align-items:center;gap:8px;padding:8px;border-radius:9px;cursor:pointer}',
    '.histrow:hover{background:rgba(127,127,127,.10)}',
    '.histrow.on{background:rgba(109,124,255,.12)}',
    '.histmain{flex:1 1 auto;min-width:0}',
    '.histtitle{display:block;font-size:12.5px;color:var(--tb-ink,#eef1fa);white-space:nowrap;',
    '  overflow:hidden;text-overflow:ellipsis}',
    '.histdate{display:block;font-size:10.5px;color:var(--tb-muted,#8b93ab);margin-top:1px}',
    '.histdel{flex:0 0 auto;width:26px;height:26px;padding:0;border:0;border-radius:50%;',
    '  background:transparent;color:var(--tb-muted,#8b93ab);font-size:15px;cursor:pointer}',
    '.histdel:hover{background:rgba(255,120,120,.14);color:#ff9a9a}',
    '.histempty{padding:14px 6px;font-size:12px;color:var(--tb-muted,#8b93ab)}',
    /* 长期记忆 */
    '.mem{max-height:74%}',
    '.memadd{display:flex;gap:6px;padding:0 4px 4px}',
    '.meminput{flex:1 1 auto;min-width:0;min-height:32px;padding:6px 9px;border-radius:9px;',
    '  border:1px solid var(--tb-line,#282d45);background:var(--tb-field,rgba(255,255,255,.04));',
    '  color:var(--tb-ink,#eef1fa);font-size:12px}',
    '.meminput::placeholder{color:var(--tb-muted,#8b93ab)}',
    '.memopt{display:flex;align-items:center;gap:8px;padding:0 4px 6px}',
    '.memopt .ctxbtn{min-height:28px;font-size:11px}',
    '.memhint{flex:1 1 auto;min-width:0;font-size:11px;color:var(--tb-muted,#8b93ab);',
    '  line-height:1.4}',
    '.memitem{display:flex;align-items:flex-start;gap:8px;padding:8px;border-radius:9px}',
    '.memitem:hover{background:rgba(127,127,127,.10)}',
    '.memmain{flex:1 1 auto;min-width:0}',
    '.memtext{display:block;font-size:12.5px;color:var(--tb-ink,#eef1fa);white-space:pre-wrap;',
    '  word-break:break-word}',
    '.memmeta{display:block;font-size:10.5px;color:var(--tb-muted,#8b93ab);margin-top:2px}',
    '.memtag{display:inline-block;padding:0 5px;border-radius:6px;margin-right:4px;',
    '  background:rgba(109,124,255,.14);color:#9aa6ff}',
    /* 手机 / 窄屏：底部抽屉 */
    '@media (max-width:700px){',
    '  #panel.show{left:0;right:0;bottom:0;width:100%;height:auto;max-height:82vh;',
    '    border-radius:18px 18px 0 0;border-left:0;border-right:0;border-bottom:0;',
    '    padding-bottom:env(safe-area-inset-bottom)}',
    '  #panel.show ~ #orb{display:none!important}',
    '  .page{display:none}',
    '  .thread{min-height:120px}',
    '  #pill{bottom:calc(84px + env(safe-area-inset-bottom));right:14px}',
    '}',
    '@media (prefers-reduced-motion:reduce){',
    '  #orb{transition:none}.dots i{animation:none}',
    '}',
  ].join('\n');

  var HTML = [
    '<div id="panel" role="dialog" aria-label="AI 助手">',
    '  <div class="hd">',
    '    <div class="brand"><span class="logo">✦</span><span class="ttl">AI 助手</span>',
    '      <span class="page" id="pageTag"></span></div>',
    '    <button class="icon" id="memBtn" type="button" aria-label="长期记忆">记忆</button>',
    '    <button class="icon" id="historyBtn" type="button" aria-label="历史对话">历史</button>',
    '    <button class="icon" id="newBtn" type="button" aria-label="新对话">＋</button>',
    '    <button class="icon" id="close" type="button" aria-label="关闭">✕</button>',
    '  </div>',
    '  <div class="hist" id="hist" hidden>',
    '    <div class="histhd"><span>最近的对话</span><button id="histClear" type="button">清空</button></div>',
    '    <div id="histList"></div>',
    '  </div>',
    '  <div class="hist mem" id="mem" hidden>',
    '    <div class="histhd"><span>长期记忆</span><button id="memClear" type="button">清空</button></div>',
    '    <div class="memadd">',
    '      <input class="meminput" id="memInput" type="text" maxlength="240"',
    '        placeholder="记一条… 例如：我偏好简短中文回答">',
    '      <button class="ctxbtn" id="memAdd" type="button">保存</button>',
    '    </div>',
    '    <div class="memopt">',
    '      <button class="ctxbtn on" id="memToggle" type="button" aria-pressed="true">自动记忆</button>',
    '      <span class="memhint" id="memHint">开启时按需带入相关记忆；关闭后不再带入，也不自动提取。</span>',
    '    </div>',
    '    <div id="memList"></div>',
    '  </div>',
    '  <div class="ctx">',
    '    <button class="ctxbtn" id="ctxBtn" type="button" aria-pressed="false">📄 读取本页</button>',
    '    <span class="ctxmeta" id="ctxMeta">默认不读取页面内容</span>',
    '  </div>',
    '  <div class="scenes" id="scenes" role="group" aria-label="对话场景">',
    '    <button class="scene on" type="button" data-scene="free">自由</button>',
    '    <button class="scene" type="button" data-scene="review">复盘</button>',
    '    <button class="scene" type="button" data-scene="training">训练</button>',
    '    <button class="scene" type="button" data-scene="plan">计划</button>',
    '    <select class="range" id="rangeDays" aria-label="记录时间范围" hidden>',
    '      <option value="7">近 7 天</option>',
    '      <option value="14">近 14 天</option>',
    '      <option value="30">近 30 天</option>',
    '    </select>',
    '  </div>',
    '  <div class="sel" id="sel" hidden><span class="seltxt" id="selTxt"></span>',
    '    <button class="selx" id="selX" type="button" aria-label="移除选中文字">×</button></div>',
    '  <div class="selacts" id="selActs" hidden>',
    '    <button type="button" data-sa="explain">解释</button>',
    '    <button type="button" data-sa="translate">翻译</button>',
    '    <button type="button" data-sa="fact">查一下</button>',
    '    <button type="button" data-sa="summary">总结</button>',
    '  </div>',
    '  <div class="thread" id="thread" aria-live="polite"></div>',
    '  <div class="acts">',
    '    <button id="remember" type="button" title="把最近一条回答加入长期记忆">记住</button>',
    '    <button id="speak" type="button" aria-pressed="false">朗读</button>',
    '    <button id="copy" type="button">复制</button>',
    '    <button id="stop" type="button" hidden>停止</button>',
    '  </div>',
    '  <div class="compose">',
    '    <textarea id="q" rows="1" maxlength="3000" placeholder="问点什么…  Enter 发送，Shift+Enter 换行"></textarea>',
    '    <button class="send" id="send" type="button">发送</button>',
    '  </div>',
    '  <div class="foot">',
    '    <span class="status" id="status"></span>',
    '  </div>',
    '</div>',
    '<button id="orb" type="button" aria-label="打开 AI 助手" title="AI 助手">✦</button>',
    '<button id="pill" type="button" aria-label="问 AI">✦ <span>问 AI</span></button>',
  ].join('');

  var host = null, root = null;
  var orb = null, pill = null, panel = null, threadEl = null, qEl = null;
  var selEl = null, selTxtEl = null, selActsEl = null, ctxBtn = null, ctxMeta = null;
  var statusEl = null, copyBtn = null, stopBtn = null, speakBtn = null, sendBtn = null;
  var rememberBtn = null, rememberedText = '';
  var histEl = null, histListEl = null, pageTagEl = null;
  var memBtn = null, memEl = null, memListEl = null, memInputEl = null;
  var memToggleBtn = null, memHintEl = null;
  var sceneBtns = [], rangeSel = null;
  var voicePromise = null, voiceBound = false;

  var LS_HIST = 'tb_ai_sessions_v1';
  var LS_CUR = 'tb_ai_current_v1';
  var LS_MEM = 'tb_ai_memory_on';
  var embedded = false;
  try { embedded = window.self !== window.top; } catch (e) { embedded = true; }
  try {
    if (new URLSearchParams(location.search).get('embed') === '1') { embedded = true; }
  } catch (e) { /* 老浏览器：按普通页面处理 */ }

  var S = {
    open: false,
    sessions: [],
    session: null,
    text: '',
    busy: false,
    ctrl: null,
    hideTimer: 0,
    contextOn: false,
    scene: 'free',
    rangeDays: 7,
    gen: 0,
    historyOpen: false,
    memoryOpen: false,
    memoryOn: true,
    memories: []
  };

  var SCENE_INFO = {
    free: { label: '自由' },
    review: {
      label: '复盘',
      ask: function (days) {
        return '请基于我最近 ' + days + ' 天的工具使用记录做一次复盘。按这个结构回答：'
          + '1. 最近主要在做什么；2. 最常用或最有效的操作；3. 反复卡住或低效的地方；'
          + '4. 还没收尾的事；5. 下周最值得做的 3 件事。'
          + '每条尽量引用具体工具、记录标题或数字；没有数据就直说，不要编造。';
      }
    },
    training: {
      label: '训练',
      ask: function (days) {
        return '请基于我最近 ' + days + ' 天的记录，找出一个最值得练的点。'
          + '先说明今天练什么、为什么选它，然后只出第一道题或第一个追问，等我回答后再点评。'
          + '不要一次给答案，也不要提前把后面几题说出来。';
      }
    },
    plan: {
      label: '计划',
      ask: function (days) {
        return '请基于我最近 ' + days + ' 天的记录、目标拆解和专注情况，帮我制定下周 3 个可执行动作。'
          + '每个动作写清：做什么、预计分钟、完成标准。动作要少而具体。';
      }
    }
  };
  var SCENE_KEYS = ['free', 'review', 'training', 'plan'];
  var RANGE_KEYS = [7, 14, 30];

  var SEL_ACTIONS = {
    explain: {
      label: '解释选中的文字',
      ask: '请解释我选中的这段文字。先给一句人话版结论，再补必要的背景；遇到术语就用一个日常例子说明。不要编造原文没有的信息。'
    },
    translate: {
      label: '翻译选中的文字',
      ask: '请翻译我选中的这段文字：中文译成自然英文，英文译成中文。只给译文；专有名词、代码、型号保持原样。'
    },
    fact: {
      label: '核实选中的说法',
      ask: '请帮我核实我选中的这段话。先给结论（对 / 错 / 无法确认），再给依据；不确定或需要实时信息时直接说无法确认，不要编造出处。'
    },
    summary: {
      label: '总结选中的文字',
      ask: '请把选中的文字压缩成 3~5 条要点，保留数字、结论和关键名词，不要加入原文没有的观点。'
    }
  };

  var PROMPTS = {
    '/image-gen': ['帮我润色当前提示词', '这个风格适合什么画面？', '给我 3 个构图思路'],
    '/image-studio': ['压缩和裁剪怎么选？', '九宫格拼图怎么排更好看？', '导出参数怎么设置？'],
    '/web-search': ['帮我把问题改成检索词', '怎么判断来源可不可信？', '帮我整理这组搜索结果'],
    '/translator': ['这句话怎么翻译更自然？', '帮我区分几个近义词', '这个命名格式怎么改？'],
    '/focus': ['帮我安排一个 25 分钟专注', '我总被打断怎么办？', '怎么复盘一次专注？'],
    '/goal-split': ['帮我把目标拆成一周动作', '帮我检查动作能不能执行', '里程碑怎么定？'],
    '/media': ['帮我按文件夹找歌', '怎么收藏和续播？', '视频打不开可能是什么原因？'],
    '/sdd-decomposer': ['帮我补验收标准', '这个需求还有哪些边界没写？', '帮我找风险点']
  };

  function uid() {
    return 's' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
  }

  function clampText(value, limit) {
    return String(value || '').slice(0, limit || 6000);
  }

  function normalizeScene(value) {
    return SCENE_KEYS.indexOf(value) >= 0 ? value : 'free';
  }

  function normalizeRange(value) {
    var n = +value;
    return RANGE_KEYS.indexOf(n) >= 0 ? n : 7;
  }

  function starterFor(scene, days) {
    var info = SCENE_INFO[scene];
    return (info && typeof info.ask === 'function') ? info.ask(days) : '';
  }

  function syncSceneFromSession() {
    S.scene = normalizeScene(S.session && S.session.scene);
    S.rangeDays = normalizeRange(S.session && S.session.rangeDays);
  }

  function isNarrow() {
    try { return window.matchMedia('(max-width:700px)').matches; } catch (e) { return false; }
  }

  function isCoarse() {
    try { return window.matchMedia('(pointer: coarse)').matches; } catch (e) { return false; }
  }

  function pageTitle() {
    var t = String(document.title || '').replace(/\s*·\s*我的工具箱\s*$/, '').trim();
    return t || location.pathname || '当前页面';
  }

  function pageMeta() {
    return {
      title: pageTitle().slice(0, 80),
      path: String(location.pathname || '/').slice(0, 200),
      tool: pageTitle().slice(0, 80)
    };
  }

  function updatePageTag() {
    if (!pageTagEl) { return; }
    var t = pageTitle();
    pageTagEl.textContent = t;
    pageTagEl.title = t + ' ' + location.pathname;
  }

  function readPageText() {
    var rootEl = document.querySelector('main, article, [role="main"], .wrap') || document.body;
    if (!rootEl) { return ''; }
    var text = '';
    try { text = rootEl.innerText || rootEl.textContent || ''; } catch (e) { text = ''; }
    text = String(text || '')
      .replace(/\r/g, '')
      .replace(/[ \t]+\n/g, '\n')
      .replace(/\n{3,}/g, '\n\n')
      .trim();
    return text.slice(0, 6000);
  }

  function readValue(sel) {
    var el = document.querySelector(sel);
    if (!el) { return ''; }
    var v = el.value;
    return v == null ? '' : String(v).trim();
  }

  function readStyleText(sel) {
    var el = document.querySelector(sel);
    return el ? String(el.textContent || '').replace(/\s+/g, ' ').trim() : '';
  }

  function readToolContext() {
    var out = [];
    var custom = window.TBAssistantContext;
    if (custom) {
      try {
        var extra = typeof custom === 'function' ? custom() :
          (typeof custom.get === 'function' ? custom.get() : '');
        if (extra && typeof extra === 'object') { extra = JSON.stringify(extra); }
        if (extra) { out.push(String(extra)); }
      } catch (e) { /* 页面适配器坏了不影响聊天 */ }
    }

    var path = location.pathname;
    if (path.indexOf('/image-gen') === 0) {
      var prompt = readValue('#prompt');
      if (prompt) { out.push('当前提示词：' + prompt); }
      var mode = readStyleText('#mode option:checked');
      if (mode) { out.push('当前模式：' + mode); }
      var style = readStyleText('#style option:checked') ||
        readStyleText('#styleChips .chip.on');
      if (style) { out.push('当前风格：' + style); }
      var pairs = [['#model', '模型'], ['#ratio', '比例'], ['#resolution', '分辨率'], ['#count', '数量']];
      pairs.forEach(function (p) {
        var v = readValue(p[0]);
        if (v) { out.push(p[1] + '：' + v); }
      });
      var neg = readValue('#negative');
      if (neg) { out.push('负向提示词：' + neg); }
    } else if (path.indexOf('/image-studio') === 0) {
      var tab = readStyleText('.tabs .tab.on');
      if (tab) { out.push('当前页签：' + tab); }
      var st = readStyleText('#status');
      if (st) { out.push('当前状态：' + st); }
    } else if (path.indexOf('/media') === 0) {
      var now = readStyleText('#nowTitle');
      if (now) { out.push('正在播放：' + now); }
      var listTitle = readStyleText('#listTitle');
      if (listTitle) { out.push('当前目录：' + listTitle); }
    } else if (path.indexOf('/focus') === 0 && window.TBFocusWidget &&
               typeof window.TBFocusWidget.state === 'function') {
      try {
        var fs = window.TBFocusWidget.state();
        var bits = [];
        if (fs.task) { bits.push('任务：' + fs.task); }
        if (fs.status) { bits.push('状态：' + fs.status); }
        if (fs.view) { bits.push('视图：' + fs.view); }
        if (bits.length) { out.push('专注台：' + bits.join('，')); }
      } catch (e) { /* 忽略 */ }
    }

    return out.join('\n').slice(0, 1200);
  }

  function toolPrompts() {
    var path = location.pathname;
    var keys = Object.keys(PROMPTS);
    for (var i = 0; i < keys.length; i++) {
      if (path === keys[i] || path.indexOf(keys[i] + '/') === 0) {
        return PROMPTS[keys[i]].slice(0, 4);
      }
    }
    return [
      '这个页面怎么用？',
      '帮我把一个问题问清楚',
      '给我一个三步行动建议',
      '帮我把这段文字改得更简洁'
    ];
  }

  /* ---------------- 本地会话 ---------------- */
  function newSession() {
    return {
      id: uid(), title: '', createdAt: Date.now(), updatedAt: Date.now(), messages: [],
      scene: 'free', rangeDays: 7
    };
  }

  function normalizeMessage(m) {
    if (!m || typeof m !== 'object') { return null; }
    var role = m.role === 'assistant' ? 'assistant' : (m.role === 'user' ? 'user' : '');
    var content = typeof m.content === 'string' ? m.content : '';
    if (!role || !content) { return null; }
    var out = { role: role, content: content, ts: +m.ts || Date.now() };
    if (typeof m.display === 'string' && m.display) { out.display = m.display; }
    return out;
  }

  function normalizeSession(s) {
    if (!s || typeof s !== 'object') { return null; }
    var msgs = Array.isArray(s.messages) ? s.messages.map(normalizeMessage).filter(Boolean) : [];
    if (!msgs.length) {
      // 空会话只保留占位，不进入历史
      return null;
    }
    return {
      id: String(s.id || uid()),
      title: clampText(s.title || '', 60),
      createdAt: +s.createdAt || Date.now(),
      updatedAt: +s.updatedAt || Date.now(),
      scene: normalizeScene(s.scene),
      rangeDays: normalizeRange(s.rangeDays),
      messages: msgs.slice(-30)
    };
  }

  function loadStore() {
    var list = [];
    try {
      var raw = JSON.parse(localStorage.getItem(LS_HIST) || '[]');
      if (Array.isArray(raw)) {
        raw.forEach(function (item) {
          var s = normalizeSession(item);
          if (s) { list.push(s); }
        });
      }
    } catch (e) { list = []; }
    list.sort(function (a, b) { return b.updatedAt - a.updatedAt; });
    S.sessions = list.slice(0, 15);
    var cur = '';
    try { cur = localStorage.getItem(LS_CUR) || ''; } catch (e) {}
    var found = null;
    for (var i = 0; i < S.sessions.length; i++) {
      if (S.sessions[i].id === cur) { found = S.sessions[i]; break; }
    }
    S.session = found || newSession();
    syncSceneFromSession();
  }

  function loadMemoryPref() {
    try {
      var v = localStorage.getItem(LS_MEM);
      if (v === '0') { S.memoryOn = false; }
      else if (v === '1') { S.memoryOn = true; }
    } catch (e) { /* 隐私模式：默认开启 */ }
  }

  function saveMemoryPref() {
    try { localStorage.setItem(LS_MEM, S.memoryOn ? '1' : '0'); } catch (e) {}
  }

  function titleFor(messages) {
    for (var i = 0; i < messages.length; i++) {
      if (messages[i].role === 'user') {
        var t = String(messages[i].display || messages[i].content || '')
          .replace(/\s+/g, ' ').trim();
        if (t) { return t.slice(0, 30); }
      }
    }
    return '新对话';
  }

  function persist() {
    if (!S.session) { return; }
    var s = S.session;
    var msgs = [];
    s.messages.forEach(function (m) {
      if (m && !m.error && typeof m.content === 'string' && m.content.trim()) {
        var item = { role: m.role, content: m.content, ts: m.ts || Date.now() };
        if (m.display) { item.display = m.display; }
        msgs.push(item);
      }
    });

    if (msgs.length) {
      if (!s.title) { s.title = titleFor(msgs); }
      s.updatedAt = Date.now();
      var record = {
        id: s.id,
        title: s.title,
        createdAt: s.createdAt,
        updatedAt: s.updatedAt,
        scene: normalizeScene(s.scene),
        rangeDays: normalizeRange(s.rangeDays),
        messages: msgs.slice(-30)
      };
      var hit = false;
      for (var i = 0; i < S.sessions.length; i++) {
        if (S.sessions[i].id === s.id) {
          S.sessions[i] = record;
          hit = true;
          break;
        }
      }
      if (!hit) { S.sessions.unshift(record); }
      S.sessions.sort(function (a, b) { return b.updatedAt - a.updatedAt; });
      S.sessions = S.sessions.slice(0, 15);
    } else {
      S.sessions = S.sessions.filter(function (x) { return x.id !== s.id; });
    }

    try {
      localStorage.setItem(LS_HIST, JSON.stringify(S.sessions));
      localStorage.setItem(LS_CUR, s.id);
    } catch (e) { /* 隐私模式 / 配额满：本次照常聊，只是不落盘 */ }
  }

  function loadSession(id) {
    var found = null;
    for (var i = 0; i < S.sessions.length; i++) {
      if (S.sessions[i].id === id) { found = S.sessions[i]; break; }
    }
    if (!found) { return; }
    persist();
    if (S.busy) {
      S.gen++;
      if (S.ctrl) { try { S.ctrl.abort(); } catch (e) {} }
      S.ctrl = null;
      S.busy = false;
    }
    S.session = found;
    syncSceneFromSession();
    S.text = '';
    S.contextOn = false;
    setSelectionUI();
    updateSceneUI();
    renderThread();
    closeHistory();
    setBusy(false);
    setStatus('');
    try { localStorage.setItem(LS_CUR, found.id); } catch (e) {}
    focusComposer();
  }

  function resetSession() {
    if (S.busy) {
      S.gen++;
      if (S.ctrl) { try { S.ctrl.abort(); } catch (e) {} }
      S.ctrl = null;
      S.busy = false;
    }
    S.session = newSession();
    syncSceneFromSession();
    S.text = '';
    S.contextOn = false;
    setSelectionUI();
    updateSceneUI();
    renderThread();
    closeHistory();
    setBusy(false);
    setStatus('');
    try { localStorage.setItem(LS_CUR, S.session.id); } catch (e) {}
    focusComposer();
  }

  function newChat() {
    persist();
    resetSession();
  }

  /* ---------------- 构建 ---------------- */
  function build() {
    host = document.createElement('div');
    host.id = 'tbqa-host';
    host.style.cssText = 'position:fixed;inset:0;z-index:2147483000;pointer-events:none';
    document.body.appendChild(host);
    root = host.attachShadow({ mode: 'open' });
    root.innerHTML = '<style>' + CSS + '</style>' + HTML;

    orb = root.getElementById('orb');
    pill = root.getElementById('pill');
    panel = root.getElementById('panel');
    threadEl = root.getElementById('thread');
    qEl = root.getElementById('q');
    selEl = root.getElementById('sel');
    selTxtEl = root.getElementById('selTxt');
    selActsEl = root.getElementById('selActs');
    ctxBtn = root.getElementById('ctxBtn');
    ctxMeta = root.getElementById('ctxMeta');
    statusEl = root.getElementById('status');
    copyBtn = root.getElementById('copy');
    stopBtn = root.getElementById('stop');
    speakBtn = root.getElementById('speak');
    rememberBtn = root.getElementById('remember');
    sendBtn = root.getElementById('send');
    histEl = root.getElementById('hist');
    histListEl = root.getElementById('histList');
    memBtn = root.getElementById('memBtn');
    memEl = root.getElementById('mem');
    memListEl = root.getElementById('memList');
    memInputEl = root.getElementById('memInput');
    memToggleBtn = root.getElementById('memToggle');
    memHintEl = root.getElementById('memHint');
    pageTagEl = root.getElementById('pageTag');
    sceneBtns = Array.prototype.slice.call(root.querySelectorAll('.scene'));
    rangeSel = root.getElementById('rangeDays');

    orb.classList.toggle('show', !embedded);
    setBusy(false);
  }

  function focusComposer() {
    if (!qEl) { return; }
    setTimeout(function () {
      try { qEl.focus(); } catch (e) {}
    }, 40);
  }

  /* ---------------- 位置：和专注旋钮并排 ---------------- */
  function focusAnchor() {
    var api = window.TBFocusWidget;
    if (api && typeof api.getAnchor === 'function') {
      try {
        var a = api.getAnchor();
        if (a && a.rect && a.rect.width) { return a; }
      } catch (e) {}
    }
    var fh = document.getElementById('tb-focus-widget');
    if (fh) {
      try {
        var r = fh.getBoundingClientRect();
        if (r.width > 0 && r.height > 0) { return { rect: r }; }
      } catch (e) {}
    }
    return null;
  }

  function layoutDock() {
    if (!orb || !panel) { return; }
    var narrow = isNarrow();
    var gap = narrow ? 12 : 18;
    var right = gap;
    var bottom = gap;
    var anchor = focusAnchor();
    if (anchor && anchor.rect) {
      var r = anchor.rect;
      right = Math.max(8, Math.round(window.innerWidth - r.left + (narrow ? 8 : 10)));
      bottom = Math.max(8, Math.round(window.innerHeight - r.bottom));
    } else {
      var bar = document.querySelector('.tabbar');
      if (bar) {
        var br = bar.getBoundingClientRect();
        if (br.height > 0 && br.top > window.innerHeight * 0.55) {
          bottom += Math.max(0, Math.round(window.innerHeight - br.top));
        }
      }
    }
    orb.style.right = right + 'px';
    orb.style.bottom = bottom + 'px';

    if (panel.classList.contains('show')) {
      if (narrow) {
        panel.style.right = '';
        panel.style.bottom = '';
        panel.style.maxHeight = '';
      } else {
        var pb = Math.round(bottom + 52 + 12);
        panel.style.right = '16px';
        panel.style.bottom = pb + 'px';
        panel.style.maxHeight = Math.max(260, window.innerHeight - pb - 16) + 'px';
      }
    }
  }

  /* ---------------- 选区浮标 ---------------- */
  function selectionInfo() {
    var ae = document.activeElement;
    if (ae && (ae.tagName === 'INPUT' || ae.tagName === 'TEXTAREA')) {
      var st = ae.selectionStart, en = ae.selectionEnd;
      if (typeof st === 'number' && typeof en === 'number' && en > st) {
        var picked = String(ae.value || '').slice(st, en).replace(/\u00a0/g, ' ').trim();
        if (picked.length >= 2) {
          return { text: picked.slice(0, 4000), rect: ae.getBoundingClientRect() };
        }
      }
    }
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

  function hidePill() {
    if (pill) { pill.classList.remove('show'); }
  }

  /* ---------------- 选区 / 上下文 UI ---------------- */
  function setSelection(text) {
    S.text = String(text || '').slice(0, 4000);
    setSelectionUI();
  }

  function setSelectionUI() {
    if (!selEl) { return; }
    var has = S.text.length >= 2;
    selEl.hidden = !has;
    selActsEl.hidden = !has;
    if (has) {
      selTxtEl.textContent = S.text.length > 360 ? S.text.slice(0, 360) + '…' : S.text;
      if (selTxtEl.scrollTop) { selTxtEl.scrollTop = 0; }
    } else {
      selTxtEl.textContent = '';
    }
  }

  function updateContextUI() {
    if (!ctxBtn) { return; }
    ctxBtn.classList.toggle('on', S.contextOn);
    ctxBtn.setAttribute('aria-pressed', S.contextOn ? 'true' : 'false');
    var sceneNote = S.scene === 'free' ? '' :
      ' · 当前场景会读取本账号最近 ' + S.rangeDays + ' 天记录';
    if (!S.contextOn) {
      ctxMeta.textContent = '默认不读取页面内容' + sceneNote;
      return;
    }
    var t = readPageText();
    ctxMeta.textContent = (t ? ('开启 · 约 ' + t.length + ' 字') : '开启 · 没读到文字') + sceneNote;
    ctxMeta.title = '只在你发送时读取页面文字；关闭后不再发送';
  }

  function toggleContext() {
    S.contextOn = !S.contextOn;
    updateContextUI();
  }

  function updateSceneUI() {
    sceneBtns.forEach(function (b) {
      b.classList.toggle('on', b.getAttribute('data-scene') === S.scene);
    });
    if (rangeSel) {
      rangeSel.hidden = S.scene === 'free';
      rangeSel.value = String(S.rangeDays);
      rangeSel.disabled = !!S.busy;
    }
    if (qEl) {
      qEl.placeholder = S.scene === 'free'
        ? '问点什么…  Enter 发送，Shift+Enter 换行'
        : '可以直接追问当前场景的结果…';
    }
    updateContextUI();
  }

  function changeRange(value) {
    S.rangeDays = normalizeRange(value);
    if (S.session) { S.session.rangeDays = S.rangeDays; }
    persist();
    updateSceneUI();
    if (S.scene !== 'free') {
      setStatus('范围已改为近 ' + S.rangeDays + ' 天，下轮生成生效');
    }
  }

  function selectScene(scene) {
    scene = normalizeScene(scene);
    if (S.busy) {
      setStatus('等这次回答结束再切换场景');
      return;
    }
    var days = S.rangeDays;
    if (scene === 'free') {
      S.scene = 'free';
      S.rangeDays = days;
      if (S.session) { S.session.scene = 'free'; S.session.rangeDays = days; }
      persist();
      updateSceneUI();
      setStatus('已切换到自由对话');
      focusComposer();
      return;
    }

    // 已经聊过的对话先存起来，复盘/训练/计划单独开一段。
    if (S.session && S.session.messages.length) { newChat(); }
    S.scene = scene;
    S.rangeDays = normalizeRange(days);
    if (S.session) {
      S.session.scene = S.scene;
      S.session.rangeDays = S.rangeDays;
    }
    updateSceneUI();
    var info = SCENE_INFO[scene] || SCENE_INFO.free;
    send(starterFor(scene, S.rangeDays), {
      scene: scene,
      rangeDays: S.rangeDays,
      display: info.label + ' · 最近 ' + S.rangeDays + ' 天'
    });
  }

  /* ---------------- 对话渲染 ---------------- */
  function removeWelcome() {
    var w = threadEl ? threadEl.querySelector('.welcome') : null;
    if (w && w.parentNode) { w.parentNode.removeChild(w); }
  }

  function scrollThread() {
    if (!threadEl) { return; }
    threadEl.scrollTop = threadEl.scrollHeight;
  }

  function renderWelcome() {
    if (!threadEl) { return; }
    threadEl.innerHTML = '';
    var wrap = document.createElement('div');
    wrap.className = 'welcome';
    var h = document.createElement('h3');
    h.textContent = '随时问我';
    var p = document.createElement('p');
    p.textContent = '可以直接问任何问题。想让我结合当前页面回答，就打开上面的「读取本页」；'
      + '也可以点「复盘 / 训练 / 计划」，我会读取最近 7 / 14 / 30 天的使用记录。';
    wrap.appendChild(h);
    wrap.appendChild(p);
    var chips = document.createElement('div');
    chips.className = 'chips';
    toolPrompts().forEach(function (text) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'chip';
      b.textContent = text;
      b.onclick = function () {
        if (S.busy) { return; }
        qEl.value = text;
        growInput();
        focusComposer();
      };
      chips.appendChild(b);
    });
    wrap.appendChild(chips);
    threadEl.appendChild(wrap);
  }

  function appendMessage(m, pending) {
    if (!threadEl) { return null; }
    removeWelcome();
    var row = document.createElement('div');
    row.className = 'msg ' + (m.role === 'user' ? 'user' : 'ai') + (m.error ? ' err' : '');
    if (m.role !== 'user') {
      var av = document.createElement('span');
      av.className = 'av';
      av.textContent = '✦';
      row.appendChild(av);
    }
    var bub = document.createElement('div');
    bub.className = 'bub';
    if (pending && !m.content) {
      bub.innerHTML = '<span class="dots"><i></i><i></i><i></i></span>';
    } else {
      bub.textContent = m.display || m.content || '';
    }
    row.appendChild(bub);
    threadEl.appendChild(row);
    scrollThread();
    return { row: row, bub: bub };
  }

  function renderThread() {
    if (!threadEl) { return; }
    threadEl.innerHTML = '';
    var msgs = S.session ? S.session.messages : [];
    if (!msgs.length) { renderWelcome(); syncActionButtons(); return; }
    msgs.forEach(function (m) { appendMessage(m, false); });
    scrollThread();
    syncActionButtons();
  }

  function apiHistory() {
    var arr = [];
    (S.session ? S.session.messages : []).forEach(function (m) {
      if (m && m.role && m.content && !m.error) {
        arr.push({ role: m.role, content: m.content });
      }
    });
    arr = arr.slice(-12);
    while (arr.length && arr[0].role !== 'user') { arr.shift(); }
    return arr;
  }

  function lastAnswer() {
    var msgs = S.session ? S.session.messages : [];
    for (var i = msgs.length - 1; i >= 0; i--) {
      if (msgs[i].role === 'assistant' && msgs[i].content && !msgs[i].error) {
        return msgs[i].content;
      }
    }
    return '';
  }

  function setStatus(text) {
    if (statusEl) { statusEl.textContent = text || ''; }
  }

  function syncRememberBtn() {
    if (!rememberBtn) { return; }
    var text = lastAnswer();
    var remembered = !!text && memoryText({ content: text }) === rememberedText;
    rememberBtn.classList.toggle('on', remembered);
    rememberBtn.textContent = remembered ? '已记住' : '记住';
    rememberBtn.disabled = !text || !!S.busy;
  }

  function syncActionButtons() {
    var has = !!lastAnswer();
    if (copyBtn) { copyBtn.disabled = !has || !!S.busy; }
    if (speakBtn) { speakBtn.disabled = !has || !!S.busy; }
    syncRememberBtn();
  }

  function resetRememberBtn() {
    rememberedText = '';
    syncRememberBtn();
  }

  function rememberAnswer() {
    var text = lastAnswer();
    if (!text) { return; }
    addMemory(text, 'conclusion', rememberBtn);
  }

  function setBusy(on) {
    S.busy = !!on;
    if (stopBtn) { stopBtn.hidden = !on; }
    if (sendBtn) { sendBtn.disabled = !!on; }
    syncActionButtons();
    if (selActsEl) {
      Array.prototype.forEach.call(selActsEl.querySelectorAll('button'), function (b) {
        b.disabled = !!on;
      });
    }
    sceneBtns.forEach(function (b) { b.disabled = !!on; });
    if (rangeSel) { rangeSel.disabled = !!on; }
  }

  function growInput() {
    if (!qEl) { return; }
    qEl.style.height = 'auto';
    qEl.style.height = Math.min(120, Math.max(38, qEl.scrollHeight)) + 'px';
  }

  function pageTextForSend() {
    if (!S.contextOn) { return ''; }
    var t = readPageText();
    var sceneNote = S.scene === 'free' ? '' :
      ' · 当前场景会读取本账号最近 ' + S.rangeDays + ' 天记录';
    ctxMeta.textContent = (t ? ('开启 · 约 ' + t.length + ' 字') : '开启 · 没读到文字') + sceneNote;
    return t;
  }

  /* ---------------- 发送 ---------------- */
  function send(rawText, opts) {
    if (!S.session || S.busy) { return; }
    opts = opts || {};
    var typed = String(rawText == null ? qEl.value : rawText).replace(/\u00a0/g, ' ').trim();
    if (typed.length > 3000) { typed = typed.slice(0, 3000); }
    var picked = String(opts.selection !== undefined ? opts.selection : S.text)
      .replace(/\u00a0/g, ' ').trim();
    if (!typed && !picked) {
      setStatus('先问点什么，或选中一段文字');
      focusComposer();
      return;
    }

    var content, display;
    if (picked) {
      var askText = typed || '请解释这段文字，先给一句人话版结论，再补充必要背景。';
      content = '【我选中的文字】\n' + picked + '\n\n【我的问题】\n' + askText;
      display = (opts.label || typed || '关于选中的文字') + '\n（附带选中文字 ' + picked.length + ' 字）';
    } else {
      content = typed;
      display = opts.display || typed;
    }

    var userMsg = { role: 'user', content: content, display: display, ts: Date.now() };
    S.session.messages.push(userMsg);
    if (!S.session.title) { S.session.title = titleFor(S.session.messages); }

    appendMessage(userMsg, false);
    qEl.value = '';
    growInput();
    setSelection('');
    hidePill();
    setStatus('正在想…');

    var aiMsg = { role: 'assistant', content: '', ts: Date.now() };
    S.session.messages.push(aiMsg);
    resetRememberBtn();
    var ui = appendMessage(aiMsg, true);
    var history = apiHistory();

    S.gen++;
    var gen = S.gen;
    S.busy = true;
    setBusy(true);
    S.ctrl = ('AbortController' in window) ? new AbortController() : null;

    var body = {
      messages: history,
      page: pageMeta(),
      page_text: pageTextForSend(),
      tool_context: S.contextOn ? readToolContext() : '',
      scene: normalizeScene(opts.scene || S.scene),
      range_days: normalizeRange(opts.rangeDays || S.rangeDays),
      memory_enabled: !!S.memoryOn
    };
    var init = {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    };
    if (S.ctrl) { init.signal = S.ctrl.signal; }

    fetch('/api/assistant/chat', init).then(function (resp) {
      if (S.gen !== gen) { return; }
      var ct = resp.headers.get('content-type') || '';
      if (ct.indexOf('application/json') >= 0) {
        return resp.json().then(function (d) {
          throw new Error((d && d.msg) || '请求被拒绝');
        });
      }
      if (!resp.ok || !resp.body) {
        throw new Error('请求失败（HTTP ' + resp.status + '）');
      }
      var reader = resp.body.getReader();
      var dec = new TextDecoder();
      var buf = '';
      function pump() {
        return reader.read().then(function (r) {
          if (S.gen !== gen) {
            try { reader.cancel(); } catch (e) {}
            return;
          }
          if (r.done) {
            if (buf.trim()) { handleSSE(buf, gen, aiMsg, ui); }
            return;
          }
          buf += dec.decode(r.value, { stream: true });
          var parts = buf.split('\n\n');
          buf = parts.pop();
          parts.forEach(function (part) { handleSSE(part, gen, aiMsg, ui); });
          return pump();
        });
      }
      return pump();
    }).catch(function (err) {
      if (S.gen !== gen || aiMsg.finalized) { return; }
      if (err && err.name === 'AbortError') {
        finishAborted(gen, aiMsg, ui);
        return;
      }
      failStream(gen, aiMsg, ui, (err && err.message) || '网络不通');
    });
  }

  function handleSSE(chunk, gen, aiMsg, ui) {
    if (S.gen !== gen || !aiMsg || aiMsg.finalized) { return; }
    String(chunk || '').split('\n').forEach(function (line) {
      if (aiMsg.finalized) { return; }
      if (line.indexOf('data:') !== 0) { return; }
      var obj;
      try { obj = JSON.parse(line.slice(5).trim()); } catch (e) { return; }
      if (S.gen !== gen || aiMsg.finalized) { return; }
      if (obj.t) {
        aiMsg.content += obj.t;
        if (ui && ui.bub) {
          ui.bub.textContent = aiMsg.content;
          ui.row.classList.remove('err');
        }
        scrollThread();
      }
      if (obj.err) {
        failStream(gen, aiMsg, ui, obj.err);
      }
      if (obj.done) {
        finishStream(gen, aiMsg, ui);
      }
    });
  }

  function finishStream(gen, aiMsg, ui) {
    if (S.gen !== gen || aiMsg.finalized) { return; }
    aiMsg.finalized = true;
    aiMsg.content = String(aiMsg.content || '').trim();
    if (!aiMsg.content) {
      aiMsg.error = true;
      aiMsg.content = '模型这次没说出话来，再试一次。';
    }
    if (ui) {
      ui.row.classList.toggle('err', !!aiMsg.error);
      ui.bub.textContent = aiMsg.content;
    }
    S.ctrl = null;
    setBusy(false);
    setStatus(aiMsg.error ? '回答失败' : '完成');
    persist();
    scrollThread();
  }

  function failStream(gen, aiMsg, ui, msg) {
    if (S.gen !== gen || aiMsg.finalized) { return; }
    aiMsg.finalized = true;
    if (S.ctrl) { try { S.ctrl.abort(); } catch (e) {} }
    S.ctrl = null;
    var partial = String(aiMsg.content || '').trim();
    if (partial) {
      aiMsg.content = partial;
      if (ui) { ui.bub.textContent = partial; ui.row.classList.remove('err'); }
      setStatus('连接中断，已保留生成内容');
      persist();
    } else {
      aiMsg.error = true;
      aiMsg.content = msg || '回答失败，稍后再试';
      if (ui) { ui.row.classList.add('err'); ui.bub.textContent = aiMsg.content; }
      setStatus('');
    }
    setBusy(false);
    scrollThread();
  }

  function finishAborted(gen, aiMsg, ui) {
    if (S.gen !== gen || aiMsg.finalized) { return; }
    aiMsg.finalized = true;
    S.ctrl = null;
    var partial = String(aiMsg.content || '').trim();
    if (partial) {
      aiMsg.content = partial;
      if (ui) { ui.bub.textContent = partial; }
      setStatus('已停止，保留已生成内容');
      persist();
    } else {
      var idx = S.session.messages.indexOf(aiMsg);
      if (idx >= 0) { S.session.messages.splice(idx, 1); }
      if (ui && ui.row && ui.row.parentNode) { ui.row.parentNode.removeChild(ui.row); }
      setStatus('已停止');
    }
    setBusy(false);
  }

  function stopStream() {
    if (S.ctrl) {
      try { S.ctrl.abort(); } catch (e) {}
    }
  }

  /* ---------------- 历史 ---------------- */
  function fmtTime(ts) {
    var d = new Date(+ts || Date.now());
    var now = new Date();
    var hm = ('0' + d.getHours()).slice(-2) + ':' + ('0' + d.getMinutes()).slice(-2);
    if (d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() &&
        d.getDate() === now.getDate()) {
      return '今天 ' + hm;
    }
    return (d.getMonth() + 1) + '-' + d.getDate() + ' ' + hm;
  }

  function renderHistory() {
    if (!histListEl) { return; }
    histListEl.innerHTML = '';
    if (!S.sessions.length) {
      var empty = document.createElement('div');
      empty.className = 'histempty';
      empty.textContent = '还没有保存的对话。发出一条消息后会自动记在这里。';
      histListEl.appendChild(empty);
      return;
    }
    S.sessions.forEach(function (s) {
      var row = document.createElement('div');
      row.className = 'histrow' + (S.session && S.session.id === s.id ? ' on' : '');
      row.tabIndex = 0;
      row.setAttribute('role', 'button');
      var main = document.createElement('div');
      main.className = 'histmain';
      var title = document.createElement('span');
      title.className = 'histtitle';
      title.textContent = s.title || '新对话';
      var date = document.createElement('span');
      date.className = 'histdate';
      date.textContent = fmtTime(s.updatedAt);
      main.appendChild(title);
      main.appendChild(date);
      row.appendChild(main);

      var del = document.createElement('button');
      del.type = 'button';
      del.className = 'histdel';
      del.setAttribute('aria-label', '删除这段对话');
      del.textContent = '×';
      del.onclick = function (e) {
        e.stopPropagation();
        S.sessions = S.sessions.filter(function (x) { return x.id !== s.id; });
        try { localStorage.setItem(LS_HIST, JSON.stringify(S.sessions)); } catch (err) {}
        if (S.session && S.session.id === s.id) {
          resetSession();
        } else {
          renderHistory();
        }
      };
      row.appendChild(del);

      row.onclick = function () { loadSession(s.id); };
      row.onkeydown = function (e) {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          loadSession(s.id);
        }
      };
      histListEl.appendChild(row);
    });
  }

  function closeHistory() {
    S.historyOpen = false;
    if (histEl) { histEl.hidden = true; }
  }

  function toggleHistory() {
    S.historyOpen = !S.historyOpen;
    if (histEl) { histEl.hidden = !S.historyOpen; }
    if (S.historyOpen) {
      closeMemory();
      renderHistory();
    }
  }

  /* ---------------- 长期记忆 ---------------- */
  function setMemoryUI() {
    if (memToggleBtn) {
      memToggleBtn.classList.toggle('on', !!S.memoryOn);
      memToggleBtn.setAttribute('aria-pressed', S.memoryOn ? 'true' : 'false');
      memToggleBtn.textContent = S.memoryOn ? '自动记忆 开' : '自动记忆 关';
    }
    if (memHintEl) {
      memHintEl.textContent = S.memoryOn
        ? '开启时按需带入相关记忆；关闭后不再带入，也不自动提取。'
        : '已关闭：不注入、不自动提取；手动「记住」和面板新增仍可用。';
    }
  }

  function toggleMemoryPref() {
    S.memoryOn = !S.memoryOn;
    saveMemoryPref();
    setMemoryUI();
    setStatus(S.memoryOn ? '已开启长期记忆' : '已关闭长期记忆（已有记忆仍可查看和删除）');
  }

  function fmtDay(stamp) {
    return String(stamp || '').slice(0, 10);
  }

  function memoryText(m) {
    var text = String((m && m.content) || '').replace(/\s+/g, ' ').trim();
    return text.length > 240 ? text.slice(0, 240) : text;
  }

  function renderMemories() {
    if (!memListEl) { return; }
    memListEl.innerHTML = '';
    if (!S.memories.length) {
      var empty = document.createElement('div');
      empty.className = 'histempty';
      empty.textContent = '还没有长期记忆。可以在对话里说「记住：…」，'
        + '或者点对话下方的「记住」，也可以在上面直接写一条。';
      memListEl.appendChild(empty);
      return;
    }
    S.memories.forEach(function (m) {
      var row = document.createElement('div');
      row.className = 'memitem';
      var main = document.createElement('div');
      main.className = 'memmain';
      var text = document.createElement('span');
      text.className = 'memtext';
      text.textContent = m.content || '';
      var meta = document.createElement('span');
      meta.className = 'memmeta';
      var tag = document.createElement('span');
      tag.className = 'memtag';
      tag.textContent = m.label || '记忆';
      meta.appendChild(tag);
      meta.appendChild(document.createTextNode(
        fmtDay(m.updated_at || m.created_at) + ' · ' + (m.source === 'manual' ? '手动' : '自动')));
      main.appendChild(text);
      main.appendChild(meta);
      row.appendChild(main);

      var del = document.createElement('button');
      del.type = 'button';
      del.className = 'histdel';
      del.setAttribute('aria-label', '删除这条记忆');
      del.textContent = '×';
      del.onclick = function () { deleteMemoryItem(m.id); };
      row.appendChild(del);
      memListEl.appendChild(row);
    });
  }

  function loadMemories() {
    return fetch('/api/assistant/memory', { cache: 'no-store' }).then(function (resp) {
      return resp.json().catch(function () { return {}; }).then(function (d) {
        if (!resp.ok || !d.ok) { throw new Error((d && d.msg) || ('HTTP ' + resp.status)); }
        return d;
      });
    }).then(function (d) {
      S.memories = Array.isArray(d.memories) ? d.memories : [];
      renderMemories();
    }).catch(function (err) {
      if (memListEl) {
        memListEl.innerHTML = '';
        var box = document.createElement('div');
        box.className = 'histempty';
        box.textContent = '读取记忆失败：' + ((err && err.message) || '网络不通');
        memListEl.appendChild(box);
      }
    });
  }

  function addMemory(content, kind, btn) {
    var text = memoryText({ content: content });
    if (text.length < 2) {
      setStatus('写一条想记住的内容');
      return;
    }
    if (btn) { btn.disabled = true; }
    fetch('/api/assistant/memory', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: text, kind: kind || 'fact' })
    }).then(function (resp) {
      return resp.json().catch(function () { return {}; }).then(function (d) {
        if (!resp.ok || !d.ok) { throw new Error((d && d.msg) || ('HTTP ' + resp.status)); }
        return d;
      });
    }).then(function (d) {
      setStatus(d.duplicate ? '这条已经记过了' : '已记住');
      if (btn === rememberBtn) {
        rememberedText = text;
        syncRememberBtn();
      } else if (btn) {
        btn.textContent = '已记住';
        btn.classList.add('on');
      }
      if (S.memoryOpen) { loadMemories(); }
    }).catch(function (err) {
      setStatus('保存失败：' + ((err && err.message) || '网络不通'));
      if (btn) { btn.disabled = false; }
    });
  }

  function deleteMemoryItem(id) {
    fetch('/api/assistant/memory/' + encodeURIComponent(id), { method: 'DELETE' })
      .then(function (resp) {
        return resp.json().catch(function () { return {}; }).then(function (d) {
          if (!resp.ok || !d.ok) { throw new Error((d && d.msg) || ('HTTP ' + resp.status)); }
          return d;
        });
      }).then(function () {
        S.memories = S.memories.filter(function (m) { return m.id !== id; });
        renderMemories();
        setStatus('已删除');
      }).catch(function (err) {
        setStatus('删除失败：' + ((err && err.message) || '网络不通'));
      });
  }

  function clearAllMemories() {
    if (!S.memories.length) { return; }
    if (!window.confirm('清空所有长期记忆？这个操作不可恢复。')) { return; }
    fetch('/api/assistant/memory', { method: 'DELETE' })
      .then(function (resp) {
        return resp.json().catch(function () { return {}; }).then(function (d) {
          if (!resp.ok || !d.ok) { throw new Error((d && d.msg) || ('HTTP ' + resp.status)); }
          return d;
        });
      }).then(function () {
        S.memories = [];
        renderMemories();
        setStatus('长期记忆已清空');
      }).catch(function (err) {
        setStatus('清空失败：' + ((err && err.message) || '网络不通'));
      });
  }

  function closeMemory() {
    S.memoryOpen = false;
    if (memEl) { memEl.hidden = true; }
  }

  function toggleMemory() {
    S.memoryOpen = !S.memoryOpen;
    if (memEl) { memEl.hidden = !S.memoryOpen; }
    if (S.memoryOpen) {
      closeHistory();
      setMemoryUI();
      renderMemories();
      loadMemories();
      setTimeout(function () {
        try { memInputEl.focus(); } catch (e) {}
      }, 40);
    }
  }

  /* ---------------- 面板 ---------------- */
  function openPanel(text) {
    if (!panel) { return; }
    if (typeof text === 'string') { setSelection(text); }
    else if (text === '') { setSelection(''); }
    S.open = true;
    panel.classList.add('show');
    hidePill();
    closeHistory();
    closeMemory();
    updatePageTag();
    if (!threadEl || !threadEl.children.length) { renderThread(); }
    layoutDock();
    try {
      document.dispatchEvent(new CustomEvent('tb-assistant-panel', { detail: { open: true } }));
    } catch (e) {}
    focusComposer();
  }

  function closePanel() {
    S.open = false;
    if (panel) { panel.classList.remove('show'); }
    closeHistory();
    closeMemory();
    stopVoice();
    try {
      document.dispatchEvent(new CustomEvent('tb-assistant-panel', { detail: { open: false } }));
    } catch (e) {}
  }

  /* ---------------- 朗读 / 复制 ---------------- */
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

  function syncSpeakBtn(on) {
    if (!speakBtn) { return; }
    speakBtn.classList.toggle('on', !!on);
    speakBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
    speakBtn.textContent = on ? '⏹ 停止' : '朗读';
  }

  function speakAnswer() {
    var text = lastAnswer();
    if (!text) { return; }
    ensureVoice().then(function (V) {
      if (!V || !V.available()) { setStatus('朗读组件没加载出来'); return; }
      if (!voiceBound) {
        voiceBound = true;
        V.on(function (st) {
          var on = !!(st && st.speaking && st.text === lastAnswer());
          syncSpeakBtn(on);
        });
      }
      V.toggle(text, { key: 'quick-ai' });
    });
  }

  function copyAnswer() {
    var text = lastAnswer();
    if (!text) { return; }
    function done(ok) {
      setStatus(ok ? '已复制' : '复制失败，手动选中吧');
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { done(true); }, fallback);
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

  /* ---------------- 事件 ---------------- */
  function inHost(e) {
    try { return (e.composedPath ? e.composedPath() : []).indexOf(host) >= 0; } catch (err) { return false; }
  }

  function bind() {
    orb.onclick = function () { openPanel(''); };
    pill.onclick = function (e) { e.stopPropagation(); openPanel(S.text); };
    root.getElementById('close').onclick = closePanel;
    root.getElementById('newBtn').onclick = newChat;
    root.getElementById('historyBtn').onclick = toggleHistory;
    root.getElementById('histClear').onclick = function () {
      if (!S.sessions.length) { return; }
      if (!window.confirm('清空所有对话记录？')) { return; }
      S.sessions = [];
      try { localStorage.removeItem(LS_HIST); } catch (e) {}
      resetSession();
    };
    memBtn.onclick = toggleMemory;
    memToggleBtn.onclick = toggleMemoryPref;
    root.getElementById('memClear').onclick = clearAllMemories;
    root.getElementById('memAdd').onclick = function () {
      if (!memInputEl) { return; }
      addMemory(memInputEl.value, 'fact', null);
      memInputEl.value = '';
    };
    if (memInputEl) {
      memInputEl.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' && !e.isComposing) {
          e.preventDefault();
          root.getElementById('memAdd').onclick();
        }
      });
    }
    ctxBtn.onclick = toggleContext;
    sceneBtns.forEach(function (b) {
      b.onclick = function () { selectScene(b.getAttribute('data-scene')); };
    });
    if (rangeSel) {
      rangeSel.onchange = function () { changeRange(rangeSel.value); };
    }
    root.getElementById('selX').onclick = function () { setSelection(''); };
    selActsEl.onclick = function (e) {
      var b = e.target && e.target.closest ? e.target.closest('[data-sa]') : null;
      if (!b) { return; }
      var a = SEL_ACTIONS[b.getAttribute('data-sa')];
      if (!a) { return; }
      send(a.ask, { selection: S.text, label: a.label });
    };
    sendBtn.onclick = function () { send(); };
    qEl.addEventListener('input', growInput);
    qEl.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        send();
      }
    });
    stopBtn.onclick = stopStream;
    copyBtn.onclick = copyAnswer;
    speakBtn.onclick = speakAnswer;
    if (rememberBtn) { rememberBtn.onclick = rememberAnswer; }
    document.addEventListener('tbvoice:error', function (e) {
      if (e && e.detail && e.detail.message && panel.classList.contains('show')) {
        setStatus(e.detail.message);
      }
    });

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
    document.addEventListener('select', function (e) {
      if (inHost(e)) { return; }
      clearTimeout(S.hideTimer);
      S.hideTimer = setTimeout(function () { showPill(selectionInfo()); }, 60);
    }, true);
    document.addEventListener('selectionchange', function () {
      var sel = window.getSelection();
      if (!sel || sel.isCollapsed) { hidePill(); }
    });
    window.addEventListener('scroll', function () { hidePill(); }, true);
    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Escape') { return; }
      if (S.memoryOpen) { closeMemory(); return; }
      if (S.historyOpen) { closeHistory(); return; }
      if (panel.classList.contains('show')) { closePanel(); }
    });
    document.addEventListener('tb-focus-dock', function () { layoutDock(); });
    document.addEventListener('tb-focus-panel', function (e) {
      if (e && e.detail && e.detail.open && S.open) { closePanel(); }
    });
    window.addEventListener('resize', layoutDock);
    window.addEventListener('orientationchange', layoutDock);
    window.addEventListener('pagehide', persist);
    document.addEventListener('visibilitychange', function () {
      if (document.hidden) { persist(); }
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
    loadStore();
    loadMemoryPref();
    bind();
    setMemoryUI();
    renderThread();
    updateSceneUI();
    updatePageTag();
    layoutDock();
    setTimeout(layoutDock, 600);
    setTimeout(layoutDock, 1600);
    window.TBQuickAI = {
      open: function (text) { openPanel(typeof text === 'string' ? text : ''); },
      close: closePanel,
      hide: hidePill,
      newChat: newChat,
      toggle: function () { if (S.open) { closePanel(); } else { openPanel(''); } }
    };
  });
})();
