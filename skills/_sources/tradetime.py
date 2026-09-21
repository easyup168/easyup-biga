"""交易日 → `as_of` 的换算 —— **唯一实现**。

为什么这需要一个共享模块
------------------------
「这份数据描述的是哪一刻」是一条判据，不是一行算术。
日线 / 股池给的是**交易日**（`20260918`），而 `Evidence.as_of` 要的是**时刻**。
换算规则有一个容易漏的分支：

    交易日 == 今天，且现在还没到收盘 ⇒ 它描述的不是「今天收盘」，
    而是**此刻的盘中快照**。

🔴 这个分支不是学术问题，它会让契约层直接拒绝构造
--------------------------------------------------
`Evidence` 校验 `as_of <= retrieved_at`（数据不可能早于自身被取回）。
交易日盘中 10:00 采数据、却把 `as_of` 写成「今天 15:00」，
就是 `as_of > retrieved_at` —— **`ValueError`，整个 skill 崩掉，
连一条 missing 都留不下**。

实测复现（2026-09-20）::

    Evidence(as_of=2026-09-21T15:00+08:00, retrieved_at=2026-09-21T10:00+08:00)
    → ValueError: as_of 晚于 retrieved_at

Phase 1 一直没撞上，纯粹因为那几天的实测都发生在**收盘后或非交易日**。
而 BigA 是短线决策系统，**盘中才是主场景**。

⇒ 两个 skill 必须用同一套换算。各写一遍，就会有一个记得这个分支、另一个不记得。
"""

from __future__ import annotations

import sys
from datetime import date, datetime, time as dtime

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from _contract import CN_TZ  # noqa: E402

__all__ = ["MARKET_CLOSE", "as_of_for_trade_date", "market_is_open",
           "session_in_progress"]

#: A 股收盘时刻。收盘后，当日数据描述的是「全天结果」。
MARKET_CLOSE = dtime(15, 0, 0)

#: 连续竞价时段。早盘 9:30–11:30，午盘 13:00–15:00。
_SESSIONS = ((dtime(9, 30), dtime(11, 30)), (dtime(13, 0), dtime(15, 0)))


def market_is_open(now: datetime) -> bool:
    """此刻**真的**在连续竞价时段内吗。

    与 `session_in_progress` 的区别 —— 两者回答的不是同一个问题：

    * `session_in_progress`：**这批数据**所属的交易日还没过完吗
      （用途：区分「这个数真的是 0」与「这一天还没产生这个数」）
    * `market_is_open`：**此刻**市场在不在交易
      （用途：只在市场活着时才启用「源静默 = 故障」这类判据）

    前者在周六上午也会是 True（那天的数据确实还没「过完」），
    所以不能拿它当后者用。

    ⚠️ **已知边界：不认节假日。** 本系统还没有交易日历
    （Phase 3 的数据层加厚才会有）。

    后果是可控的、且朝安全方向：节假日这里会返回 True，
    依赖它的静默判据可能多报一条缺失项 ——
    **多一条缺失项只会让 Card 更保守，不会让它更激进**（红线 R-3）。
    调用方在写缺失项文案时要把「也可能是休市日」一并说出来，
    否则读的人会去查一个不存在的故障。
    """
    n = now.astimezone(CN_TZ)
    if n.weekday() >= 5:            # 周六 / 周日
        return False
    return any(a <= n.time() < b for a, b in _SESSIONS)


def as_of_for_trade_date(
    trade_date: str,
    *,
    retrieved_at: datetime,
) -> tuple[datetime, str | None]:
    """把交易日换成这份数据真正描述的时刻。

    Args:
        trade_date: ``YYYYMMDD``，数据自己声明的交易日。
        retrieved_at: 取回这份数据的时刻（带时区）。

    Returns:
        ``(as_of, warning)``。`warning` 非空时调用方**必须**把它放进
        `AgentVerdict.warnings` —— 盘中快照与收盘数据不是一回事，
        不标出来就会让读卡的人以为看到的是全天结果。

    Raises:
        ValueError: `trade_date` 不是 ``YYYYMMDD``。
    """
    if len(trade_date) != 8 or not trade_date.isdigit():
        raise ValueError(f"trade_date 必须是 YYYYMMDD，收到 {trade_date!r}")

    d = datetime.strptime(trade_date, "%Y%m%d").date()
    close = datetime.combine(d, MARKET_CLOSE, tzinfo=CN_TZ)
    today: date = retrieved_at.astimezone(CN_TZ).date()

    if d < today:
        return close, None

    if d > today:
        # 数据声称的交易日在未来 —— 不合理，但不能自己把它改掉。
        # 退回「取回时刻」，并如实说出来。
        return retrieved_at, (
            f"数据源声称的交易日 {trade_date} 晚于当前日期 {today:%Y%m%d}，"
            "已改用取回时刻作为 as_of"
        )

    # trade_date == today
    if retrieved_at >= close:
        return close, None

    return retrieved_at, (
        f"{trade_date} 尚未收盘（现在 {retrieved_at:%H:%M}），"
        "这是**盘中快照**而不是全天结果"
    )


def session_in_progress(trade_date: str, *, retrieved_at: datetime) -> bool:
    """这批数据所属的交易时段**还在进行中**吗。

    用途：区分「这个数真的是 0」与「这一天还没产生这个数」。

    🔴 实测（2026-09-21 周一 09:05，开盘前）
    ----------------------------------------
    ============  ==========================  ====================
    源            盘前返回                      如果当成事实
    ============  ==========================  ====================
    东财股池       `tc=0`，`qdate=今天`          「今日涨停 0 家」⇒ 冰点
    东财涨跌家数    `0/0/0`                     「全市场无人交易」
    东财板块榜      496 行全 `pct=0`             「所有板块都平盘」
    新浪日线 / 腾讯  **保留上一交易日**            正确
    ============  ==========================  ====================

    三个实时源都会给出**看起来合法的 0**，而两个历史源保留旧值 ——
    **同一时刻，不同端点对「新一天还没开始」的表现是相反的。**
    所以不能靠「有没有数据」判断，每个源都要自己判断。
    """
    d = datetime.strptime(trade_date, "%Y%m%d").date()
    now = retrieved_at.astimezone(CN_TZ)
    return d == now.date() and now.time() < MARKET_CLOSE
