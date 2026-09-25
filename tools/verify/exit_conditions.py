#!/usr/bin/env python3
"""Phase 2 出口条件 3/4 的严格验收工具。

覆盖：
- 条件 3：真实 risk 否决已经落库，Card 为 AVOID/BLOCK，且 ``replay --check`` 通过。
- 条件 4：源级真实 Missing 至少 5 张 Card，且跨至少 2 个自然日；演练注入和编排层弱证据不计数。

为什么这两个条件必须共用现有的严格读取/分类边界：
- Card 必须通过 ``load_online_card()`` 反序列化，不能直接 ``json.loads(card_json)`` 绕过契约。
- Missing 的“真实/演练/弱证据”必须复用 ``missing_ledger.py`` 的唯一判据，不能再写第二套。
- “看到任意 stance=否决”不等于真实 Risk Veto。必须是 risk agent、Card 状态确实被拦住，
  并且冻结输入能通过 ``replay --check`` 的组装一致性检查。

退出码：0 = 两条都达成；1 = 至少一条未达成；2 = 当前环境判不了。
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
from typing import Callable, Iterable

_REPO = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO / "skills"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _verdict as _v  # noqa: E402
import missing_ledger as _ml  # noqa: E402
from _contract import VETO_STANCE  # noqa: E402
from _store import StoreNotInitialised, db  # noqa: E402

REQUIRED_MISSING_CARDS = 5
REQUIRED_MISSING_DAYS = 2
VETO_CARD_STATUSES = frozenset({"AVOID", "BLOCK"})


def _missing_gate(rows: Iterable[dict]) -> tuple[bool, list[dict], set[str]]:
    """只统计**源级真实缺失**，演练和弱证据不计入出口条件 4。"""
    hit: list[dict] = []
    for row in rows:
        strong = [code for code in row.get("real", ()) if not _ml._weak(code)]
        if strong:
            hit.append({**row, "strong": strong})
    days = {str(row.get("day", "")) for row in hit if row.get("day")}
    ok = len(hit) >= REQUIRED_MISSING_CARDS and len(days) >= REQUIRED_MISSING_DAYS
    return ok, hit, days


def _run_replay_check(decision_id: str) -> tuple[bool, str]:
    """执行真正的 replay --check；不落库、不联网。"""
    try:
        r = subprocess.run(
            [sys.executable, str(_REPO / "skills/decision-card/scripts/replay.py"),
             decision_id, "--check"],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"replay check 无法完成: {e}"
    detail = (r.stdout or r.stderr).strip().splitlines()
    return r.returncode == 0, (detail[0] if detail else f"exit={r.returncode}")


def _qualified_vetoes(
    decision_ids: Iterable[str],
    *,
    loader: Callable[[str], object | None] | None = None,
    replay_checker: Callable[[str], tuple[bool, str]] | None = None,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """返回 (合格否决, 被拒候选)。

    合格必须同时满足：
    1) ``risk.stance == VETO_STANCE``；
    2) Card.status 为 AVOID/BLOCK；
    3) ``replay --check`` 通过。
    """
    load = loader or db.load_online_card
    replay = replay_checker or _run_replay_check
    ok: list[tuple[str, str]] = []
    rejected: list[tuple[str, str]] = []

    for did in decision_ids:
        card = load(did)
        if card is None:
            continue
        risk_veto = any(
            getattr(v, "agent", None) == "risk"
            and getattr(v, "stance", None) == VETO_STANCE
            for v in getattr(card, "verdicts", ())
        )
        if not risk_veto:
            continue

        status = str(getattr(card, "status", ""))
        if status not in VETO_CARD_STATUSES:
            rejected.append((did, f"risk 否决但 Card.status={status!r}"))
            continue

        replay_ok, detail = replay(did)
        if not replay_ok:
            rejected.append((did, f"replay --check 未通过：{detail}"))
            continue
        ok.append((did, status))

    return ok, rejected


def main(argv: list[str] | None = None) -> int:
    del argv  # 当前无参数；保留签名便于测试和未来扩展。
    try:
        rows = _ml.collect()
        with db.connect(readonly=True) as conn:
            decision_ids = [r["decision_id"] for r in conn.execute(
                "SELECT DISTINCT decision_id FROM decision_records "
                "WHERE replay_of IS NULL ORDER BY decision_id"
            )]
    except StoreNotInitialised as e:
        print(f"\n🔶 判不了 —— {e}")
        return _v.UNKNOWN

    print("══ Phase 2 出口条件（严格机器判定）══\n")

    ok4, hit, days = _missing_gate(rows)
    print(f"{'✅' if ok4 else '⬜'} 条件 4 · 源级真实 missing ≥{REQUIRED_MISSING_CARDS} 张且跨天")
    by_day: dict[str, int] = {}
    for row in hit:
        by_day[row["day"]] = by_day.get(row["day"], 0) + 1
    for day in sorted(by_day):
        print(f"      {day}  {by_day[day]} 张")
    print(f"      ⇒ {len(hit)} 张卡，跨 {len(days)} 天；"
          "--break-source / supervisor / legacy 不计入\n")

    vetoes, rejected = _qualified_vetoes(decision_ids)
    ok3 = bool(vetoes)
    print(f"{'✅' if ok3 else '⬜'} 条件 3 · 真实 Risk Veto + AVOID/BLOCK + replay --check")
    for did, status in vetoes:
        print(f"      {did}  status={status}  replay=PASS")
    for did, reason in rejected:
        print(f"      ⚠️ {did} 不计入：{reason}")
    if not vetoes:
        print("      尚无满足三项严格判据的真实否决证据。")
        print("      可以等待未来实时行情命中，也可以用未篡改的真实历史市场快照做验收。")
    print()

    return _v.PASS if (ok3 and ok4) else _v.FAIL


if __name__ == "__main__":
    raise SystemExit(main())
