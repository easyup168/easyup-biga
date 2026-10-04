#!/usr/bin/env python3
"""回放：用冻结的证据重跑合成。

🔴 **与在线路径共用 `card_ops.synthesize()`** —— 这是硬性要求。
   回放的用途是「换个模型重跑，看结论会不会变」；
   如果两条路径的组装代码不同，观察到的差异里就混进了代码差异，实验作废。

两种用法：

1. **一致性检查**（不换判断）—— 验证「同样的输入 → 同样的结论」::

       replay.py BIGA-20260919-001 --check

2. **换模型对比** —— 由新模型给出新的判断，组装逻辑不变::

       replay.py BIGA-20260919-001 --status AVOID \\
           --headline "..." --model-ref anthropic/claude-opus-5

回放**永不覆盖**原始记录：它追加一行，并用 `replay_of` 指回原始 `record_id`。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE.parent.parent.parent.parent / "skills"))

from _store import connect, verify_verdict_refs  # noqa: E402
from card_ops import Judgment, comparable, load_original, persist, synthesize  # noqa: E402


def _record_id_of(decision_id: str) -> int | None:
    with connect(readonly=True) as c:
        row = c.execute(
            "SELECT record_id FROM decision_records "
            "WHERE decision_id=? AND replay_of IS NULL", (decision_id,)
        ).fetchone()
    return int(row["record_id"]) if row else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="用冻结证据重跑合成")
    ap.add_argument("decision_id")
    ap.add_argument("--check", action="store_true",
                    help="一致性检查：沿用原判断重跑，断言结论逐字段相同")
    ap.add_argument("--status", choices=["BUY", "WAIT", "AVOID", "BLOCK"])
    ap.add_argument("--headline")
    ap.add_argument("--synthesis")
    ap.add_argument("--model-ref", help="新的模型标识；不给则沿用原始 Card 的")
    ap.add_argument("--store", action="store_true",
                    help="把回放结果追加落库（replay_of 指向原始记录）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    original = load_original(args.decision_id)
    if original is None:
        print(f"decision_records 里没有 {args.decision_id} 的在线记录。", file=sys.stderr)
        return 1

    # model_ref 落库时带了组装版本后缀，回放取回时要剥掉，否则会层层累积。
    base_model = original.model_ref.split(" (")[0]

    # 🔴 Supervisor 自己发现的缺失项（「risk agent 尚未上线」这类）也是**判断的一部分**，
    #    必须在回放时还原。它没有单独存列，但可以从原卡精确反推：
    #        extra = 原卡 missing − 各 Verdict 报的 missing
    #    因为 synthesize() 的聚合是「先 Verdict 后 extra、去重、保序」，
    #    所以这个反推是无损的。
    #    这个 bug 是 `--check` 抓出来的：不还原 extra_missing 时，
    #    回放出的 Card 会悄悄少掉三条缺失项 —— 正是本项目最怕的那种「静默变好看」。
    from_verdicts = {m for v in original.verdicts for m in v.missing}
    extra_missing = [m for m in original.missing if m not in from_verdicts]

    judgment = Judgment(
        status=args.status or original.status,
        headline=args.headline if args.headline is not None else original.headline,
        synthesis=args.synthesis if args.synthesis is not None else original.synthesis,
        extra_missing=extra_missing,
    )

    # 回放历史卡：Stage 0 统一占号之前的卡，判定编号与卡号不一致。
    # 让契约层降级为提示而不是拒绝 —— 否则历史读不出来。详见 card_ops.synthesize。
    replayed = synthesize(
        historical=True,
        decision_id=original.decision_id,
        verdicts=original.verdicts,      # 冻结的证据，不重新采集
        judgment=judgment,
        model_ref=args.model_ref or base_model,
        elapsed_ms=0,
        # 🔴 A6：原样带过去，否则 --check 会把「回放没有重新记 VerdictRef」
        #    误判成「组装不一致」——input_verdict_refs 是 comparable() 会
        #    比对的字段之一，不带就是从有变成没有。
        verdict_refs=list(original.input_verdict_refs),
        # 🔴 批 K：冻结名单同 input_verdict_refs —— 回放**原样带过原卡冻结的那份**，
        #    绝不用今天的 Registry 重算。老卡是 None ⇒ 回放也 None，两边一致；
        #    新卡带着它生成时冻结的 roster ⇒ 回放带同一份。这样 comparable() 里
        #    这个字段两边恒等，--check 不会因它误报「组装不一致」（gate 4）。
        expected_roster=original.expected_roster,
        # 🔴 批 N：同 expected_roster —— 原样带过原卡记的那份切片 id，不重铸。
        #    它**不**被 comparable() 剥掉（与 run_id 不同）：run_id 描述「这次执行」，
        #    而 evidence_set_id 描述「这个结论基于哪份数据」，属于结论本身的一部分，
        #    回放必须落在同一份上，否则「同样的输入 → 同样的结论」这句话没有锚点。
        evidence_set_id=original.evidence_set_id,
    )

    if args.check:
        # 🔴 A6：这一步与下面「组装一致」是两件不同的事——
        #    组装一致验的是 synthesize() 这个纯函数本身没有隐藏的非确定性；
        #    这里验的是「卡上记的判定原件，现在的 agent_verdicts 还认不认」。
        #    两者都过，「这张卡当时确实基于这些原件」才站得住。
        ref_problems = verify_verdict_refs(original)
        if ref_problems:
            print("❌ VerdictRef 核对不一致 —— 卡上引用的判定原件已经变了："
                  , file=sys.stderr)
            for p in ref_problems:
                print(f"  {p}", file=sys.stderr)
            return 2

        a, b = comparable(original), comparable(replayed)
        if a == b:
            print(f"✅ 组装一致：{args.decision_id} 用冻结证据重跑，"
                  f"逐字段相同。")
            # 🔴 外部评审 F14：这条命令**验证范围比名字暗示的小**，
            #    所以把边界印在输出里，而不是只写在文档某处。
            #
            #    评审的构造：把 headline 改成「外星人今日登陆陆家嘴，沪指熔断」，
            #    --check 照样报「一致」—— 因为 status/headline/synthesis
            #    在 --check 模式下 100% 照抄原卡，两侧本质是同一个纯函数
            #    喂入被证明相等的输入，**数学上必然相等**。
            #
            #    ⚠️ 但它并非测了个寂寞：评审把 extra_missing 的计算改坏之后，
            #      --check 正确报出了不一致。它守的是「落库→取回→再拼一次」
            #      这条管线无损、且 synthesize() 没有隐藏的非确定性。
            print("   ⚠️ 它验的是**组装管线无损**（落库→取回→再拼一次），"
                  "不是「这个结论能被独立复算出来」。")
            print("   判断本身（status / headline / synthesis）在 --check 下"
                  "照抄原卡，不参与比对。")
            return 0
        print("❌ 不一致 —— 在线与回放的组装结果不同：", file=sys.stderr)
        for k in sorted(set(a) | set(b)):
            if a.get(k) != b.get(k):
                print(f"  {k}:\n    在线 {a.get(k)!r}\n    回放 {b.get(k)!r}", file=sys.stderr)
        return 2

    if args.store:
        rid = _record_id_of(args.decision_id)
        try:
            new_id = persist(replayed, replay_of=rid)
        except ValueError as e:
            print(f"❌ 回放落库被拒：{e}", file=sys.stderr)
            return 1
        print(f"回放已追加 record_id={new_id}（replay_of={rid}，原记录未改动）",
              file=sys.stderr)

    print(json.dumps(replayed.to_dict(), ensure_ascii=False, indent=2) if args.json
          else replayed.render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
