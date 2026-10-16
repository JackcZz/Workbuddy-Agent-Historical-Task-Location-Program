#!/bin/bash
# 双击启动：历史任务控制台（自动打开浏览器）
cd "$(dirname "$0")" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
  echo "未找到 python3，请先安装 Python 3。"
  exit 1
fi

exec python3 server.py --port "${WB_HISTORY_PORT:-8901}"
