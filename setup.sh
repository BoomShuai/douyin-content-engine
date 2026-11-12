#!/bin/sh
set -eu
DOUYIN_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$DOUYIN_ROOT"
DOUYIN_BOOTSTRAP=${DOUYIN_PYTHON:-python3}
"$DOUYIN_BOOTSTRAP" -c 'import sys; assert sys.version_info >= (3, 9), "Python 3.9+ required"'
if [ ! -x .venv/bin/python ]; then
    "$DOUYIN_BOOTSTRAP" -m venv .venv
fi
case "${1:-}" in
  "") .venv/bin/python -m pip install . ;;
  --model) .venv/bin/python -m pip install '.[model]' ;;
  *) echo "Usage: sh setup.sh [--model]" >&2; exit 2 ;;
esac
.venv/bin/python -m douyin_engine doctor
echo "安装完成。模型权重未下载；使用 start.command 或 python -m douyin_engine serve 启动。"
