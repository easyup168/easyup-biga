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
import pathlib
import sys
from collections import defaultdict

_REPO = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO / "skills"))

from _store import StoreNotInitialised  # noqa: E402
from _store.runtime import list_agents, read_tool_calls  # noqa: E402

# 退出码的唯一定义 —— 见 tools/verify/_verdict.py 的 docstring
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _verdict as _v  # noqa: E402

#: 这些形状一出现基本就是「在找东西」，值得直接标出来。
SMELLS = (
    ("find /", "🔴 扫全文件系统 —— 契约明令禁止，且必然很慢"),
    ("--help", "⚠️ 去读帮助 —— 说明契约里缺了它要的字面量"),
    ("grep -r", "⚠️ 全仓搜索 —— 同上"),
    ("find .", "⚠️ 在找文件路径"),
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Agent 工具调用序列（北京时间）")
    ap.add_argument("--agent", help="只看某个 agent")
    ap.add_argument("-n", "--expand", type=int, default=0,
                    help="展开最近 N 次会话的调用序列")
    ap.add_argument("--sessions", type=int, default=4, help="每个 agent 列几次会话")
    args = ap.parse_args(argv)

    agents = [args.agent] if args.agent else list_agents()
    if not agents:
        # 🔴 判不了，不是不通过。一个 agent 库都没有 ⇒ 还没跑过任何会话。
        print("🔶 判不了 —— 没有找到任何 agent 库（还没跑过会话？）。")
        return _v.UNKNOWN

    for agent in agents:
        calls, why = read_tool_calls(agent)
        for w in why:
            print(f"⚠️  {w}")
        if not calls:
            continue
        by_session: dict[str, list] = defaultdict(list)
        for c in calls:
            by_session[c.session_key].append(c)
        rows = sorted((min(c.at for c in cs), max(c.at for c in cs), key, cs)
                      for key, cs in by_session.items())
        print(f"── {agent}")
        for start, end, key, calls in rows[-args.sessions:]:
            span = (end - start).total_seconds()
            print(f"   {start:%m-%d %H:%M:%S}  {span:6.1f}s  "
                  f"工具调用 {len(calls):>2} 次   {key.split(':')[-1][:12]}")
        print()

        for start, end, key, calls in rows[-args.expand:] if args.expand else []:
            print(f"   ══ 展开 {start:%m-%d %H:%M:%S}（{key}）")
            for c in calls:
                hit = [why for pat, why in SMELLS if pat in c.command]
                print(f"      {c.at:%H:%M:%S}  {c.name:<10} {c.args_len:>6} 字符"
                      + ("   " + hit[0] if hit else ""))
                if c.command and c.args_len > 120:
                    print(f"                 {c.command[:110]}")
            print()
    return _v.PASS


if __name__ == "__main__":
    # 🔴 F23：库不存在是**全新环境的正常状态**，不该是一屏 traceback。
    #    统一在入口转成人话 —— 每个工具各写一遍就又是一份散开的判据。
    #    退出码 2 = 判不了，与 isolation.py 的三态口径一致。
    try:
        raise SystemExit(main())
    except StoreNotInitialised as e:
        # 🔴 业务结论（PASS/FAIL/UNKNOWN）一律走 **stdout**，只有参数错误与
        #    程序异常走 stderr。「事实库还不存在」是一个**业务结论**——
        #    R-3 的「算不出来」，不是程序出错。
        #    ⚠️ 这里原来打 stderr，与同一批工具的其他 UNKNOWN 分支（走 stdout）
        #      构成两套口径：两条测试各钉一边，**都绿**，因为它们走的是不同
        #      代码路径。外部评审把它并排放在一起才看出来。
        print(f"\n🔶 判不了 —— {e}")
        raise SystemExit(_v.UNKNOWN) from None
