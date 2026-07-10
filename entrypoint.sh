#!/bin/sh
set -e

# MODE (once/daemon) の分岐は src/main.py 側で行う。ここでは Xvfb 上で
# 非ヘッドレス Chromium を起動できるようにするだけのシンプルなラッパー。
exec xvfb-run -a python3 src/main.py
