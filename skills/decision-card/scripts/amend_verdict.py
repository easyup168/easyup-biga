#!/usr/bin/env python3
"""修订一份判定原件 —— Specialist 追加缺失项时用，**不重打 JSON**。

为什么需要它
------------
契约允许 Specialist 在 skill 的事实之上追加自己发现的局限
（最典型的：「只有单日快照，无法判断趋势」），并相应降级 `verdict`。

在此之前，唯一的做法是让 Specialist **把整份 JSON 重打一遍**，改那三个字段。
实测代价（2026-09-20 `BIGA-20260920-002`）：

- 转述后 15 条 evidence 的 `retrieved_at` **一条不剩**，
  落库 Card 上的 `retrieved_at` 变成了「敲命令的时刻」而不是采集时刻
- Supervisor 那一轮有 **47 秒零工具调用**，纯粹在生成 6460 字符的 JSON
- 之后 `synthesize.py` 因为缺字段连失败两次，又花 49 秒自救

⇒ 改成一条命令：原件不动，**追加一行修订**并指回原行
（与 `decision_records.replay_of` 同一套做法 —— L-8：当时看到的必须可重建）。

用法::

    python3 amend_verdict.py --ref 17 \\
      --add-missing emotion.cycle.no_history "情绪周期趋势 —— 只有单日快照，无法区分衰退与修复" \\
      --verdict WARNING \\
      --stance 修复
    # → stderr: verdict_ref=18
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent.parent.parent.parent / "skills"))

from _contract import STANCE_VOCAB, AgentVerdict, MissingItem  # noqa: E402
from _store import load_verdict, load_verdict_meta, save_verdict  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="追加缺失项并降级 verdict —— 原件不改，写一行修订")
    ap.add_argument("--ref", type=int, required=True,
                    help="要修订的 verdict_id（skill 在 stderr 上打印 verdict_ref=NN）")
    ap.add_argument("--add-missing", action="append", default=[], nargs=2,
                    metavar=("CODE", "TEXT"),
                    help="追加一条缺失项：机器可读代码 + 人话，可重复。"
                         "代码形如 market.trend.no_history —— 它要能被聚合")
    ap.add_argument("--verdict", choices=["PASS", "WARNING", "UNKNOWN"],
                    help="降级后的 verdict（数据完整度）")
    ap.add_argument("--stance", metavar="WORD",
                    help="方向判断，必须取自本 agent 的词表。"
                         "它与 --verdict 回答的是两个不同的问题")
    ap.add_argument("--add-warning", action="append", default=[], metavar="TEXT",
                    help="追加一条 warning，可重复")
    ap.add_argument("--reason", help="为什么修订。缺省用追加的第一条缺失项")
    ap.add_argument("--json", action="store_true", help="把修订后的 verdict 打到 stdout")
    args = ap.parse_args(argv)

    original = load_verdict(args.ref)
    if original is None:
        print(f"verdict_id={args.ref} 不存在。确认 skill 跑的时候没有加 --no-store "
              f"—— 加了就不会落原件，也就没有 id 可引用。", file=sys.stderr)
        return 2

    if args.stance is not None:
        # 🔴 外部评审 F8：这里原来抄了一遍契约层的逻辑，
        #    连同那个盲区一起抄（`if vocab and ...` —— 没登记就放行）。
        #    ⇒ 不再自己判，只负责把契约层的报错**翻译成命令行口吻**。
        #      判据只留一处，这里只管好不好读。
        vocab = STANCE_VOCAB.get(original.agent)
        if vocab is None:
            print(f"{original.agent} 没有登记 stance 词表 —— "
                  f"在 `_contract/verdict.py` 的 STANCE_VOCAB 里加一行。\n"
                  f"已登记：{', '.join(sorted(STANCE_VOCAB))}", file=sys.stderr)
            return 2
        if args.stance not in vocab:
            print(f"stance={args.stance!r} 不在 {original.agent} 的词表里。可选："
                  f"{' / '.join(vocab)}\n"
                  f"（方向判断必须可聚合 —— 自由发挥的措辞做不了统计）", file=sys.stderr)
            return 2

    if not args.add_missing and not args.add_warning and args.stance is None:
        print("没有要追加的东西，也没给 --stance。"
              "修订一份原件却什么都不改，只会多一行噪音。", file=sys.stderr)
        return 2

    try:
        added = [MissingItem(text, code) for code, text in args.add_missing]
    except ValueError as e:
        print(f"缺失项代码不合规：{e}", file=sys.stderr)
        return 2
    missing = list(original.missing) + added
    warnings = list(original.warnings) + list(args.add_warning)

    # 🔴 追加缺失项就必须降级 —— 契约不允许「有缺失项却说一切正常」。
    #    这里给一句指路的报错，而不是让契约层抛一个需要回头猜的 ValueError：
    #    实测 agent 会为此多花两轮去试。
    if args.add_missing and not args.verdict:
        print(
            "追加了缺失项但没给 --verdict。契约不允许「有缺失项却说一切正常」。\n"
            "  核心指标齐备  → --verdict WARNING\n"
            "  核心指标有缺  → --verdict UNKNOWN\n"
            "（哪些算核心，见你自己 AGENTS.md 的「缺失项怎么处理」一节）",
            file=sys.stderr)
        return 2

    verdict = args.verdict or original.verdict
    # status 是机械记账不是判断：有缺失项就不可能是 completed。
    status = "partial" if missing else original.status

    amended = dataclasses.replace(
        original, missing=missing, warnings=warnings,
        verdict=verdict, status=status,
        stance=args.stance if args.stance is not None else original.stance)

    reason = args.reason or (added[0] if added
                             else (args.add_warning[0] if args.add_warning
                                   else f"stance={args.stance}"))
    new_ref = save_verdict(amended, amends=args.ref, amend_reason=reason)

    meta = load_verdict_meta(new_ref) or {}
    print(f"verdict_ref={new_ref}", file=sys.stderr)
    print(f"  {original.agent}  {original.status}/{original.verdict}"
          f"  →  {status}/{verdict}   缺失 {len(original.missing)} → {len(missing)}"
          f"   stance={amended.stance or '—'}"
          f"   （修订自 #{meta.get('amends')}，原件未改动）", file=sys.stderr)
    if args.json:
        print(json.dumps(amended.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
