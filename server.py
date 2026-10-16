#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wb-history-console 本地服务

仅用 Python 标准库，零依赖。启动后提供：
  /                     控制台（历史任务总览）
  /float                悬浮面板模式（贴右侧用，默认展示当前会话）
  /api/tasks            全部历史任务
  /api/current          当前会话
  /api/session/<id>     某任务的对话流水（每条带行号）
  /api/locate           定位操作（Finder / VS Code 打开到指定位置）
  /api/health           健康检查

用法：
  python3 server.py [--port 8901] [--no-open]
环境变量：
  WB_HISTORY_PORT   端口（默认 8901）
  WB_SESSION_ID     强制指定当前会话 id
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wbhistory as W  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")
DEFAULT_PORT = int(os.environ.get("WB_HISTORY_PORT", "8901"))

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


# ------------------------------------------------------------------ 定位动作

def _is_allowed(path: str) -> bool:
    """只允许操作 WorkBuddy 已知目录内的路径，防止任意路径执行。"""
    if not path or not os.path.isabs(path):
        return False
    path = os.path.normpath(path)
    wb = W.wb_dir()
    home = os.path.expanduser("~")
    allowed_roots = [wb, os.path.join(home, "WorkBuddy")]
    # 工作目录若被用户改到别处，也放行当前任务列表里的 cwd
    for t in getattr(_STATE, "tasks", []):
        if t.get("cwd"):
            allowed_roots.append(os.path.normpath(t["cwd"]))
    for root in allowed_roots:
        root = os.path.normpath(root)
        if path == root or path.startswith(root + os.sep):
            return True
    return False


def run_locate(action: str, path: str, line: int = 1) -> dict:
    """执行定位动作。action: reveal | open_dir | open_editor"""
    if not _is_allowed(path):
        return {"ok": False, "error": "路径不在允许范围内，已拒绝: %s" % path}
    if not os.path.exists(path):
        return {"ok": False, "error": "路径不存在: %s" % path}

    try:
        if action == "reveal":
            subprocess.Popen(["open", "-R", path])
            return {"ok": True, "msg": "已在访达中显示"}
        if action == "open_dir":
            if not os.path.isdir(path):
                path = os.path.dirname(path)
            subprocess.Popen(["open", path])
            return {"ok": True, "msg": "已打开目录"}
        if action == "open_editor":
            # 优先 VS Code；没有则退回系统默认文本编辑
            target = "%s:%d" % (path, max(1, int(line or 1)))
            for cmd in ("code", "cursor", "subl"):
                if _which(cmd):
                    subprocess.Popen([cmd, "-g", target])
                    return {"ok": True, "msg": "已用 %s 打开到第 %d 行" % (cmd, line)}
            subprocess.Popen(["open", "-a", "TextEdit", path])
            return {"ok": True, "msg": "未检测到 VS Code，已用文本编辑打开（无行号）"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": False, "error": "未知动作: %s" % action}


def _which(name: str) -> bool:
    from shutil import which
    return which(name) is not None


# ------------------------------------------------------------------ 状态

class _State:
    def __init__(self):
        self.lock = threading.Lock()
        self.tasks: list = []
        self.loaded_at = 0.0

    def tasks_cached(self, force: bool = False) -> list:
        with self.lock:
            now = time.time()
            if force or not self.tasks or now - self.loaded_at > 30:
                self.tasks = W.list_tasks()
                self.loaded_at = now
            return self.tasks


_STATE = _State()


def find_task(sid: str):
    sid = (sid or "").lower()
    for t in _STATE.tasks_cached():
        if t["id"].lower() == sid:
            return t
    return None


# ------------------------------------------------------------------ Handler

class Handler(BaseHTTPRequestHandler):
    server_version = "wb-history-console"

    def log_message(self, fmt, *args):
        # 安静模式：只打错误
        if args and str(args[0]).startswith(("4", "5")):
            sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    # ---- helpers
    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _text(self, s: str, code: int = 200):
        self._send(code, s.encode("utf-8"), "text/plain; charset=utf-8")

    def _static(self, rel: str):
        rel = rel.split("?")[0]
        if rel in ("", "/"):
            rel = "/index.html"
        elif rel.rstrip("/") in ("/float", "/panel"):
            rel = "/float.html"
        safe = os.path.normpath(rel).lstrip("/")
        if safe.startswith("..") or os.path.isabs(safe):
            self._text("forbidden", 403)
            return
        p = os.path.join(WEB, safe)
        if not os.path.isfile(p):
            self._text("not found: %s" % safe, 404)
            return
        ext = os.path.splitext(p)[1].lower()
        with open(p, "rb") as f:
            self._send(200, f.read(), MIME.get(ext, "application/octet-stream"))

    # ---- routes
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

        if path == "/api/health":
            self._json({"ok": True, "wb_dir": W.wb_dir(), "port": self.server.server_address[1]})
            return

        if path == "/api/tasks":
            force = q.get("refresh", ["0"])[0] in ("1", "true")
            tasks = _STATE.tasks_cached(force=force)
            cur = W.detect_current_session(tasks)
            # 首条提问按需懒加载，避免启动就解析 1.6GB
            self._json({"tasks": tasks, "current_id": cur["id"] if cur else "",
                        "wb_dir": W.wb_dir(), "count": len(tasks)})
            return

        if path == "/api/prompts":
            # 全部任务的首条提问（只读每个文件开头，供列表全文搜索用）
            force = q.get("refresh", ["0"])[0] in ("1", "true")
            self._json({"prompts": W.all_first_prompts(force=force)})
            return

        if path == "/api/current":
            tasks = _STATE.tasks_cached()
            cur = W.detect_current_session(tasks)
            if not cur:
                self._json({"task": None})
                return
            msgs = W.load_messages(cur["id"])["messages"]
            self._json({"task": cur, "message_count": len(msgs)})
            return

        if path.startswith("/api/session/"):
            sid = path[len("/api/session/"):].strip("/")
            task = find_task(sid) or ({"id": sid, "title": "", "cwd": "", "jsonl": ""})
            data = W.load_messages(sid, include_tools=q.get("tools", ["1"])[0] != "0")
            data["task"] = task
            self._json(data)
            return

        if path == "/api/prompt":
            sid = q.get("id", [""])[0]
            self._json({"id": sid, "prompt": W.first_prompt(sid)})
            return

        if path == "/api/refresh":
            W.jsonl_index(force=True)
            _STATE.tasks_cached(force=True)
            self._json({"ok": True, "reloaded_at": time.time()})
            return

        # 静态资源
        self._static(path)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            body = {}

        if path == "/api/locate":
            action = body.get("action", "reveal")
            target = body.get("path", "")
            line = int(body.get("line", 1) or 1)
            self._json(run_locate(action, target, line))
            return

        self._json({"ok": False, "error": "unknown endpoint"}, 404)


def main():
    ap = argparse.ArgumentParser(description="WorkBuddy 历史任务控制台")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--no-open", action="store_true", help="启动后不自动打开浏览器")
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    if not os.path.exists(W.db_path()):
        sys.stderr.write("未找到 WorkBuddy 数据库：%s\n" % W.db_path())
        sys.stderr.write("请确认 WorkBuddy 已安装并至少运行过一次。\n")
        return 1

    # 端口被占用则自动顺延
    port = args.port
    for _ in range(20):
        try:
            httpd = ThreadingHTTPServer((args.host, port), Handler)
            break
        except OSError:
            port += 1
    else:
        sys.stderr.write("找不到可用端口\n")
        return 1

    tasks = _STATE.tasks_cached(force=True)
    cur = W.detect_current_session(tasks)

    # 后台预扫首条提问，让列表的全文搜索开箱即用
    def _warm():
        try:
            W.all_first_prompts(force=True)
        except Exception:
            pass
    threading.Thread(target=_warm, daemon=True).start()

    print("=" * 56)
    print(" WorkBuddy 历史任务控制台")
    print("=" * 56)
    print(" 地址        http://127.0.0.1:%d" % port)
    print(" 悬浮模式    http://127.0.0.1:%d/float" % port)
    print(" 任务总数    %d 条" % len(tasks))
    print(" 当前会话    %s" % (cur["title"] if cur else "-"))
    print(" 数据目录    %s" % W.wb_dir())
    print("=" * 56)
    print(" Ctrl+C 停止\n")

    url = "http://127.0.0.1:%d/" % port
    if not args.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
