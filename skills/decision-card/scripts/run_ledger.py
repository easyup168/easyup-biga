#!/usr/bin/env python3
"""run_ledger.py —— 运行状态机的 CLI 边界，兼 `biga-card --status` 的渲染。

它是 bash（`bin/biga-card`）与 Python 状态机（`_store.runs`）之间的桥：
bash 没法直接调 `open_run()` / `transition()`，就调这个脚本的子命令。

    run_ledger.py open   --origin cli                 → 打印新 run_id
    run_ledger.py move   <run_id> --expect X --to Y   → compare-and-set 一步
    run_ledger.py status <run_id>                     → 复述这次运行走过的路

🔴 `STATE_MEANING` 是「谁读这个状态」的答案
--------------------------------------------
设计文档 §5 / 批 B 判据：**每个状态都要能指出谁读它，指不出读取方的状态删掉。**
批 B 里所有状态的通用读取方就是 `biga-card --status` —— 而 `--status` 能不能
说清一个状态，取决于它在不在 `STATE_MEANING` 里。

⇒ `STATE_MEANING` 就是这个消费方的全部知识。它必须覆盖 `_contract.RUN_STATES`
  的每一个（由 `tests/test_run_state_machine.py::test_每个状态都有消费方` 钉死）：
  往契约层加一个状态却不在这里给它一句话，`describe_state()` 会当场抛错 ——
  也就是「这个状态没有读取方」。这是 P4 的落点。

  🔴 它**放在消费方这一侧**，不放契约层：这样「加了状态但没人读」才会被抓到。
     把 meaning 也塞进契约层，就成了「定义方自己声明自己被读了」，什么都没验。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from _contract import (  # noqa: E402
    RUN_ORIGINS,
    TERMINAL_STATES,
    RunState,
    new_run_context,
)
from _store import (  # noqa: E402
    IllegalTransition,
    UnknownRun,
    init_schema,
    open_run,
    run_journey,
    transition,
)

#: 每个状态对给人的一句话 —— `--status` 用它说「死在哪一步」。
#: 终态附上对应的 `bin/biga-card` 退出码（设计文档 §5：终态的读取方是「排查 + 退出码」）。
STATE_MEANING: dict[str, str] = {
    RunState.RECEIVED: "已接下这次请求，还没过预检。",
    RunState.PREFLIGHTED: "五道守卫全过（熔断→ownership→锁→预算），准备起 Specialist。",
    RunState.SNAPSHOT_FROZEN: "数据切片已冻结，Specialist 取的是同一份（批 D 起才有生产方）。",
    RunState.STAGE1_RUNNING: "Stage 1 五个 Specialist 并行采集/判断中（批 C 起才有生产方）。",
    RunState.STAGE1_COMPLETED: "Stage 1 全部返回，等制衡层。",
    RunState.RISK_RUNNING: "制衡层 risk 审查中。",
    RunState.SYNTHESIZING: "Supervisor 合成 Card 中。",
    RunState.CARD_PERSISTED: "Card 已落库，等 spawn 核验与收尾。",
    RunState.COMPLETED: "正常完成，卡已落库并通过核验（bin/biga-card 退出码 0）。",
    RunState.FAILED: "执行失败：守卫拒绝 / spawn 核验未过 / 采集异常（退出码 4）。",
    RunState.TIMEOUT: "超过硬超时预算，被收掉（退出码 1）。",
    RunState.CANCELLED: "被主动收掉：守卫拦下 / 并发锁 / 人工中止（退出码 3）。",
    RunState.INPUT_REQUIRED: "卡在非交互 ask_user 上，需要人去看提示词（退出码 5）。",
}


class NoConsumerForState(KeyError):
    """`--status` 遇到一个自己不认识的状态 —— 即「这个状态没有读取方」。"""


def describe_state(state: str) -> str:
    """一个状态对给人的解释。状态没登记进 `STATE_MEANING` ⇒ 抛错。

    🔴 抛错而不是回退成「未知状态」：一个 `--status` 说不清的状态，就是一个
    没有读取方的状态，批 B 判据要求这种状态根本不该存在。让它当场炸，
    才能被 `test_每个状态都有消费方` 抓到（P4）。
    """
    try:
        return STATE_MEANING[state]
    except KeyError as e:
        raise NoConsumerForState(
            f"状态 {state!r} 没有登记进 STATE_MEANING —— "
            f"biga-card --status 说不清它，也就是没有读取方。"
            f"在 run_ledger.py 的 STATE_MEANING 里给它一句话，或从 RUN_STATES 删掉它。"
        ) from e


def cmd_open(a: argparse.Namespace) -> int:
    # 🔴 自己建 schema —— open 是 run 记账的第一步，可能发生在全新库上
    #    （第一张卡的 Stage 0 建库在 LLM 那侧，晚于这里）。与 reserve_decision_id
    #    同款：任何入口都该能在空环境里独立跑起来。
    init_schema()
    ctx = new_run_context(
        origin=a.origin,
        non_interactive=not a.interactive,
        decision_id=a.decision_id,
        trigger_id=a.trigger_id,
    )
    print(open_run(ctx))
    return 0


def cmd_move(a: argparse.Namespace) -> int:
    detail = json.loads(a.detail) if a.detail else None
    try:
        transition(a.run_id, a.expect, a.to, detail=detail)
        return 0
    except (IllegalTransition, UnknownRun) as e:
        print(str(e), file=sys.stderr)
        return 1


def cmd_status(a: argparse.Namespace) -> int:
    j = run_journey(a.run_id)
    if j is None:
        print(f"没有 run {a.run_id} —— 用 biga-card --status <run_id>，"
              f"run_id 从出卡时的输出里拿。", file=sys.stderr)
        return 2
    h = j["header"]
    cur = j["current_state"]
    print(f"run     {h['run_id']}")
    print(f"decision {h['decision_id'] or '（legacy 路径，占号在 LLM 那侧，见下方事件 detail）'}")
    print(f"trigger  {h['trigger_id']}   origin={h['origin']}   "
          f"non_interactive={h['non_interactive']}")
    print(f"created  {h['created_at']}")
    print()
    print("走过的路：")
    for e in j["events"]:
        arrow = f"{e['from_state'] or '·'} → {e['to_state']}"
        extra = f"   {json.dumps(e['detail'], ensure_ascii=False)}" if e["detail"] else ""
        print(f"  [{e['seq']:>2}] {e['at'][11:19]}  {arrow}{extra}")
    print()
    tag = "✅ 完成" if cur == RunState.COMPLETED else (
        "🔴 死在这一步" if cur in TERMINAL_STATES else "⏳ 停在这一步（未到终态）")
    print(f"当前：{cur}  —— {tag}")
    print(f"      {describe_state(cur)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="运行状态机的 CLI 边界（bin/biga-card 调它）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    po = sub.add_parser("open", help="开一个新 run，打印 run_id")
    po.add_argument("--origin", required=True, choices=sorted(RUN_ORIGINS))
    po.add_argument("--decision-id", default=None, help="已知则带上；legacy 路径留空")
    po.add_argument("--trigger-id", default=None, help="外部请求的幂等键；留空自动生成")
    po.add_argument("--interactive", action="store_true",
                    help="有人能回答 ask_user（默认非交互 —— CLI/cron 没人应答）")
    po.set_defaults(func=cmd_open)

    pm = sub.add_parser("move", help="compare-and-set 推进一步")
    pm.add_argument("run_id")
    pm.add_argument("--expect", required=True, help="当前应处的状态")
    pm.add_argument("--to", required=True, help="要转移到的状态")
    pm.add_argument("--detail", default=None, help="附加信息 JSON（如发现的 decision_id）")
    pm.set_defaults(func=cmd_move)

    ps = sub.add_parser("status", help="复述这次运行走过的路")
    ps.add_argument("run_id")
    ps.set_defaults(func=cmd_status)

    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
