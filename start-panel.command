#!/bin/bash
# 双击启动：macOS 悬浮面板（模式 B）
# 首次运行会自动编译 .app，之后直接打开
cd "$(dirname "$0")" || exit 1

APP="build/WBHistoryPanel.app"

if [ ! -d "$APP" ]; then
  echo "首次运行，正在编译悬浮面板…"
  bash swift/build.sh || exit 1
fi

echo "启动悬浮面板（菜单栏会出现「会话」，点击可显示/隐藏）"
open "$APP"
