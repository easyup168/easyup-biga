#!/usr/bin/env python3
"""在线路径：把 Specialist 的 Verdict 合成为 Decision Card 并落库。

Supervisor 提供**判断**（status / headline / synthesis），
本脚本负责**组装 + 校验 + 落库 + 渲染**。

🔴 Supervisor 不许自己手写 Card 的 JSON。
   手写的 Card 无法被回放复现 —— 回放走的是这里的 `synthesize()`，
   而你手写的那份组装逻辑只存在于当时那段对话里。

用法::

    # verdicts 从文件读
    synthesize.py --verdicts v.json --status WAIT --headline "..." \\
        --model-ref anthropic/claude-sonnet-5

    # 或从 stdin（可以直接接 emotion-calc 的输出）
    emotion_calc.py | synthesize.py --verdicts - --status WAIT --headline "..."
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE.parent.parent.parent.parent / "skills"))

from _contract import CONTRACT_VERSION, AgentVerdict, MissingItem, VerdictRef  # noqa: E402
from _store import (  # noqa: E402
    init_schema,
    load_verdict,
    load_verdict_meta,
    next_decision_id,
)
from card_ops import Judgment, persist, synthesize  # noqa: E402


def _read_by_ids(spec: str) -> tuple[list[AgentVerdict], list[VerdictRef]]:
    """按 `verdict_id` 取回判定原件 —— **推荐路径**。

    🔴 为什么优先用 id 而不是贴 JSON
       贴 JSON 要求 agent 逐字复述结构化数据。实测它做不到：
       Specialist 转述后 15 条 evidence 的 `retrieved_at` 一条不剩，
       落库 Card 上的 `retrieved_at` 变成了「敲命令的时刻」而非采集时刻。
       走 id，数据根本不经过 LLM。

    🔴 A6：这条路能拿到 `verdict_id`，因此也是唯一能建出 `VerdictRef`
       的路径——`_read_verdicts()` 那条退路读的是裸 JSON，从没落过库，
       没有 verdict_id 可引用。
    """
    verdicts: list[AgentVerdict] = []
    refs: list[VerdictRef] = []
    for raw in spec.replace(",", " ").split():
        if not raw.isdigit():
            raise SystemExit(f"--verdict-ids 只接受数字 id，收到 {raw!r}。"
                             f"这个 id 由 skill 在 stderr 上打印：verdict_ref=NN")
        vid = int(raw)
        v = load_verdict(vid)
        if v is None:
            raise SystemExit(
                f"verdict_id={raw} 在 agent_verdicts 里不存在。"
                f"确认 Specialist 跑 skill 时没有加 --no-store —— "
                f"加了就不会落原件，也就没有 id 可引用。")
        verdicts.append(v)
        meta = load_verdict_meta(vid)
        refs.append(VerdictRef(agent=v.agent, verdict_id=vid,
                               content_sha256=meta["content_sha256"],
                               contract_version=CONTRACT_VERSION,
                               # 🔴 批 J-I：从存量行的 run_id 列直接搬（历史行是 None）。
                               run_id=meta.get("run_id")))
    return verdicts, refs


def _read_verdicts(src: str) -> list[AgentVerdict]:
    text = sys.stdin.read() if src == "-" else pathlib.Path(src).read_text(encoding="utf-8")
    data = json.loads(text)
    if isinstance(data, dict):
        data = [data]
    return [AgentVerdict.from_dict(d) for d in data]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="合成 Decision Card（在线路径）")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--verdict-ids",
                     help="🔴 推荐：判定原件的 id，逗号或空格分隔（skill 在 stderr "
                          "上打印 verdict_ref=NN）。数据不经过 LLM，不会被复述丢字段")
    src.add_argument("--verdicts",
                     help="退路：AgentVerdict JSON 文件路径，或 - 表示 stdin。"
                          "⚠️ 需要 agent 逐字搬运，实测会丢 retrieved_at")
    ap.add_argument("--status", required=True, choices=["BUY", "WAIT", "AVOID", "BLOCK"])
    ap.add_argument("--headline", required=True, help="核心矛盾，一句话")
    ap.add_argument("--synthesis", default="", help="合成说明（可选）")
    ap.add_argument("--extra-missing", action="append", default=[], nargs=2,
                    metavar=("CODE", "TEXT"),
                    help="Supervisor 自己发现的缺失项：机器可读代码 + 人话，可重复。"
                         "🔴 登记某个 agent 缺席要用 supervisor.<agent>.agent_no_response"
                         "（批 P：代码里带 agent 名，roster 检查靠它对号；共用一个 "
                         "supervisor.agent_offline 的写法对不上号、会被拒）。"
                         "例如 supervisor.risk.agent_offline \"risk agent 尚未上线\"")
    ap.add_argument("--model-ref", required=True, help="做这次合成的模型标识")
    ap.add_argument("--decision-id", help="BIGA-YYYYMMDD-NNN，缺省自动生成")
    ap.add_argument("--elapsed-ms", type=int, default=0, help="端到端耗时")
    ap.add_argument("--no-store", action="store_true", help="只渲染不落库")
    # 🔴 批 N（外部评审 §7-9）：落库的在线卡必须说得清属于哪次执行尝试、基于哪份
    #    冻结切片。这条旧 standalone 路径没有 RunContext，只能由调用方显式给 ——
    #    给不出就只能 --no-store（渲染看看可以，落一张来历不明的卡不行）。
    ap.add_argument("--run-id", help="这次编排执行尝试的 run_id（落库时必填）")
    ap.add_argument("--evidence-set-id", help="这次决策用的冻结切片 id（落库时必填）")
    ap.add_argument("--json", action="store_true", help="输出 Card 的 JSON 而非文本卡")
    args = ap.parse_args(argv)

    verdict_refs: list[VerdictRef] = []
    if args.verdict_ids:
        verdicts, verdict_refs = _read_by_ids(args.verdict_ids)
    else:
        verdicts = _read_verdicts(args.verdicts)
    if not verdicts:
        print("没有任何 Verdict —— 不出卡。", file=sys.stderr)
        return 1

    # --- 🔴 先查身份，再查完整性 ---
    #
    # 2026-09-21 盘中，两次端到端相隔两分钟。每个 specialist 的 verdict 都写着
    # 同一个 task_id（当时全是硬编码的 -001），于是**两次运行的证据合成了一张卡**，
    # 而卡上没有任何字段能暴露这件事 —— 五个 agent 齐全、时间戳都在几十秒内。
    #
    # 顺序不是随意的：证据来自两次运行时，先抱怨「缺 stance」会把人引向
    # 错误的修复 —— 补完 stance，真正的问题还在，而且更难看见了。
    # **身份比完整性更根本。**
    tids = {v.task_id for v in verdicts}
    if len(tids) > 1:
        print(
            f"这些 verdict 来自不止一次决策：{sorted(tids)}\n"
            f"  合成会把不同时刻采到的证据混进同一张卡，而卡上看不出来。\n"
            f"  怎么办：只传属于本次的 verdict_id；\n"
            f"          本次的号由 Stage 0 的 new_decision.py 占下并 --task-id 下发。",
            file=sys.stderr)
        return 1

    # 🔴 每个给得出判断的 Specialist 都必须提交 stance。
    #    放在这里而不是契约层，是因为契约层会在 `from_dict` 时也生效 ——
    #    那会让 Phase 1/2 早期落库的卡（没有 stance）再也回放不了。
    #    ⇒ 新卡严格，旧卡可读。这是「能不能重建当时看到的东西」优先于形式一致。
    silent = [v.agent for v in verdicts if v.verdict != "UNKNOWN" and not v.stance]
    if silent:
        print(
            f"这些 Specialist 给出了判断却没有提交 stance：{silent}\n"
            f"  stance 是方向判断（市场偏哪边），verdict 是数据完整度（数据全不全），\n"
            f"  两者不能互相替代。没有 stance，这次结论就无法参与后续的区分力检验。\n"
            f"  让它补一条：amend_verdict.py --ref <它的 ref> --stance <词表里的词>",
            file=sys.stderr)
        return 1

    if not args.no_store:
        init_schema()
    # 🔴 不要硬编码序号。原来写的是 new_task_id(1)，当天第二次决策必撞主键，
    #    Supervisor 只好每次自己查库推序号 —— 一轮多花一百多秒。
    # 🔴 没给 --decision-id 时，**用证据自己带的号**，不要另分配一个。
    #
    # 实测（BIGA-20260921-014）：Supervisor 在 Stage 0 占了 013、
    # 一路传给五个 specialist，合成时却忘了 --decision-id ⇒
    # 脚本又分配了 014，于是**卡是 014、它的证据全写着 013**。
    #
    # 混血闸门不会红（所有 verdict 的号是一致的），但归属仍然断了。
    # 根子还是那一条：**决策的身份在 Stage 0 就定了**，
    # 合成阶段的职责是用它，不是再造一个。
    #
    # next_decision_id() 退化成兜底：只有在完全没有上游号时才会走到。
    shared = {v.task_id for v in verdicts}
    decision_id = (args.decision_id
                   or (shared.pop() if len(shared) == 1 else next_decision_id()))
    stray = {v.task_id for v in verdicts} - {decision_id}
    if args.decision_id and stray:
        print(
            f"verdict 的 task_id {sorted(stray)} 与 --decision-id {decision_id} 不符。\n"
            f"  说明这批证据不是为这次决策采的。\n"
            f"  怎么办：核对 Stage 0 占的号有没有原样传给每个 specialist。",
            file=sys.stderr)
        return 1

    card = synthesize(
        decision_id=decision_id,
        verdicts=verdicts,
        judgment=Judgment(status=args.status, headline=args.headline,
                          synthesis=args.synthesis,
                          extra_missing=[MissingItem(t, c)
                                         for c, t in args.extra_missing]),
        model_ref=args.model_ref,
        elapsed_ms=args.elapsed_ms,
        verdict_refs=verdict_refs,
        run_id=args.run_id,
        evidence_set_id=args.evidence_set_id,
    )

    if not args.no_store and not (args.run_id and args.evidence_set_id):
        # 🔴 抢在 save_card 之前给一句能照做的话。`save_card` 也会拒（那才是边界），
        #    但它的报错是从存储层的角度写的，这里补上「在这条命令上该怎么办」。
        print(
            "落库需要 --run-id 与 --evidence-set-id —— 一张在线卡必须说得清它属于\n"
            "  哪次执行尝试、基于哪份冻结的数据切片（外部评审 §7-9）。\n"
            "  · 正常出卡请走 bin/biga-card（orchestrator 两样都会填）；\n"
            "  · 只想看看这批 verdict 合出来什么样：加 --no-store。",
            file=sys.stderr)
        return 1

    if not args.no_store:
        # card_ops.persist() 是唯一账本写入 Owner（批 C-II + P2-2）。
        # 旧版在这里手动记一遍 record_verdict_run，persist() 内部还会再记一遍——
        # 造成每条 Verdict 双写。直接让 persist() 负责即可。
        rid = persist(card)
        print(f"已落库 record_id={rid}  decision_id={decision_id}", file=sys.stderr)

    print(json.dumps(card.to_dict(), ensure_ascii=False, indent=2) if args.json
          else card.render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
