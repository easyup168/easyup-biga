#!/usr/bin/env python3
"""当前的出卡用量与闸门状态 —— 花钱之前先看一眼。

为什么需要它
------------
预算闸门（`skills/decision-card/scripts/budget.py`）拒绝时会说
「想看当前用量：python3 tools/verify/budget_report.py」——
**那条路必须真的存在**。设计文档点名不存在的文件，这个项目刚踩过
（外部评审 F7，一次扫出 6 个幽灵文件）。

它同时是闸门的**读取方**：本仓库要求任何新机制都有被证明的消费方，
判据是调度命令的字面量（`architecture.md` §9 L-1）。

只读，不占号、不落库。
"""

from __future__ import annotations

import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
sys.path.insert(0, str(_REPO / "skills"))
sys.path.insert(0, str(_REPO / "skills" / "decision-card" / "scripts"))

from _contract import now_cn  # noqa: E402
from _store import StoreNotInitialised, db  # noqa: E402

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _verdict as _v  # noqa: E402  —— 退出码的唯一定义

sys.path.insert(0, str(_HERE))

from phase1_acceptance import orphan_spawns  # noqa: E402

from budget import (  # noqa: E402
    DAILY_CAP,
    INFLIGHT_SEC,
    MIN_GAP_SEC,
    check_budget,
    explain,
)

#: 单次出卡的实测成本区间（`architecture.md` §10.2）。
#: ⚠️ 是区间不是单值 —— 盘中 $1.20 / 盘后 $1.37，差在快讯条数。
COST_LO, COST_HI = 1.20, 1.37


def main(argv: list[str] | None = None) -> int:
    day = (argv or sys.argv[1:] or [now_cn().strftime("%Y%m%d")])[0]
    with db.connect(readonly=True) as conn:
        res = conn.execute(
            "SELECT decision_id, reserved_at, reserved_by FROM decision_ids"
            " WHERE decision_id LIKE ? ORDER BY reserved_at", (f"BIGA-{day}-%",)
        ).fetchall()
        cards = {r["decision_id"] for r in conn.execute(
            "SELECT decision_id FROM decision_records WHERE decision_id LIKE ?",
            (f"BIGA-{day}-%",))}

    print(f"═══ {day} 出卡用量 ═══")
    print(f"  占号 {len(res)} / 上限 {DAILY_CAP}"
          f"    已出卡 {len(cards)} 张")
    # 🔴 成本按**占号**估，不按出卡估：跑到一半失败的那次，钱一样花了。
    print(f"  估算花费 ${len(res) * COST_LO:.2f} ~ ${len(res) * COST_HI:.2f}"
          f"（按占号计 —— 跑一半失败的钱也是花了的）")
    if res:
        print(f"  最近一次 {res[-1]['decision_id']} "
              f"{res[-1]['reserved_at'][11:19]} by={res[-1]['reserved_by']}")
    print(f"  阈值：最小间隔 {MIN_GAP_SEC}s · 在跑窗口 {INFLIGHT_SEC}s")

    # 🔴 孤儿 spawn = 钱花了、结果进不了任何卡。
    #    `spawn_check` 按决策号查，**查不到它们** —— 孤儿的特征
    #    恰恰是没有号可查。两个检查分工不同，都得有。
    orphans = orphan_spawns(day)
    print()
    if orphans is None:
        print("🔶 孤儿 spawn 判不了 —— 读不到运行时库")
    elif orphans:
        print(f"🔴 孤儿 spawn {len(orphans)} 个 —— 跑了但进不了任何卡"
              f"（约 ${len(orphans) * 0.10:.2f}~${len(orphans) * 0.15:.2f}）")
        for when, agent, why in orphans[-6:]:
            print(f"     {when}  {agent:11} {why}")
        if len(orphans) > 6:
            print(f"     …（只列最近 6 个，共 {len(orphans)} 个）")
        print("     根因多半是 Stage 0 顺序反了：**先占号，再 spawn**。"
              "见 architecture.md §5.3.2（L-11 身份晚于证据）")
    else:
        print("✅ 无孤儿 spawn —— 每次 spawn 都带着决策号")

    print()
    reasons = check_budget(day=day)
    if reasons:
        print(explain(reasons))
        # 🔴 这里原来是裸 `return 3` —— 而 `3` 根本不在这套词汇表里，
        #    `_verdict.describe(3)` 打出来就是「未定义的退出码 3」。
        #    「闸门此刻拒绝」是**查了、确实观察到**的状态（不是没查成）
        #    ⇒ FAIL。这样 `budget_report.py && biga-card` 才读得对。
        return _v.FAIL
    print("✅ 此刻可以出卡")
    return _v.PASS


if __name__ == "__main__":
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
