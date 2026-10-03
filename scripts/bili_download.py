# -*- coding: utf-8 -*-
"""
B站视频下载器（本地小脚本，跟工具箱那个网页没关系）

用法：
    python bili_download.py                      # 不带参数，进入粘贴模式
    python bili_download.py <链接>               # 直接给链接
    python bili_download.py <链接> -o D:\视频     # 指定保存目录
    python bili_download.py <链接> --browser edge  # 借用浏览器登录态（能下 1080P60 / 4K）

它自己挑清晰度最高的那一路，再用 ffmpeg 把画面和声音合进一个 mp4。
双击同目录的 B站下载工具.bat 也能用。
"""
import argparse
import io
import os
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import urlparse

URL_RE = re.compile(r'https?://[^\s<>"\'）】]+')
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')


def pick_url(text):
    """从一段分享文案里抠出第一个链接（B站分享出来的都是「【标题】 https://... 」）。"""
    m = URL_RE.search(text or '')
    return m.group(0).rstrip('.,;') if m else ''


def is_bili(url):
    host = (urlparse(url).hostname or '').lower()
    return bool(host) and (host == 'b23.tv' or host.endswith('.b23.tv') or host.endswith('bilibili.com'))


def tidy(url):
    """去掉 /video/ 链接上的分享参数（?share_source=...&vd_source=...），这些对下载没用。"""
    u = urlparse(url)
    if '/video/' in u.path:
        return '%s://%s%s' % (u.scheme, u.netloc, u.path)
    return url


def resolve_short(url):
    """b23.tv 短链展开成真实视频页。"""
    if 'b23.tv' not in url:
        return url
    try:
        import requests
        r = requests.get(url, headers={'User-Agent': UA}, timeout=(5, 20), allow_redirects=True)
        return r.url or url
    except Exception as exc:
        print('  ! 短链展开失败（%s），直接交给 yt-dlp 再试一次' % type(exc).__name__)
        return url


def find_ffmpeg():
    """按 优先 imageio-ffmpeg 自带 → PATH → 项目 venv 的顺序找一个能用的 ffmpeg。"""
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.exists(exe):
            return exe
    except Exception:
        pass
    exe = shutil.which('ffmpeg')
    if exe:
        return exe
    root = Path(__file__).resolve().parent.parent
    for cand in sorted(root.glob('.venv/Lib/site-packages/imageio_ffmpeg/binaries/ffmpeg-*.exe')):
        return str(cand)
    return None


def make_progress():
    """把 yt-dlp 的进度压成一行，别刷屏。"""
    def hook(d):
        st = d.get('status')
        if st == 'downloading':
            pct = (d.get('_percent_str') or '').strip()
            spd = (d.get('_speed_str') or '').strip()
            eta = (d.get('_eta_str') or '').strip()
            sys.stdout.write('\r  下载中 %s  速度 %s  剩余 %s        ' % (pct, spd, eta))
            sys.stdout.flush()
        elif st == 'finished':
            sys.stdout.write('\r  一路下载完成，继续下一路…                    \n')
            sys.stdout.flush()
    return hook


def new_ydl(opts):
    """建一个 yt-dlp 实例。

    它在 Python 3.10 下会在构造时往 stderr 打一句英文弃用提示，看着像出错，这里顺手吞掉。
    """
    import contextlib
    with contextlib.redirect_stderr(io.StringIO()):
        from yt_dlp import YoutubeDL
        return YoutubeDL(opts)


def download(url, outdir, browser=None, all_parts=False):
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        print('  × 没找到 ffmpeg。B站的高清画面和声音是分开两路，没有它合不成一个文件。')
        print('    装一个就行：pip install imageio-ffmpeg')
        return 1

    Path(outdir).mkdir(parents=True, exist_ok=True)
    opts = {
        # 最高清晰度的画面 + 最好的一条音轨
        'format': 'bv*+ba/b',
        # 同分辨率下优先 h264：码率更高、Windows 上哪里都能放（默认会挑 AV1）
        'format_sort': ['res', 'fps', 'vcodec:h264', 'br'],
        'merge_output_format': 'mp4',
        'outtmpl': {'default': os.path.join(outdir, '%(title).80s [%(id)s].%(ext)s')},
        'noplaylist': not all_parts,
        'windowsfilenames': True,
        'ffmpeg_location': ffmpeg,
        'http_headers': {'User-Agent': UA, 'Referer': 'https://www.bilibili.com/'},
        'retries': 3,
        'socket_timeout': 30,
        'noprogress': True,
        'nocolor': True,
        'quiet': True,
        'no_warnings': True,
        'progress_hooks': [make_progress()],
    }
    if browser:
        opts['cookiesfrombrowser'] = (browser,)

    try:
        with new_ydl(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as exc:
        msg = str(exc).replace('ERROR: ', '')
        sys.stdout.write('\r' + ' ' * 60 + '\r')
        print('  × 下载失败：%s' % msg[:300])
        low = msg.lower()
        if 'ffmpeg' in low:
            print('    合流那一步缺 ffmpeg，装一下：pip install imageio-ffmpeg')
        elif 'premium' in low or '会员' in msg:
            print('    这条要大会员才有更高清晰度，加 --browser edge 借浏览器登录态试试。')
        elif 'login' in low or 'cookie' in low:
            print('    需要登录态，加 --browser edge（或 chrome / firefox）再试。')
        return 1

    path = ''
    rds = (info or {}).get('requested_downloads') or []
    if rds:
        path = rds[0].get('filepath') or rds[0].get('filename') or ''

    heights = []
    for f in ((info or {}).get('requested_formats') or []):
        if f.get('height') and str(f['height']) not in heights:
            heights.append(str(f['height']))

    sys.stdout.write('\r' + ' ' * 60 + '\r')
    print('  √ 标题：%s' % (info or {}).get('title', ''))
    if heights:
        print('  √ 清晰度：%sp' % ' / '.join(heights))
    print('  √ 保存到：%s' % (path or outdir))
    return 0


def run_one(raw, outdir, args):
    url = pick_url(raw) or (raw or '').strip()
    if not is_bili(url):
        print('\n  ! 这不像 B站 链接：%s' % (url or '(空)'))
        print('    只认 bilibili.com 和 b23.tv。')
        return 1
    url = tidy(resolve_short(url))
    print('\n>>> %s' % url)
    return download(url, outdir, args.browser, args.all_parts)


def main():
    ap = argparse.ArgumentParser(description='B站视频下载：自动挑最高清晰度，自动合流。')
    ap.add_argument('link', nargs='*', help='视频链接，可以一次给多个；不填就进入粘贴模式')
    ap.add_argument('-o', '--out', default=str(Path.home() / 'Downloads'),
                    help='保存目录，默认是你系统的下载文件夹')
    ap.add_argument('--browser', help='借浏览器登录态（chrome / edge / firefox），能下到 1080P60 / 4K')
    ap.add_argument('--all-parts', action='store_true', help='分P / 合集整个下（默认只下当前这一个）')
    args = ap.parse_args()

    outdir = os.path.abspath(os.path.expanduser(args.out))
    print('保存目录：%s' % outdir)

    targets = [pick_url(x) or x.strip() for x in args.link]
    targets = [t for t in targets if t]
    if targets:
        code = 0
        for t in targets:
            code |= run_one(t, outdir, args)
        return code

    print('把链接粘进来，回车就开始；直接回车或输入 q 退出。\n')
    while True:
        try:
            raw = input('链接> ').strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not raw or raw.lower() in ('q', 'quit', 'exit'):
            break
        u = pick_url(raw) or raw
        if not is_bili(u):
            print('  ! 没看出 B站 链接，再试一次（http 开头那种）\n')
            continue
        run_one(u, outdir, args)
        print()
    return 0


if __name__ == '__main__':
    sys.exit(main())