#!/usr/bin/env python3
"""Agent 轨迹速览 —— 一次运行里，它到底调了哪些工具。

为什么需要它
------------
本项目两次最有价值的诊断都来自同一个动作：**把某一轮的工具调用序列拆开看**。

1. Supervisor 合成轮 159s —— 拆开发现 47 秒零工具调用，纯粹在重打 JSON
2. Specialist 加 stance 后慢一倍 —— 拆开发现 62 秒、12 次调用全在找词表，
   还跑了被明令禁止的 `find /`

两次的第一反应都是「提示词是不是写得不好」，两次都错。

> **Agent 的「慢」，多数时候是它在找东西，不是在想事情。**

在有这个脚本之前，每次诊断都要现写一段 SQL + JSON 解析 ——
写了四遍，其中一遍还把 UTC 当成了本地时间，白白对不上号。

🔴 时间一律北京时间
-------------------
OpenClaw 的 trajectory 里 `ts` 是 UTC。转换在 `_store.runtime` 的读取边界
统一做掉，本脚本直接用。

用法::

    agent_trace.py                     # 各 agent 最近几次会话的调用次数
    agent_trace.py --agent market      # 只看 market
    agent_trace.py --agent market -n 1 # 展开最近 1 次会话的调用序列
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3  # store-exempt: 读 OpenClaw 运行时的外部库，与 _store.runtime 同源
import sys
from collections import defaultdict
from datetime import datetime

_REPO = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO / "skills"))

from _contract import CN_TZ  # noqa: E402
from _store.runtime import OPENCLAW_HOME  # noqa: E402

#: 这些形状一出现基本就是「在找东西」，值得直接标出来。
SMELLS = (
    ("find /", "🔴 扫全文件系统 —— 契约明令禁止，且必然很慢"),
    ("--help", "⚠️ 去读帮助 —— 说明契约里缺了它要的字面量"),
    ("grep -r", "⚠️ 全仓搜索 —— 同上"),
    ("find .", "⚠️ 在找文件路径"),
)


def _sessions(agent: str) -> dict[str, list[dict]]:
    db = OPENCLAW_HOME / "agents" / agent / "agent" / "openclaw-agent.sqlite"
    if not db.is_file():
        return {}
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    out: dict[str, list[dict]] = defaultdict(list)
    try:
        for row in conn.execute(
                "SELECT event_json FROM trajectory_runtime_events ORDER BY seq"):
            ev = json.loads(row["event_json"])
            if ev.get("type") in ("tool.call", "tool.result"):
                out[ev.get("sessionKey") or "?"].append(ev)
    except sqlite3.Error:
        return {}
    finally:
        conn.close()
    return out


def _ts(ev: dict) -> datetime | None:
    v = ev.get("ts")
    if not isinstance(v, str):
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(CN_TZ)
    except ValueError:
        return None


def _agents() -> list[str]:
    root = OPENCLAW_HOME / "agents"
    return sorted(d.name for d in root.iterdir()
                  if (d / "agent" / "openclaw-agent.sqlite").is_file()) \
        if root.is_dir() else []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Agent 工具调用序列（北京时间）")
    ap.add_argument("--agent", help="只看某个 agent")
    ap.add_argument("-n", "--expand", type=int, default=0,
                    help="展开最近 N 次会话的调用序列")
    ap.add_argument("--sessions", type=int, default=4, help="每个 agent 列几次会话")
    args = ap.parse_args(argv)

    agents = [args.agent] if args.agent else _agents()
    if not agents:
        print("没有找到任何 agent 库。")
        return 1

    for agent in agents:
        sess = _sessions(agent)
        if not sess:
            continue
        rows = []
        for key, evs in sess.items():
            calls = [e for e in evs if e["type"] == "tool.call"]
            if not calls:
                continue
            times = [t for t in (_ts(e) for e in evs) if t]
            rows.append((min(times), max(times), key, calls))
        rows.sort()
        print(f"── {agent}")
        for start, end, key, calls in rows[-args.sessions:]:
            span = (end - start).total_seconds()
            print(f"   {start:%m-%d %H:%M:%S}  {span:6.1f}s  "
                  f"工具调用 {len(calls):>2} 次   {key.split(':')[-1][:12]}")
        print()

        for start, end, key, calls in rows[-args.expand:] if args.expand else []:
            print(f"   ══ 展开 {start:%m-%d %H:%M:%S}（{key}）")
            for e in calls:
                d = e.get("data") or {}
                name = d.get("name") or d.get("toolName") or "?"
                arg = json.dumps(d.get("args") or d.get("input") or {},
                                 ensure_ascii=False)
                t = _ts(e)
                line = f"      {t:%H:%M:%S}  {name:<10} {len(arg):>6} 字符"
                hit = [why for pat, why in SMELLS if pat in arg]
                print(line + ("   " + hit[0] if hit else ""))
                if name == "exec" and len(arg) > 120:
                    cmd = (d.get("args") or {}).get("command", "")
                    print(f"                 {cmd[:110]}")
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
