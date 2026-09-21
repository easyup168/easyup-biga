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

from _store import connect  # noqa: E402
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
    )

    if args.check:
        a, b = comparable(original), comparable(replayed)
        if a == b:
            print(f"✅ 一致：{args.decision_id} 用冻结证据重跑，结论逐字段相同。")
            return 0
        print("❌ 不一致 —— 在线与回放的组装结果不同：", file=sys.stderr)
        for k in sorted(set(a) | set(b)):
            if a.get(k) != b.get(k):
                print(f"  {k}:\n    在线 {a.get(k)!r}\n    回放 {b.get(k)!r}", file=sys.stderr)
        return 2

    if args.store:
        rid = _record_id_of(args.decision_id)
        new_id = persist(replayed, replay_of=rid)
        print(f"回放已追加 record_id={new_id}（replay_of={rid}，原记录未改动）",
              file=sys.stderr)

    print(json.dumps(replayed.to_dict(), ensure_ascii=False, indent=2) if args.json
          else replayed.render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
