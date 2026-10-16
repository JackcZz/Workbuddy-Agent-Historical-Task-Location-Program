#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wbhistory — WorkBuddy 历史会话（任务）读取层

纯 Python 标准库，零依赖。所有本机路径都在运行时通过 expanduser 解析，
源码中不含任何个人绝对路径，可安全公开。

数据来源
--------
1. ~/.workbuddy/workbuddy.db           -> sessions 表：任务元数据（标题/时间/状态/工作目录/模型）
2. ~/.workbuddy/projects/<项目>/<id>.jsonl -> 完整对话流水（JSONL，逐行一条记录）

设计要点
--------
* 任务索引从 SQLite 读，很快（毫秒级）。
* 对话正文按需解析，并给每条消息标注它在该 jsonl 中的**行号**，
  用以外界「定位到历史对话的具体位置」（如用编辑器打开到某一行）。
* 解析结果按 (mtime, size) 做内存缓存，避免重复解析大文件。
"""

from __future__ import annotations

import datetime
import glob
import json
import os
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------- 路径工具

def wb_dir() -> str:
    """WorkBuddy 配置根目录。可用 WB_HOME 覆盖（便于测试）。"""
    env = os.environ.get("WB_HOME")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    return os.path.join(os.path.expanduser("~"), ".workbuddy")


def db_path() -> str:
    return os.path.join(wb_dir(), "workbuddy.db")


def projects_dir() -> str:
    return os.path.join(wb_dir(), "projects")


UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def ts_str(ms: Optional[int], fmt: str = "%Y-%m-%d %H:%M") -> str:
    if not ms:
        return ""
    try:
        return datetime.datetime.fromtimestamp(ms / 1000).strftime(fmt)
    except Exception:
        return ""


def ts_time(ms: Optional[int]) -> str:
    return ts_str(ms, "%H:%M:%S")


# ---------------------------------------------------------------- 文本清洗

# 这些标签内部是「系统注入的上下文」，不是人说的话 —— 整块丢弃
_DROP_TAGS = (
    "system-reminder", "user_info", "user-context", "memory_and_skills_reminder",
    "craft_mode", "additional-data", "user_memory", "working_modes",
    "available_skills", "agent_skills", "tool_use", "result_presentation",
    "sharing_files", "final_answer_instructions", "search_routing",
    "automations", "task_management", "asking_questions", "binary_context",
    "response_language", "instructions_for_visualizer", "visualizer_examples",
    "library_routing", "app_development_routing", "office_skill_routing",
    "browser_use", "plugin_recommendation", "expert_management",
    "mcp_configuration", "personal_files_safety", "regional_conventions",
    "content_policy", "wecom_connector_off", "agent_mail", "web_development_policy",
    "memory_system", "system-reminder",
)

# 这些标签只是「包裹层」，内容要保留
_KEEP_TAGS = ("user_query", "user-query", "user_input")


def strip_noise(text: str) -> str:
    """剥掉系统注入，只留下人真正说的话。"""
    if not text:
        return ""
    t = text

    # 0) 先把“包裹标签”换成哨兵字符，使其内容在后续清洗中幸存
    #    （<user_query> 里装的才是人真正说的话，绝不能被当标签删掉）
    SENT = [("\x01", "\x02"), ("\x03", "\x04"), ("\x05", "\x06")]
    for (o, c), tag in zip(SENT, _KEEP_TAGS):
        t = re.sub(r"<%s\s*>" % re.escape(tag), o, t)
        t = re.sub(r"</%s\s*>" % re.escape(tag), c, t)

    # 1) 整块丢弃的系统标签（可跨行）
    for tag in _DROP_TAGS:
        t = re.sub(r"<%s\b[^>]*>.*?</%s>" % (re.escape(tag), re.escape(tag)), "", t, flags=re.S)
        t = re.sub(r"<%s\b[^>]*/\s*>" % re.escape(tag), "", t)

    # 2) 兜底：其余成对标签整块丢弃
    t = re.sub(r"<[A-Za-z][A-Za-z0-9_-]*\b[^>]*>.*?</[A-Za-z][A-Za-z0-9_-]*\s*>", "", t, flags=re.S)

    # 3) 还原哨兵为“无标签的纯内容”
    for o, c in SENT:
        t = t.replace(o, "").replace(c, "")

    t = t.replace("\u200b", "")
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def blocks_to_text(content: Any) -> str:
    """jsonl 的 content 可能是 str，也可能是块数组。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for b in content:
            if isinstance(b, str):
                out.append(b)
            elif isinstance(b, dict):
                v = b.get("text")
                if isinstance(v, str):
                    out.append(v)
        return "\n".join(out)
    return str(content)


# ---------------------------------------------------------------- 任务索引

def _connect() -> sqlite3.Connection:
    p = db_path()
    if not os.path.exists(p):
        raise FileNotFoundError("找不到 WorkBuddy 数据库: %s" % p)
    conn = sqlite3.connect("file:%s?mode=ro" % p, uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def list_tasks() -> List[Dict[str, Any]]:
    """读取全部历史任务（不含对话正文）。"""
    conn = _connect()
    try:
        rows = conn.execute("SELECT * FROM sessions ORDER BY created_at DESC").fetchall()
    finally:
        conn.close()

    jmap = jsonl_index()
    out: List[Dict[str, Any]] = []
    for r in rows:
        sid = r["id"]
        jl = jmap.get(sid, "")
        size = os.path.getsize(jl) if jl and os.path.exists(jl) else 0
        cwd = r["cwd"] or ""
        out.append({
            "id": sid,
            "title": r["custom_title"] or r["title"] or "(无标题)",
            "status": r["status"] or "",
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
            "created_str": ts_str(r["created_at"]),
            "updated_str": ts_str(r["updated_at"]),
            "date": ts_str(r["created_at"], "%Y-%m-%d"),
            "cwd": cwd,
            "workspace": os.path.basename(cwd) if cwd else "",
            "user_id": r["user_id"],
            "model": r["model"] or "",
            "expert_id": r["expert_id"] or "",
            "mode": r["mode"] or "",
            "jsonl": jl,
            "size_mb": round(size / 1024 / 1024, 2) if size else 0,
            "has_transcript": bool(jl),
        })
    return out


_jsonl_cache: Optional[Dict[str, str]] = None
_jsonl_cache_at = 0.0


def jsonl_index(force: bool = False) -> Dict[str, str]:
    """sessionId -> jsonl 绝对路径。带 60 秒缓存。"""
    global _jsonl_cache, _jsonl_cache_at
    now = time.time()
    if not force and _jsonl_cache is not None and now - _jsonl_cache_at < 60:
        return _jsonl_cache
    idx: Dict[str, str] = {}
    for p in glob.glob(os.path.join(projects_dir(), "*", "*.jsonl")):
        m = UUID_RE.search(os.path.basename(p))
        if m:
            idx.setdefault(m.group(0).lower(), p)
    _jsonl_cache = idx
    _jsonl_cache_at = now
    return idx


# ---------------------------------------------------------------- 当前会话

def detect_current_session(tasks: Optional[List[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
    """
    判定「当前正在跑的会话」。优先级：
      1. 环境变量 WB_SESSION_ID（宿主可注入，最准）
      2. 数据库中 status = working
      3. 最近更新的一条
    """
    if tasks is None:
        tasks = list_tasks()
    if not tasks:
        return None

    env_id = (os.environ.get("WB_SESSION_ID") or "").strip().lower()
    if env_id:
        for t in tasks:
            if t["id"].lower() == env_id:
                return t

    for t in tasks:
        if t["status"] == "working":
            return t

    env_cwd = (os.environ.get("WB_SESSION_CWD") or "").strip()
    if env_cwd:
        for t in tasks:
            if t["cwd"] == env_cwd:
                return t

    return max(tasks, key=lambda x: x["updated_at"] or 0)


# ---------------------------------------------------------------- 对话解析

_msg_cache: Dict[str, Tuple[float, int, Dict[str, Any]]] = {}
MAX_TOOL_TEXT = 400


def _tool_summary(name: str, args: Any) -> str:
    """把工具调用压成一行人话摘要。"""
    try:
        if isinstance(args, str):
            d = json.loads(args)
        else:
            d = args or {}
    except Exception:
        d = {}

    if not isinstance(d, dict):
        return name

    for key in ("command", "file_path", "path", "pattern", "query", "url", "description"):
        v = d.get(key)
        if isinstance(v, str) and v.strip():
            v = v.strip().replace("\n", " ")
            return "%s · %s" % (name, v[:200])
    if d:
        return "%s · %s" % (name, json.dumps(d, ensure_ascii=False)[:200])
    return name


def load_messages(session_id: str,
                  include_tools: bool = True,
                  max_text: int = 20000) -> Dict[str, Any]:
    """
    解析某个会话的完整对话流水，每条消息带它在 jsonl 中的行号（1-based）。

    返回 {session_id, jsonl, total_lines, truncated, messages:[...]}
    message: {line, kind, role, name, text, ts, time}
      kind: user | assistant | tool | tool_result | note
    """
    session_id = (session_id or "").lower()
    jmap = jsonl_index()
    path = jmap.get(session_id)
    if not path or not os.path.exists(path):
        return {"session_id": session_id, "jsonl": "", "total_lines": 0,
                "truncated": False, "messages": [], "error": "该任务没有对话流水文件"}

    st = os.stat(path)
    cached = _msg_cache.get(session_id)
    if cached and cached[0] == st.st_mtime and cached[1] == st.st_size:
        return cached[2]

    messages: List[Dict[str, Any]] = []
    total_lines = 0
    truncated = False

    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        for lineno, raw in enumerate(fh, start=1):
            total_lines = lineno
            if not raw.strip():
                continue
            try:
                d = json.loads(raw)
            except Exception:
                continue

            t = d.get("type")
            ts = d.get("timestamp")

            if t == "message":
                role = d.get("role") or ""
                raw_text = blocks_to_text(d.get("content"))
                if role == "user":
                    text = strip_noise(raw_text)
                    if not text:
                        continue
                    messages.append({"line": lineno, "kind": "user", "role": "user",
                                     "name": "", "text": text[:max_text],
                                     "ts": ts, "time": ts_time(ts)})
                elif role == "assistant":
                    text = strip_noise(raw_text).strip()
                    if not text:
                        continue
                    if len(text) > max_text:
                        text = text[:max_text] + "\n…（已截断）"
                    messages.append({"line": lineno, "kind": "assistant", "role": "assistant",
                                     "name": "", "text": text,
                                     "ts": ts, "time": ts_time(ts)})

            elif t == "function_call" and include_tools:
                name = d.get("name") or d.get("function", {}).get("name") or "tool"
                args = d.get("arguments", d.get("function", {}).get("arguments"))
                messages.append({"line": lineno, "kind": "tool", "role": "tool",
                                 "name": name, "text": _tool_summary(str(name), args),
                                 "ts": ts, "time": ts_time(ts)})

            elif t == "function_call_result" and include_tools:
                rc = d.get("content") or d.get("result") or d.get("output")
                txt = blocks_to_text(rc)
                txt = re.sub(r"\s+", " ", txt).strip()
                if len(txt) > MAX_TOOL_TEXT:
                    txt = txt[:MAX_TOOL_TEXT] + " …（已截断）"
                if txt:
                    messages.append({"line": lineno, "kind": "tool_result", "role": "tool",
                                     "name": "", "text": txt,
                                     "ts": ts, "time": ts_time(ts)})

    result = {
        "session_id": session_id,
        "jsonl": path,
        "total_lines": total_lines,
        "truncated": truncated,
        "messages": messages,
        "size_mb": round(st.st_size / 1024 / 1024, 2),
    }

    _msg_cache[session_id] = (st.st_mtime, st.st_size, result)
    if len(_msg_cache) > 40:  # 简单淘汰
        for k in list(_msg_cache.keys())[:10]:
            _msg_cache.pop(k, None)
    return result


def _quick_first_prompt(path: str, max_lines: int = 400, limit: int = 600) -> str:
    """只读文件前若干行拿首条真实用户提问 —— 首问必然在文件开头，避免解析大文件。"""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            for i, raw in enumerate(fh):
                if i >= max_lines:
                    break
                if not raw.strip():
                    continue
                try:
                    d = json.loads(raw)
                except Exception:
                    continue
                if d.get("type") == "message" and d.get("role") == "user":
                    text = strip_noise(blocks_to_text(d.get("content")))
                    if text:
                        return text[:limit]
    except Exception:
        pass
    return ""


def first_prompt(session_id: str, quick: bool = True) -> str:
    """某会话的首条真实用户提问。quick=True 时只读文件开头，非常快。"""
    session_id = (session_id or "").lower()
    path = jsonl_index().get(session_id)
    if not path or not os.path.exists(path):
        return ""
    if quick:
        return _quick_first_prompt(path)
    r = load_messages(session_id, include_tools=False)
    for m in r["messages"]:
        if m["kind"] == "user":
            return m["text"]
    return ""


_prompt_cache: Optional[Dict[str, str]] = None
_prompt_cache_at = 0.0


def all_first_prompts(force: bool = False, ttl: float = 120.0) -> Dict[str, str]:
    """全部任务的首条提问，带 TTL 缓存。用于列表全文搜索。"""
    global _prompt_cache, _prompt_cache_at
    now = time.time()
    if not force and _prompt_cache is not None and now - _prompt_cache_at < ttl:
        return _prompt_cache
    out: Dict[str, str] = {}
    for sid, path in jsonl_index(force=force).items():
        out[sid] = _quick_first_prompt(path)
    _prompt_cache = out
    _prompt_cache_at = now
    return out


# ---------------------------------------------------------------- 定位操作

def locate_targets(task: Dict[str, Any], line: Optional[int] = None) -> Dict[str, Any]:
    """
    生成「定位」所需的目标信息。
      - workspace : 该任务的工作目录（agent 当时所在的项目位置）
      - file/line : 对话流水文件与行号（历史对话的精确位置）
    """
    return {
        "session_id": task.get("id", ""),
        "title": task.get("title", ""),
        "workspace": task.get("cwd", ""),
        "file": task.get("jsonl", ""),
        "line": line or 1,
        "status": task.get("status", ""),
    }


if __name__ == "__main__":
    import pprint
    tasks = list_tasks()
    print("任务总数:", len(tasks))
    cur = detect_current_session(tasks)
    print("当前会话:", cur["title"] if cur else None, "|", cur["id"] if cur else "")
    if cur:
        pprint.pprint(locate_targets(cur))
        msgs = load_messages(cur["id"])["messages"]
        print("对话条数:", len(msgs))
        for m in msgs[:5]:
            print("  L%-5s %-12s %s" % (m["line"], m["kind"], m["text"][:70].replace("\n", " ")))
