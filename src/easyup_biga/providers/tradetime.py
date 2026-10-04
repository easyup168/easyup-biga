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

import pathlib
from datetime import date, datetime, time as dtime

# 🔴 批 U-I：这里原来有一段 `sys.path.insert(0, 仓库根/"skills")` 的自举——
#    它存在的唯一理由是让下面那行当时写作 `from _contract import CN_TZ` 能解析
#    （经薄壳 → 薄壳再把 src/ 挂上）。本批把跨包引用改成 `easyup_biga.*` 绝对
#    导入之后，那段自举**连一个消费方都不剩**：本模块要的 `easyup_biga.domain`
#    与 `easyup_biga.persistence` 跟 skills/ 无关。
#
#    ⚠️ 删它不属于批 U-III（"逐一核实 sys.path.insert 还有没有用"）——
#    U-III 要判断的是"这行现在还有没有用"，而这一处的唯一用途是**同一次编辑
#    删掉的那行**，不需要判断。留着它只会留下一条指向已不存在的机制的注释。
from easyup_biga.domain import CN_TZ

__all__ = ["MARKET_CLOSE", "as_of_for_trade_date", "as_of_for_undated_snapshot",
           "market_is_open", "session_in_progress"]

#: A 股收盘时刻。收盘后，当日数据描述的是「全天结果」。
MARKET_CLOSE = dtime(15, 0, 0)

#: 连续竞价时段。早盘 9:30–11:30，午盘 13:00–15:00。
_SESSIONS = ((dtime(9, 30), dtime(11, 30)), (dtime(13, 0), dtime(15, 0)))


def market_is_open(now: datetime, *, path: pathlib.Path | str | None = None) -> bool:
    """此刻**真的**在连续竞价时段内吗。

    与 `session_in_progress` 的区别 —— 两者回答的不是同一个问题：

    * `session_in_progress`：**这批数据**所属的交易日还没过完吗
      （用途：区分「这个数真的是 0」与「这一天还没产生这个数」）
    * `market_is_open`：**此刻**市场在不在交易
      （用途：只在市场活着时才启用「源静默 = 故障」这类判据）

    前者在周六上午也会是 True（那天的数据确实还没「过完」），
    所以不能拿它当后者用。

    节假日感知（批 L）
    ------------------
    有交易日历数据时以它为准，没有时回退到纯 weekday 判据：

    * `fact_trading_calendar` 里查得到今天 ⇒ 直接用它的「开/休」标志
      （法定节假日会被正确判成休市 ⇒ 返回 False）；
    * 查不到（日历还没抓到、或超出已抓月份）⇒ 回退到「非周末即可能开市」，
      **结果与批 L 之前逐一相同**（探针 P5）。

    🔴 **回退是朝安全方向的**（红线 R-3）：查不到就当「可能开市」，依赖它的静默
    判据顶多**多报**一条缺失项，让 Card 更保守 —— 绝不把「查不到」当成「休市」，
    那会在真实交易日里以为休市，是危险得多的方向。调用方写缺失项文案时，若日历
    没覆盖到，仍要把「也可能是休市日」一并说出来。

    ⚠️ 关于 `path`：默认读默认库。日历数据由 `szse.refresh_trading_calendar` 在**能
    连通深交所的环境**里填入；本项目当前 WSL 部署连不通深交所（见 `szse.py` 模块头），
    在那里 `fact_trading_calendar` 为空 ⇒ 恒走回退分支。这不是缺陷，是网络可达性事实，
    退化方向安全。
    """
    n = now.astimezone(CN_TZ)
    # 🔴 惰性 import：让本模块的 import 图保持纯（时间数学层不在导入期拉起存储层），
    #    只有真的要查日历时才碰持久化层。
    from easyup_biga.persistence import is_trading_day

    known = is_trading_day(n.strftime("%Y%m%d"), path=path)   # True / False / None
    if known is None:
        # 日历没覆盖到 —— 先问兜底表，再退到 weekday。
        known = holiday_fallback(n.date())
    if known is None:
        # 兜底表也不认 —— 回退到「非周末即可能开市」（与批 L 之前逐一相同）。
        if n.weekday() >= 5:            # 周六 / 周日
            return False
    elif not known:
        return False                    # 日历明确说这天休市（法定节假日或周末）
    # known is True（确认交易日），或 known is None 且是工作日 —— 再看是否落在竞价时段。
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


def as_of_for_undated_snapshot(
    *,
    retrieved_at: datetime,
    latest_trade_date: str | None,
) -> tuple[datetime, str | None]:
    """**不带日期的实时端点**（涨跌家数、板块榜）这份数据真正描述的时刻。

    为什么不能直接用「取回时刻」，也不能直接用「日线的交易日」
    ---------------------------------------------------------
    两个方向都踩过：

    1. **用日线的交易日** —— F4 实测（`BIGA-20260921-017`，周一 12:41 午休）：
       涨跌家数取回的是**今天此刻**的 4385/1100/145，而日线还停在上周五
       ⇒ 卡面写着 `as_of 09-18 15:00`。**一个今天的数，挂着上周五的时间戳。**

    2. **用取回时刻** —— 2026-09-26 周六实测：这个端点返回的其实是
       **09-24 收盘**的 1120/4305/137（它自己不会说），而 `as_of` 标成
       `09-26 18:11`。**一个上周四的数，挂着周六的时间戳。**
       后果是 risk 判「上游报告了不同的交易日，不能当作同一天的事实一起审」，
       整张卡降级成 WAIT —— 而那个「不一致」是我们自己标出来的。

    > 两次都不是取错了数，是**给对的数配了错的时刻**。而它不报错。

    判据（三态，靠日历不靠推算）
    ----------------------------
    ====================  ================================  ==================
    此刻                   端点给的是什么                      `as_of`
    ====================  ================================  ==================
    交易日 & 收盘前         今天的盘中快照（午休也算）           取回时刻 + 盘中警告
    交易日 & 收盘后         今天的收盘                         今天 15:00
    **非交易日**            **最近交易日的收盘**（不再变化）      **那天 15:00** + 警告
    ====================  ================================  ==================

    Args:
        retrieved_at: 取回这份数据的时刻（带时区）。
        latest_trade_date: 最近一个交易日（``YYYYMMDD``），由
            `persistence.latest_trading_day` 查日历得到。
            **`None` = 日历没覆盖到** ⇒ 退回取回时刻并如实说出来（R-3）。

    Returns:
        ``(as_of, warning)``。`warning` 非空时调用方**必须**放进 warnings。
    """
    if latest_trade_date is None:
        # 🔴 不推算。「往前找第一个非周末」在长假里会给出错误答案，
        #    而错误答案和正确答案长得一模一样。
        return retrieved_at, (
            "交易日历没覆盖到今天 ⇒ 无法判断这份快照属于哪个交易日，"
            "as_of 退回取回时刻。**非交易日时它会偏晚**"
        )

    as_of, warning = as_of_for_trade_date(
        latest_trade_date, retrieved_at=retrieved_at)
    today = retrieved_at.astimezone(CN_TZ).strftime("%Y%m%d")
    if latest_trade_date < today:
        return as_of, (
            f"今天（{today}）不是交易日 ⇒ 这份不带日期的快照是 "
            f"{latest_trade_date} 的收盘值，as_of 已对齐到那天"
        )
    return as_of, warning


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


# ─────────────────────────────────────────────── 第 3 层：硬编码兜底（带过期守卫）
#
# 🔴 它只在**日历表没覆盖到**那一天时才被问到（见 `market_is_open`）。
#    正常路径是 `fact_trading_calendar`（由 `providers/sina_calendar.py` 刷新，
#    含交易所已公布的未来排期）。这一层管的是「刷新还没跑过 / 跑失败了」。
#
# ⚠️ **硬编码表会烂，而且是静默地烂。** 实测证据：一个同类系统的硬编码表把
#    2026 年的中秋写成「与国庆合并」（只列了 10 月那几天），于是 2026-09-25
#    这个真实的休市日不在表里 —— 它的权威源一旦不可达，那天就会被判成交易日。
#    表本身写得很认真，注释也写了维护规则，照样烂了。
#
# ⇒ 所以这一层带**过期守卫**：`_COVER_THROUGH` 之后一律返回 None（交给 weekday
#   回退），**绝不乐观地答 True**。并且 `tests/test_tradetime.py` 有一条会在
#   到期前若干天就变红的测试 —— 把「静默地烂」换成「按期响亮地提醒」。
#
#: 兜底表的覆盖**区间**（两端含）。区间外 ⇒ 这一层弃权，返回 None。
#:
#: 🔴 **下界和上界一样重要。** 第一版只有上界，于是这一层对 `_COVER_THROUGH`
#: 之前的任何一天都敢答 —— 而 `_HOLIDAYS` 只列了导出那天之后的假期，
#: 结果 2026-01-01（元旦）被自信地判成交易日。
#: 「不在假期表里 ⇒ 是交易日」这句话，只有在**表确实覆盖那一天**时才成立。
#: 更早的日期由 `fact_trading_calendar` 负责（刷新默认往回覆盖 730 天）。
_COVER_FROM = datetime(2026, 9, 25, tzinfo=CN_TZ).date()
_COVER_THROUGH = datetime(2026, 12, 31, tzinfo=CN_TZ).date()

#: 覆盖区间内的**法定休市工作日**（周末不列，第 1 层已经短路）。
#:
#: 🔴 **这份表是从权威源导出的，不是手抄的**，导出方式见
#: `tools/verify/dump_holiday_fallback.py`（同一个解码器，同一份数据）。
#:
#: ⚠️ 第一版真的是手抄的——照着另一份同类系统的假期表抄了 2026 年，把
#: **20261008 抄成了休市日**，而它是国庆后的复市日、是交易日。抄的时候那张表
#: 看起来很可信（有分组注释、有维护规则）。这件事发生在我写完上面那段
#: 「硬编码表会烂」的注释**之后的十分钟内** —— 所以判据只能是「从数据导出」，
#: 不能是「仔细一点」。
_HOLIDAYS: frozenset[str] = frozenset({
    "20260925",                                     # 中秋
    "20261001", "20261002", "20261005",             # 国庆（10-08 复市，是交易日）
    "20261006", "20261007",
})


def holiday_fallback(day: "date") -> bool | None:
    """兜底层：`True` 交易日 / `False` 法定休市 / `None` **本层不敢答**。

    🔴 `None` 是这层存在的关键，不是缺省值：超出 `_COVER_THROUGH` 就弃权，
    而不是「不在假期表里 ⇒ 是交易日」。后者正是硬编码表烂掉时的失败形状 ——
    表停止更新之后，每一个新的节假日都会被悄悄判成交易日。
    """
    if not (_COVER_FROM <= day <= _COVER_THROUGH):
        return None
    if day.weekday() >= 5:
        return False
    return day.strftime("%Y%m%d") not in _HOLIDAYS
