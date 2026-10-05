/* export-md.js —— 通用「导出 Markdown」小件
   -------------------------------------------------------------
   好几个小工具都要干同一件事：把屏幕上的东西拼成 Markdown、起一个干净的
   文件名、落盘或者复制。逻辑一模一样，所以收口成这一份，谁要谁引。

   引一行就能用：给页面加一个 script 标签，src 指向 /static/export-md.js

   拼：
     TBExport.doc({ title:'标题', meta:[['谁','什么']], body:[ ...块 ] })
     TBExport.chat({ title:'标题', meta:[...], turns:[{name,note,text}] })
     TBExport.h(2,'小节') / para / quote / bullets / numbered / kv / table / fence / hr

   出：
     TBExport.filename('苏格拉底提问 换工作')   -> '苏格拉底提问 换工作-2026-10-04.md'
     TBExport.download(md, name)                -> 触发下载，返回 true/false
     TBExport.copy(md)                          -> Promise<true/false>
     TBExport.attach(btn, build, { mode:'download', done:fn })
     TBExport.run(build, opts)                  -> build 可以是字符串、对象或 Promise

   几个刻意的选择：
   - 内部换行一律 \n；要 CRLF 就传 { crlf:true }
   - 下载默认带 UTF-8 BOM，Windows 记事本打开中文不乱码；不想要传 { bom:false }
   - 复制优先 navigator.clipboard，线上是 http（非安全来源）拿不到它，
     自动退回 execCommand('copy')，所以老浏览器也能用
   - 下载失败不抛异常，返回 false，让调用方自己提示
*/
(function(global){
'use strict';

function pad2(n){ return (n < 10 ? '0' : '') + n; }
function str(v){ return String(v == null ? '' : v); }

/* 掐掉行尾空格、把连续空行压成一个、去掉首尾空行 */
function tidy(s){
  var lines = str(s).replace(/\r\n?/g, '\n').split('\n').map(function(l){
    return l.replace(/[ \t]+$/, '');
  });
  return lines.join('\n').replace(/\n{3,}/g, '\n\n').replace(/^\n+/, '').replace(/\n+$/, '');
}

function day(d){
  d = d || new Date();
  return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate());
}
function now(d){
  d = d || new Date();
  return day(d) + ' ' + pad2(d.getHours()) + ':' + pad2(d.getMinutes());
}

/* ------------------------- 拼 Markdown ------------------------- */

function h(level, t){
  var n = Math.max(1, Math.min(6, Number(level) || 1));
  return new Array(n + 1).join('#') + ' ' + tidy(t);
}
function para(t){ return tidy(t); }
function hr(){ return '---'; }

function quote(t){
  return tidy(t).split('\n').map(function(l){ return l ? '> ' + l : '>'; }).join('\n');
}

function list_(list, mark){
  var out = [], n = 0;
  (list || []).forEach(function(x){
    var t = tidy(typeof x === 'string' ? x : (x && x.text));
    if(!t){ return; }
    n++;
    if(mark){
      out.push(mark + t.replace(/\n/g, '\n  '));
    } else {
      out.push(n + '. ' + t.replace(/\n/g, '\n   '));
    }
  });
  return out.join('\n');
}
function bullets(list){ return list_(list, '- '); }
function numbered(list){ return list_(list, ''); }

/* [['时间','昨天'], ...] -> '- **时间**：昨天' */
function kv(pairs){
  var out = [];
  (pairs || []).forEach(function(p){
    if(!p){ return; }
    var k = tidy(p[0]), v = tidy(p[1]);
    if(!k && !v){ return; }
    out.push(k ? ('- **' + k + '**：' + (v || '—')) : ('- ' + v));
  });
  return out.join('\n');
}

function fence(t, lang){
  var body = str(t).replace(/\r\n?/g, '\n').replace(/\n+$/, '');
  var tick = '```';
  while(body.indexOf(tick) >= 0){ tick += '`'; }
  return tick + str(lang) + '\n' + body + '\n' + tick;
}

function table(headers, rows){
  var head = (headers || []).map(function(x){ return tidy(x) || ' '; });
  if(!head.length){ return ''; }
  var lines = ['| ' + head.join(' | ') + ' |',
               '| ' + head.map(function(){ return '---'; }).join(' | ') + ' |'];
  (rows || []).forEach(function(r){
    var cells = head.map(function(_, i){
      // 竖线会把表格拆坏，转义掉
      return tidy((r || [])[i]).replace(/\n+/g, ' ').replace(/\|/g, '\\|') || ' ';
    });
    lines.push('| ' + cells.join(' | ') + ' |');
  });
  return lines.join('\n');
}
/* ------------------------- 组合 ------------------------- */

/* 一篇文档：一级标题 + 头部信息 + 正文块 */
function doc(spec){
  spec = spec || {};
  var blocks = [];
  if(spec.title){ blocks.push(h(1, spec.title)); }
  var head = kv(spec.meta);
  if(head){ blocks.push(head); }
  (spec.body || []).forEach(function(b){
    var t = tidy(b);
    if(t){ blocks.push(t); }
  });
  return tidy(blocks.join('\n\n')) + '\n';
}

/* 对话类最常见：一轮一块，用三级标题分开，在大纲里能直接跳 */
function chat(spec){
  spec = spec || {};
  var body = [], n = 0;
  (spec.turns || []).forEach(function(t){
    if(!t){ return; }
    var tx = tidy(t.text);
    if(!tx){ return; }
    var name = tidy(t.name) || '发言';
    var note = tidy(t.note);
    n++;  // 按真正写出来的轮次编号：中间空掉的轮次不留断层
    body.push(h(3, n + '. ' + name + (note ? ' · ' + note : '')));
    body.push(tx);
  });
  return doc({ title: spec.title, meta: spec.meta, body: body });
}

/* ------------------------- 出 ------------------------- */

var CTRL = /[\u0000-\u001f\u007f]/g;
var BAD  = /[\\\/:*?"<>|]/g;

function filename(name, opts){
  opts = opts || {};
  var base = str(name).replace(CTRL, ' ').replace(BAD, ' ').replace(/\s+/g, ' ');
  base = base.replace(/^[.\s]+/, '').trim();
  if(base.length > 60){ base = base.slice(0, 60).trim(); }
  if(!base){ base = 'export'; }
  var ext = str(opts.ext || 'md').replace(/^\.+/, '') || 'md';
  var stamp = opts.stamp === false ? '' : ('-' + day(opts.date));
  return base + stamp + '.' + ext;
}

function blob(md, opts){
  opts = opts || {};
  var body = str(md);
  if(opts.crlf){ body = body.replace(/\r?\n/g, '\r\n'); }
  var parts = opts.bom === false ? [body] : ['\ufeff', body];
  try{ return new Blob(parts, { type:'text/markdown;charset=utf-8' }); }
  catch(e){ /* 老浏览器不认这个 MIME，退一步 */ }
  try{ return new Blob(parts, { type:'text/plain;charset=utf-8' }); }
  catch(e){ return null; }
}

/* 返回 true/false，不抛异常 */
function download(md, name, opts){
  opts = opts || {};
  var text = str(md);
  if(!text.replace(/\s/g, '')){ return false; }
  var b = blob(text, opts);
  if(!b){ return false; }
  var url = '';
  try{
    url = URL.createObjectURL(b);
    var a = document.createElement('a');
    a.href = url;
    a.download = name || filename(opts.title, opts);
    a.rel = 'noopener';
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    setTimeout(function(){
      try{ URL.revokeObjectURL(url); }catch(e){}
      if(a.parentNode){ a.parentNode.removeChild(a); }
    }, 1200);
    return true;
  }catch(e){
    try{ if(url){ URL.revokeObjectURL(url); } }catch(e2){}
    return false;
  }
}

/* 复制：http 下 navigator.clipboard 拿不到，退回 execCommand */
function copy(md){
  var text = str(md);
  return new Promise(function(resolve){
    function fallback(){ resolve(oldCopy(text)); }
    try{
      if(global.navigator && navigator.clipboard && navigator.clipboard.writeText){
        navigator.clipboard.writeText(text).then(function(){ resolve(true); }, fallback);
        return;
      }
    }catch(e){ /* 落到下面 */ }
    fallback();
  });
}

function oldCopy(text){
  try{
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.cssText = 'position:fixed;top:0;left:-9999px;opacity:0';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    try{ ta.setSelectionRange(0, ta.value.length); }catch(e){}
    var ok = false;
    try{ ok = document.execCommand('copy'); }catch(e){ ok = false; }
    document.body.removeChild(ta);
    return !!ok;
  }catch(e){
    return false;
  }
}

/* ------------------------- 一键 ------------------------- */

/* build 可以是：字符串、{md, filename, title}、函数，或返回它们的 Promise */
function build_(build){ return (typeof build === 'function') ? build() : build; }

function tell(opts, ok, err){
  if(typeof opts.done !== 'function'){ return; }
  try{ opts.done(!!ok, err || null); }catch(e){ /* 回调里出的错不往上抛 */ }
}

function out(res, opts){
  var md = (typeof res === 'string') ? res : str(res && res.md);
  var title = opts.title || (res && res.title);
  var name = str(res && res.filename) || opts.filename || filename(title, opts);
  if(!str(md).replace(/\s/g, '')){
    tell(opts, false, new Error('没有可导出的内容'));
    return;
  }
  if(opts.mode === 'copy'){
    copy(md).then(function(ok){ tell(opts, ok, ok ? null : new Error('浏览器不让复制')); });
    return;
  }
  tell(opts, download(md, name, opts), null);
}

function run(build, opts){
  opts = opts || {};
  var r;
  try{ r = build_(build); }
  catch(e){ tell(opts, false, e); return; }
  if(r && typeof r.then === 'function'){
    r.then(function(res){ out(res, opts); }, function(e){ tell(opts, false, e); });
    return;
  }
  out(r, opts);
}

function attach(btn, build, opts){
  if(!btn){ return null; }
  opts = opts || {};
  var was = !!btn.disabled;
  btn.onclick = function(){
    btn.disabled = true;
    var r;
    try{ r = build_(build); }
    catch(e){ btn.disabled = was; tell(opts, false, e); return; }
    if(r && typeof r.then === 'function'){
      r.then(function(res){ btn.disabled = was; out(res, opts); },
             function(e){ btn.disabled = was; tell(opts, false, e); });
      return;
    }
    btn.disabled = was;
    out(r, opts);
  };
  return btn;
}

global.TBExport = {
  version : '1',
  day     : day,
  now     : now,
  tidy    : tidy,
  h       : h,
  para    : para,
  quote   : quote,
  hr      : hr,
  bullets : bullets,
  numbered: numbered,
  kv      : kv,
  fence   : fence,
  table   : table,
  doc     : doc,
  chat    : chat,
  filename: filename,
  blob    : blob,
  download: download,
  copy    : copy,
  run     : run,
  attach  : attach
};

})(typeof window !== 'undefined' ? window : this);