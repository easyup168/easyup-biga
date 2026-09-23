#!/usr/bin/env python3
"""给一份 FactBundle 追加一个判断（stance）—— **不重打事实**。

为什么需要它
------------
批 E 把 `AgentVerdict` 拆成 `FactBundle`（事实，skill 产）+ `AgentAssessment`
（判断，Agent 产）。Agent 想加一个方向判断时，本来唯一的办法是把整份 JSON 重打一遍
改字段 —— 实测代价（2026-09-20 `BIGA-20260920-002`）：转述后 15 条 evidence 的
`retrieved_at` **一条不剩**，Supervisor 那一轮 **47 秒零工具调用**纯在生成 JSON。

⇒ 这条命令让 Agent 只写**一行 stance**：事实（fact 行）一个字不动，判断单独落一行
`AgentAssessment` 指回它（与 `decision_records.replay_of` 同一套 —— L-8：当时看到的必须
可重建）。

🔴 批 E-III：六个 skill 全部产 FactBundle 之后，操作合体 `AgentVerdict` 的**旧修订
路径**（复制原件 + 改字段 + `save_verdict(amends=…)`）已**退役**。fact 行只接受
`--stance`；历史合体行只读、不能再修订。`--add-missing`/`--add-warning`/`--verdict`
是退役的旧参数，保留只为在有人照抄旧命令时给一句指路的报错。

用法::

    python3 amend_verdict.py --ref 17 --stance 修复
    # → stderr: verdict_ref=18   （一条 AgentAssessment，指回 fact #17，事实没被重打）
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent.parent.parent.parent / "skills"))

from _contract import AgentAssessment  # noqa: E402
from _store import load_verdict_meta, save_assessment  # noqa: E402


def _assess_fact(args, meta: dict) -> int:
    """新形状（批 E-I）：给一份 `FactBundle` 追加一个 `AgentAssessment`（stance）。

    🔴 这才是补丁路径本该有的样子：Agent 只加一个判断，**一行 stance**，事实一个字
    不重打（对比老路径要 `dataclasses.replace` 整份原件）。校验全走契约/存储层
    （`AgentAssessment` 查词表、`save_assessment` 查跨型铁律 UNKNOWN⇒无法判定），
    这里只把报错翻译成命令行口吻，不自己判（F8：判据只留一处）。
    """
    if args.add_missing or args.add_warning or args.verdict:
        print(
            f"verdict_id={args.ref} 是一条 FactBundle（事实行），不接受 "
            "--add-missing / --add-warning / --verdict。\n"
            "  批 E 把事实与判断拆开之后，「事后修订事实」这条路对 fact 行是关闭的 ——\n"
            "  它当年就是 fail-open 的入口（agent 拿 --verdict 把 skill 算的完整度事后降级）：\n"
            "  · 真正的数据缺口：skill 跑的时候就知道抓到了什么，已经写进它自己的 missing[]；\n"
            "  · 范围外的判断边界 / agent 观察到的保留：那是自然语言 caveat，写进回答的\n"
            "    「需要注意」，不是数据缺失项，也不能事后改 skill 算的 verdict。\n"
            "  这一行只接受 --stance（追加一个判断）。", file=sys.stderr)
        return 2
    if args.stance is None:
        print("给 FactBundle 追加判断必须带 --stance —— 它就是这一步唯一要加的东西。",
              file=sys.stderr)
        return 2
    try:
        assessment = AgentAssessment(
            task_id=meta["task_id"], agent=meta["agent"], stance=args.stance)
        new_ref = save_assessment(assessment, fact_id=args.ref)
    except (ValueError, TypeError) as e:
        print(str(e), file=sys.stderr)
        return 2
    print(f"verdict_ref={new_ref}", file=sys.stderr)
    print(f"  {meta['agent']}  fact #{args.ref}  +  判断 stance={args.stance}"
          f"   （新形状：判断单独落一行 #{new_ref}，事实没有被重打）", file=sys.stderr)
    if args.json:
        print(json.dumps(assessment.to_dict(), ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="给一份 FactBundle 追加一个判断（stance）—— 事实不动，写一行 AgentAssessment")
    ap.add_argument("--ref", type=int, required=True,
                    help="要追加判断的 fact 行 verdict_id（skill 在 stderr 打印 verdict_ref=NN）")
    ap.add_argument("--stance", metavar="WORD",
                    help="方向判断，必须取自本 agent 的词表 —— fact 行唯一支持的操作")
    ap.add_argument("--json", action="store_true", help="把新的 AgentAssessment 打到 stdout")
    # 🔴 批 E-III：下面三个是**退役的旧参数**。argparse 仍收下它们，只为在有人照抄
    #    旧命令（`--add-missing … --verdict …`）时由 `_assess_fact` 给一句指路的报错，
    #    而不是抛一个难懂的 argparse "unrecognized arguments"。它们**不再有任何效果**。
    ap.add_argument("--add-missing", action="append", default=[], nargs=2,
                    metavar=("CODE", "TEXT"), help=argparse.SUPPRESS)
    ap.add_argument("--add-warning", action="append", default=[], metavar="TEXT",
                    help=argparse.SUPPRESS)
    ap.add_argument("--verdict", choices=["PASS", "WARNING", "UNKNOWN"],
                    help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    meta = load_verdict_meta(args.ref)
    if meta is None:
        print(f"verdict_id={args.ref} 不存在。确认 skill 跑的时候没有加 --no-store "
              f"—— 加了就不会落原件，也就没有 id 可引用。", file=sys.stderr)
        return 2

    # 🔴 判据是 kind 列，不是猜 json。fact 行走「加一个 AgentAssessment（判断）」——
    #    **不重打事实**（补丁路径当年就是为了防这个）。
    if meta["kind"] == "fact":
        return _assess_fact(args, meta)
    if meta["kind"] == "assessment":
        print(f"verdict_id={args.ref} 是一条**判断**（assessment），不是事实行。\n"
              f"  给一份判定加判断，请指向 skill 打印的那条 fact 的 verdict_ref。",
              file=sys.stderr)
        return 2

    # 🔴 批 E-III：kind 是 NULL/'verdict' 的历史合体 AgentVerdict。操作它的旧修订路径
    #    （复制原件 + 改字段 + `save_verdict(amends=…)`）**已退役** —— 六个 skill 全产
    #    FactBundle 之后再没有活的产出方写这个形状。历史合体行是 `LegacyAdapter` 读路径
    #    宽的测试对象（`save_verdict()` 因此保留在 `_store`），只读、不再被修订。
    print(f"verdict_id={args.ref} 是一条历史合体 AgentVerdict（kind={meta['kind']!r}）。\n"
          f"  批 E-III 之后所有 skill 都产 FactBundle，旧的「复制原件+改字段」修订路径已退役。\n"
          f"  历史合体行只读，不能再修订；新决策请对 skill 打印的那条 fact 行加 --stance。",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
