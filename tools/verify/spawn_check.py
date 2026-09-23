#!/usr/bin/env python3
"""这张卡上的 Specialist，是**真的被 spawn 的**吗 —— 外部评审 F3。

为什么需要它
------------
根 `AGENTS.md` 写着：「每次调用都会在 `agent_runs` 表留下一行。
那张表是唯一凭证 —— 你在回答里声称调用过，不算数。」

**这句话本身不成立。** 写那行的代码只是把 verdict JSON 自带的 agent
字段抄进表里；`started_at` / `finished_at` 还被写死成 Card 生成的同一时刻。

⚠️ 于是这两种情况产出的 Card 与 `agent_runs` 记录**逐字节相同**：

  1. Supervisor 真的 spawn 了 market
  2. 有人手工跑 `market_calc.py --task-id …`，把 verdict_ref 喂给 synthesize

两份都是 BigA 自己写的 —— 自己写的东西证明不了自己。

🔴 唯一能做真区分的是 OpenClaw 运行时自己记的 `subagent_runs`：
   那张表由 spawn 机制写入，**BigA 的业务代码碰不到它**。
   被验证方控制不到的地方，才算证据。

为什么接在 `bin/biga-card` 后面而不是放进 pytest
------------------------------------------------
它需要一次**真实运行**才有东西可查。而 `bin/biga-card` 是唯一每次出卡
都会走到的地方 —— 按本仓库的约定（`architecture.md` §9 L-1），
判据是**调度命令的字面量**，不是「谁调用了它」。

退出码：0 全部对上 · 1 有伪造 · 2 判不了（R-3：`UNKNOWN` ≠ `PASS`）
"""

from __future__ import annotations

import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent.parent / "skills"))

from phase1_acceptance import spawn_proof  # noqa: E402

# 退出码的唯一定义 —— 见 tools/verify/_verdict.py 的 docstring
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _verdict as _v  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("用法: spawn_check.py <决策号>", file=sys.stderr)
        return _v.UNKNOWN
    decision_id = argv[0]

    proof = spawn_proof(decision_id)
    if not proof.readable:
        print("🔶 spawn 核验判不了 —— 读不到运行时的 spawn 记录"
              "（subagent_runs / task_runs 两张都不在，或在却读不了）。",
              file=sys.stderr)
        print("   这**不算通过**：无法区分「真 spawn」与「手工跑脚本」。",
              file=sys.stderr)
        return _v.UNKNOWN

    ours = sorted(a for a, (o, _) in proof.per_agent.items() if o)
    forged = sorted(a for a, (o, sp) in proof.per_agent.items() if o and not sp)
    absent = sorted(a for a, (o, _) in proof.per_agent.items() if not o)
    ok = sorted(a for a, (o, sp) in proof.per_agent.items() if o and sp)

    # 🔴 库能读、这个号一条运行时记录都没有，而 agent_runs 里却有行
    #    ⇒ 那些行是凭空写进去的。这是**伪造**，不是「判不了」。
    #
    #    复查发现的洞就长这样：伪造一个决策号 + 用合法 API 写几行，
    #    第一版判定「6 个 agent 两份独立记录都齐 ✅」——
    #    因为它只问「这个 agent 名字在最近 50 条里出现过吗」，
    #    而机器当天确实跑过别的真实决策，伪造的号**蹭上了别人的记录**。
    #    ⚠️ `bin/biga-card` 正常使用就会反复运行 ⇒ 这个条件几乎总成立。
    if proof.rows == 0 and ours:
        print(f"🔴 spawn 核验失败：{decision_id} 在运行时的 spawn 记录里"
              f"**一条都没有**（读到的来源：{proof.by_source or '两张表都是空的'}），"
              f"而 agent_runs 里有 {ours}。", file=sys.stderr)
        print("   那些行是凭空写进去的，这个决策号从未被 spawn 过。", file=sys.stderr)
        return _v.FAIL

    if forged:
        print(f"🔴 spawn 核验失败：{forged} 在 agent_runs 里有行，"
              f"但本次决策的运行时记录里没有它们。", file=sys.stderr)
        print("   那些行是被**直接写入**的，不是 Supervisor spawn 出来的。",
              file=sys.stderr)
        return _v.FAIL

    if not ok:
        print(f"🔶 spawn 核验判不了 —— {decision_id} 一个 agent 都没核到"
              f"（运行时记录 {proof.rows} 条，agent_runs {len(ours)} 个）。",
              file=sys.stderr)
        return _v.UNKNOWN

    line = f"▸ spawn 核验：{len(ok)} 个 agent 两份独立记录都齐 {ok}"
    if absent:
        line += f"；本次缺席 {absent}"
    print(line)
    return _v.PASS


if __name__ == "__main__":
    raise SystemExit(main())
