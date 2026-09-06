# -*- coding: utf-8 -*-
# 打包入口：双击 exe 后启动本地服务并自动打开「工具箱」主页。
# 关闭黑色控制台窗口即可退出。

import socket
import sys
import threading
import webbrowser

import uvicorn

HOST = '127.0.0.1'
START_PORT = 8000


def _find_free_port():
    for port in range(START_PORT, START_PORT + 50):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind((HOST, port))
            sock.close()
            return port
        except OSError:
            sock.close()
            continue
    return START_PORT


def _open_browser(url):
    def _do():
        try:
            webbrowser.open(url)
        except Exception:
            pass
    threading.Timer(1.8, _do).start()


def main():
    from main import app
    port = _find_free_port()
    url = 'http://' + HOST + ':' + str(port) + '/'
    print('本地工具箱正在启动 ...')
    print('工具箱主页：' + url)
    print('主页里可以进入 AI 生成图表 与 抖音下载器；浏览器会自动打开主页。')
    print('关闭本窗口即可退出程序。')
    _open_browser(url)
    uvicorn.run(app, host=HOST, port=port, reload=False, log_level='info')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
