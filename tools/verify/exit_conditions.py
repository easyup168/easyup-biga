#!/usr/bin/env python3
"""Phase 2 出口条件里**能由程序判定**的那几条 —— 别手写状态表。

用法::

    python3 tools/verify/exit_conditions.py

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：条件 3（真实否决落库）、条件 4（真实缺数据跨天累积）
- **不覆盖**：条件 1/2/5/6/7/8 —— 它们要么是结构保证（有 AST 测试），
  要么要读 trajectory（`latency_report.py` 的事）

🔴 为什么要有它
---------------
条件 4 在 2026-09-21 被记成「🔶 数上够了但全在同一天」，而 09-22/23/24 三天的
数据早就把它填满了 —— **四天没人回来重新评估**，因为那张表是手写的。
与 README 徽章停在一个早就不成立的数字上，是同一个形状（L-3：数字有两个出处，
其中一个没人维护）。

退出码：0 = 全部达成 / 1 = 有未达成 / 2 = 判不了（库读不到）。
"""
from __future__ import annotations

import collections
import json
import pathlib
import sys

_REPO = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO / "skills"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _verdict as _v  # noqa: E402
from _store import StoreNotInitialised, connect  # noqa: E402

#: 「真实缺数据」的判据：代码里带点号（`emotion.pool.unavailable`）。
#: 不算数的是 `xxx agent 尚未上线` 这类**结构性占位** —— 那不是缺数据，
#: 是这个 agent 还没建，两者混在一起数会让条件 4 提前「达成」。
def _is_real_missing(code: str) -> bool:
    return "." in code and not code.startswith(("supervisor.", "roster."))


def main(argv: list[str] | None = None) -> int:
    try:
        with connect(readonly=True) as c:
            rows = c.execute(
                "SELECT decision_id, created_at, card_json, status "
                "FROM decision_records WHERE replay_of IS NULL "
                "ORDER BY record_id").fetchall()
    except StoreNotInitialised as e:
        print(f"\n🔶 判不了 —— {e}")
        return _v.UNKNOWN

    by_day: dict[str, set[str]] = collections.defaultdict(set)
    cards = 0
    vetoes: list[tuple[str, str]] = []
    for r in rows:
        card = json.loads(r["card_json"])  # contract-exempt: 只数 missing 的 code
        codes = {m.get("code", "") for m in card.get("missing", [])
                 if isinstance(m, dict)}
        real = {c0 for c0 in codes if _is_real_missing(c0)}
        if real:
            by_day[r["created_at"][:10]] |= real
            cards += 1
        # 条件 3：真实否决 —— 有 agent 给出否决 stance，且卡的状态体现了它
        for v in card.get("verdicts", []):
            if v.get("stance") == "否决":
                vetoes.append((r["decision_id"], r["status"]))
                break

    print("══ Phase 2 出口条件（可机器判定的部分）══\n")

    ok4 = len(by_day) >= 2 and cards >= 5
    print(f"{'✅' if ok4 else '⬜'} 条件 4 · missing[] 在真实缺数据时非空 ≥5 次、跨天")
    for d in sorted(by_day):
        print(f"      {d}  {len(by_day[d])} 种")
    print(f"      ⇒ {cards} 张卡，跨 {len(by_day)} 天"
          f"（判据：≥5 张且 ≥2 天）\n")

    ok3 = bool(vetoes)
    print(f"{'✅' if ok3 else '⬜'} 条件 3 · 至少 1 次真实否决落库")
    if vetoes:
        for did, st in vetoes:
            print(f"      {did}  status={st}")
    else:
        print("      一次都没有。⚠️ 这条**等不来就是等不来**：要行情真的命中")
        print("      risk 的六条阈值之一（炸板率 >50% / 量能 >2× 或 <0.5× /")
        print("      上涨占比 <20% / 最高板 ≥7 / 跌停 >30）。不是开发问题。")
    print()
    return _v.PASS if (ok3 and ok4) else _v.FAIL


if __name__ == "__main__":
    raise SystemExit(main())
