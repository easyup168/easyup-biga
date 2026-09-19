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

from _contract import AgentVerdict, new_task_id  # noqa: E402
from _store import init_schema, record_verdict_run  # noqa: E402
from card_ops import Judgment, persist, synthesize  # noqa: E402


def _read_verdicts(src: str) -> list[AgentVerdict]:
    text = sys.stdin.read() if src == "-" else pathlib.Path(src).read_text(encoding="utf-8")
    data = json.loads(text)
    if isinstance(data, dict):
        data = [data]
    return [AgentVerdict.from_dict(d) for d in data]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="合成 Decision Card（在线路径）")
    ap.add_argument("--verdicts", required=True,
                    help="AgentVerdict JSON 文件路径，或 - 表示 stdin")
    ap.add_argument("--status", required=True, choices=["BUY", "WAIT", "AVOID", "BLOCK"])
    ap.add_argument("--headline", required=True, help="核心矛盾，一句话")
    ap.add_argument("--synthesis", default="", help="合成说明（可选）")
    ap.add_argument("--extra-missing", action="append", default=[],
                    help="Supervisor 自己发现的缺失项，可重复")
    ap.add_argument("--model-ref", required=True, help="做这次合成的模型标识")
    ap.add_argument("--decision-id", help="BIGA-YYYYMMDD-NNN，缺省自动生成")
    ap.add_argument("--elapsed-ms", type=int, default=0, help="端到端耗时")
    ap.add_argument("--no-store", action="store_true", help="只渲染不落库")
    ap.add_argument("--json", action="store_true", help="输出 Card 的 JSON 而非文本卡")
    args = ap.parse_args(argv)

    verdicts = _read_verdicts(args.verdicts)
    if not verdicts:
        print("没有任何 Verdict —— 不出卡。", file=sys.stderr)
        return 1

    decision_id = args.decision_id or new_task_id(1)
    card = synthesize(
        decision_id=decision_id,
        verdicts=verdicts,
        judgment=Judgment(status=args.status, headline=args.headline,
                          synthesis=args.synthesis,
                          extra_missing=list(args.extra_missing)),
        model_ref=args.model_ref,
        elapsed_ms=args.elapsed_ms,
    )

    if not args.no_store:
        init_schema()
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
