#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
本地报告服务器：用 HTTP 协议打开线索报告。

为什么需要它：报告 HTML 若用 file:// 直接双击打开，Chrome 会把 file: 页面
当作「独立安全源」，点击 target=_blank 的官网 / Google Maps 外链会被安全策略
拦截（console 报 "file: URLs are treated as unique security origins"）。
改走本地 HTTP 服务后外链即可正常跳转。

用法:
    python serve_report.py report.html [--port 8123]
"""
import argparse
import os
import sys
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="本地 HTTP 服务器打开线索报告")
    ap.add_argument("html", help="报告 HTML 文件路径")
    ap.add_argument("--port", type=int, default=8123, help="端口（默认 8123）")
    ap.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = ap.parse_args()

    path = os.path.abspath(args.html)
    directory = os.path.dirname(path)
    filename = os.path.basename(path)
    url = f"http://127.0.0.1:{args.port}/{filename}"

    os.chdir(directory)
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), SimpleHTTPRequestHandler)
    print(f"报告服务已启动: {url}")
    print("关闭请按 Ctrl+C（或在终端结束进程）")

    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
