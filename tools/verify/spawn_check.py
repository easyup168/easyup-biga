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


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("用法: spawn_check.py <决策号>", file=sys.stderr)
        return 2
    decision_id = argv[0]

    proof = spawn_proof(decision_id)
    if not proof:
        print("🔶 spawn 核验判不了 —— 读不到运行时的 subagent_runs。", file=sys.stderr)
        print("   这**不算通过**：无法区分「真 spawn」与「手工跑脚本」。",
              file=sys.stderr)
        return 2

    forged = sorted(a for a, (ours, sp) in proof.items() if ours and not sp)
    absent = sorted(a for a, (ours, _) in proof.items() if not ours)
    ok = sorted(a for a, (ours, sp) in proof.items() if ours and sp)

    if forged:
        print(f"🔴 spawn 核验失败：{forged} 在 agent_runs 里有行，"
              f"但运行时 subagent_runs 里没有。", file=sys.stderr)
        print("   那些行是被**直接写入**的，不是 Supervisor spawn 出来的。",
              file=sys.stderr)
        return 1

    line = f"▸ spawn 核验：{len(ok)} 个 agent 两份独立记录都齐 {ok}"
    if absent:
        line += f"；本次缺席 {absent}"
    print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
