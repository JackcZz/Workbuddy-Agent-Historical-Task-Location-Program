#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成项目展示截图。

用无头 Chrome 打开 **demo 模式**（?demo=1）的页面截图 —— 该模式下所有真实
任务标题、提问、路径都会被替换成占位文本，因此截图天然不含任何个人信息，
无需事后靠猜坐标去打码。

    python3 scripts/screenshot.py [--port 8901] [--out docs/screenshots]

产物（PNG，已优化体积）：
    docs/screenshots/console.png   控制台（双栏）
    docs/screenshots/panel.png     悬浮面板
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
]

SHOTS = [
    # (文件名, 路径, 窗口尺寸)
    ("console.png", "/?demo=1", (1440, 900)),
    ("panel.png", "/float?demo=1", (420, 880)),
]


def find_chrome() -> str:
    for c in CHROME_CANDIDATES:
        if os.path.exists(c):
            return c
    raise SystemExit("未找到 Chrome / Edge，无法生成截图")


def health_ok(port: int) -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/health" % port, timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def shoot(chrome: str, url: str, out: str, size, budget: int = 12000) -> bool:
    # --no-sandbox 与独立 --user-data-dir 是必须的：
    # 缺了它们 Chrome 在受限环境下会以 SIGTRAP 直接退出。
    with tempfile.TemporaryDirectory(prefix="wbhc-shot-") as udd:
        cmd = [
            chrome,
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--hide-scrollbars",
            "--user-data-dir=" + udd,
            "--force-device-scale-factor=2",     # 2x 更清晰
            "--window-size=%d,%d" % size,
            "--virtual-time-budget=%d" % budget,
            "--screenshot=" + out,
            url,
        ]
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=120)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            print("  截图失败：%s" % e)
            return False
    return os.path.exists(out) and os.path.getsize(out) > 1000


def optimize(path: str) -> tuple:
    """压 PNG 体积（无损：调色板 + 最高压缩）。返回 (原大小KB, 新大小KB)。"""
    try:
        from PIL import Image
    except ImportError:
        return (0, 0)
    before = os.path.getsize(path)
    im = Image.open(path)
    # 2x 截图偏大，缩到 1x 宽度上限 1440
    w, h = im.size
    max_w = 1600
    if w > max_w:
        im = im.resize((max_w, int(h * max_w / w)), Image.LANCZOS)
    im.save(path, "PNG", optimize=True, compress_level=9)
    after = os.path.getsize(path)
    return (before // 1024, after // 1024)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8901)
    ap.add_argument("--out", default=os.path.join(ROOT, "docs", "screenshots"))
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    chrome = find_chrome()
    base = "http://127.0.0.1:%d" % args.port

    proc = None
    if not health_ok(args.port):
        print("服务未运行，自动启动…")
        proc = subprocess.Popen([sys.executable, "server.py", "--port", str(args.port),
                                 "--no-open"], cwd=ROOT,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(40):
            time.sleep(0.3)
            if health_ok(args.port):
                break
        else:
            print("服务启动失败")
            return 1

    ok = True
    for name, path, size in SHOTS:
        out = os.path.join(args.out, name)
        print("  截图 %-14s <- %s" % (name, path))
        if not shoot(chrome, base + path, out, size):
            ok = False
            continue
        b, a = optimize(out)
        if b:
            print("    体积 %d KB → %d KB" % (b, a))
        else:
            print("    %d KB" % (os.path.getsize(out) // 1024))

    if proc:
        proc.terminate()

    print("\n输出目录：" + args.out)
    print("提示：这些图全部由 ?demo=1 模式生成，不含真实任务内容或本地路径。")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
