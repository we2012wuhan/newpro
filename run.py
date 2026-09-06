"""
调试入口：用 PyCharm 直接调试这个文件即可启动 FastAPI 服务。

用法：
    在 PyCharm 的 Run/Debug Configurations 里，把调试配置的
    Script path 指向本文件 run.py，然后点 Debug 即可。
    不要再去指向 .venv/Scripts/uvicorn.exe（那是二进制文件，调试器解析不了）。

说明：
    reload 调试时建议关闭（True 会重启子进程，导致断点不稳定）。
    日常开发想热重载，直接终端运行 `uvicorn main:app --reload` 即可，
    本文件和那个方式互不影响。
"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
