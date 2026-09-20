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

from _contract import AgentVerdict, MissingItem  # noqa: E402
from _store import (  # noqa: E402
    init_schema,
    load_verdict,
    next_decision_id,
    record_verdict_run,
)
from card_ops import Judgment, persist, synthesize  # noqa: E402


def _read_by_ids(spec: str) -> list[AgentVerdict]:
    """按 `verdict_id` 取回判定原件 —— **推荐路径**。

    🔴 为什么优先用 id 而不是贴 JSON
       贴 JSON 要求 agent 逐字复述结构化数据。实测它做不到：
       Specialist 转述后 15 条 evidence 的 `retrieved_at` 一条不剩，
       落库 Card 上的 `retrieved_at` 变成了「敲命令的时刻」而非采集时刻。
       走 id，数据根本不经过 LLM。
    """
    out: list[AgentVerdict] = []
    for raw in spec.replace(",", " ").split():
        if not raw.isdigit():
            raise SystemExit(f"--verdict-ids 只接受数字 id，收到 {raw!r}。"
                             f"这个 id 由 skill 在 stderr 上打印：verdict_ref=NN")
        v = load_verdict(int(raw))
        if v is None:
            raise SystemExit(
                f"verdict_id={raw} 在 agent_verdicts 里不存在。"
                f"确认 Specialist 跑 skill 时没有加 --no-store —— "
                f"加了就不会落原件，也就没有 id 可引用。")
        out.append(v)
    return out


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
                         "例如 supervisor.agent_offline \"risk agent 尚未上线\"")
    ap.add_argument("--model-ref", required=True, help="做这次合成的模型标识")
    ap.add_argument("--decision-id", help="BIGA-YYYYMMDD-NNN，缺省自动生成")
    ap.add_argument("--elapsed-ms", type=int, default=0, help="端到端耗时")
    ap.add_argument("--no-store", action="store_true", help="只渲染不落库")
    ap.add_argument("--json", action="store_true", help="输出 Card 的 JSON 而非文本卡")
    args = ap.parse_args(argv)

    verdicts = (_read_by_ids(args.verdict_ids) if args.verdict_ids
                else _read_verdicts(args.verdicts))
    if not verdicts:
        print("没有任何 Verdict —— 不出卡。", file=sys.stderr)
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
    decision_id = args.decision_id or next_decision_id()
    card = synthesize(
        decision_id=decision_id,
        verdicts=verdicts,
        judgment=Judgment(status=args.status, headline=args.headline,
                          synthesis=args.synthesis,
                          extra_missing=[MissingItem(t, c)
                                         for c, t in args.extra_missing]),
        model_ref=args.model_ref,
        elapsed_ms=args.elapsed_ms,
    )

    if not args.no_store:
        # 先记每个 Specialist 的执行账本 —— 它是「确实调用过」的唯一凭证。
        for v in verdicts:
            record_verdict_run(v, decision_id=decision_id,
                               started_at=card.generated_at,
                               finished_at=card.generated_at,
                               model=args.model_ref)
        rid = persist(card)
        print(f"已落库 record_id={rid}  decision_id={decision_id}", file=sys.stderr)

    print(json.dumps(card.to_dict(), ensure_ascii=False, indent=2) if args.json
          else card.render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
