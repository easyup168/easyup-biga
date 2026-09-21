"""出卡预算闸门 —— 在**花钱之前**拦一次。

为什么需要它
------------
接飞书之后，**手机上一句话就能触发一次真实出卡**：实测 198s / $1.37。
在那之前每次出卡都要人手工敲一条命令，误触的代价是零；现在不是了。

2026-09-21 当天出了 18 张卡，约 $23。连点两下就是多花一次。

🔴 去重挡不住这个
-----------------
外部设计文档提到了「飞书事件去重」—— 那挡的是**飞书重发同一个事件**，
挡不住**人连点两下**：那是两个不同的事件，去重会让它们都通过。

放在哪
------
放在 **Stage 0 占号**这个咽喉点上。任何路径（CLI / 飞书 / 将来的 cron）
要跑一次真实出卡，都必须先占号 —— 这是唯一一处**绕不过去**的地方。

> 守卫放在唯一入口，不是五个调用点。
> 同一个 bug 能同时活在五个文件里，就是因为每个调用点各写一遍。

⚠️ 它拦的是**新占号**，不是已经在跑的运行。已经花掉的钱拦不住，
   能拦的只有下一次。

覆盖什么 / 不覆盖什么
---------------------
- 覆盖：频率、当日总量、以及「上一次还在跑」
- **不覆盖**：这次该不该跑（那是人的判断）、单次成本（那由模型与数据量决定）
"""

from __future__ import annotations

import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from _contract import now_cn  # noqa: E402
from _store import StoreNotInitialised, db  # noqa: E402

__all__ = ["MIN_GAP_SEC", "DAILY_CAP", "INFLIGHT_SEC", "check_budget"]


def _envint(name: str, default: int) -> int:
    """环境变量可覆盖 —— 只为测试与「今天确实要多跑」准备，不是常规开关。"""
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


#: 两次出卡之间的最小间隔。
#: 🔴 取值依据是**实测单次耗时**（盘中 172.6s / 盘后 198s）而不是拍脑袋：
#:    比一次运行还短的间隔没有意义 —— 那时上一张卡还没出来。
#:    留一倍余量。
MIN_GAP_SEC = _envint("BIGA_MIN_GAP_SEC", 400)

#: 当日上限。2026-09-21 是开发日，出了 18 张；正常使用远低于此。
#: ⚠️ 定这个数的目的不是省钱，是**让失控可见** —— 真要跑第 21 次，
#:    应该是一个有意识的决定（`--force`），而不是手滑。
DAILY_CAP = _envint("BIGA_DAILY_CAP", 20)

#: 占了号但还没出卡，多久之内算「还在跑」。
#: 🔴 必须有这个窗口：库里有 3 个 2026-09-21 上午的占号来自冒烟测试，
#:    它们**永远不会**出卡。不设窗口的话闸门会永久关死。
INFLIGHT_SEC = _envint("BIGA_INFLIGHT_SEC", 600)


def check_budget(*, day: str | None = None,
                 path: pathlib.Path | str | None = None) -> list[str]:
    """返回拒绝理由（空列表 = 放行）。

    🔴 **只读，不写。** 它不占号、不落库 —— 否则「检查一下能不能跑」
    本身就会消耗配额，而那正是这类闸门最常见的设计错误。
    """
    now = now_cn()
    today = day or now.strftime("%Y%m%d")
    reasons: list[str] = []

    # 🔴 全新环境里库还不存在 —— 那是**正常状态**，不是错误。
    #    Stage 0 是整条链路的第一步，它必须能在空环境里独立跑起来
    #    （外部评审 P2-2 已经为 `reserve_decision_id` 修过同一件事，
    #     而这个闸门排在它**前面**，于是把坑原样重踩了一遍）。
    #    没有历史 ⇒ 没有可限的东西 ⇒ 放行。
    # ⚠️ try 必须包住 `with` 而不是 `db.connect(...)` 本身 ——
    #    它是 contextmanager，异常在 `__enter__` 时才抛。
    #    第一版写在外面，测试原样报同一个错，那是探针在告诉我写错了层。
    try:
        with db.connect(path, readonly=True) as conn:
            reserved = conn.execute(
                "SELECT decision_id, reserved_at, reserved_by FROM decision_ids"
                " WHERE decision_id LIKE ? ORDER BY reserved_at DESC",
                (f"BIGA-{today}-%",)).fetchall()
            carded = {r["decision_id"] for r in conn.execute(
                "SELECT decision_id FROM decision_records"
                " WHERE decision_id LIKE ?", (f"BIGA-{today}-%",))}
    except StoreNotInitialised:
        return []

    if len(reserved) >= DAILY_CAP:
        reasons.append(
            f"当日已占 {len(reserved)} 个号，达到上限 {DAILY_CAP}。"
            f"（当日已出卡 {len(carded)} 张）")

    if reserved:
        last = reserved[0]
        try:
            gap = (now - _parse(last["reserved_at"])).total_seconds()
        except ValueError:
            gap = MIN_GAP_SEC          # 解析不了就不拿它当拒绝理由
        if gap < MIN_GAP_SEC:
            reasons.append(
                f"距上次占号只有 {int(gap)}s，最小间隔 {MIN_GAP_SEC}s"
                f"（{last['decision_id']}，by={last['reserved_by']}）。"
                f"一次出卡实测要 170~200s —— 这么快再来一次，多半是误触")

    # 「还在跑」：占了号、没出卡、且在窗口内
    inflight = [r for r in reserved
                if r["decision_id"] not in carded
                and _age(r["reserved_at"], now) < INFLIGHT_SEC]
    if inflight:
        ids = ", ".join(r["decision_id"] for r in inflight[:3])
        reasons.append(
            f"还有 {len(inflight)} 次运行没出卡且在 {INFLIGHT_SEC}s 窗口内（{ids}）。"
            f"等它结算 —— 两次运行交叠出过事故（见 architecture.md §5.3.2）")

    return reasons


def _parse(ts: str):
    from datetime import datetime
    return datetime.fromisoformat(ts)


def _age(ts: str, now) -> float:
    try:
        return (now - _parse(ts)).total_seconds()
    except ValueError:
        return float("inf")           # 解析不了 ⇒ 当成很久以前，不算在跑


def explain(reasons: list[str]) -> str:
    """把拒绝理由写成人话 —— **报错要指路**，只说「不行」的闸门会被绕过。"""
    body = "\n".join(f"  · {r}" for r in reasons)
    return (
        "🔴 出卡预算闸门拒绝了这次请求：\n" + body + "\n\n"
        "  一次真实出卡 = 约 3 分钟 / $1.2~1.4，不是瞬时操作。\n"
        "  确实要跑就加 --force（并说明理由），或调 BIGA_MIN_GAP_SEC / BIGA_DAILY_CAP。\n"
        "  想看当前用量：python3 tools/verify/budget_report.py"
    )
