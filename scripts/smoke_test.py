#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
端到端冒烟测试：自动拉起服务（若未运行），逐个验证接口。

    python3 scripts/smoke_test.py [--port 8901]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import wbhistory as W  # noqa: E402

FAILS = []
OKS = []


def check(name: str, cond: bool, detail: str = ""):
    (OKS if cond else FAILS).append(name)
    print(("  ✓ " if cond else "  ✗ ") + name + (("  — " + detail) if detail else ""))


def get(url: str, timeout: int = 30):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def post(url: str, payload: dict, timeout: int = 15):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def wait_health(base: str, tries: int = 40) -> bool:
    for _ in range(tries):
        try:
            s, d = get(base + "/api/health", timeout=3)
            if s == 200 and d.get("ok"):
                return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8901)
    args = ap.parse_args()
    base = "http://127.0.0.1:%d" % args.port

    print("== 数据层 ==")
    tasks = W.list_tasks()
    check("list_tasks 返回任务", len(tasks) > 0, "%d 条" % len(tasks))
    cur = W.detect_current_session(tasks)
    check("detect_current_session 命中", cur is not None,
          (cur["title"] if cur else "无"))
    if cur:
        msgs = W.load_messages(cur["id"])["messages"]
        check("load_messages 有内容", len(msgs) > 0, "%d 条" % len(msgs))
        check("消息带行号", all(isinstance(m.get("line"), int) and m["line"] > 0 for m in msgs))
        check("行号递增", all(a["line"] <= b["line"] for a, b in zip(msgs, msgs[1:])))
        check("首条提问非空", bool(W.first_prompt(cur["id"])))
    check("strip_noise 剥离注入",
          W.strip_noise("<system-reminder><user_info>OS X</user_info></system-reminder>"
                        "<user_query>我要做控制台</user_query>") == "我要做控制台")

    print("\n== 服务 ==")
    proc = None
    if not wait_health(base, tries=2):
        print("  （未检测到运行中的服务，自动启动）")
        proc = subprocess.Popen([sys.executable, "server.py", "--port", str(args.port),
                                 "--no-open"], cwd=ROOT,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not wait_health(base):
            check("服务启动", False, "超时")
            return 1
    check("服务健康检查", True)

    s, d = get(base + "/api/tasks")
    check("GET /api/tasks", s == 200 and d.get("count", 0) > 0, "%d 条" % d.get("count", 0))

    s, d = get(base + "/api/current")
    check("GET /api/current", s == 200 and d.get("task") is not None)

    s, d = get(base + "/api/prompts")
    n = sum(1 for v in d.get("prompts", {}).values() if v)
    check("GET /api/prompts", s == 200 and n > 0, "%d 条有提问" % n)

    if cur:
        s, d = get(base + "/api/session/%s?tools=0" % cur["id"])
        check("GET /api/session/<id>", s == 200 and len(d.get("messages", [])) > 0,
              "%d 条消息 / 共 %d 行" % (len(d.get("messages", [])), d.get("total_lines", 0)))

    for p in ("/", "/float", "/app.css", "/console.js", "/float.js"):
        try:
            with urllib.request.urlopen(base + p, timeout=10) as r:
                check("静态资源 %s" % p, r.status == 200)
        except urllib.error.HTTPError as e:
            check("静态资源 %s" % p, False, "HTTP %s" % e.code)

    print("\n== 安全边界 ==")
    s, d = post(base + "/api/locate", {"action": "reveal", "path": "/etc/passwd"})
    check("拒绝越界路径", s == 200 and d.get("ok") is False)
    s, d = post(base + "/api/locate", {"action": "evil", "path": W.db_path()})
    check("拒绝未知动作", s == 200 and d.get("ok") is False)

    s, d = post(base + "/api/locate", {"action": "reveal", "path": W.db_path()})
    check("放行合法路径", s == 200 and d.get("ok") is True, d.get("msg", d.get("error", "")))

    if proc:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    print("\n" + "=" * 46)
    print("通过 %d 项，失败 %d 项" % (len(OKS), len(FAILS)))
    if FAILS:
        print("失败项：" + ", ".join(FAILS))
        return 1
    print("冒烟测试全部通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
