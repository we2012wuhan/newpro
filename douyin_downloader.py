# -*- coding: utf-8 -*-
"""
抖音无水印视频下载器（网页版）
==============================
本项目是 FastAPI 学习项目里的一个实用小工具：
粘贴一条抖音分享链接 -> 自动解析 -> 浏览器直接保存 mp4 视频。

两种启动方式：
    1. 独立启动：python douyin_downloader.py
       然后浏览器打开 http://127.0.0.1:8000/douyin-download
    2. 集成启动：uvicorn main:app --reload （main.py 会 include 本文件里的 router）
       然后浏览器打开 http://127.0.0.1:8000/douyin-download

解析通道（按顺序自动尝试）：
    1. 浏览器自动解析：无头打开抖音视频页，拦截页面自带签名的请求拿到直链
       （最稳定；成功后自动刷新并保存 douyin_cookies.txt 里的游客 Cookie）
    2. 游客 Cookie 详情接口直连（不依赖 yt-dlp，偶尔被抽样风控时自动跳过）
    3. yt-dlp 直接解析 / 携带游客 Cookie 解析
    4. 无登录直连通道（share 页取 video_id -> aweme.snssdk.com，随时可能失效，仅兜底）

由于抖音官方没有开放下载接口，反爬策略也在不断变化，
如果某天所有通道都失败，请先升级 yt-dlp（pip install -U yt-dlp），
或在页面"帮助"里按步骤更新一份最新 Cookie。
"""

import os
import re
import secrets
import sys
import time
from http.cookiejar import Cookie, CookieJar
from pathlib import Path
from urllib.parse import quote, urlparse

import requests
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

try:
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadError
except Exception:  # 依赖未安装时给出友好提示（见 parse 接口）
    YoutubeDL = None
    DownloadError = None

try:
    from playwright.sync_api import sync_playwright
except Exception:
    sync_playwright = None  # 未安装 playwright 时自动跳过"浏览器自动解析"通道


# =========================================================
# 常量
# =========================================================
UA_DESKTOP = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
UA_IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

COMMON_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

DOUYIN_HOSTS = {
    "douyin.com",
    "v.douyin.com",
    "www.douyin.com",
    "m.douyin.com",
    "iesdouyin.com",
    "www.iesdouyin.com",
}

if getattr(sys, 'frozen', False):
    _APP_DIR = Path(sys.executable).resolve().parent
else:
    _APP_DIR = Path(__file__).resolve().parent
COOKIE_FILE = _APP_DIR / 'douyin_cookies.txt'

_JOBS = {}  # 解析成功后的"下载任务"暂存区：token -> 直链信息
_JOB_TTL = 2 * 60 * 60  # 直链一般 1-2 小时有效，2 小时后自动清理

_PAGE_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>抖音视频下载器</title>
<style>
  * { box-sizing: border-box; }
  body { margin:0; background:#f5f6fa; color:#222; font-family:"Microsoft YaHei",system-ui,sans-serif; }
  .wrap { max-width: 780px; margin: 0 auto; padding: 24px 16px 60px; }
  .card { background:#fff; border-radius:14px; box-shadow:0 2px 12px rgba(0,0,0,.06); padding:22px; margin-top:18px; }
  h1 { font-size:24px; margin:0 0 6px; }
  .sub { color:#777; font-size:14px; margin-bottom:6px; }
  label { font-weight:600; }
  .row { display:flex; gap:10px; flex-wrap:wrap; }
  input[type=text] { flex:1; min-width:260px; padding:12px 14px; font-size:15px;
    border:1px solid #d9dde5; border-radius:10px; outline:none; }
  input[type=text]:focus { border-color:#3b82f6; }
  .btn { padding:12px 22px; font-size:15px; border:0; border-radius:10px; cursor:pointer; }
  .btn-primary { background:#ff3b6b; color:#fff; }
  .btn-primary:hover { background:#e6355f; }
  .btn-primary:disabled { background:#f2a8bd; cursor:not-allowed; }
  .btn-ghost { background:#eef1f6; color:#333; }
  .btn-ghost:hover { background:#e2e7ef; }
  .opts { margin:14px 0 0; font-size:13px; color:#555; display:flex; align-items:center; gap:6px; flex-wrap:wrap; }
  #status { margin-top:16px; font-size:14px; line-height:1.8; }
  .err { color:#dc2626; }
  .info { color:#2563eb; }
  .spinner { display:inline-block; width:16px; height:16px; border:2px solid #cbd5e1;
    border-top-color:#ff3b6b; border-radius:50%; animation:spin .8s linear infinite; vertical-align:-3px; }
  @keyframes spin { to { transform: rotate(360deg); } }
  details { margin-top:22px; background:#fff; border-radius:14px; box-shadow:0 2px 12px rgba(0,0,0,.06); padding:4px 20px 18px; }
  summary { cursor:pointer; font-weight:600; padding:14px 0; }
  .step { margin:8px 0; font-size:14px; color:#444; }
  textarea { width:100%; min-height:90px; padding:10px; font-size:13px; font-family:Consolas,monospace;
    border:1px solid #d9dde5; border-radius:10px; resize:vertical; }
  .save-note { font-size:13px; color:#666; margin-top:8px; }
  .result-box { background:#f0fdf4; border:1px solid #bbf7d0; border-radius:10px; padding:12px 14px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>🎬 抖音视频下载器</h1>
  <p class="sub">粘贴抖音分享链接 → 自动解析 → 保存无水印视频（支持 <code>v.douyin.com</code> 短链接）</p>

  <div class="card">
    <label for="url">视频链接 / 分享文案</label>
    <div class="row" style="margin-top:8px;">
      <input id="url" type="text" autocomplete="off"
        placeholder="例如：https://v.douyin.com/xxxxxxx/ （也可直接粘贴 App 里的整段分享文字）">
      <button id="btn" class="btn btn-primary">解析并下载</button>
    </div>
    <div class="opts">
      <input type="checkbox" id="useBrowser" checked>
      <label for="useBrowser" style="font-weight:400;">失败时自动尝试读取本机浏览器 Cookie（Edge / Chrome / Firefox）</label>
    </div>
    <div id="status"></div>
  </div>

  <div class="card">
    <label>手动提供 Cookie（遇到风控时的备用方案）</label>
    <p class="save-note">抖音要求请求携带"游客 Cookie"（不需要登录）。如果自动下载失败，请按下方
      帮助里的步骤复制一份 Cookie 粘贴到这里并保存，之后每次解析都会自动使用。有效期通常很长。</p>
    <textarea id="cookieText" placeholder="粘贴 douyin.com 请求头里的 Cookie 值，形如：s_v_web_id=xxxx; ttwid=xxxx; ..."></textarea>
    <div style="margin-top:10px;">
      <button class="btn btn-ghost" onclick="saveCookie()">💾 保存 Cookie</button>
      <span id="cookieStatus" class="save-note"></span>
    </div>
  </div>

  <details>
    <summary>❓ 下载失败？先看这里（重要）</summary>
    <div class="step">1. 先用 Chrome / Edge 打开 <a href="https://www.douyin.com/" target="_blank" rel="noopener">www.douyin.com</a>，随便刷一刷（<b>不用登录</b>）。</div>
    <div class="step">2. 回到本页面重新点"解析并下载"（上面的"自动读取浏览器 Cookie"勾选框保持打开）。</div>
    <div class="step">3. 如果还是失败：回到抖音页面按 <code>F12</code> → 切到 <b>Network（网络）</b> → 刷新页面 →
        点击第一个 <code>douyin.com</code> 请求 → 在 <b>Request Headers</b> 里找到 <code>Cookie</code> 一行，
        右键 → <b>Copy value</b>，粘贴到上面输入框并点"保存 Cookie"，然后重新下载。</div>
    <div class="step">4. 依旧失败：多半是抖音更新了风控，请在终端执行 <code>pip install -U yt-dlp</code> 升级后再试。</div>
  </details>
</div>

<script>
var BASE = '';
var dlUrl = null;

function show(html) {
  var box = document.getElementById('status');
  box.innerHTML = html;
}

async function start() {
  var raw = document.getElementById('url').value.trim();
  var useBrowser = document.getElementById('useBrowser').checked;
  var cookie = document.getElementById('cookieText').value.trim();
  var btn = document.getElementById('btn');
  if (!raw) { show('<span class="err">请先粘贴抖音链接。</span>'); return; }
  btn.disabled = true;
  show('<span class="spinner"></span> 正在解析视频地址…（正常需要几秒到十几秒）');
  try {
    var resp = await fetch(BASE + '/douyin/api/parse', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: raw, cookie: cookie, use_browser: useBrowser })
    });
    var data = await resp.json();
    if (data.ok) {
      var lines = ['<div class="result-box"><b>✅ 解析成功，通道：' + data.channel + '</b>',
                   '<div>📄 标题：' + (data.title || '未知') + '</div>'];
      if (data.author) lines.push('<div>👤 作者：' + data.author + '</div>');
      if (data.duration) lines.push('<div>⏱️ 时长：' + data.duration + ' 秒</div>');
      lines.push('<div style="margin-top:8px;">👇 浏览器正在自动保存视频（大文件请耐心等待进度条走完）。</div></div>');
      show(lines.join(''));
      dlUrl = BASE + data.dl_url;
      var a = document.createElement('a');
      a.href = dlUrl;
      document.body.appendChild(a);
      a.click();
      a.remove();
    } else {
      var hint = data.hint ? '<div class="info">💡 ' + data.hint + '</div>' : '';
      show('<span class="err">❌ ' + (data.message || '解析失败') + '</span>' + hint);
    }
  } catch (e) {
    show('<span class="err">网络或服务器异常：' + e + '</span>');
  } finally {
    btn.disabled = false;
  }
}

async function saveCookie() {
  var cookie = document.getElementById('cookieText').value.trim();
  var st = document.getElementById('cookieStatus');
  try {
    var resp = await fetch(BASE + '/douyin/api/cookie', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cookie: cookie })
    });
    var data = await resp.json();
    st.textContent = data.message || '';
    st.style.color = data.ok ? '#16a34a' : '#dc2626';
  } catch (e) {
    st.textContent = '保存失败：' + e;
    st.style.color = '#dc2626';
  }
}

document.getElementById('btn').addEventListener('click', start);
document.getElementById('url').addEventListener('keydown', function (e) {
  if (e.key === 'Enter') start();
});
</script>
</body>
</html>
"""


# =========================================================
# 自定义错误：带 kind 标记，方便页面给出针对性提示
# =========================================================
class DouyinError(Exception):
    def __init__(self, message, kind="general", hint=""):
        super().__init__(message)
        self.kind = kind
        self.hint = hint


def _cookie_kind_hint():
    return (
        "抖音当前需要携带网页访问 Cookie（游客状态即可，无需登录）。"
        "请先在浏览器打开一次 douyin.com，再重试；仍不行就按页面下方帮助步骤，"
        "手动复制一份最新 Cookie 粘贴保存后再试。"
    )


# =========================================================
# 通用工具函数
# =========================================================
def _cleanup_jobs():
    now = time.time()
    expired = [key for key, value in _JOBS.items() if now - value["created"] > _JOB_TTL]
    for key in expired:
        _JOBS.pop(key, None)


def _extract_link_from_text(text):
    """从分享文案/链接中提取第一条 http 链接。"""
    match = re.search(r"https?://[^\s，。；、\"'<>]+", text or "")
    if not match:
        raise DouyinError("没有识别到链接，请粘贴完整的抖音分享链接（或整段分享文字）", "bad_url")
    return match.group(0).rstrip(".,;:)]}）")


def _host_of(url):
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def _is_douyin_host(host):
    host = (host or "").lower()
    return host in DOUYIN_HOSTS or host.endswith(".douyin.com")


def _resolve_url(url):
    """把 v.douyin.com 短链重定向成完整链接（只读响应头，不下载页面）。"""
    if _host_of(url) != "v.douyin.com":
        return url
    headers = {**COMMON_HEADERS, "User-Agent": UA_DESKTOP}
    try:
        resp = requests.get(url, headers=headers, timeout=(5, 15), allow_redirects=True, stream=True)
        final_url = resp.url or url
        resp.close()
        return final_url
    except requests.RequestException as exc:
        raise DouyinError(f"打开分享链接失败：{exc}", "network") from exc


def _extract_video_id(url):
    """从抖音 URL 中提取纯数字的视频 id。"""
    full = urlparse(url).path + "?" + urlparse(url).query
    match = re.search(r"/(?:video|share/video|note)/(\d+)", full) or re.search(r"modal_id=(\d+)", full)
    if not match:
        raise DouyinError("无法从链接中识别视频编号，请确认这是抖音视频分享链接", "bad_url")
    if "/note/" in full:
        raise DouyinError("这个链接是抖音图文作品，不是视频，暂时无法下载", "note")
    return match.group(1)


def _make_douyin_cookie(name, value):
    """构造一个只对 www.douyin.com 生效的 Cookie（yt-dlp 请求详情接口时使用）。"""
    return Cookie(
        version=0,
        name=name,
        value=value,
        port=None,
        port_specified=False,
        domain="www.douyin.com",
        domain_specified=False,
        domain_initial_dot=False,
        path="/",
        path_specified=True,
        secure=False,
        expires=None,
        discard=True,
        comment=None,
        comment_url=None,
        rest={},
    )


def _cookie_header_to_jar(cookie_text):
    """把浏览器里复制出来的 Cookie 请求头字符串转成 CookieJar。"""
    jar = CookieJar()
    if not cookie_text:
        return jar
    cookie_text = re.sub(r"^cookie\s*:\s*", "", cookie_text.strip(), flags=re.IGNORECASE)
    for part in cookie_text.split(";"):
        if "=" not in part:
            continue
        name, _, value = part.strip().partition("=")
        name, value = name.strip(), value.strip()
        if name and value:
            try:
                jar.set_cookie(_make_douyin_cookie(name, value))
            except Exception:
                continue
    return jar


def _save_cookie_text(text):
    text = (text or "").strip()
    if not text:
        if COOKIE_FILE.exists():
            COOKIE_FILE.unlink()
        return "已清除本地保存的 Cookie"
    COOKIE_FILE.write_text(text, encoding="utf-8")
    return "Cookie 保存成功，之后每次解析都会自动使用"


def _load_saved_cookie():
    if COOKIE_FILE.exists():
        text = COOKIE_FILE.read_text(encoding="utf-8").strip()
        if text:
            return text
    return ""


def _safe_filename(title, fallback):
    if not title:
        title = fallback
    title = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "_", str(title))
    title = re.sub(r"\s+", " ", title).strip(" ._")
    title = title[:80]
    return title or fallback


def _installed_browsers():
    """返回本机已安装、且 yt-dlp 支持自动读取 Cookie 的浏览器列表。"""
    browser_paths = {
        "edge": [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ],
        "chrome": [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ],
        "firefox": [
            r"C:\Program Files\Mozilla Firefox\firefox.exe",
            r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
        ],
    }
    return [name for name, paths in browser_paths.items() if any(os.path.exists(path) for path in paths)]


# =========================================================
# 三套解析实现
# =========================================================
def _ytdl_extract(video_url, cookie_jar=None, browser=None):
    """用 yt-dlp 只做信息提取（不下载文件），拿到视频直链。"""
    if YoutubeDL is None:
        raise DouyinError("缺少依赖 yt-dlp，请先在终端执行：pip install -r requirements.txt", "dep")
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 15,
        "http_headers": {
            **COMMON_HEADERS,
            "User-Agent": UA_DESKTOP,
            "Referer": "https://www.douyin.com/",
        },
    }
    if cookie_jar is not None:
        opts["cookiejar"] = cookie_jar
    if browser:
        opts["cookiesfrombrowser"] = (browser, None, None, None)
    try:
        with YoutubeDL(opts) as ydl:
            return ydl.extract_info(video_url, download=False)
    except DownloadError as exc:
        message = re.sub(r"^ERROR:\s*", "", str(exc)).strip()
        raise DouyinError(message or "yt-dlp 解析失败", "ytdlp") from exc
    except Exception as exc:
        raise DouyinError(f"yt-dlp 解析异常：{exc}", "ytdlp") from exc


def _pick_format(info):
    """从 yt-dlp 返回的格式列表里挑选最佳直链（带声音、尽量清晰、优先 h264）。"""
    formats = info.get("formats") or []
    if not formats and info.get("url"):
        formats = [info]
    candidates = []
    for fmt in formats:
        url = fmt.get("url")
        if not url:
            continue
        vcodec = (fmt.get("vcodec") or "none").lower()
        acodec = (fmt.get("acodec") or "none").lower()
        if vcodec == "none" or acodec == "none":
            continue  # 只要音画合一的单文件（抖音播放地址基本都是这种 mp4）
        candidates.append(fmt)
    if not candidates:
        candidates = [fmt for fmt in formats if fmt.get("url")]
    if not candidates:
        return None

    def score(fmt):
        width = int(fmt.get("width") or 0)
        height = int(fmt.get("height") or 0)
        tbr = float(fmt.get("tbr") or 0)
        vcodec = (fmt.get("vcodec") or "").lower()
        note = (fmt.get("format_note") or "").lower()
        fmt_url = fmt.get("url") or ""
        penalty = 0
        if vcodec in ("h265", "hevc", "bytevc1", "bytevc2"):
            penalty += 200_000_000  # h265 兼容性差，非必要不选
        if "aweme/v1" in fmt_url or "api" in note:
            penalty += 50_000_000  # 走 API 的地址容易再被风控
        if "watermark" in note:
            penalty += 100_000_000  # 带水印的下载地址放后面
        return width * height + int(tbr) * 1000 - penalty

    return max(candidates, key=score)


def _ssr_no_cookie(video_id):
    """备用通道：iesdouyin share 页取 video_id -> aweme.snssdk.com 播放接口。
    这个接口随时可能被抖音关闭，因此只作为 yt-dlp 全部失败后的最后尝试。"""
    share_url = f"https://www.iesdouyin.com/share/video/{video_id}/"
    headers = {**COMMON_HEADERS, "User-Agent": UA_IPHONE}
    try:
        resp = requests.get(share_url, headers=headers, timeout=15)
        match = re.search(r'"uri"\s*:\s*"(v[0-9a-f]{10,})"', resp.text or "")
        if not match:
            return None
        uri = match.group(1)
        play_url = f"https://aweme.snssdk.com/aweme/v1/play/?video_id={uri}&ratio=1080p&line=0"
        return {
            "url": play_url,
            "ext": "mp4",
            "headers": {**COMMON_HEADERS, "User-Agent": UA_IPHONE, "Referer": "https://www.douyin.com/"},
        }
    except requests.RequestException:
        return None


def _pick_detail_url(video_info):
    """从抖音详情 JSON 的 video 字段里挑最合适的直链（h264、无水印、高分优先）。"""
    candidates = []

    def add(addr, note, codec):
        if not addr:
            return
        width = int(addr.get("width") or 0)
        height = int(addr.get("height") or 0)
        size = int(addr.get("data_size") or 0)
        for url in addr.get("url_list") or []:
            if url:
                candidates.append({"url": url, "width": width, "height": height,
                                   "size": size, "note": note, "codec": codec})

    add(video_info.get("download_addr"), "download", "h264")
    add(video_info.get("play_addr_h264"), "play_h264", "h264")
    add(video_info.get("play_addr"), "play", "h265" if video_info.get("is_h265") else "h264")
    for item in video_info.get("bit_rate") or []:
        add(item.get("play_addr"), "gear", "h264")

    def score(c):
        penalty = 0
        host = urlparse(c["url"]).netloc.lower()
        if "douyinvod.com" not in host:
            penalty += 30_000_000  # 更倾向 CDN 直链
        if c["codec"] in ("h265", "hevc", "bytevc1", "bytevc2"):
            penalty += 200_000_000
        if "aweme/v1" in c["url"] or "playwm" in c["url"]:
            penalty += 100_000_000  # 带水印/走 API 的地址放后面
        if "watermark" in c["note"].lower():
            penalty += 50_000_000
        return c["width"] * c["height"] + c["size"] // 1024 - penalty

    if not candidates:
        return None
    return max(candidates, key=score)["url"]


def _detail_api_parse(video_id, cookie_text):
    """通道：用游客 Cookie 直接请求抖音 Web 详情接口（页面同款参数），拿到视频直链。"""
    api_url = ("https://www.douyin.com/aweme/v1/web/aweme/detail/?"
               "device_platform=webapp&aid=6383&channel=channel_pc_web"
               "&aweme_id=" + video_id + "&request_source=600&origin_type=video_page"
               "&update_version_code=1704")
    headers = {
        "User-Agent": UA_DESKTOP,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://www.douyin.com/video/" + video_id,
        "Origin": "https://www.douyin.com",
        "Cookie": cookie_text,
    }
    detail = None
    for _ in range(2):  # 接口偶尔抽样风控，稍等重试一次
        try:
            resp = requests.get(api_url, headers=headers, timeout=15)
            if resp.status_code == 200 and resp.text.lstrip().startswith("{"):
                try:
                    detail = (resp.json() or {}).get("aweme_detail") or {}
                except Exception:
                    detail = {}
                if detail:
                    break
        except requests.RequestException:
            pass
        time.sleep(1.5)
    if not detail:
        raise DouyinError("游客 Cookie 详情接口未返回视频数据（偶发风控或 Cookie 失效）",
                          "blocked", hint=_cookie_kind_hint())
    video_info = detail.get("video") or {}
    play_url = _pick_detail_url(video_info)
    if not play_url:
        raise DouyinError("详情接口返回成功，但没有找到可用的视频地址", "no_format")
    duration = video_info.get("duration") or 0
    title = (detail.get("desc") or "").strip() or ("douyin_" + video_id)
    author_info = detail.get("author") or {}
    author = author_info.get("nickname") or author_info.get("unique_id") or ""
    return {
        "url": play_url,
        "ext": "mp4",
        "title": title,
        "author": author,
        "duration": int(duration / 1000) if duration else "",
        "channel": "游客 Cookie 直连",
        "download_headers": {
            "User-Agent": UA_DESKTOP,
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": "https://www.douyin.com/video/" + video_id,
            "Cookie": cookie_text,
        },
    }

def _pw_page_parse(video_id):
    """首选通道：用无头浏览器打开视频页，拦截页面自带签名的详情响应拿到直链。
    抖音页面请求带 a_bogus 签名，比直接调接口稳定很多；
    每次成功后还会顺手刷新 douyin_cookies.txt 里的游客 Cookie。"""
    if sync_playwright is None:
        raise DouyinError("缺少 playwright，无法使用浏览器自动解析，请先 pip install playwright", "dep")
    detail = {}
    media_urls = []
    cookie_header = ""
    launch_args = ['--disable-blink-features=AutomationControlled', '--no-first-run', '--no-default-browser-check']
    with sync_playwright() as pw:
        browser = None
        try:
            browser = pw.chromium.launch(channel='msedge', headless=True, args=launch_args)
        except Exception:
            try:
                browser = pw.chromium.launch(headless=True, args=launch_args)
            except Exception as exc:
                raise DouyinError("启动无头浏览器失败：" + str(exc), "browser")
        try:
            ctx = browser.new_context(locale='zh-CN', timezone_id='Asia/Shanghai',
                                      viewport={'width': 1280, 'height': 800},
                                      user_agent=UA_DESKTOP)
            page = ctx.new_page()

            def on_request(request):
                url = request.url
                if 'douyinvod.com' in url or '/aweme/v1/play' in url:
                    if url not in media_urls:
                        media_urls.append(url)

            page.on('request', on_request)

            def handle_detail(route):
                try:
                    resp = route.fetch()
                    body = resp.body()
                    if body[:1] == b'{':
                        import json
                        aweme = (json.loads(body.decode('utf-8', 'ignore')) or {}).get('aweme_detail') or {}
                        if aweme:
                            detail['obj'] = aweme
                    route.fulfill(response=resp)
                except Exception:
                    try:
                        route.continue_()
                    except Exception:
                        pass

            page.route('**/aweme/v1/web/aweme/detail/**', handle_detail)
            try:
                page.goto('https://www.douyin.com/video/' + video_id,
                          timeout=60000, wait_until='domcontentloaded')
                try:
                    page.wait_for_selector('video', timeout=12000)
                    page.locator('video').first.evaluate(
                        '(v) => { v.muted = true; v.play().catch(() => {}); }')
                except Exception:
                    pass
            except Exception as exc:
                raise DouyinError("打开抖音视频页失败：" + str(exc), "browser")

            deadline = time.time() + 25
            while time.time() < deadline and not detail and not media_urls:
                page.wait_for_timeout(1500)
            try:
                cookies = ctx.cookies()
            except Exception:
                cookies = []
            cookie_header = '; '.join(c['name'] + '=' + c['value']
                                      for c in cookies if c.get('name') and c.get('value'))
        finally:
            try:
                browser.close()
            except Exception:
                pass

    if not detail:
        raise DouyinError("浏览器自动解析未拿到视频数据（可能被抖音风控，已改用其它方式）",
                          "blocked", hint="如果反复失败，请稍后再试，或在浏览器里打开一次 douyin.com。")
    video_info = (detail.get('obj') or {}).get('video') or {}
    play_url = _pick_detail_url(video_info)
    if not play_url and media_urls:
        play_url = media_urls[0]
    if not play_url:
        raise DouyinError("页面返回成功，但没有找到可用的视频地址", "no_format")

    # 顺手用刚拿到的新鲜游客 Cookie 刷新本地保存，方便其它通道复用
    if 's_v_web_id=' in cookie_header:
        try:
            COOKIE_FILE.write_text(cookie_header, encoding='utf-8')
        except Exception:
            pass

    duration = video_info.get('duration') or 0
    title = (detail.get('obj').get('desc') or '').strip() or ('douyin_' + video_id)
    author_info = (detail.get('obj') or {}).get('author') or {}
    author = author_info.get('nickname') or author_info.get('unique_id') or ''
    return {
        "url": play_url,
        "ext": "mp4",
        "title": title,
        "author": author,
        "duration": int(duration / 1000) if duration else "",
        "channel": "浏览器自动解析",
        "download_headers": {
            "User-Agent": UA_DESKTOP,
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": "https://www.douyin.com/",
            "Cookie": cookie_header,
        },
    }

# =========================================================
# 核心：解析链接（失败时自动换下一种方式）
# =========================================================
def _do_parse(raw_text, manual_cookie, use_browser):
    """返回 dict：{url, title, author, duration, ext, channel, download_headers}"""
    cookie_text = (manual_cookie or "").strip() or _load_saved_cookie()

    # 1. 取出链接并校验域名
    link = _extract_link_from_text(raw_text)
    host = _host_of(link)
    if not _is_douyin_host(host):
        raise DouyinError("目前只支持抖音链接（douyin.com / v.douyin.com）", "bad_url")

    # 2. 短链转完整链接，提取数字视频 id
    final_url = _resolve_url(link)
    video_id = _extract_video_id(final_url)
    video_url = f"https://www.douyin.com/video/{video_id}"
    fallback_title = f"douyin_{video_id}"

    # 3. 依次尝试：手动/已保存 Cookie -> 无 Cookie -> 本机浏览器 Cookie -> 无登录直连
    errors = []

    if sync_playwright is not None:
        try:
            # 首选：无头浏览器自动解析（页面自带签名，最稳定，成功时自动刷新 Cookie）
            return _pw_page_parse(video_id)
        except DouyinError as exc:
            errors.append(("浏览器自动解析", exc))
    if cookie_text:
        try:
            # 首选：直接请求抖音详情接口（该通道已用游客 Cookie 实测通过）
            return _detail_api_parse(video_id, cookie_text)
        except DouyinError as exc:
            errors.append(("游客Cookie详情接口", exc))
        try:
            info = _ytdl_extract(video_url, cookie_jar=_cookie_header_to_jar(cookie_text))
            return _build_result(info, "手动 Cookie + yt-dlp", fallback_title)
        except DouyinError as exc:
            errors.append(("手动 Cookie", exc))

    try:
        info = _ytdl_extract(video_url)
        return _build_result(info, "yt-dlp 直连解析", fallback_title)
    except DouyinError as exc:
        errors.append(("无 Cookie", exc))

    if use_browser:
        for browser in _installed_browsers():
            try:
                info = _ytdl_extract(video_url, browser=browser)
                return _build_result(info, f"浏览器 Cookie({browser}) + yt-dlp", fallback_title)
            except DouyinError as exc:
                errors.append((f"浏览器 {browser}", exc))

    try:
        ssr = _ssr_no_cookie(video_id)
        if ssr:
            return {
                "url": ssr["url"],
                "ext": ssr.get("ext", "mp4"),
                "title": fallback_title,
                "author": "",
                "duration": "",
                "channel": "无登录直连通道",
                "download_headers": ssr["headers"],
            }
    except Exception:
        pass

    # 4. 全部失败：整理出易懂的错误提示。
    #    优先显示"缺少 Cookie"这类真正的根因（浏览器读取失败通常只是次要问题）。
    root_message = ""
    for _, exc in errors:
        low = str(exc).lower()
        if "cookie" in low or "fresh" in low:
            root_message = str(exc)
            break
    message = root_message or (str(errors[-1][1]) if errors else "所有解析方式都失败了（可能是抖音风控或视频不可见）")

    # 附加提示：缺 Cookie 时给出操作方向；浏览器库被占用/加密时单独说明。
    hints = []
    if not cookie_text and ("cookie" in message.lower() or "fresh" in message.lower()):
        hints.append(_cookie_kind_hint())
    browser_locked = any("cookie database" in str(exc).lower() for _, exc in errors)
    if browser_locked:
        hints.append("另外：自动读取浏览器 Cookie 失败，多半是 Chrome/Edge 正在运行导致数据库被占用。"
                     "请先完全关闭 Chrome/Edge 再重试；若关闭后仍失败（新版浏览器加密 Cookie），"
                     "就按页面下方帮助步骤，手动复制一份 Cookie 粘贴保存，最省事。")
    hint = " ".join(hints)
    raise DouyinError(message, "blocked", hint=hint)


def _build_result(info, channel, fallback_title):
    fmt = _pick_format(info)
    if not fmt or not fmt.get("url"):
        raise DouyinError("解析成功但没有找到可用的视频地址", "no_format")
    title = (info.get("title") or "").strip() or fallback_title
    return {
        "url": fmt["url"],
        "ext": (fmt.get("ext") or "mp4").lower(),
        "title": title,
        "author": info.get("channel") or info.get("uploader") or "",
        "duration": info.get("duration") or "",
        "channel": channel,
        "download_headers": {
            **COMMON_HEADERS,
            "User-Agent": UA_DESKTOP,
            "Referer": "https://www.douyin.com/",
        },
    }


def _register_job(result):
    _cleanup_jobs()
    token = secrets.token_urlsafe(12)
    _JOBS[token] = {
        "url": result["url"],
        "filename": f"{_safe_filename(result['title'], 'douyin_video')}.{result['ext']}",
        "headers": result.get("download_headers") or {**COMMON_HEADERS, "User-Agent": UA_DESKTOP},
        "created": time.time(),
    }
    return token


# =========================================================
# FastAPI 路由
# =========================================================
router = APIRouter()


@router.get("/douyin-download", response_class=HTMLResponse)
def douyin_page():
    return HTMLResponse(_PAGE_HTML)


@router.get("/douyin/api/parse")
def douyin_parse_get(url: str = "", cookie: str = "", use_browser: bool = True):
    """GET 版本，方便用浏览器地址栏或命令行直接调试。"""
    return _douyin_parse_json(url, cookie, use_browser)


@router.post("/douyin/api/parse")
async def douyin_parse_post(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    # 解析里会用 Playwright 同步 API 启动无头浏览器，
    # 必须放到线程池执行，不能在 asyncio 事件循环里直接调用。
    return await run_in_threadpool(
        _douyin_parse_json,
        payload.get("url", ""),
        payload.get("cookie", ""),
        bool(payload.get("use_browser", True)),
    )


def _douyin_parse_json(url, cookie, use_browser):
    try:
        result = _do_parse(url, cookie, use_browser)
        token = _register_job(result)
        return JSONResponse({
            "ok": True,
            "title": result["title"],
            "author": result["author"],
            "duration": result["duration"],
            "channel": result["channel"],
            "dl_url": f"/douyin/dl/{token}",
        })
    except DouyinError as exc:
        return JSONResponse({
            "ok": False,
            "message": str(exc),
            "kind": exc.kind,
            "hint": exc.hint,
        })
    except Exception as exc:  # 兜底：任何意外错误都转成可读消息
        return JSONResponse({
            "ok": False,
            "message": f"服务器内部错误：{exc.__class__.__name__}: {exc}",
            "hint": "如果问题持续，请查看运行终端的日志。",
        })


@router.get("/douyin/api/cookie")
def douyin_cookie_status():
    saved = bool(_load_saved_cookie())
    return JSONResponse({
        "ok": True,
        "saved": saved,
        "message": "已保存一份 Cookie，解析时会自动使用" if saved else "暂未保存 Cookie",
    })


@router.post("/douyin/api/cookie")
async def douyin_cookie_save(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    message = _save_cookie_text(payload.get("cookie", ""))
    return JSONResponse({"ok": True, "message": message})


@router.get("/douyin/dl/{token}")
def douyin_download(token: str, request: Request):
    """把解析得到的直链视频流式转发给浏览器保存。"""
    job = _JOBS.get(token)
    if not job:
        return JSONResponse({"ok": False, "message": "下载地址已过期或不存在，请重新点一次“解析并下载”"})

    upstream_headers = dict(job["headers"])
    range_header = request.headers.get("range")
    if range_header:
        upstream_headers["Range"] = range_header
    try:
        upstream = requests.get(job["url"], headers=upstream_headers, stream=True,
                                timeout=(10, 60), allow_redirects=True)
    except requests.RequestException as exc:
        return JSONResponse({"ok": False, "message": f"连接视频服务器失败：{exc}"})

    if upstream.status_code >= 400:
        upstream.close()
        return JSONResponse({"ok": False, "message": f"视频服务器返回错误（HTTP {upstream.status_code}），请重新解析"})
    content_type = upstream.headers.get("content-type", "video/mp4")
    if "text/html" in content_type:
        upstream.close()
        return JSONResponse({"ok": False, "message": "视频地址返回了错误内容（可能被风控拦截），请重新解析"})

    filename = job["filename"]
    disposition = f"attachment; filename=\"douyin_video.mp4\"; filename*=UTF-8''{quote(filename)}"
    headers = {
        "Content-Disposition": disposition,
        "Accept-Ranges": "bytes",
    }
    if upstream.headers.get("content-length"):
        headers["Content-Length"] = upstream.headers["content-length"]

    def iter_content():
        try:
            for chunk in upstream.iter_content(chunk_size=64 * 1024):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    return StreamingResponse(iter_content(), headers=headers,
                             media_type=content_type, status_code=upstream.status_code)


# =========================================================
# FastAPI 应用（独立运行时用；被 main.py import 时只用到上面的 router）
# =========================================================
app = FastAPI(
    title="抖音视频下载器",
    description="粘贴抖音分享链接即可下载无水印视频",
    version="1.0.0",
)
app.include_router(router)


@app.get("/")
def index():
    return RedirectResponse("/douyin-download")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
